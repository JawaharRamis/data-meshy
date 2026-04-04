"""
event_validator.py — Central event validation for all Data Meshy event handlers.

Responsibilities:
1. Validate event source: verify the account that sent the event is registered
   for the domain claimed in the event body (prevents event injection).
2. Deduplication: check event_id against mesh-event-dedup (TTL 24h).
3. Out-of-order resilience: if event references a product not yet in the catalog,
   return PRODUCT_NOT_FOUND so the caller can queue for retry.

This module is called by catalog_writer, audit_writer, and other handlers
BEFORE processing any event.

Runtime: Python 3.12
"""

import json
import logging
import time
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Status constants
VALID = "VALID"
DUPLICATE_EVENT = "DUPLICATE_EVENT"
DOMAIN_MISMATCH = "DOMAIN_MISMATCH"
PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
SECURITY_ALERT = "SECURITY_ALERT"

# Environment variable keys
ENV_DOMAINS_TABLE = "MESH_DOMAINS_TABLE"
EVENT_DEDUP_TABLE = "MESH_EVENT_DEDUP_TABLE"
ENV_PRODUCTS_TABLE = "MESH_PRODUCTS_TABLE"

# Default table names (matching governance module outputs)
DEFAULT_DOMAINS_TABLE = "mesh-domains"
DEFAULT_DEDUP_TABLE = "mesh-event-dedup"
DEFAULT_PRODUCTS_TABLE = "mesh-products"

# TTL for dedup records: 24 hours in seconds
DEDUP_TTL_SECONDS = 86400


def _get_table_name(env_key: str, default: str) -> str:
    return os.environ.get(env_key, default)


def validate_event_source(event: dict, domains_table: str = None) -> dict:
    """
    Validate that the event's source account is registered for the claimed domain.

    Args:
        event: EventBridge event envelope (contains 'account' and 'detail.domain')
        domains_table: Override for mesh-domains table name

    Returns:
        dict with 'status' (VALID or DOMAIN_MISMATCH) and optional 'domain'
    """
    table_name = domains_table or _get_table_name(ENV_DOMAINS_TABLE, DEFAULT_DOMAINS_TABLE)
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    # Extract account from EventBridge envelope (set by AWS, not caller-controlled)
    source_account = event.get("account", "")
    detail = event.get("detail", {})
    claimed_domain = detail.get("domain", "")

    if not source_account or not claimed_domain:
        logger.warning("Missing account or domain in event", extra={
            "account": source_account, "domain": claimed_domain
        })
        return {"status": DOMAIN_MISMATCH, "reason": "missing_account_or_domain"}

    # Look up the domain registered to this account
    try:
        response = table.get_item(Key={"domain_name": claimed_domain})
    except ClientError as exc:
        logger.error("Failed to query mesh-domains table", extra={"error": str(exc)})
        return {"status": DOMAIN_MISMATCH, "reason": "table_error"}

    item = response.get("Item")
    if not item:
        logger.warning("Domain not registered", extra={"domain": claimed_domain})
        return {"status": DOMAIN_MISMATCH, "reason": "domain_not_registered"}

    registered_account = item.get("account_id", "")
    if registered_account != source_account:
        logger.error("SECURITY_ALERT: domain mismatch", extra={
            "claimed_domain": claimed_domain,
            "source_account": source_account,
            "registered_account": registered_account,
        })
        return {"status": DOMAIN_MISMATCH, "reason": "account_domain_mismatch"}

    return {"status": VALID, "domain": claimed_domain}


def check_dedup(event_id: str, dedup_table: str = None) -> dict:
    """
    Check if an event_id has already been processed (24h TTL dedup).

    Uses conditional write (attribute_not_exists) to atomically detect duplicates.

    Args:
        event_id: Unique event identifier (UUID)
        dedup_table: Override for mesh-event-dedup table name

    Returns:
        dict with 'status' (VALID or DUPLICATE_EVENT)
    """
    table_name = dedup_table or _get_table_name(EVENT_DEDUP_TABLE, DEFAULT_DEDUP_TABLE)
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    ttl_value = int(time.time()) + DEDUP_TTL_SECONDS

    try:
        table.put_item(
            Item={
                "event_id": event_id,
                "ttl": ttl_value,
                "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            ConditionExpression="attribute_not_exists(event_id)",
        )
        return {"status": VALID}
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.info("Duplicate event detected", extra={"event_id": event_id})
            return {"status": DUPLICATE_EVENT}
        raise


def check_product_exists(product_id: str, products_table: str = None) -> dict:
    """
    Check if a product exists in the mesh-products catalog.

    Used for out-of-order resilience: if a handler receives an event for a
    product not yet registered, it should queue for retry rather than dropping.

    Args:
        product_id: Composite key (e.g., "sales#customer_orders")
        products_table: Override for mesh-products table name

    Returns:
        dict with 'status' (VALID or PRODUCT_NOT_FOUND)
    """
    table_name = products_table or _get_table_name(ENV_PRODUCTS_TABLE, DEFAULT_PRODUCTS_TABLE)
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    try:
        response = table.get_item(Key={"domain#product_name": product_id})
    except ClientError as exc:
        logger.error("Failed to query mesh-products table", extra={"error": str(exc)})
        return {"status": PRODUCT_NOT_FOUND}

    if response.get("Item"):
        return {"status": VALID}
    return {"status": PRODUCT_NOT_FOUND}
