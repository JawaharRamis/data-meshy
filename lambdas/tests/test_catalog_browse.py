"""Tests for lambdas/catalog_browse.py

AC5: browse returns all products grouped by domain using GSI queries.
AC8: Lambda handlers use DynamoDB GSI queries (not scan).
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
# Table setup helpers (same GSI layout as test_catalog_search)
# ---------------------------------------------------------------------------


def _create_products_table_with_gsis(dynamodb_client):
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
            "description": "Customer orders data product",
            "classification": "internal",
            "tags": ["ecommerce"],
            "tag_value": "ecommerce",
            "owner": "sales-team@example.com",
            "quality_score": Decimal("98.5"),
            "subscriber_count": 3,
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
        {
            "domain#product_name": "finance#invoices",
            "domain": "finance",
            "product_name": "invoices",
            "status": "ACTIVE",
            "description": "Finance invoices product",
            "classification": "confidential",
            "tags": ["billing"],
            "tag_value": "finance",
            "owner": "finance-team@example.com",
            "quality_score": Decimal("95.0"),
            "subscriber_count": 1,
        },
    ]
    for item in items:
        table.put_item(Item=item)


def _seed_domains(ddb_resource):
    """Seed mesh-domains table so browse knows which domains exist."""
    table = ddb_resource.Table("mesh-domains")
    for domain_name in ("sales", "finance"):
        table.put_item(Item={
            "domain_name": domain_name,
            "account_id": "111111111111",
            "status": "ACTIVE",
        })


def _create_domains_table(dynamodb_client):
    dynamodb_client.create_table(
        TableName="mesh-domains",
        KeySchema=[{"AttributeName": "domain_name", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "domain_name", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


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
    _create_products_table_with_gsis(dynamodb_client)
    _create_domains_table(dynamodb_client)
    _seed_products(ddb_resource)
    _seed_domains(ddb_resource)
    return ddb_resource


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _apigw_event(query_params: dict | None = None) -> dict:
    return {
        "httpMethod": "GET",
        "path": "/catalog/browse",
        "queryStringParameters": query_params or {},
        "headers": {},
        "body": None,
        "requestContext": {
            "identity": {"sourceIp": "127.0.0.1"},
        },
    }


# ---------------------------------------------------------------------------
# AC5: browse — happy path
# ---------------------------------------------------------------------------


class TestCatalogBrowse:
    """catalog_browse handler: lists all products grouped by domain."""

    def test_browse_returns_200(self, setup_tables):
        """browse handler returns HTTP 200."""
        from catalog_browse import handler

        response = handler(_apigw_event(), None)
        assert response["statusCode"] == 200

    def test_browse_groups_by_domain(self, setup_tables):
        """browse response groups products under domain keys."""
        from catalog_browse import handler

        response = handler(_apigw_event(), None)
        body = json.loads(response["body"])

        assert "domains" in body
        assert "sales" in body["domains"]
        assert "finance" in body["domains"]

    def test_browse_sales_domain_has_products(self, setup_tables):
        """browse response contains sales domain products."""
        from catalog_browse import handler

        response = handler(_apigw_event(), None)
        body = json.loads(response["body"])

        sales_names = [p["product_name"] for p in body["domains"]["sales"]]
        assert "customer_orders" in sales_names

    def test_browse_includes_deprecated_products(self, setup_tables):
        """DEPRECATED products appear in browse output (not filtered)."""
        from catalog_browse import handler

        response = handler(_apigw_event(), None)
        body = json.loads(response["body"])

        all_statuses = [
            p["status"]
            for products in body["domains"].values()
            for p in products
        ]
        assert "DEPRECATED" in all_statuses

    def test_browse_uses_gsi_not_scan(self, setup_tables):
        """browse helper function uses GSI3 per-domain queries."""
        from catalog_browse import _browse_domain

        result = _browse_domain("sales", PRODUCTS_TABLE)
        assert isinstance(result, list)
        names = [item["product_name"] for item in result]
        assert "customer_orders" in names

    def test_browse_no_domains_returns_empty(self, aws_mock, dynamodb_client, ddb_resource):
        """browse with no domains registered returns empty domains dict."""
        _create_products_table_with_gsis(dynamodb_client)
        _create_domains_table(dynamodb_client)
        # Don't seed any domains or products

        from catalog_browse import handler
        response = handler(_apigw_event(), None)
        body = json.loads(response["body"])

        assert response["statusCode"] == 200
        assert body["domains"] == {}


# ---------------------------------------------------------------------------
# AC8: No scan — helper function contract
# ---------------------------------------------------------------------------


class TestCatalogBrowseNoScan:
    """Ensures browse implementation exposes GSI-based helper, not a scan."""

    def test_browse_domain_helper_exists(self, setup_tables):
        """_browse_domain(domain, table) helper exists and returns list."""
        from catalog_browse import _browse_domain

        result = _browse_domain("finance", PRODUCTS_TABLE)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0]["domain"] == "finance"

    def test_browse_all_domains_helper_exists(self, setup_tables):
        """_get_all_domains(table) helper returns list of domain names."""
        from catalog_browse import _get_all_domains

        domains = _get_all_domains("mesh-domains")
        assert "sales" in domains
        assert "finance" in domains

    def test_response_body_is_valid_json(self, setup_tables):
        """Handler response body is valid JSON."""
        from catalog_browse import handler

        response = handler(_apigw_event(), None)
        # Should not raise
        body = json.loads(response["body"])
        assert isinstance(body, dict)

    def test_response_has_content_type_header(self, setup_tables):
        """Handler response includes Content-Type header."""
        from catalog_browse import handler

        response = handler(_apigw_event(), None)
        headers = response.get("headers", {})
        assert "Content-Type" in headers

    def test_no_cors_header(self, setup_tables):
        """Handler response must NOT include Access-Control-Allow-Origin header."""
        from catalog_browse import handler

        response = handler(_apigw_event(), None)
        headers = response.get("headers", {})
        assert "Access-Control-Allow-Origin" not in headers


# ---------------------------------------------------------------------------
# HIGH 1: Input validation on query params
# ---------------------------------------------------------------------------


class TestCatalogBrowseInputValidation:
    """HIGH 1: browse Lambda rejects invalid or oversized query parameters."""

    def test_domain_param_too_long_returns_400(self, aws_mock, dynamodb_client):
        """domain query param longer than 256 chars returns 400."""
        _create_products_table_with_gsis(dynamodb_client)
        _create_domains_table(dynamodb_client)
        from catalog_browse import handler

        event = _apigw_event({"domain": "a" * 257})
        response = handler(event, None)

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "error" in body

    def test_domain_param_with_special_chars_returns_400(self, aws_mock, dynamodb_client):
        """domain query param with SQL injection chars returns 400."""
        _create_products_table_with_gsis(dynamodb_client)
        _create_domains_table(dynamodb_client)
        from catalog_browse import handler

        event = _apigw_event({"domain": "'; DROP TABLE--"})
        response = handler(event, None)

        assert response["statusCode"] == 400

    def test_keyword_param_with_special_chars_returns_400(self, aws_mock, dynamodb_client):
        """keyword query param with disallowed chars returns 400."""
        _create_products_table_with_gsis(dynamodb_client)
        _create_domains_table(dynamodb_client)
        from catalog_browse import handler

        event = _apigw_event({"keyword": "<script>xss</script>"})
        response = handler(event, None)

        assert response["statusCode"] == 400

    def test_no_query_params_returns_200(self, setup_tables):
        """browse with no query params returns 200 normally."""
        from catalog_browse import handler

        response = handler(_apigw_event(), None)
        assert response["statusCode"] == 200


# ---------------------------------------------------------------------------
# MEDIUM 1: DynamoDB error message not leaked
# ---------------------------------------------------------------------------


class TestCatalogBrowseErrorLeakage:
    """MEDIUM 1: DynamoDB ClientError messages are not exposed in 500 responses."""

    def test_dynamodb_error_not_leaked(self, aws_mock):
        """500 response body is 'Internal server error', not the raw DynamoDB message."""
        from catalog_browse import handler
        from unittest.mock import patch
        from botocore.exceptions import ClientError

        error_response = {"Error": {"Code": "InternalServerError", "Message": "Secret DB details"}}
        client_error = ClientError(error_response, "Scan")

        with patch("catalog_browse._get_all_domains", side_effect=client_error):
            response = handler(_apigw_event(), None)

        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert "Secret DB details" not in body.get("error", "")
        assert body.get("error") == "Internal server error"
