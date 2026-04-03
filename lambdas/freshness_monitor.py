"""
freshness_monitor.py — Daily cron that checks SLA freshness for all active products.

Responsibilities:
- Triggered by EventBridge Scheduler (daily cron: cron(0 6 * * ? *))
- Scans mesh-products table for all ACTIVE products
- For each product: checks last_refreshed_at vs sla.freshness_target
- If SLA breached: emits FreshnessViolation event to central bus + SNS alert
- Uses MeshCatalogWriterRole for DynamoDB reads

Triggered by: EventBridge Scheduler
Runtime: Python 3.12
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Table name defaults
DEFAULT_PRODUCTS_TABLE = "mesh-products"

# Default SNS topic for freshness alerts
DEFAULT_SNS_TOPIC_ARN = ""


def _get_table(env_key: str, default: str) -> str:
    return os.environ.get(env_key, default)


def parse_freshness_hours(freshness_target: str) -> float:
    """
    Parse a freshness_target string like '24 hours', '7 days', '4 hours' into hours.

    Args:
        freshness_target: Duration string from product SLA

    Returns:
        Number of hours as a float
    """
    if not freshness_target:
        return 24.0  # Default to 24 hours

    freshness_target = str(freshness_target).strip().lower()

    # Try "N hours" pattern
    match = re.match(r"(\d+(?:\.\d+)?)\s*hours?", freshness_target)
    if match:
        return float(match.group(1))

    # Try "N days" pattern
    match = re.match(r"(\d+(?:\.\d+)?)\s*days?", freshness_target)
    if match:
        return float(match.group(1)) * 24.0

    # Try plain number (assume hours)
    try:
        return float(freshness_target)
    except ValueError:
        return 24.0  # Default


def handler(event, context):
    """
    Lambda handler for freshness monitoring.

    Args:
        event: EventBridge Scheduler event
        context: Lambda context

    Returns:
        dict with products_checked count and violations count
    """
    products_table = _get_table("MESH_PRODUCTS_TABLE", DEFAULT_PRODUCTS_TABLE)
    sns_topic_arn = os.environ.get("FRESHNESS_SNS_TOPIC_ARN", DEFAULT_SNS_TOPIC_ARN)

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(products_table)

    now = datetime.now(timezone.utc)
    violations = []
    products_checked = 0

    # Scan all products (pagination handled automatically)
    try:
        response = table.scan()
        items = response.get("Items", [])

        # Handle pagination
        while "LastEvaluatedKey" in response:
            response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))
    except ClientError as exc:
        logger.error("Failed to scan mesh-products", extra={"error": str(exc)})
        return {"status": "error", "products_checked": 0, "violations": 0}

    for item in items:
        # Skip inactive products
        if item.get("status", "").upper() != "ACTIVE":
            continue

        products_checked += 1

        product_id = item.get("domain#product_name", "unknown")
        domain = item.get("domain", "unknown")
        product_name = item.get("product_name", "unknown")
        owner = item.get("owner", "")
        last_refreshed = item.get("last_refreshed_at", "")

        # Parse SLA freshness target
        sla = item.get("sla", {})
        freshness_target = sla.get("freshness_target", "24 hours") if isinstance(sla, dict) else "24 hours"
        max_age_hours = parse_freshness_hours(freshness_target)

        # Calculate age
        if not last_refreshed:
            # Never refreshed -> always a violation
            violations.append({
                "product_id": product_id,
                "domain": domain,
                "product_name": product_name,
                "reason": "never_refreshed",
                "owner": owner,
            })
            continue

        try:
            last_refreshed_dt = datetime.fromisoformat(
                last_refreshed.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            logger.warning("Could not parse last_refreshed_at", extra={
                "product_id": product_id, "last_refreshed_at": last_refreshed
            })
            continue

        age_hours = (now - last_refreshed_dt).total_seconds() / 3600.0

        if age_hours > max_age_hours:
            violations.append({
                "product_id": product_id,
                "domain": domain,
                "product_name": product_name,
                "reason": "sla_breached",
                "age_hours": round(age_hours, 1),
                "max_age_hours": max_age_hours,
                "owner": owner,
                "last_refreshed_at": last_refreshed,
            })

    # Emit FreshnessViolation events for each breach
    if violations:
        events_client = boto3.client("events")
        sns_client = boto3.client("sns") if sns_topic_arn else None

        for violation in violations:
            # Emit FreshnessViolation event to central bus
            violation_event = {
                "event_id": f"freshness-{violation['product_id']}-{int(now.timestamp())}",
                "domain": violation["domain"],
                "product_name": violation["product_name"],
                "product_id": violation["product_id"],
                "violation_reason": violation["reason"],
                "age_hours": violation.get("age_hours", 0),
                "sla_target_hours": violation.get("max_age_hours", 0),
                "last_refreshed_at": violation.get("last_refreshed_at", ""),
                "timestamp": now.isoformat(),
                "version": "1.0",
            }

            try:
                event_bus_arn = os.environ.get(
                    "CENTRAL_EVENT_BUS_ARN",
                    "arn:aws:events:us-east-1:000000000000:event-bus/mesh-central-bus"
                )
                events_client.put_events(
                    Entries=[
                        {
                            "EventBusName": event_bus_arn,
                            "Source": "datameshy.central",
                            "DetailType": "FreshnessViolation",
                            "Detail": json.dumps(violation_event),
                        }
                    ]
                )
            except ClientError as exc:
                logger.error("Failed to emit FreshnessViolation event", extra={
                    "product_id": violation["product_id"], "error": str(exc)
                })

            # Send SNS alert
            if sns_client and sns_topic_arn:
                try:
                    sns_client.publish(
                        TopicArn=sns_topic_arn,
                        Subject=f"Data Meshy: Freshness SLA Violation - {violation['product_name']}",
                        Message=json.dumps(violation_event, indent=2),
                    )
                except ClientError as exc:
                    logger.error("Failed to send SNS alert", extra={
                        "product_id": violation["product_id"], "error": str(exc)
                    })

    logger.info("Freshness monitor complete", extra={
        "products_checked": products_checked,
        "violations": len(violations),
    })

    return {
        "status": "success",
        "products_checked": products_checked,
        "violations": len(violations),
        "violation_details": violations if violations else [],
    }
