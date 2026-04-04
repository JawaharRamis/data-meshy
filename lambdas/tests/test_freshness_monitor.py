"""Tests for lambdas/freshness_monitor.py"""
import sys
import os
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from freshness_monitor import handler as freshness_handler, parse_freshness_hours

PRODUCTS_TABLE = "mesh-products"
TEST_ACCOUNT_ID = "111111111111"
TEST_DOMAIN = "sales"
TEST_PRODUCT = "customer_orders"
TEST_PRODUCT_ID = "sales#customer_orders"


def _register_product_with_freshness(
    ddb_resource, product_id, domain, product_name,
    last_refreshed_at, freshness_target="24 hours", status="ACTIVE"
):
    table = ddb_resource.Table(PRODUCTS_TABLE)
    table.put_item(
        Item={
            "domain#product_name": product_id,
            "domain": domain,
            "product_name": product_name,
            "status": status,
            "owner": "sales-team@example.com",
            "classification": "internal",
            "sla": {
                "refresh_frequency": "daily",
                "freshness_target": freshness_target,
            },
            "last_refreshed_at": last_refreshed_at,
            "quality_score": Decimal("98.5"),
            "schema_version": 1,
        }
    )


def _make_scheduled_event():
    return {
        "version": "0",
        "id": "scheduled-event-001",
        "source": "aws.events",
        "account": TEST_ACCOUNT_ID,
        "time": datetime.now(timezone.utc).isoformat(),
        "region": "us-east-1",
        "detail-type": "Scheduled Event",
        "detail": {},
    }


class TestFreshnessMonitor:
    def test_detects_sla_breach(self, setup_tables, register_domain, ddb_resource):
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        _register_product_with_freshness(
            ddb_resource,
            TEST_PRODUCT_ID, TEST_DOMAIN, TEST_PRODUCT,
            stale_time, freshness_target="24 hours",
        )
        event = _make_scheduled_event()
        result = freshness_handler(event, None)
        assert result["products_checked"] == 1
        assert result["violations"] == 1

    def test_no_breach_when_within_sla(self, setup_tables, register_domain, ddb_resource):
        fresh_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _register_product_with_freshness(
            ddb_resource,
            TEST_PRODUCT_ID, TEST_DOMAIN, TEST_PRODUCT,
            fresh_time, freshness_target="24 hours",
        )
        event = _make_scheduled_event()
        result = freshness_handler(event, None)
        assert result["products_checked"] == 1
        assert result["violations"] == 0

    def test_no_breach_when_no_products(self, setup_tables, register_domain):
        event = _make_scheduled_event()
        result = freshness_handler(event, None)
        assert result["products_checked"] == 0
        assert result["violations"] == 0

    def test_inactive_products_skipped(self, setup_tables, register_domain, ddb_resource):
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        _register_product_with_freshness(
            ddb_resource,
            TEST_PRODUCT_ID, TEST_DOMAIN, TEST_PRODUCT,
            stale_time, freshness_target="24 hours",
            status="DEPRECATED",
        )
        event = _make_scheduled_event()
        result = freshness_handler(event, None)
        assert result["products_checked"] == 0
        assert result["violations"] == 0


class TestParseFreshnessHours:
    def test_hours_format(self):
        assert parse_freshness_hours("24 hours") == 24.0
        assert parse_freshness_hours("4 hours") == 4.0

    def test_days_format(self):
        assert parse_freshness_hours("7 days") == 168.0
        assert parse_freshness_hours("1 days") == 24.0

    def test_numeric_fallback(self):
        assert parse_freshness_hours("48") == 48.0
