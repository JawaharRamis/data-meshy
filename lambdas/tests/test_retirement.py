"""
Tests for lambdas/retirement.py — Issue #13

Covers handle_retirement:
  - revokes LF grants for all ACTIVE subscribers
  - marks subscriptions REVOKED_BY_RETIREMENT
  - marks product RETIRED in DynamoDB
  - emits audit event
  - idempotent: already-RETIRED product returns early
  - product not found returns not_found status
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

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
    monkeypatch.setenv("CENTRAL_EVENT_BUS_NAME", "datameshy-central")
    monkeypatch.setenv("MESH_LF_GRANTOR_ROLE_ARN", "arn:aws:iam::111111111111:role/MeshLFGrantorRole")


def _make_tables(ddb, product_status="DEPRECATED"):
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

    products_table = ddb.create_table(
        TableName=PRODUCTS_TABLE,
        KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    audit_table = ddb.create_table(
        TableName=AUDIT_TABLE,
        KeySchema=[{"AttributeName": "audit_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "audit_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    products_table.put_item(Item={
        "product_id": PRODUCT_ID,
        "domain": DOMAIN,
        "product_name": PRODUCT_NAME,
        "status": product_status,
        "glue_catalog_db_gold": "sales_gold",
        "glue_table": PRODUCT_NAME,
    })

    return subs_table, products_table, audit_table


def _make_event(source="aws.scheduler"):
    return {
        "source": source,
        "product_id": PRODUCT_ID,
        "domain": DOMAIN,
        "product_name": PRODUCT_NAME,
    }


@mock_aws
def test_marks_product_retired():
    """Product status updated to RETIRED with retired_at timestamp."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    subs_table, products_table, audit_table = _make_tables(ddb)

    with patch("boto3.client") as mock_boto_client:
        mock_lf = MagicMock()
        mock_lf.batch_revoke_permissions.return_value = {"Failures": []}
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "111111111111"}
        mock_events = MagicMock()

        def client_factory(service, **kwargs):
            if service == "lakeformation":
                return mock_lf
            if service == "sts":
                return mock_sts
            if service == "events":
                return mock_events
            return MagicMock()

        mock_boto_client.side_effect = client_factory

        import retirement
        result = retirement.handle_retirement(_make_event(), None)

    assert result["status"] == "retired"
    item = products_table.get_item(Key={"product_id": PRODUCT_ID}).get("Item", {})
    assert item["status"] == "RETIRED"
    assert "retired_at" in item


@mock_aws
def test_revokes_active_subscriptions():
    """BatchRevokePermissions called and subscription records updated."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    subs_table, products_table, audit_table = _make_tables(ddb)

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
        mock_lf = MagicMock()
        mock_lf.batch_revoke_permissions.return_value = {"Failures": []}
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "111111111111"}
        mock_events = MagicMock()

        def client_factory(service, **kwargs):
            if service == "lakeformation":
                return mock_lf
            if service == "sts":
                return mock_sts
            if service == "events":
                return mock_events
            return MagicMock()

        mock_boto_client.side_effect = client_factory

        import retirement
        result = retirement.handle_retirement(_make_event(), None)

    assert result["subscriptions_revoked"] == 2
    mock_lf.batch_revoke_permissions.assert_called_once()

    # Check subscriptions were updated
    sub1 = subs_table.get_item(Key={"subscription_id": "sub-001"}).get("Item", {})
    sub2 = subs_table.get_item(Key={"subscription_id": "sub-002"}).get("Item", {})
    assert sub1["status"] == "REVOKED_BY_RETIREMENT"
    assert sub2["status"] == "REVOKED_BY_RETIREMENT"


@mock_aws
def test_skips_non_active_subscriptions():
    """Only ACTIVE subscriptions are revoked."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    subs_table, products_table, audit_table = _make_tables(ddb)

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
        "status": "REVOKED",  # already revoked
    })

    with patch("boto3.client") as mock_boto_client:
        mock_lf = MagicMock()
        mock_lf.batch_revoke_permissions.return_value = {"Failures": []}
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "111111111111"}

        def client_factory(service, **kwargs):
            if service == "lakeformation":
                return mock_lf
            if service == "sts":
                return mock_sts
            return MagicMock()

        mock_boto_client.side_effect = client_factory

        import retirement
        result = retirement.handle_retirement(_make_event(), None)

    assert result["subscriptions_revoked"] == 1


