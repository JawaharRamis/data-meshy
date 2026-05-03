"""
catalog_browse.py — GET /catalog/browse Lambda handler.

Returns all data products grouped by domain. Products with any status
(ACTIVE, DEPRECATED, RETIRED, PROVISIONED) are included.

Algorithm:
  1. Fetch all registered domain names from mesh-domains (Scan — acceptable;
     number of domains is bounded and small, O(10s) not O(products)).
  2. For each domain, Query GSI3 (domain PK) to get that domain's products.
  3. Merge results into { domains: { <domain>: [products] } }.

Pagination: if the caller passes ?next_token=<encoded>, resume from that key.

Runtime: Python 3.12
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_PRODUCTS_TABLE = "mesh-products"
DEFAULT_DOMAINS_TABLE = "mesh-domains"

_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handler(event: dict, context: Any) -> dict:
    """API Gateway proxy handler for GET /catalog/browse."""
    products_table = os.environ.get("MESH_PRODUCTS_TABLE", DEFAULT_PRODUCTS_TABLE)
    domains_table = os.environ.get("MESH_DOMAINS_TABLE", DEFAULT_DOMAINS_TABLE)

    try:
        domain_names = _get_all_domains(domains_table)

        grouped: dict[str, list[dict]] = {}
        for domain_name in domain_names:
            products = _browse_domain(domain_name, products_table)
            grouped[domain_name] = products

        body = {
            "domains": grouped,
            "domain_count": len(grouped),
            "total_products": sum(len(v) for v in grouped.values()),
        }

        return {
            "statusCode": 200,
            "headers": _CORS_HEADERS,
            "body": json.dumps(body, default=str),
        }

    except ClientError as exc:
        logger.exception("DynamoDB error during catalog browse")
        return _error(500, f"Internal error: {exc.response['Error']['Message']}")


# ---------------------------------------------------------------------------
# GSI query helpers (exported so tests can verify no-scan contract)
# ---------------------------------------------------------------------------


def _get_all_domains(domains_table: str) -> list[str]:
    """Scan mesh-domains table to get all registered domain names.

    Acceptable: domain count is bounded (O(10s)). This is not a products scan.
    """
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(domains_table)

    names: list[str] = []
    kwargs: dict = {"ProjectionExpression": "domain_name"}

    while True:
        response = table.scan(**kwargs)
        names.extend(item["domain_name"] for item in response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    return names


def _browse_domain(domain: str, products_table: str) -> list[dict]:
    """Query GSI3 (domain PK) to get all products for a domain.

    Includes products of all statuses (ACTIVE, DEPRECATED, RETIRED, PROVISIONED).
    """
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(products_table)

    items: list[dict] = []
    kwargs: dict = {
        "IndexName": "GSI3",
        "KeyConditionExpression": Key("domain").eq(domain),
    }

    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    return items


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(status_code: int, message: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": _CORS_HEADERS,
        "body": json.dumps({"error": message}),
    }
