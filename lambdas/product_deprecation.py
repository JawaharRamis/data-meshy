"""
product_deprecation.py — Handles ProductDeprecated events for Data Meshy.

Triggered by: EventBridge rule matching detail-type == "ProductDeprecated"

Responsibilities:
  1. Read all ACTIVE subscriber accounts from mesh-subscriptions for the product
  2. Send an SNS notification to each subscriber's topic with the sunset date
  3. Write an audit entry via MeshAuditWriterRole

Runtime: Python 3.12
"""

import json
import logging
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Environment variable defaults ─────────────────────────────────────────────
SUBSCRIPTIONS_TABLE = os.environ.get("MESH_SUBSCRIPTIONS_TABLE", "mesh-subscriptions")
PRODUCTS_TABLE = os.environ.get("MESH_PRODUCTS_TABLE", "mesh-products")
AUDIT_TABLE = os.environ.get("MESH_AUDIT_TABLE", "mesh-audit-log")
# SNS topic name pattern in subscriber accounts (e.g. "datameshy-domain-notifications")
SUBSCRIBER_SNS_TOPIC_NAME = os.environ.get(
    "SUBSCRIBER_SNS_TOPIC_NAME", "datameshy-domain-notifications"
)
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_dynamodb():
    return boto3.resource("dynamodb")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _get_active_subscribers(product_id: str) -> list[dict]:
    """Return all ACTIVE subscription records for the product."""
    ddb = _get_dynamodb()
    table = ddb.Table(SUBSCRIPTIONS_TABLE)
    response = table.query(
        IndexName="product-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("product_id").eq(product_id),
        FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("ACTIVE"),
    )
    return response.get("Items", [])


def _notify_subscriber(
    subscriber_account_id: str,
    product_id: str,
    sunset_date: str,
    domain: str,
    product_name: str,
) -> None:
    """
    Send SNS notification to the subscriber's domain notification topic.
    Constructs the cross-account SNS topic ARN from the subscriber account ID.
    """
    topic_arn = (
        f"arn:aws:sns:{AWS_REGION}:{subscriber_account_id}:{SUBSCRIBER_SNS_TOPIC_NAME}"
    )
    sns = boto3.client("sns")
    subject = f"[DataMeshy] Data product '{domain}/{product_name}' will be retired on {sunset_date}"
    message = (
        f"NOTICE: The data product '{domain}/{product_name}' (ID: {product_id}) "
        f"has been marked DEPRECATED and will be retired on {sunset_date}.\n\n"
        "Action required:\n"
        "  - Update your pipelines to stop consuming this product before the sunset date.\n"
        "  - Contact the data product owner to discuss alternatives.\n"
        "  - No new subscriptions will be accepted for this product.\n\n"
        f"Sunset date: {sunset_date}\n"
        f"Product ID: {product_id}"
    )
    try:
        sns.publish(
            TopicArn=topic_arn,
            Subject=subject,
            Message=message,
            MessageAttributes={
                "event_type": {"DataType": "String", "StringValue": "ProductDeprecated"},
                "product_id": {"DataType": "String", "StringValue": product_id},
            },
        )
        logger.info(
            "SNS notification sent",
            extra={"subscriber_account": subscriber_account_id, "topic": topic_arn},
        )
    except ClientError as exc:
        logger.warning(
            "Failed to send SNS notification",
            extra={
                "subscriber_account": subscriber_account_id,
                "topic": topic_arn,
                "error": str(exc),
            },
        )


def _write_audit(product_id: str, sunset_date: str, notified_count: int) -> None:
    """Write a deprecation audit event to the audit log table."""
    ddb = _get_dynamodb()
    table = ddb.Table(AUDIT_TABLE)
    try:
        table.put_item(Item={
            "audit_id": f"deprecation#{product_id}#{_now_iso()}",
            "event_type": "ProductDeprecated",
            "product_id": product_id,
            "sunset_date": sunset_date,
            "subscribers_notified": notified_count,
            "timestamp": _now_iso(),
        })
    except ClientError as exc:
        logger.warning("Failed to write audit entry", extra={"error": str(exc)})


# ── Handler ───────────────────────────────────────────────────────────────────

def handle_product_deprecated(event: dict, context: Any) -> dict:
    """
    Handle the ProductDeprecated EventBridge event.

    Expected event detail fields:
      product_id   : str  — e.g. "sales#customer_orders"
      domain       : str  — e.g. "sales"
      product_name : str  — e.g. "customer_orders"
      sunset_date  : str  — ISO-8601 date, e.g. "2026-08-01"
      breaking     : bool — always True for deprecation

    Returns a summary dict with the count of notified subscribers.
    """
    detail = event.get("detail", event)  # Support both EventBridge wrapper and direct invocation
    if isinstance(detail, str):
        detail = json.loads(detail)

    product_id: str = detail["product_id"]
    domain: str = detail.get("domain", product_id.split("#")[0])
    product_name: str = detail.get("product_name", product_id.split("#", 1)[-1])
    sunset_date: str = detail["sunset_date"]
    breaking: bool = detail.get("breaking", True)

    logger.info(
        "Processing ProductDeprecated event",
        extra={"product_id": product_id, "sunset_date": sunset_date, "breaking": breaking},
    )

    # 1. Find active subscribers
    subscribers = _get_active_subscribers(product_id)
    notified = 0

    # 2. Notify each subscriber
    for sub in subscribers:
        subscriber_account_id = sub.get("subscriber_account_id", "")
        if not subscriber_account_id:
            continue
        _notify_subscriber(
            subscriber_account_id=subscriber_account_id,
            product_id=product_id,
            sunset_date=sunset_date,
            domain=domain,
            product_name=product_name,
        )
        notified += 1

    logger.info(
        "Deprecation notifications sent",
        extra={"product_id": product_id, "notified": notified},
    )

    # 3. Write audit
    _write_audit(product_id=product_id, sunset_date=sunset_date, notified_count=notified)

    return {
        "product_id": product_id,
        "sunset_date": sunset_date,
        "subscribers_notified": notified,
        "status": "ok",
    }


# Lambda entrypoint alias
handler = handle_product_deprecated
