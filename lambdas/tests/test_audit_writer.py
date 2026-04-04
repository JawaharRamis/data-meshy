"""Tests for lambdas/audit_writer.py"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from audit_writer import handler as audit_handler

AUDIT_TABLE = "mesh-audit-log"
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


class TestAuditWriter:
    def test_appends_event_correctly(self, setup_tables, register_domain, ddb_resource):
        event = _make_event(
            "ProductRefreshed",
            {
                "event_id": "uuid-audit-001",
                "domain": TEST_DOMAIN,
                "product_name": TEST_PRODUCT,
                "product_id": TEST_PRODUCT_ID,
                "timestamp": "2026-04-03T10:00:00Z",
                "version": "1.0",
                "quality_score": 98.5,
            },
            account=TEST_ACCOUNT_ID,
        )
        result = audit_handler(event, None)
        assert result["status"] == "success"

        table = ddb_resource.Table(AUDIT_TABLE)
        items = table.scan()
        assert items["Count"] == 1
        item = items["Items"][0]
        assert item["event_id"] == "uuid-audit-001"
        assert item["event_type"] == "ProductRefreshed"
        assert item["domain"] == TEST_DOMAIN
        assert item["source_account"] == TEST_ACCOUNT_ID
        assert "event_payload" in item

    def test_multiple_events_all_appended(self, setup_tables, register_domain, ddb_resource):
        for i in range(3):
            event = _make_event(
                "ProductRefreshed",
                {
                    "event_id": "uuid-audit-multi-{}".format(i),
                    "domain": TEST_DOMAIN,
                    "product_name": TEST_PRODUCT,
                    "product_id": TEST_PRODUCT_ID,
                    "timestamp": "2026-04-03T10:0{}:00Z".format(i),
                    "version": "1.0",
                },
                account=TEST_ACCOUNT_ID,
            )
            result = audit_handler(event, None)
            assert result["status"] == "success"

        table = ddb_resource.Table(AUDIT_TABLE)
        items = table.scan()
        assert items["Count"] == 3

    def test_never_updates_existing_record(self, setup_tables, register_domain, ddb_resource):
        event1 = _make_event(
            "ProductRefreshed",
            {
                "event_id": "uuid-audit-dup-001",
                "domain": TEST_DOMAIN,
                "product_name": TEST_PRODUCT,
                "product_id": TEST_PRODUCT_ID,
                "timestamp": "2026-04-03T10:00:00Z",
                "version": "1.0",
            },
            account=TEST_ACCOUNT_ID,
        )
        audit_handler(event1, None)

        # Replay same event - audit log uses composite key (event_id + timestamp)
        event2 = _make_event(
            "ProductRefreshed",
            {
                "event_id": "uuid-audit-dup-001",
                "domain": TEST_DOMAIN,
                "product_name": TEST_PRODUCT,
                "product_id": TEST_PRODUCT_ID,
                "timestamp": "2026-04-03T10:00:00Z",
                "version": "1.0",
            },
            account=TEST_ACCOUNT_ID,
        )
        result = audit_handler(event2, None)
        assert result["status"] == "success"
