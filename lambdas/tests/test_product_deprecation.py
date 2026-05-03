"""
Tests for lambdas/product_deprecation.py — Issue #13

Covers handle_product_deprecated:
  - sends SNS notification to each active subscriber
  - skips subscribers without account_id
  - writes audit log entry
  - returns count of notified subscribers
  - handles SNS failure gracefully (warn, don't raise)
  - handles no subscribers
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SUBSCRIPTIONS_TABLE = "mesh-subscriptions"
PRODUCTS_TABLE = "mesh-products"
AUDIT_TABLE = "mesh-audit-log"
PRODUCT_ID = "sales#customer_orders"
DOMAIN = "sales"
PRODUCT_NAME = "customer_orders"
SUNSET_DATE = "2026-08-01"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "test")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "test")
    monkeypatch.setenv("MESH_SUBSCRIPTIONS_TABLE", SUBSCRIPTIONS_TABLE)
    monkeypatch.setenv("MESH_PRODUCTS_TABLE", PRODUCTS_TABLE)
    monkeypatch.setenv("MESH_AUDIT_TABLE", AUDIT_TABLE)
    monkeypatch.setenv("SUBSCRIBER_SNS_TOPIC_NAME", "datameshy-domain-notifications")
    monkeypatch.setenv("CENTRAL_EVENT_BUS_NAME", "datameshy-central")


def _make_tables(ddb):
    """Create required DynamoDB tables and return (subs_table, audit_table)."""
    subs_table = ddb.create_table(
        TableName=SUBSCRIPTIONS_TABLE,
        KeySchema=[{"AttributeName": "subscription_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "subscription_id", "AttributeType": "S"},
            {"AttributeName": "product_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[{
            "IndexName": "product-index",
            "KeySchema": [{"AttributeName": "product_id", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )
    audit_table = ddb.create_table(
        TableName=AUDIT_TABLE,
        KeySchema=[{"AttributeName": "audit_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "audit_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return subs_table, audit_table


def _make_event(product_id=PRODUCT_ID, domain=DOMAIN, product_name=PRODUCT_NAME, sunset_date=SUNSET_DATE):
    return {
        "detail": {
            "product_id": product_id,
            "domain": domain,
            "product_name": product_name,
            "sunset_date": sunset_date,
            "breaking": True,
        }
    }


@mock_aws
def test_notifies_active_subscribers():
    """SNS notification sent for each active subscriber."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    subs_table, _ = _make_tables(ddb)

    subs_table.put_item(Item={
        "subscription_id": "sub-001",
        "product_id": PRODUCT_ID,
        "subscriber_account_id": "222222222222",
        "status": "ACTIVE",
    })
    subs_table.put_item(Item={
        "subscription_id": "sub-002",
        "product_id": PRODUCT_ID,
        "subscriber_account_id": "333333333333",
        "status": "ACTIVE",
    })

    with patch("boto3.client") as mock_boto_client:
        mock_sns = MagicMock()
        mock_boto_client.return_value = mock_sns

        import product_deprecation
        result = product_deprecation.handle_product_deprecated(_make_event(), None)

    assert result["subscribers_notified"] == 2
    assert result["status"] == "ok"
    assert mock_sns.publish.call_count == 2


@mock_aws
def test_skips_non_active_subscribers():
    """Only ACTIVE subscribers are notified; PENDING/REVOKED are skipped."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    subs_table, _ = _make_tables(ddb)

    subs_table.put_item(Item={
        "subscription_id": "sub-001",
        "product_id": PRODUCT_ID,
        "subscriber_account_id": "222222222222",
        "status": "ACTIVE",
    })
    subs_table.put_item(Item={
        "subscription_id": "sub-002",
        "product_id": PRODUCT_ID,
        "subscriber_account_id": "333333333333",
        "status": "PENDING",
    })
    subs_table.put_item(Item={
        "subscription_id": "sub-003",
        "product_id": PRODUCT_ID,
        "subscriber_account_id": "444444444444",
        "status": "REVOKED",
    })

    with patch("boto3.client") as mock_boto_client:
        mock_sns = MagicMock()
        mock_boto_client.return_value = mock_sns

        import product_deprecation
        result = product_deprecation.handle_product_deprecated(_make_event(), None)

    assert result["subscribers_notified"] == 1


@mock_aws
def test_handles_no_subscribers():
    """When there are no subscribers, returns 0 notified."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    _make_tables(ddb)

    with patch("boto3.client") as mock_boto_client:
        mock_sns = MagicMock()
        mock_boto_client.return_value = mock_sns

        import product_deprecation
        result = product_deprecation.handle_product_deprecated(_make_event(), None)

    assert result["subscribers_notified"] == 0
    assert result["status"] == "ok"
    mock_sns.publish.assert_not_called()


