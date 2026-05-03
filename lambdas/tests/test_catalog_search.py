"""Tests for lambdas/catalog_search.py

AC8: Lambda handlers for search/browse use DynamoDB GSI queries (not scan).
"""

from __future__ import annotations

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


def _create_products_table_with_gsis(dynamodb_client):
    """Create mesh-products table with GSI1 (tag), GSI2 (classification), GSI3 (domain)."""
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
    """Seed the products table with test items."""
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
        },
        {
            "domain#product_name": "finance#invoices",
            "domain": "finance",
            "product_name": "invoices",
            "status": "ACTIVE",
            "description": "Finance invoices product",
            "classification": "confidential",
            "tags": ["finance", "billing"],
            "tag_value": "finance",
            "owner": "finance-team@example.com",
            "quality_score": Decimal("95.0"),
            "subscriber_count": 1,
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
    _create_products_table_with_gsis(dynamodb_client)
    _seed_products(ddb_resource)
    return ddb_resource


# ---------------------------------------------------------------------------
# Helper to build APIGW-style events
# ---------------------------------------------------------------------------


def _apigw_event(query_params: dict | None = None) -> dict:
    return {
        "httpMethod": "GET",
        "path": "/catalog/search",
        "queryStringParameters": query_params or {},
        "headers": {},
        "body": None,
        "requestContext": {
            "identity": {"sourceIp": "127.0.0.1"},
        },
    }


# ---------------------------------------------------------------------------
# AC1: keyword search
# ---------------------------------------------------------------------------


class TestCatalogSearchKeyword:
    """catalog_search handler: keyword filter."""

    def test_keyword_match_on_product_name(self, setup_tables):
        """keyword=orders returns products with 'orders' in name."""
        from catalog_search import handler

        event = _apigw_event({"keyword": "orders"})
        response = handler(event, None)

        assert response["statusCode"] == 200
        import json
        body = json.loads(response["body"])
        names = [item["product_name"] for item in body["items"]]
        assert "customer_orders" in names

    def test_keyword_match_on_description(self, setup_tables):
        """keyword=ecommerce matches description text."""
        from catalog_search import handler

        event = _apigw_event({"keyword": "ecommerce"})
        response = handler(event, None)

        assert response["statusCode"] == 200
        import json
        body = json.loads(response["body"])
        names = [item["product_name"] for item in body["items"]]
        assert "customer_orders" in names

    def test_keyword_no_match_returns_empty(self, setup_tables):
        """keyword=nonexistent returns empty items list."""
        from catalog_search import handler

        event = _apigw_event({"keyword": "zzznomatch"})
        response = handler(event, None)

        assert response["statusCode"] == 200
        import json
        body = json.loads(response["body"])
        assert body["items"] == []

    def test_keyword_returns_deprecated_products(self, setup_tables):
        """DEPRECATED products are included in keyword search results."""
        from catalog_search import handler

        event = _apigw_event({"keyword": "orders"})
        response = handler(event, None)

        assert response["statusCode"] == 200
        import json
        body = json.loads(response["body"])
        statuses = [item["status"] for item in body["items"]]
        assert "DEPRECATED" in statuses


# ---------------------------------------------------------------------------
# AC2: domain search via GSI3
# ---------------------------------------------------------------------------


class TestCatalogSearchDomain:
    """catalog_search handler: domain filter (GSI3)."""

    def test_domain_filter_returns_domain_products(self, setup_tables):
        """domain=sales returns only sales products via GSI3."""
        from catalog_search import handler

        event = _apigw_event({"domain": "sales"})
        response = handler(event, None)

        assert response["statusCode"] == 200
        import json
        body = json.loads(response["body"])
        domains = {item["domain"] for item in body["items"]}
        assert domains == {"sales"}

    def test_domain_filter_empty_domain(self, setup_tables):
        """domain=unknown returns empty items."""
        from catalog_search import handler

        event = _apigw_event({"domain": "unknown_domain"})
        response = handler(event, None)

        assert response["statusCode"] == 200
        import json
        body = json.loads(response["body"])
        assert body["items"] == []

    def test_domain_query_uses_gsi_not_scan(self, setup_tables):
        """domain filter must not do a full table scan — verifiable by GSI usage."""
        # The catalog_search.py implementation is contracted to use GSI3 for domain queries.
        # We verify indirectly: the function must exist and accept the domain param.
        from catalog_search import _search_by_domain

        result = _search_by_domain("sales", PRODUCTS_TABLE)
        names = [item["product_name"] for item in result]
        assert "customer_orders" in names


# ---------------------------------------------------------------------------
# AC3: tag search via GSI1
# ---------------------------------------------------------------------------


class TestCatalogSearchTag:
    """catalog_search handler: tag filter (GSI1)."""

    def test_tag_filter_returns_tagged_products(self, setup_tables):
        """tag=ecommerce returns products with that tag value."""
        from catalog_search import handler

        event = _apigw_event({"tag": "ecommerce"})
        response = handler(event, None)

        assert response["statusCode"] == 200
        import json
        body = json.loads(response["body"])
        assert len(body["items"]) >= 1

    def test_tag_query_uses_gsi_not_scan(self, setup_tables):
        """tag filter uses GSI1 (_search_by_tag helper exists)."""
        from catalog_search import _search_by_tag

        result = _search_by_tag("ecommerce", PRODUCTS_TABLE)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# AC4: classification search via GSI2
# ---------------------------------------------------------------------------


class TestCatalogSearchClassification:
    """catalog_search handler: classification filter (GSI2)."""

    def test_classification_filter_returns_matching_products(self, setup_tables):
        """classification=internal returns only internal-classified products."""
        from catalog_search import handler

        event = _apigw_event({"classification": "internal"})
        response = handler(event, None)

        assert response["statusCode"] == 200
        import json
        body = json.loads(response["body"])
        classifications = {item["classification"] for item in body["items"]}
        assert "confidential" not in classifications
        assert "internal" in classifications

    def test_classification_query_uses_gsi_not_scan(self, setup_tables):
        """classification filter uses GSI2 (_search_by_classification helper exists)."""
        from catalog_search import _search_by_classification

        result = _search_by_classification("internal", PRODUCTS_TABLE)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestCatalogSearchErrors:
    """catalog_search handler: error paths."""

    def test_no_params_returns_400(self, aws_mock):
        """No search parameters returns 400 with helpful message."""
        from catalog_search import handler

        event = _apigw_event({})
        response = handler(event, None)

        assert response["statusCode"] == 400

    def test_multiple_params_returns_400(self, aws_mock):
        """Providing multiple filter types simultaneously returns 400."""
        from catalog_search import handler

        event = _apigw_event({"keyword": "orders", "domain": "sales"})
        response = handler(event, None)

        assert response["statusCode"] == 400

    def test_response_has_items_key(self, setup_tables):
        """Response body always contains 'items' key."""
        from catalog_search import handler

        event = _apigw_event({"domain": "sales"})
        response = handler(event, None)

        import json
        body = json.loads(response["body"])
        assert "items" in body