@mock_aws
def test_idempotent_for_already_retired():
    """Already-RETIRED product returns early without re-processing."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    _make_tables(ddb, product_status="RETIRED")

    with patch("boto3.client") as mock_boto_client:
        mock_lf = MagicMock()
        mock_boto_client.return_value = mock_lf

        import retirement
        result = retirement.handle_retirement(_make_event(), None)

    assert result["status"] == "already_retired"
    mock_lf.batch_revoke_permissions.assert_not_called()


@mock_aws
def test_product_not_found_returns_gracefully():
    """If product doesn't exist in DynamoDB, returns not_found without raising."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    # Create tables but don't put the product
    ddb.create_table(
        TableName=SUBSCRIPTIONS_TABLE,
        KeySchema=[{"AttributeName": "subscription_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "subscription_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName=PRODUCTS_TABLE,
        KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName=AUDIT_TABLE,
        KeySchema=[{"AttributeName": "audit_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "audit_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    with patch("boto3.client") as mock_boto_client:
        mock_boto_client.return_value = MagicMock()

        import retirement
        result = retirement.handle_retirement(_make_event(), None)

    assert result["status"] == "not_found"


@mock_aws
def test_writes_audit_entry():
    """Audit table entry created after retirement."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    subs_table, products_table, audit_table = _make_tables(ddb)

    with patch("boto3.client") as mock_boto_client:
        mock_lf = MagicMock()
        mock_lf.batch_revoke_permissions.return_value = {"Failures": []}
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "111111111111"}

        def client_factory(service, **kwargs):
            if service == "lakeformation":
                return mock_lf
            if service == "sts":
                return mock_sts
            return MagicMock()

        mock_boto_client.side_effect = client_factory

        import retirement
        retirement.handle_retirement(_make_event(), None)

    audit_items = audit_table.scan().get("Items", [])
    assert len(audit_items) >= 1
    audit = next((i for i in audit_items if i.get("event_type") == "ProductRetired"), None)
    assert audit is not None
    assert audit["product_id"] == PRODUCT_ID


@mock_aws
def test_no_subscribers_retires_cleanly():
    """Retirement works cleanly with zero subscriptions."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    _make_tables(ddb)  # no subscriptions added

    with patch("boto3.client") as mock_boto_client:
        mock_lf = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "111111111111"}

        def client_factory(service, **kwargs):
            if service == "lakeformation":
                return mock_lf
            if service == "sts":
                return mock_sts
            return MagicMock()

        mock_boto_client.side_effect = client_factory

        import retirement
        result = retirement.handle_retirement(_make_event(), None)

    assert result["status"] == "retired"
    assert result["subscriptions_revoked"] == 0
    mock_lf.batch_revoke_permissions.assert_not_called()


@mock_aws
def test_unauthorized_source_raises_value_error():
    """Invocation from an unknown source raises ValueError before any business logic."""
    import retirement

    bad_event = _make_event(source="unknown.source")

    with pytest.raises(ValueError, match="Unauthorized invocation source"):
        retirement.handle_retirement(bad_event, None)


@mock_aws
def test_missing_source_raises_value_error():
    """Invocation with no source field raises ValueError."""
    import retirement

    no_source_event = {
        "product_id": PRODUCT_ID,
        "domain": DOMAIN,
        "product_name": PRODUCT_NAME,
        # "source" deliberately omitted
    }

    with pytest.raises(ValueError, match="Unauthorized invocation source"):
        retirement.handle_retirement(no_source_event, None)


@mock_aws
def test_datameshy_scheduler_source_allowed():
    """datameshy.scheduler source is accepted (internal scheduler)."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    _make_tables(ddb)

    with patch("boto3.client") as mock_boto_client:
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {"Account": "111111111111"}

        def client_factory(service, **kwargs):
            if service == "sts":
                return mock_sts
            return MagicMock()

        mock_boto_client.side_effect = client_factory

        import retirement
        result = retirement.handle_retirement(_make_event(source="datameshy.scheduler"), None)

    assert result["status"] in ("retired", "already_retired")
