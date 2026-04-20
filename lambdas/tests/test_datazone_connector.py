"""
Tests for lambdas/datazone_connector.py

Covers:
  - handler: unexpected source ignored
  - handler: unhandled detail-type ignored
  - handler: domain ID mismatch rejected
  - _approve_subscription: happy path translates to mesh approval + SubscriptionApproved
  - _approve_subscription: subscription not in PENDING → skipped
  - _approve_subscription: no mesh subscription found → skipped
  - _approve_subscription: PII columns excluded from granted_columns
  - _extract_subscription_info: correct parsing of DataZone event
"""
import json
import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SUBSCRIPTIONS_TABLE = "mesh-subscriptions"
PRODUCTS_TABLE = "mesh-products"

PRODUCER_ACCOUNT = "111111111111"
CONSUMER_ACCOUNT = "222222222222"
PRODUCT_ID = "sales#customer_orders"
DOMAIN = "sales"
PRODUCT_NAME = "customer_orders"
OWNER_EMAIL = "sales-owner@example.com"
DZ_DOMAIN_ID = "dzd_test123456"
SUB_ID = "sub-dz-001"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "test")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "test")
    monkeypatch.setenv("MESH_SUBSCRIPTIONS_TABLE", SUBSCRIPTIONS_TABLE)
    monkeypatch.setenv("MESH_PRODUCTS_TABLE", PRODUCTS_TABLE)
    monkeypatch.setenv("DATAZONE_DOMAIN_ID", DZ_DOMAIN_ID)
    monkeypatch.setenv("SUBSCRIPTION_SFN_ARN", "arn:aws:states:us-east-1:111111111111:stateMachine:sub")
    monkeypatch.setenv("CENTRAL_EVENT_BUS_NAME", "datameshy-central")


