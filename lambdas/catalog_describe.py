"""
catalog_describe.py — GET /catalog/{domain}/{product_name} Lambda handler.

Returns the full metadata item for a single data product identified by
domain and product_name path parameters.

Returns: the full DynamoDB item, or 404 if not found.

Runtime: Python 3.12
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_PRODUCTS_TABLE = "mesh-products"

_RESPONSE_HEADERS = {
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handler(event: dict, context: Any) -> dict:
    """API Gateway proxy handler for GET /catalog/{domain}/{product_name}."""
    path_params: dict[str, str] = event.get("pathParameters") or {}

    domain = path_params.get("domain")
    product_name = path_params.get("product_name")

    if not domain or not product_name:
        return _error(400, "Missing path parameters: domain and product_name are required")

    products_table = os.environ.get("MESH_PRODUCTS_TABLE", DEFAULT_PRODUCTS_TABLE)

    try:
        ddb = boto3.resource("dynamodb")
        table = ddb.Table(products_table)

        composite_key = f"{domain}#{product_name}"
        response = table.get_item(Key={"domain#product_name": composite_key})

        item = response.get("Item")
        if item is None:
            return _error(404, f"Product '{domain}/{product_name}' not found")

        return {
            "statusCode": 200,
            "headers": _RESPONSE_HEADERS,
            "body": json.dumps(item, default=str),
        }

    except ClientError as exc:
        logger.exception("DynamoDB error during catalog describe")
        return _error(500, "Internal server error")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(status_code: int, message: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": _RESPONSE_HEADERS,
        "body": json.dumps({"error": message}),
    }
