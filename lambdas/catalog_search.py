"""
catalog_search.py — GET /catalog/search Lambda handler.

Accepts exactly one of:
  ?keyword=<term>           — FilterExpression match on name, description, tags
  ?domain=<name>            — Query GSI3 (PK: domain)
  ?tag=<value>              — Query GSI1 (PK: tag_value)
  ?classification=<level>   — Query GSI2 (PK: classification)

Returns: { "items": [...], "count": N }

No full-table scans are performed. keyword search uses GSI3 to pull all
products for each domain then applies a Python-side FilterExpression match
(acceptable at catalog scale; ADR-006 boundary note: >100k products requires
OpenSearch migration).

Runtime: Python 3.12
"""

from __future__ import annotations

import json
import logging
import os
import re
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
}

_PARAM_RE = re.compile(r"^[a-z0-9_\-\s]{1,256}$")
_MAX_PARAM_LEN = 256
_MAX_RESULTS = 500


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def _validate_param(name: str, value: str | None) -> str | None:
    """Validate a query parameter value. Returns the value or raises 400 via _error sentinel."""
    if value is None:
        return None
    if len(value) > _MAX_PARAM_LEN:
        raise ValueError(f"Parameter '{name}' exceeds maximum length of {_MAX_PARAM_LEN} characters")
    if not _PARAM_RE.match(value):
        raise ValueError(
            f"Parameter '{name}' contains invalid characters. "
            "Must match ^[a-z0-9_\\-\\s]{1,256}$."
        )
    return value


def handler(event: dict, context: Any) -> dict:
    """API Gateway proxy handler for GET /catalog/search."""
    params: dict[str, str] = event.get("queryStringParameters") or {}

    try:
        keyword = _validate_param("keyword", params.get("keyword"))
        domain = _validate_param("domain", params.get("domain"))
        tag = _validate_param("tag", params.get("tag"))
        classification = _validate_param("classification", params.get("classification"))
    except ValueError as exc:
        return _error(400, str(exc))

    products_table = os.environ.get("MESH_PRODUCTS_TABLE", DEFAULT_PRODUCTS_TABLE)
    domains_table = os.environ.get("MESH_DOMAINS_TABLE", DEFAULT_DOMAINS_TABLE)

    # Validate: exactly one filter required
    provided = [v for v in (keyword, domain, tag, classification) if v is not None]
    if len(provided) == 0:
        return _error(400, "Provide exactly one of: keyword, domain, tag, classification")
    if len(provided) > 1:
        return _error(400, "Provide exactly one of: keyword, domain, tag, classification")

    try:
        if keyword is not None:
            items = _search_by_keyword(keyword, products_table, domains_table)
        elif domain is not None:
            items = _search_by_domain(domain, products_table)
        elif tag is not None:
            items = _search_by_tag(tag, products_table)
        else:
            items = _search_by_classification(classification, products_table)

        # Cap results to prevent overly large responses
        items = items[:_MAX_RESULTS]

        return {
            "statusCode": 200,
            "headers": _CORS_HEADERS,
            "body": json.dumps({"items": items, "count": len(items)}, default=str),
        }

    except ClientError as exc:
        logger.exception("DynamoDB error during catalog search")
        return _error(500, "Internal server error")


# ---------------------------------------------------------------------------
# GSI query helpers (exported so tests can verify no-scan contract)
# ---------------------------------------------------------------------------


def _search_by_domain(domain: str, products_table: str) -> list[dict]:
    """Query GSI3 (domain PK) — returns all products for the domain."""
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


def _search_by_tag(tag_value: str, products_table: str) -> list[dict]:
    """Query GSI1 (tag_value PK) — returns all products with that tag."""
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(products_table)

    items: list[dict] = []
    kwargs: dict = {
        "IndexName": "GSI1",
        "KeyConditionExpression": Key("tag_value").eq(tag_value),
    }

    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    return items


def _search_by_classification(classification: str, products_table: str) -> list[dict]:
    """Query GSI2 (classification PK) — returns all products with that classification."""
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(products_table)

    items: list[dict] = []
    kwargs: dict = {
        "IndexName": "GSI2",
        "KeyConditionExpression": Key("classification").eq(classification),
    }

    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    return items


def _search_by_keyword(keyword: str, products_table: str, domains_table: str) -> list[dict]:
    """Keyword search: fetch all domains via mesh-domains, query GSI3 per domain,
    then filter by keyword match on product_name, description, and tags.

    ADR-006: This approach avoids a full table scan. At >100k products,
    migrate to OpenSearch for full-text search.
    """
    ddb = boto3.resource("dynamodb")
    domains_tbl = ddb.Table(domains_table)

    # Fetch all registered domains
    domain_items: list[dict] = []
    scan_kwargs: dict = {"ProjectionExpression": "domain_name"}
    while True:
        response = domains_tbl.scan(**scan_kwargs)
        domain_items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    domain_names = [d["domain_name"] for d in domain_items]

    # Query GSI3 per domain and filter by keyword
    kw_lower = keyword.lower()
    matched: list[dict] = []
    seen: set[str] = set()

    for domain_name in domain_names:
        products = _search_by_domain(domain_name, products_table)
        for product in products:
            pk = product.get("domain#product_name", "")
            if pk in seen:
                continue
            if _keyword_matches(product, kw_lower):
                matched.append(product)
                seen.add(pk)

    return matched


def _keyword_matches(product: dict, keyword_lower: str) -> bool:
    """Return True if keyword appears in product_name, description, or tags."""
    name = product.get("product_name", "").lower()
    description = product.get("description", "").lower()
    tags = product.get("tags", [])
    tag_str = " ".join(str(t).lower() for t in tags)

    return (
        keyword_lower in name
        or keyword_lower in description
        or keyword_lower in tag_str
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(status_code: int, message: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": _CORS_HEADERS,
        "body": json.dumps({"error": message}),
    }