@mock_aws
def test_writes_audit_entry():
    """Audit log entry written after notifications."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    subs_table, audit_table = _make_tables(ddb)

    subs_table.put_item(Item={
        "subscription_id": "sub-001",
        "product_id": PRODUCT_ID,
        "subscriber_account_id": "222222222222",
        "status": "ACTIVE",
    })

    with patch("boto3.client") as mock_boto_client:
        mock_sns = MagicMock()
        mock_boto_client.return_value = mock_sns

        import product_deprecation
        product_deprecation.handle_product_deprecated(_make_event(), None)

    audit_items = audit_table.scan().get("Items", [])
    assert len(audit_items) >= 1
    audit = audit_items[0]
    assert audit["event_type"] == "ProductDeprecated"
    assert audit["product_id"] == PRODUCT_ID
    assert audit["sunset_date"] == SUNSET_DATE


@mock_aws
def test_sns_failure_does_not_raise():
    """If SNS publish fails for one subscriber, the handler still completes."""
    from botocore.exceptions import ClientError

    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    subs_table, _ = _make_tables(ddb)

    subs_table.put_item(Item={
        "subscription_id": "sub-001",
        "product_id": PRODUCT_ID,
        "subscriber_account_id": "222222222222",
        "status": "ACTIVE",
    })

    with patch("boto3.client") as mock_boto_client:
        mock_sns = MagicMock()
        mock_sns.publish.side_effect = ClientError(
            {"Error": {"Code": "AuthorizationError", "Message": "Not authorized"}},
            "Publish",
        )
        mock_boto_client.return_value = mock_sns

        import product_deprecation
        # Should not raise
        result = product_deprecation.handle_product_deprecated(_make_event(), None)

    # Handler completes even with SNS error; notified count is 0 because send failed
    assert result["status"] == "ok"
    assert result["subscribers_notified"] == 0


@mock_aws
def test_invalid_account_id_format_skipped():
    """Subscribers with non-12-digit account IDs are skipped and not counted."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    subs_table, _ = _make_tables(ddb)

    # Invalid account IDs
    subs_table.put_item(Item={
        "subscription_id": "sub-bad-1",
        "product_id": PRODUCT_ID,
        "subscriber_account_id": "not-an-account",
        "status": "ACTIVE",
    })
    subs_table.put_item(Item={
        "subscription_id": "sub-bad-2",
        "product_id": PRODUCT_ID,
        "subscriber_account_id": "12345",  # too short
        "status": "ACTIVE",
    })
    # Valid account — should still be notified
    subs_table.put_item(Item={
        "subscription_id": "sub-good",
        "product_id": PRODUCT_ID,
        "subscriber_account_id": "222222222222",
        "status": "ACTIVE",
    })

    with patch("boto3.client") as mock_boto_client:
        mock_sns = MagicMock()
        mock_boto_client.return_value = mock_sns

        import product_deprecation
        result = product_deprecation.handle_product_deprecated(_make_event(), None)

    # Only the valid account is counted
    assert result["subscribers_notified"] == 1
    assert mock_sns.publish.call_count == 1


@mock_aws
def test_event_without_wrapper():
    """Handler works when called directly without EventBridge detail wrapper."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    _make_tables(ddb)

    direct_event = {
        "product_id": PRODUCT_ID,
        "domain": DOMAIN,
        "product_name": PRODUCT_NAME,
        "sunset_date": SUNSET_DATE,
        "breaking": True,
    }

    with patch("boto3.client") as mock_boto_client:
        mock_boto_client.return_value = MagicMock()

        import product_deprecation
        result = product_deprecation.handle_product_deprecated(direct_event, None)

    assert result["product_id"] == PRODUCT_ID
    assert result["sunset_date"] == SUNSET_DATE
    assert result["status"] == "ok"
