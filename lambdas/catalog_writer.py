"""
catalog_writer.py — Handles ProductCreated and ProductRefreshed events.

Responsibilities:
- ProductCreated: PutItem to mesh-products DynamoDB table
- ProductRefreshed: UpdateItem on mesh-products + PutItem to mesh-quality-scores
- Validates event source via event_validator before processing

Triggered by: EventBridge rules matching ProductCreated, ProductRefreshed
Runtime: Python 3.12
"""

import json
import logging
import os
import time
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

from event_validator import validate_event_source, check_dedup

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Table name defaults (matching governance module outputs)
DEFAULT_PRODUCTS_TABLE = "mesh-products"
DEFAULT_QUALITY_TABLE = "mesh-quality-scores"
DEFAULT_DOMAINS_TABLE = "mesh-domains"
DEFAULT_DEDUP_TABLE = "mesh-event-dedup"


def _get_table(env_key: str, default: str) -> str:
    return os.environ.get(env_key, default)


def handler(event, context):
    """
    Lambda handler for ProductCreated and ProductRefreshed events.

    Args:
        event: EventBridge event envelope
        context: Lambda context (unused)

    Returns:
        dict with status and action taken

    Raises:
        ValueError: for unhandled event types
        RuntimeError: for security failures (domain mismatch)
    """
    detail = event.get("detail", {})
    event_type = event.get("detail-type", "")
    event_id = detail.get("event_id", "")

    products_table = _get_table("MESH_PRODUCTS_TABLE", DEFAULT_PRODUCTS_TABLE)
    quality_table = _get_table("MESH_QUALITY_TABLE", DEFAULT_QUALITY_TABLE)
    domains_table = _get_table("MESH_DOMAINS_TABLE", DEFAULT_DOMAINS_TABLE)
    dedup_table = _get_table("MESH_EVENT_DEDUP_TABLE", DEFAULT_DEDUP_TABLE)

    logger.info("catalog_writer invoked", extra={
        "event_type": event_type, "event_id": event_id
    })

    # Step 1: Validate event source
    source_result = validate_event_source(event, domains_table)
    if source_result["status"] != "VALID":
        logger.error("Event source validation failed", extra={"result": source_result})
        raise RuntimeError(f"Event source validation failed: {source_result}")

    # Step 2: Dedup check
    dedup_result = check_dedup(event_id, dedup_table)
    if dedup_result["status"] == "DUPLICATE_EVENT":
        logger.info("Duplicate event, returning early", extra={"event_id": event_id})
        return {"status": "duplicate", "action": event_type}

    # Step 3: Route to handler
    dynamodb = boto3.resource("dynamodb")

    if event_type == "ProductCreated":
        return _handle_product_created(detail, dynamodb, products_table)
    elif event_type == "ProductRefreshed":
        return _handle_product_refreshed(detail, dynamodb, products_table, quality_table)
    else:
        raise ValueError(f"Unhandled event type: {event_type}")


def _handle_product_created(detail, dynamodb, products_table):
    """PutItem to mesh-products for a new data product."""
    table = dynamodb.Table(products_table)

    product_id = detail["product_id"]
    domain = detail["domain"]
    product_name = detail["product_name"]
    timestamp = detail.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    item = {
        "domain#product_name": product_id,
        "domain": domain,
        "product_name": product_name,
        "status": "ACTIVE",
        "owner": detail.get("owner", ""),
        "classification": detail.get("classification", "internal"),
        "description": detail.get("description", ""),
        "tags": detail.get("tags", []),
        "schema_version": detail.get("schema_version", 1),
        "created_at": timestamp,
        "last_refreshed_at": timestamp,
        "quality_score": 0,
        "sla": detail.get("sla", {}),
    }

    table.put_item(Item=item)

    logger.info("ProductCreated: wrote to catalog", extra={
        "product_id": product_id, "domain": domain
    })

    return {"status": "success", "action": "ProductCreated", "product_id": product_id}


def _handle_product_refreshed(detail, dynamodb, products_table, quality_table):
    """UpdateItem on mesh-products + PutItem to mesh-quality-scores."""
    product_id = detail["product_id"]
    domain = detail["domain"]
    quality_score = detail.get("quality_score", 0)
    rows_written = detail.get("rows_written", 0)
    timestamp = detail.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    # Convert float to Decimal for DynamoDB compatibility
    if isinstance(quality_score, float):
        quality_score = Decimal(str(quality_score))
    if isinstance(rows_written, float):
        rows_written = int(rows_written)

    # Update mesh-products
    prod_table = dynamodb.Table(products_table)
    prod_table.update_item(
        Key={"domain#product_name": product_id},
        UpdateExpression="SET last_refreshed_at = :ts, quality_score = :qs, "
                         "schema_version = :sv, rows_written = :rw",
        ExpressionAttributeValues={
            ":ts": timestamp,
            ":qs": quality_score,
            ":sv": detail.get("schema_version", 1),
            ":rw": rows_written,
        },
    )

    # Write quality score history
    q_table = dynamodb.Table(quality_table)
    q_table.put_item(
        Item={
            "product_id": product_id,
            "timestamp": timestamp,
            "quality_score": quality_score,
            "rows_written": rows_written,
            "domain": domain,
            "pipeline_execution_arn": detail.get("pipeline_execution_arn", ""),
        }
    )

    logger.info("ProductRefreshed: updated catalog and quality", extra={
        "product_id": product_id, "quality_score": quality_score
    })

    return {
        "status": "success",
        "action": "ProductRefreshed",
        "product_id": product_id,
        "quality_score": quality_score,
    }
