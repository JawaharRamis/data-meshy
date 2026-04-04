"""
datazone_connector.py — Bridges DataZone EventBridge approval events to the mesh workflow.

Handles DataZone "Subscription Grant Requested" events from EventBridge and
translates them into mesh subscription approval calls.

This allows product owners to approve subscriptions via the DataZone web UI
with the same effect as calling `datameshy subscribe approve` from the CLI.

Runtime: Python 3.12
"""

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Environment variables ──────────────────────────────────────────────────────
DATAZONE_DOMAIN_ID = os.environ.get("DATAZONE_DOMAIN_ID", "")
SUBSCRIPTIONS_TABLE = os.environ.get("MESH_SUBSCRIPTIONS_TABLE", "mesh-subscriptions")
PRODUCTS_TABLE = os.environ.get("MESH_PRODUCTS_TABLE", "mesh-products")
SFN_ARN = os.environ.get("SUBSCRIPTION_SFN_ARN", "")
EVENT_BUS_NAME = os.environ.get("CENTRAL_EVENT_BUS_NAME", "datameshy-central")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_dynamodb():
    return boto3.resource("dynamodb")


def _validate_datazone_domain(event: dict) -> bool:
    """
    Validate that the DataZone event originates from the expected domain.

    The DataZone domain ID is embedded in the event detail or as part of
    the event resources ARN.
    """
    detail = event.get("detail", {})
    event_domain_id = detail.get("domainId", "")

    if not DATAZONE_DOMAIN_ID:
        logger.warning("DATAZONE_DOMAIN_ID env var not set; skipping domain validation")
        return True

    if event_domain_id != DATAZONE_DOMAIN_ID:
        logger.error(
            "DataZone domain mismatch",
            extra={
                "expected": DATAZONE_DOMAIN_ID,
                "received": event_domain_id,
            },
        )
        return False

    return True


def _extract_subscription_info(event: dict) -> dict:
    """
    Parse the DataZone event detail to extract subscription information.

    DataZone "Subscription Grant Requested" detail shape (simplified):
    {
      "domainId": "dzd_...",
      "subscriptionId": "...",
      "subscribedListings": [{"id": "...", "name": "<domain/product>", ...}],
      "subscribedPrincipals": [{"id": "...", "type": "PROJECT", ...}],
      "requestReason": "...",
    }
    """
    detail = event.get("detail", {})

    datazone_subscription_id = detail.get("subscriptionId", "")
    request_reason = detail.get("requestReason", "")

    # Extract product reference from subscribedListings
    listings = detail.get("subscribedListings", [])
    product_id = ""
    if listings:
        listing = listings[0]
        product_id = listing.get("name", listing.get("id", ""))

    # Extract consumer project / account from subscribedPrincipals
    principals = detail.get("subscribedPrincipals", [])
    consumer_account_id = ""
    if principals:
        principal = principals[0]
        consumer_account_id = principal.get("accountId", principal.get("id", ""))

    requested_columns = detail.get("requestedColumns", [])

    return {
        "datazone_subscription_id": datazone_subscription_id,
        "product_id": product_id,
        "consumer_account_id": consumer_account_id,
        "requested_columns": requested_columns,
        "justification": request_reason,
    }


def _find_mesh_subscription(product_id: str, consumer_account_id: str) -> dict | None:
    """Locate a mesh subscription record by product_id + consumer_account_id."""
    ddb = _get_dynamodb()
    table = ddb.Table(SUBSCRIPTIONS_TABLE)
    resp = table.get_item(
        Key={
            "product_id": product_id,
            "subscriber_account_id": consumer_account_id,
        }
    )
    return resp.get("Item")


def _get_product(product_id: str) -> dict | None:
    ddb = _get_dynamodb()
    table = ddb.Table(PRODUCTS_TABLE)
    resp = table.get_item(Key={"domain#product_name": product_id})
    return resp.get("Item")


def _start_sfn(sfn_arn: str, input_payload: dict) -> str:
    """Start the subscription provisioner SFN."""
    import uuid
    sfn = boto3.client("stepfunctions")
    exec_name = f"dz-{uuid.uuid4()}"
    response = sfn.start_execution(
        stateMachineArn=sfn_arn,
        name=exec_name,
        input=json.dumps(input_payload),
    )
    return response["executionArn"]


def _emit_event(detail_type: str, detail: dict) -> None:
    """Put an event onto the central EventBridge bus."""
    eb = boto3.client("events")
    eb.put_events(
        Entries=[
            {
                "Source": "datameshy.central",
                "DetailType": detail_type,
                "Detail": json.dumps(detail),
                "EventBusName": EVENT_BUS_NAME,
            }
        ]
    )


