"""Tests for lambdas/catalog_describe.py

GET /catalog/{domain}/{product_name} — returns full product item or 404.
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PRODUCTS_TABLE = "mesh-products"


# ---------------------------------------------------------------------------
# Table setup helpers
# ---------------------------------------------------------------------------


def _create_products_table(dynamodb_client):
    """Create mesh-products table with the composite key used by describe."""
    dynamodb_client.create_table(
        TableName=PRODUCTS_TABLE,
        KeySchema=[
            {"AttributeName": "domain#product_name", "KeyType": "HASH"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "domain#product_name", "AttributeType": "S"},
            {"AttributeName": "domain", "AttributeType": "S"},
            {"AttributeName": "classification", "AttributeType": "S"},
            {"AttributeName": "tag_value", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI3",
                "KeySchema": [{"AttributeName": "domain", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "GSI2",
                "KeySchema": [{"AttributeName": "classification", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "GSI1",
                "KeySchema": [{"AttributeName": "tag_value", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _seed_products(ddb_resource):
    table = ddb_resource.Table(PRODUCTS_TABLE)
    items = [
        {
            "domain#product_name": "sales#customer_orders",
            "domain": "sales",
            "product_name": "customer_orders",
            "status": "ACTIVE",
            "description": "Customer orders data product for ecommerce",
            "classification": "internal",
            "tags": ["ecommerce", "transactions"],
            "tag_value": "ecommerce",
            "owner": "sales-team@example.com",
            "quality_score": Decimal("98.5"),
            "subscriber_count": 3,
            "sla": {"refresh_frequency": "daily"},
            "schema": {
                "columns": [
                    {"name": "order_id", "type": "string", "pii": False},
                    {"name": "customer_email", "type": "string", "pii": True},
                ]
            },
        },
        {
            "domain#product_name": "sales#old_orders",
            "domain": "sales",
            "product_name": "old_orders",
            "status": "DEPRECATED",
            "description": "Legacy orders — use customer_orders",
            "classification": "internal",
            "tags": [],
            "tag_value": "legacy",
            "owner": "sales-team@example.com",
            "quality_score": Decimal("70.0"),
            "subscriber_count": 0,
        },
    ]
    for item in items:
        table.put_item(Item=item)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aws_mock():
    with mock_aws():
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        os.environ["AWS_REGION"] = "us-east-1"
        os.environ["AWS_ACCESS_KEY_ID"] = "test"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
        yield


@pytest.fixture
def dynamodb_client(aws_mock):
    return boto3.client("dynamodb", region_name="us-east-1")


@pytest.fixture
def ddb_resource(aws_mock):
    return boto3.resource("dynamodb", region_name="us-east-1")


@pytest.fixture
def setup_tables(dynamodb_client, ddb_resource):
    _create_products_table(dynamodb_client)
    _seed_products(ddb_resource)
    return ddb_resource


# ---------------------------------------------------------------------------
# Helper to build APIGW-style events
# ---------------------------------------------------------------------------


def _apigw_event(domain: str, product_name: str) -> dict:
    return {
        "httpMethod": "GET",
        "path": f"/catalog/{domain}/{product_name}",
        "pathParameters": {"domain": domain, "product_name": product_name},
        "queryStringParameters": {},
        "headers": {},
        "body": None,
        "requestContext": {
            "identity": {"sourceIp": "127.0.0.1"},
        },
    }


def _apigw_event_no_path_params() -> dict:
    return {
        "httpMethod": "GET",
        "path": "/catalog/",
        "pathParameters": {},
        "queryStringParameters": {},
        "headers": {},
        "body": None,
        "requestContext": {
            "identity": {"sourceIp": "127.0.0.1"},
        },
    }


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------


class TestCatalogDescribeHappyPath:
    """catalog_describe handler: successful retrieval."""

    def test_describe_returns_200_for_existing_product(self, setup_tables):
        """GET /catalog/sales/customer_orders returns 200 with the full item."""
        from catalog_describe import handler

        response = handler(_apigw_event("sales", "customer_orders"), None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["product_name"] == "customer_orders"
        assert body["domain"] == "sales"

    def test_describe_returns_full_item_fields(self, setup_tables):
        """Response body contains all stored fields."""
        from catalog_describe import handler

        response = handler(_apigw_event("sales", "customer_orders"), None)

        body = json.loads(response["body"])
        assert "status" in body
        assert body["status"] == "ACTIVE"
        assert "owner" in body
        assert "quality_score" in body

    def test_describe_returns_schema(self, setup_tables):
        """Response body includes schema/columns."""
        from catalog_describe import handler

        response = handler(_apigw_event("sales", "customer_orders"), None)

        body = json.loads(response["body"])
        assert "schema" in body
        column_names = [c["name"] for c in body["schema"]["columns"]]
        assert "order_id" in column_names

    def test_describe_returns_deprecated_product(self, setup_tables):
        """DEPRECATED products are returned normally (not filtered)."""
        from catalog_describe import handler

        response = handler(_apigw_event("sales", "old_orders"), None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "DEPRECATED"

    def test_describe_response_body_is_valid_json(self, setup_tables):
        """Response body parses as valid JSON."""
        from catalog_describe import handler

        response = handler(_apigw_event("sales", "customer_orders"), None)
        body = json.loads(response["body"])
        assert isinstance(body, dict)

    def test_describe_content_type_header(self, setup_tables):
        """Response includes Content-Type header."""
        from catalog_describe import handler

        response = handler(_apigw_event("sales", "customer_orders"), None)
        assert response["headers"]["Content-Type"] == "application/json"

    def test_describe_no_cors_header(self, setup_tables):
        """Response must NOT include Access-Control-Allow-Origin header."""
        from catalog_describe import handler

        response = handler(_apigw_event("sales", "customer_orders"), None)
        assert "Access-Control-Allow-Origin" not in response.get("headers", {})


# ---------------------------------------------------------------------------
# 404 — product not found
# ---------------------------------------------------------------------------


class TestCatalogDescribeNotFound:
    """catalog_describe handler: 404 when product does not exist."""

    def test_describe_returns_404_for_missing_product(self, setup_tables):
        """GET /catalog/sales/nonexistent returns 404."""
        from catalog_describe import handler

        response = handler(_apigw_event("sales", "nonexistent"), None)

        assert response["statusCode"] == 404
        body = json.loads(response["body"])
        assert "error" in body

    def test_describe_returns_404_for_wrong_domain(self, setup_tables):
        """GET /catalog/marketing/customer_orders returns 404 (wrong domain)."""
        from catalog_describe import handler

        response = handler(_apigw_event("marketing", "customer_orders"), None)

        assert response["statusCode"] == 404

    def test_describe_404_body_has_error_key(self, setup_tables):
        """404 response body contains 'error' key."""
        from catalog_describe import handler

        response = handler(_apigw_event("sales", "ghost_product"), None)

        body = json.loads(response["body"])
        assert "error" in body


# ---------------------------------------------------------------------------
# 400 — missing path parameters
# ---------------------------------------------------------------------------


class TestCatalogDescribeBadRequest:
    """catalog_describe handler: 400 when path parameters are missing."""

    def test_describe_returns_400_when_no_path_params(self, aws_mock, dynamodb_client):
        """Missing pathParameters dict returns 400."""
        _create_products_table(dynamodb_client)
        from catalog_describe import handler

        response = handler(_apigw_event_no_path_params(), None)

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "error" in body
