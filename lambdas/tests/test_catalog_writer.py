"""Tests for lambdas/catalog_writer.py"""
import sys
import os
import pytest
import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from catalog_writer import handler as catalog_handler

PRODUCTS_TABLE = "mesh-products"
QUALITY_TABLE = "mesh-quality-scores"
TEST_ACCOUNT_ID = "111111111111"
TEST_DOMAIN = "sales"
TEST_PRODUCT = "customer_orders"
TEST_PRODUCT_ID = "sales#customer_orders"


def _make_event(event_type, detail, account=TEST_ACCOUNT_ID):
    return {
        "version": "0",
        "id": "test-eb-id-001",
        "source": "datameshy",
        "account": account,
        "time": "2026-04-03T10:00:00Z",
        "region": "us-east-1",
        "resources": [],
        "detail-type": event_type,
        "detail": detail,
    }


class TestProductCreated:
    def test_product_created_writes_to_products_table(
        self, setup_tables, register_domain, ddb_resource
    ):
        event = _make_event(
            "ProductCreated",
            {
                "event_id": "uuid-create-001",
                "domain": TEST_DOMAIN,
                "product_name": TEST_PRODUCT,
                "product_id": TEST_PRODUCT_ID,
                "schema_version": 1,
                "owner": "sales-team@example.com",
                "classification": "internal",
                "description": "Customer orders data product",
                "tags": ["ecommerce", "transactions"],
                "timestamp": "2026-04-03T10:00:00Z",
                "version": "1.0",
                "sla": {"refresh_frequency": "daily", "freshness_target": "24 hours"},
            },
            account=TEST_ACCOUNT_ID,
        )
        result = catalog_handler(event, None)
        assert result["status"] == "success"
        assert result["action"] == "ProductCreated"

        table = ddb_resource.Table(PRODUCTS_TABLE)
        item = table.get_item(Key={"domain#product_name": TEST_PRODUCT_ID})
        assert "Item" in item
        assert item["Item"]["domain#product_name"] == TEST_PRODUCT_ID
        assert item["Item"]["domain"] == TEST_DOMAIN
        assert item["Item"]["product_name"] == TEST_PRODUCT
        assert item["Item"]["status"] == "ACTIVE"


class TestProductRefreshed:
    def test_product_refreshed_updates_products_and_quality(
        self, setup_tables, register_product, ddb_resource
    ):
        event = _make_event(
            "ProductRefreshed",
            {
                "event_id": "uuid-refresh-001",
                "domain": TEST_DOMAIN,
                "product_name": TEST_PRODUCT,
                "product_id": TEST_PRODUCT_ID,
                "schema_version": 1,
                "quality_score": 97.5,
                "rows_written": 15000,
                "pipeline_execution_arn": "arn:aws:states:us-east-1:111111111111:execution:test",
                "timestamp": "2026-04-03T10:00:00Z",
                "version": "1.0",
            },
            account=TEST_ACCOUNT_ID,
        )
        result = catalog_handler(event, None)
        assert result["status"] == "success"
        assert result["action"] == "ProductRefreshed"

        table = ddb_resource.Table(PRODUCTS_TABLE)
        item = table.get_item(Key={"domain#product_name": TEST_PRODUCT_ID})
        assert "Item" in item
        assert item["Item"]["quality_score"] == 97.5
        assert item["Item"]["last_refreshed_at"] == "2026-04-03T10:00:00Z"

        q_table = ddb_resource.Table(QUALITY_TABLE)
        q_items = q_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("product_id").eq(
                TEST_PRODUCT_ID
            )
        )
        assert q_items["Count"] == 1
        assert q_items["Items"][0]["quality_score"] == 97.5

    def test_unknown_event_type_raises(self, setup_tables, register_domain):
        event = _make_event(
            "UnknownEvent",
            {
                "event_id": "uuid-unknown-001",
                "domain": TEST_DOMAIN,
                "timestamp": "2026-04-03T10:00:00Z",
                "version": "1.0",
            },
            account=TEST_ACCOUNT_ID,
        )
        with pytest.raises(ValueError, match="Unhandled event type"):
            catalog_handler(event, None)