def _approve_subscription(sub_info: dict) -> dict:
    """
    Translate a DataZone approval into a mesh subscription approval.

    Applies the same logic as handle_approve() in subscription_request.py:
    - Determine non-PII granted columns
    - Start the provisioner SFN
    - Emit SubscriptionApproved from datameshy.central
    """
    product_id = sub_info["product_id"]
    consumer_account_id = sub_info["consumer_account_id"]
    requested_columns = sub_info.get("requested_columns", [])

    product = _get_product(product_id)
    if product is None:
        raise ValueError(f"Product not found: {product_id}")

    # Filter out PII columns
    schema = product.get("schema", {})
    pii_cols = {c["name"] for c in schema.get("columns", []) if c.get("pii", False)}
    granted_columns = [c for c in requested_columns if c not in pii_cols]

    # Locate existing mesh subscription or prepare a synthetic subscription_id
    mesh_sub = _find_mesh_subscription(product_id, consumer_account_id)
    if mesh_sub:
        subscription_id = mesh_sub["subscription_id"]
        # Only proceed if in PENDING state
        if mesh_sub.get("status") not in ("PENDING",):
            logger.info(
                "Subscription not in PENDING state — DataZone approval ignored",
                extra={"subscription_id": subscription_id, "status": mesh_sub.get("status")},
            )
            return {"skipped": True, "reason": "not_pending"}
    else:
        logger.warning(
            "No mesh subscription found for DataZone event",
            extra={"product_id": product_id, "consumer": consumer_account_id},
        )
        return {"skipped": True, "reason": "no_mesh_subscription"}

    # Update status to APPROVED
    import time as time_mod
    now = time_mod.strftime("%Y-%m-%dT%H:%M:%SZ", time_mod.gmtime())
    ddb = _get_dynamodb()
    subs_table = ddb.Table(SUBSCRIPTIONS_TABLE)
    try:
        subs_table.update_item(
            Key={
                "product_id": product_id,
                "subscriber_account_id": consumer_account_id,
            },
            UpdateExpression="SET #s = :approved, updated_at = :now, approval_source = :src",
            ConditionExpression="#s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":approved": "APPROVED",
                ":pending": "PENDING",
                ":now": now,
                ":src": "datazone",
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.info("Subscription already approved or not pending")
            return {"skipped": True, "reason": "already_approved"}
        raise

    # Start provisioner SFN
    execution_arn = _start_sfn(
        SFN_ARN,
        {
            "subscription_id": subscription_id,
            "product_id": product_id,
            "consumer_account_id": consumer_account_id,
            "requested_columns": granted_columns,
        },
    )

    # Emit SubscriptionApproved — always from central, source datameshy.central
    _emit_event(
        "SubscriptionApproved",
        {
            "subscription_id": subscription_id,
            "product_id": product_id,
            "consumer_account_id": consumer_account_id,
            "granted_columns": granted_columns,
            "sfn_execution_arn": execution_arn,
            "approval_source": "datazone",
        },
    )

    logger.info("DataZone approval translated to mesh approval", extra={
        "subscription_id": subscription_id,
        "sfn": execution_arn,
    })

    return {
        "subscription_id": subscription_id,
        "status": "APPROVED",
        "granted_columns": granted_columns,
        "sfn_execution_arn": execution_arn,
    }


# ── Lambda handler ─────────────────────────────────────────────────────────────

def handler(event: dict, context: Any) -> dict:
    """
    EventBridge rule target for DataZone subscription events.

    Supported detail-types:
      - "Subscription Grant Requested" (DataZone)
    """
    detail_type = event.get("detail-type", "")
    source = event.get("source", "")

    logger.info("DataZone connector invoked", extra={
        "detail_type": detail_type,
        "source": source,
    })

    # Only handle DataZone events
    if source not in ("aws.datazone",):
        logger.warning("Unexpected event source; ignoring", extra={"source": source})
        return {"status": "ignored", "reason": "unexpected_source"}

    if detail_type != "Subscription Grant Requested":
        logger.info("Unhandled DataZone event type; ignoring", extra={"detail_type": detail_type})
        return {"status": "ignored", "reason": "unhandled_detail_type"}

    # Validate the DataZone domain matches central config
    if not _validate_datazone_domain(event):
        return {
            "status": "rejected",
            "reason": "datazone_domain_mismatch",
        }

    # Extract subscription info from DataZone event
    sub_info = _extract_subscription_info(event)

    logger.info("DataZone subscription info extracted", extra={"sub_info": sub_info})

    # Approve the subscription
    result = _approve_subscription(sub_info)

    return {"status": "processed", "result": result}
