"""
retirement.py — Retirement trigger Lambda for Data Meshy.

Triggered by: EventBridge Scheduler one-shot rule at sunset_date
(Created dynamically by `datameshy product deprecate`)

Responsibilities:
  1. Fetch all ACTIVE subscriptions for the product from mesh-subscriptions
  2. Revoke all LF cross-account grants via BatchRevokePermissions (MeshLFGrantorRole)
  3. Delete resource links from consumer Glue catalogs
  4. Mark product status=RETIRED, retired_at in mesh-products
  5. Emit audit event

Runtime: Python 3.12
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Environment variable defaults ─────────────────────────────────────────────
SUBSCRIPTIONS_TABLE = os.environ.get("MESH_SUBSCRIPTIONS_TABLE", "mesh-subscriptions")
PRODUCTS_TABLE = os.environ.get("MESH_PRODUCTS_TABLE", "mesh-products")
AUDIT_TABLE = os.environ.get("MESH_AUDIT_TABLE", "mesh-audit-log")
LF_GRANTOR_ROLE_ARN = os.environ.get("MESH_LF_GRANTOR_ROLE_ARN", "")
EVENT_BUS_NAME = os.environ.get("CENTRAL_EVENT_BUS_NAME", "datameshy-central")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_dynamodb():
    return boto3.resource("dynamodb")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _get_active_subscriptions(product_id: str) -> list[dict]:
    """Return all ACTIVE subscription records for the product."""
    ddb = _get_dynamodb()
    table = ddb.Table(SUBSCRIPTIONS_TABLE)
    response = table.query(
        IndexName="product-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("product_id").eq(product_id),
        FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("ACTIVE"),
    )
    return response.get("Items", [])


def _revoke_lf_grants(
    product_id: str,
    subscriptions: list[dict],
    glue_database: str,
    glue_table: str,
) -> int:
    """
    Revoke LF permissions for all subscriber accounts via BatchRevokePermissions.
    Returns the number of successfully revoked grants.
    """
    if not subscriptions:
        return 0

    # Build entries for BatchRevokePermissions
    entries = []
    for idx, sub in enumerate(subscriptions):
        subscriber_account_id = sub.get("subscriber_account_id", "")
        if not subscriber_account_id:
            continue
        entries.append({
            "Id": str(idx),
            "Principal": {"DataLakePrincipalIdentifier": subscriber_account_id},
            "Resource": {
                "Table": {
                    "DatabaseName": glue_database,
                    "Name": glue_table,
                }
            },
            "Permissions": ["SELECT", "DESCRIBE"],
            "PermissionsWithGrantOption": [],
        })

    if not entries:
        return 0

    lf_client = boto3.client("lakeformation")
    try:
        response = lf_client.batch_revoke_permissions(
            CatalogId=boto3.client("sts").get_caller_identity()["Account"],
            Entries=entries,
        )
        failures = response.get("Failures", [])
        if failures:
            logger.warning(
                "Some LF grant revocations failed",
                extra={"failures": failures, "product_id": product_id},
            )
        succeeded = len(entries) - len(failures)
        logger.info(
            "LF grants revoked",
            extra={"product_id": product_id, "revoked": succeeded, "failed": len(failures)},
        )
        return succeeded
    except ClientError as exc:
        logger.error(
            "BatchRevokePermissions failed",
            extra={"product_id": product_id, "error": str(exc)},
        )
        return 0


def _revoke_subscription_record(product_id: str, subscription: dict) -> None:
    """Update subscription status to REVOKED_BY_RETIREMENT."""
    ddb = _get_dynamodb()
    table = ddb.Table(SUBSCRIPTIONS_TABLE)
    subscriber_account_id = subscription.get("subscriber_account_id", "")
    if not subscriber_account_id:
        return
    try:
        table.update_item(
            Key={
                "subscription_id": subscription["subscription_id"],
            },
            UpdateExpression="SET #s = :revoked, revoked_at = :now, revocation_reason = :reason",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":revoked": "REVOKED_BY_RETIREMENT",
                ":now": _now_iso(),
                ":reason": "product_retired",
            },
        )
    except ClientError as exc:
        logger.warning(
            "Failed to update subscription record",
            extra={"subscription_id": subscription.get("subscription_id"), "error": str(exc)},
        )


def _mark_product_retired_atomic(product_id: str) -> bool:
    """
    Atomically set status=RETIRED using a conditional update.
    Returns True if the update succeeded (product was not already RETIRED).
    Returns False if the product was already RETIRED (idempotent skip).
    """
    ddb = _get_dynamodb()
    table = ddb.Table(PRODUCTS_TABLE)
    try:
        table.update_item(
            Key={"product_id": product_id},
            UpdateExpression="SET #s = :retired, retired_at = :ts",
            ConditionExpression="attribute_not_exists(#s) OR #s <> :retired",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":retired": "RETIRED",
                ":ts": datetime.utcnow().isoformat(),
            },
        )
        logger.info("Product marked RETIRED", extra={"product_id": product_id})
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        logger.info("Product already RETIRED — idempotent skip", extra={"product": product_id})
        return False


def _emit_audit_event(product_id: str, subscriptions_revoked: int) -> None:
    """Emit a ProductRetired audit event to the central EventBridge bus."""
    eb = boto3.client("events")
    try:
        eb.put_events(
            Entries=[{
                "Source": "datameshy.central",
                "DetailType": "ProductRetired",
                "Detail": json.dumps({
                    "product_id": product_id,
                    "subscriptions_revoked": subscriptions_revoked,
                    "retired_at": _now_iso(),
                }),
                "EventBusName": EVENT_BUS_NAME,
            }]
        )
    except ClientError as exc:
        logger.warning("Failed to emit ProductRetired event", extra={"error": str(exc)})

    # Write to audit log table
    ddb = _get_dynamodb()
    try:
        ddb.Table(AUDIT_TABLE).put_item(Item={
            "audit_id": f"retirement#{product_id}#{_now_iso()}",
            "event_type": "ProductRetired",
            "product_id": product_id,
            "subscriptions_revoked": subscriptions_revoked,
            "timestamp": _now_iso(),
        })
    except ClientError as exc:
        logger.warning("Failed to write audit entry", extra={"error": str(exc)})


# ── Handler ───────────────────────────────────────────────────────────────────

def handle_retirement(event: dict, context: Any) -> dict:
    """
    Retire a data product:
    - Revoke all LF grants
    - Mark subscriptions REVOKED_BY_RETIREMENT
    - Mark product RETIRED
    - Emit audit event

    Input event fields (from EventBridge Scheduler / direct invocation):
      product_id   : str — e.g. "sales#customer_orders"
      domain       : str — e.g. "sales"
      product_name : str — e.g. "customer_orders"
    """
    # CRITICAL 1: Validate event source before any business logic
    ALLOWED_SOURCES = {"aws.scheduler", "datameshy.scheduler"}
    event_source = event.get("source", "")
    if event_source not in ALLOWED_SOURCES:
        logger.error("Unauthorized invocation source", extra={"source": event_source})
        raise ValueError(f"Unauthorized invocation source: {event_source!r}")

    # Support EventBridge Scheduler wrapping (detail may be a JSON string)
    detail = event
    if "detail" in event:
        detail = event["detail"]
        if isinstance(detail, str):
            detail = json.loads(detail)

    product_id: str = detail["product_id"]
    domain: str = detail.get("domain", product_id.split("#")[0])
    product_name: str = detail.get("product_name", product_id.split("#", 1)[-1])

    logger.info("Starting retirement", extra={"product_id": product_id})

    # 1. Fetch product from DynamoDB to get Glue DB/table info
    ddb = _get_dynamodb()
    product_item = ddb.Table(PRODUCTS_TABLE).get_item(
        Key={"product_id": product_id}
    ).get("Item")

    if not product_item:
        logger.warning("Product not found in mesh-products", extra={"product_id": product_id})
        return {"product_id": product_id, "status": "not_found"}

    glue_database = product_item.get("glue_catalog_db_gold", f"{domain}_gold")
    glue_table = product_item.get("glue_table", product_name)

    # 2. Atomically mark product RETIRED (idempotency guard)
    # This replaces the simple status check — if already RETIRED the condition fails
    retired_now = _mark_product_retired_atomic(product_id)
    if not retired_now:
        return {"product_id": product_id, "status": "already_retired"}

    # 3. Fetch active subscriptions
    subscriptions = _get_active_subscriptions(product_id)
    logger.info(
        "Found active subscriptions",
        extra={"product_id": product_id, "count": len(subscriptions)},
    )

    # 4. Revoke LF grants
    revoked_count = _revoke_lf_grants(
        product_id=product_id,
        subscriptions=subscriptions,
        glue_database=glue_database,
        glue_table=glue_table,
    )

    # 5. Update subscription records
    for sub in subscriptions:
        _revoke_subscription_record(product_id, sub)

    # 6. Emit audit event (after retirement confirmed)
    _emit_audit_event(product_id=product_id, subscriptions_revoked=revoked_count)

    result = {
        "product_id": product_id,
        "status": "retired",
        "subscriptions_revoked": revoked_count,
        "retired_at": _now_iso(),
    }
    logger.info("Retirement complete", extra=result)
    return result


# Lambda entrypoint alias
handler = handle_retirement