@pytest.fixture
def ddb_tables():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")

        ddb.create_table(
            TableName=PRODUCTS_TABLE,
            KeySchema=[{"AttributeName": "domain#product_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "domain#product_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        ddb.create_table(
            TableName=SUBSCRIPTIONS_TABLE,
            KeySchema=[
                {"AttributeName": "product_id", "KeyType": "HASH"},
                {"AttributeName": "subscriber_account_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "product_id", "AttributeType": "S"},
                {"AttributeName": "subscriber_account_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield ddb


@pytest.fixture
def seeded_tables(ddb_tables):
    ddb_tables.Table(PRODUCTS_TABLE).put_item(Item={
        "domain#product_name": PRODUCT_ID,
        "domain": DOMAIN,
        "product_name": PRODUCT_NAME,
        "account_id": PRODUCER_ACCOUNT,
        "status": "ACTIVE",
        "owner": OWNER_EMAIL,
        "schema": {
            "columns": [
                {"name": "order_id", "type": "string", "pii": False},
                {"name": "order_date", "type": "date", "pii": False},
                {"name": "customer_email", "type": "string", "pii": True},
            ]
        },
    })

    ddb_tables.Table(SUBSCRIPTIONS_TABLE).put_item(Item={
        "product_id": PRODUCT_ID,
        "subscriber_account_id": CONSUMER_ACCOUNT,
        "subscription_id": SUB_ID,
        "status": "PENDING",
        "requested_columns": ["order_id", "order_date"],
        "created_at": "2026-04-01T00:00:00Z",
        "updated_at": "2026-04-01T00:00:00Z",
        "subscriber_domain": DOMAIN,
        "provisioning_steps": {},
    })

    yield ddb_tables


def _make_dz_event(detail_type="Subscription Grant Requested", domain_id=DZ_DOMAIN_ID,
                   source="aws.datazone", product_name=PRODUCT_ID,
                   consumer_account=CONSUMER_ACCOUNT, columns=None) -> dict:
    return {
        "version": "0",
        "id": "dz-event-001",
        "source": source,
        "account": PRODUCER_ACCOUNT,
        "time": "2026-04-04T10:00:00Z",
        "region": "us-east-1",
        "resources": [],
        "detail-type": detail_type,
        "detail": {
            "domainId": domain_id,
            "subscriptionId": f"dz-sub-{uuid.uuid4()}",
            "subscribedListings": [
                {"id": "listing-001", "name": product_name}
            ],
            "subscribedPrincipals": [
                {"id": "project-001", "type": "PROJECT", "accountId": consumer_account}
            ],
            "requestReason": "For reporting dashboards",
            "requestedColumns": columns or ["order_id", "order_date"],
        },
    }


class TestHandlerRouting:

    def test_unexpected_source_is_ignored(self, seeded_tables):
        """Non-DataZone sources should be silently ignored."""
        from datazone_connector import handler

        event = _make_dz_event(source="datameshy.domain")
        result = handler(event, None)

        assert result["status"] == "ignored"
        assert result["reason"] == "unexpected_source"

    def test_unhandled_detail_type_ignored(self, seeded_tables):
        """Only 'Subscription Grant Requested' is handled; others ignored."""
        from datazone_connector import handler

        event = _make_dz_event(detail_type="Subscription Revoked")
        result = handler(event, None)

        assert result["status"] == "ignored"
        assert result["reason"] == "unhandled_detail_type"

    def test_domain_mismatch_rejected(self, seeded_tables):
        """Event from wrong DataZone domain ID is rejected."""
        from datazone_connector import handler

        event = _make_dz_event(domain_id="dzd_wrong_domain")
        result = handler(event, None)

        assert result["status"] == "rejected"
        assert result["reason"] == "datazone_domain_mismatch"


class TestApproveSubscription:

    @patch("datazone_connector._start_sfn", return_value="arn:sfn:exec:dz-1")
    @patch("datazone_connector._emit_event")
    def test_happy_path_approves_and_starts_sfn(self, mock_emit, mock_sfn, seeded_tables):
        """DataZone approval → mesh APPROVED, SFN started, SubscriptionApproved emitted."""
        from datazone_connector import handler

        event = _make_dz_event()
        result = handler(event, None)

        assert result["status"] == "processed"
        approval = result["result"]
        assert approval["status"] == "APPROVED"
        assert approval["subscription_id"] == SUB_ID
        mock_sfn.assert_called_once()

        # SubscriptionApproved must be emitted from datameshy.central
        emit_call = mock_emit.call_args
        assert emit_call.args[0] == "SubscriptionApproved"
        detail = emit_call.args[1]
        assert detail["subscription_id"] == SUB_ID
        assert detail["approval_source"] == "datazone"

    @patch("datazone_connector._start_sfn", return_value="arn:sfn:exec:dz-2")
    @patch("datazone_connector._emit_event")
    def test_pii_columns_excluded_from_granted(self, mock_emit, mock_sfn, seeded_tables):
        """PII column customer_email should not appear in granted_columns."""
        from datazone_connector import handler

        # Request PII column in addition to non-PII
        event = _make_dz_event(columns=["order_id", "customer_email"])
        result = handler(event, None)

        approval = result["result"]
        assert "customer_email" not in approval["granted_columns"]
        assert "order_id" in approval["granted_columns"]

    def test_subscription_not_pending_skipped(self, seeded_tables):
        """If subscription is already ACTIVE, DataZone approval is a no-op."""
        from datazone_connector import handler

        # Update to ACTIVE
        seeded_tables.Table(SUBSCRIPTIONS_TABLE).update_item(
            Key={"product_id": PRODUCT_ID, "subscriber_account_id": CONSUMER_ACCOUNT},
            UpdateExpression="SET #s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":active": "ACTIVE"},
        )

        event = _make_dz_event()
        result = handler(event, None)

        assert result["status"] == "processed"
        assert result["result"]["skipped"] is True
        assert result["result"]["reason"] == "not_pending"

    def test_no_mesh_subscription_skipped(self, seeded_tables):
        """If no mesh subscription exists for product/consumer, skip gracefully."""
        from datazone_connector import handler

        event = _make_dz_event(consumer_account="999999999999")
        result = handler(event, None)

        assert result["status"] == "processed"
        assert result["result"]["skipped"] is True
        assert result["result"]["reason"] == "no_mesh_subscription"


class TestExtractSubscriptionInfo:

    def test_extracts_product_and_consumer(self):
        from datazone_connector import _extract_subscription_info

        event = _make_dz_event()
        info = _extract_subscription_info(event)

        assert info["product_id"] == PRODUCT_ID
        assert info["consumer_account_id"] == CONSUMER_ACCOUNT
        assert "order_id" in info["requested_columns"]
        assert info["justification"] == "For reporting dashboards"

    def test_extracts_datazone_subscription_id(self):
        from datazone_connector import _extract_subscription_info

        event = _make_dz_event()
        info = _extract_subscription_info(event)

        assert "datazone_subscription_id" in info
        assert info["datazone_subscription_id"] != ""


class TestValidateDatazoneDomain:

    def test_matching_domain_passes(self):
        from datazone_connector import _validate_datazone_domain

        event = _make_dz_event(domain_id=DZ_DOMAIN_ID)
        assert _validate_datazone_domain(event) is True

    def test_mismatched_domain_fails(self):
        from datazone_connector import _validate_datazone_domain

        event = _make_dz_event(domain_id="dzd_incorrect")
        assert _validate_datazone_domain(event) is False

    def test_no_env_var_passes_all(self, monkeypatch):
        """Without DATAZONE_DOMAIN_ID configured, all events are allowed through."""
        monkeypatch.delenv("DATAZONE_DOMAIN_ID", raising=False)
        from datazone_connector import _validate_datazone_domain
        import importlib
        import datazone_connector
        importlib.reload(datazone_connector)

        event = _make_dz_event(domain_id="any-domain-id")
        # After reload, env var is empty → should return True (permissive)
        from datazone_connector import _validate_datazone_domain as reloaded_fn
        assert reloaded_fn(event) is True
