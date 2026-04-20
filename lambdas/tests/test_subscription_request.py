"""
Tests for lambdas/subscription_request.py

Covers:
  - handle_subscribe_request: happy path (manual), auto-approve path, unregistered domain, duplicate
  - handle_approve: happy path, wrong owner, not-pending state
  - handle_revoke: owner path, admin path, not-owner rejection
  - handle_list: by product_id, by subscriber_domain, missing param
"""
import json
import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Constants ──────────────────────────────────────────────────────────────────
SUBSCRIPTIONS_TABLE = "mesh-subscriptions"
PRODUCTS_TABLE = "mesh-products"
DOMAINS_TABLE = "mesh-domains"

PRODUCER_ACCOUNT = "111111111111"
CONSUMER_ACCOUNT = "222222222222"
PRODUCT_ID = "sales#customer_orders"
DOMAIN = "sales"
PRODUCT_NAME = "customer_orders"
OWNER_EMAIL = "sales-owner@example.com"


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "test")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "test")
    monkeypatch.setenv("MESH_SUBSCRIPTIONS_TABLE", SUBSCRIPTIONS_TABLE)
    monkeypatch.setenv("MESH_PRODUCTS_TABLE", PRODUCTS_TABLE)
    monkeypatch.setenv("MESH_DOMAINS_TABLE", DOMAINS_TABLE)
    monkeypatch.setenv("SUBSCRIPTION_SFN_ARN", "arn:aws:states:us-east-1:111111111111:stateMachine:sub-provisioner")
    monkeypatch.setenv("OWNER_NOTIFY_SNS_ARN", "arn:aws:sns:us-east-1:111111111111:owner-notify")
    monkeypatch.setenv("CENTRAL_EVENT_BUS_NAME", "datameshy-central")
    monkeypatch.setenv("CENTRAL_ACCOUNT_ID", PRODUCER_ACCOUNT)


@pytest.fixture
def ddb_tables():
    """Create all required DynamoDB tables."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")

        # mesh-domains
        ddb.create_table(
            TableName=DOMAINS_TABLE,
            KeySchema=[{"AttributeName": "domain_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "domain_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # mesh-products
        ddb.create_table(
            TableName=PRODUCTS_TABLE,
            KeySchema=[{"AttributeName": "domain#product_name", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "domain#product_name", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # mesh-subscriptions (PK=product_id, SK=subscriber_account_id)
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
def populated_tables(ddb_tables):
    """Seed domain + product records."""
    domains_table = ddb_tables.Table(DOMAINS_TABLE)
    domains_table.put_item(Item={
        "domain_name": DOMAIN,
        "account_id": CONSUMER_ACCOUNT,
        "owner": "sales-team@example.com",
        "business_unit": "sales",
        "status": "ACTIVE",
    })

    products_table = ddb_tables.Table(PRODUCTS_TABLE)
    products_table.put_item(Item={
        "domain#product_name": PRODUCT_ID,
        "domain": DOMAIN,
        "product_name": PRODUCT_NAME,
        "account_id": PRODUCER_ACCOUNT,
        "status": "ACTIVE",
        "owner": OWNER_EMAIL,
        "classification": "internal",
        "business_unit": "sales",
        "schema": {
            "columns": [
                {"name": "order_id", "type": "string", "pii": False},
                {"name": "order_date", "type": "date", "pii": False},
                {"name": "customer_email", "type": "string", "pii": True},
            ]
        },
    })

    yield ddb_tables


def _api_event(body: dict, account_id: str = CONSUMER_ACCOUNT, caller_arn: str = "") -> dict:
    return {
        "requestContext": {
            "accountId": account_id,
            "identity": {"userArn": caller_arn},
        },
        "headers": {},
        "queryStringParameters": None,
        "body": json.dumps(body),
    }


# ── handle_subscribe_request tests ────────────────────────────────────────────

class TestHandleSubscribeRequest:

    @patch("subscription_request._start_sfn", return_value="arn:sfn:exec:1")
    @patch("subscription_request._emit_event")
    @patch("subscription_request._notify_owner")
    def test_manual_approval_path(self, mock_notify, mock_emit, mock_sfn, populated_tables):
        """Non-PII columns but different BU — should be manual (no auto-approve)."""
        from subscription_request import handle_subscribe_request

        # Different BU triggers manual path
        populated_tables.Table(DOMAINS_TABLE).update_item(
            Key={"domain_name": DOMAIN},
            UpdateExpression="SET business_unit = :bu",
            ExpressionAttributeValues={":bu": "marketing"},
        )

        event = _api_event({
            "product_id": PRODUCT_ID,
            "consumer_account_id": CONSUMER_ACCOUNT,
            "requested_columns": ["order_id", "order_date"],
            "justification": "For campaign analysis",
        })

        result = handle_subscribe_request(event, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["status"] == "PENDING"
        assert "subscription_id" in body
        mock_notify.assert_called_once()
        mock_sfn.assert_not_called()
        # Verify SubscriptionRequested was emitted (detail shape checked by inspecting call args)
        mock_emit.assert_called_once()
        assert mock_emit.call_args.args[0] == "SubscriptionRequested"

    @patch("subscription_request._start_sfn", return_value="arn:sfn:exec:2")
    @patch("subscription_request._emit_event")
    @patch("subscription_request._notify_owner")
    def test_auto_approve_path_same_bu_no_pii(self, mock_notify, mock_emit, mock_sfn, populated_tables):
        """Non-PII + same BU → auto-approve, SFN started, notify not called."""
        from subscription_request import handle_subscribe_request

        event = _api_event({
            "product_id": PRODUCT_ID,
            "consumer_account_id": CONSUMER_ACCOUNT,
            "requested_columns": ["order_id", "order_date"],
            "justification": "BI reporting",
        })

        result = handle_subscribe_request(event, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["status"] == "PROVISIONING"
        mock_sfn.assert_called_once()
        mock_notify.assert_not_called()

    @patch("subscription_request._emit_event")
    def test_pii_column_triggers_manual(self, mock_emit, populated_tables):
        """Requesting PII column → always manual, even same BU."""
        from subscription_request import handle_subscribe_request

        with patch("subscription_request._start_sfn") as mock_sfn, \
             patch("subscription_request._notify_owner") as mock_notify:

            event = _api_event({
                "product_id": PRODUCT_ID,
                "consumer_account_id": CONSUMER_ACCOUNT,
                "requested_columns": ["order_id", "customer_email"],
                "justification": "Need email for GDPR lookup",
            })

            result = handle_subscribe_request(event, None)
            body = json.loads(result["body"])

            assert result["statusCode"] == 200
            assert body["status"] == "PENDING"
            mock_sfn.assert_not_called()
            mock_notify.assert_called_once()

    def test_unregistered_domain_rejected(self, populated_tables):
        """Caller from an account not in mesh-domains should get 403."""
        from subscription_request import handle_subscribe_request

        event = _api_event(
            {"product_id": PRODUCT_ID, "consumer_account_id": "999999999999"},
            account_id="999999999999",
        )
        result = handle_subscribe_request(event, None)
        assert result["statusCode"] == 403

    def test_product_not_found_returns_404(self, populated_tables):
        from subscription_request import handle_subscribe_request

        event = _api_event({
            "product_id": "nonexistent#product",
            "consumer_account_id": CONSUMER_ACCOUNT,
            "requested_columns": [],
        })
        result = handle_subscribe_request(event, None)
        assert result["statusCode"] == 404

    @patch("subscription_request._emit_event")
    @patch("subscription_request._notify_owner")
    def test_duplicate_subscription_returns_409(self, mock_notify, mock_emit, populated_tables):
        """Second subscription attempt for same product/account returns 409."""
        from subscription_request import handle_subscribe_request

        event = _api_event({
            "product_id": PRODUCT_ID,
            "consumer_account_id": CONSUMER_ACCOUNT,
            "requested_columns": ["order_id"],
        })
        # Seed existing ACTIVE subscription
        populated_tables.Table(SUBSCRIPTIONS_TABLE).put_item(Item={
            "product_id": PRODUCT_ID,
            "subscriber_account_id": CONSUMER_ACCOUNT,
            "subscription_id": str(uuid.uuid4()),
            "status": "ACTIVE",
            "requested_columns": ["order_id"],
            "created_at": "2026-04-01T00:00:00Z",
            "updated_at": "2026-04-01T00:00:00Z",
            "subscriber_domain": DOMAIN,
            "provisioning_steps": {},
        })

        result = handle_subscribe_request(event, None)
        assert result["statusCode"] == 409


# ── handle_approve tests ───────────────────────────────────────────────────────

class TestHandleApprove:

    def _seed_pending(self, ddb, sub_id: str) -> None:
        ddb.Table(SUBSCRIPTIONS_TABLE).put_item(Item={
            "product_id": PRODUCT_ID,
            "subscriber_account_id": CONSUMER_ACCOUNT,
            "subscription_id": sub_id,
            "status": "PENDING",
            "requested_columns": ["order_id", "order_date"],
            "created_at": "2026-04-01T00:00:00Z",
            "updated_at": "2026-04-01T00:00:00Z",
            "subscriber_domain": DOMAIN,
            "provisioning_steps": {},
        })

    @patch("subscription_request._start_sfn", return_value="arn:sfn:exec:3")
    @patch("subscription_request._emit_event")
    def test_approve_happy_path(self, mock_emit, mock_sfn, populated_tables):
        from subscription_request import handle_approve

        sub_id = str(uuid.uuid4())
        self._seed_pending(populated_tables, sub_id)

        event = _api_event(
            {"subscription_id": sub_id, "comment": "Looks good"},
            caller_arn=f"arn:aws:iam::{PRODUCER_ACCOUNT}:user/{OWNER_EMAIL}",
        )
        event["headers"]["x-mesh-owner-token"] = OWNER_EMAIL

        result = handle_approve(event, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["status"] == "APPROVED"
        # PII column (customer_email) excluded from grant
        assert "customer_email" not in body["granted_columns"]
        assert "order_id" in body["granted_columns"]
        mock_sfn.assert_called_once()
        # SubscriptionApproved emitted from central
        emit_call = mock_emit.call_args
        assert emit_call.args[0] == "SubscriptionApproved"
        assert emit_call.args[1]["subscription_id"] == sub_id

    def test_approve_not_owner_rejected(self, populated_tables):
        from subscription_request import handle_approve

        sub_id = str(uuid.uuid4())
        self._seed_pending(populated_tables, sub_id)

        event = _api_event(
            {"subscription_id": sub_id},
            caller_arn="arn:aws:iam::999999999999:user/outsider@example.com",
        )
        result = handle_approve(event, None)
        assert result["statusCode"] == 403

    @patch("subscription_request._start_sfn", return_value="arn:sfn:exec:4")
    @patch("subscription_request._emit_event")
    def test_approve_already_approved_returns_409(self, mock_emit, mock_sfn, populated_tables):
        from subscription_request import handle_approve

        sub_id = str(uuid.uuid4())
        # Seed APPROVED subscription
        populated_tables.Table(SUBSCRIPTIONS_TABLE).put_item(Item={
            "product_id": PRODUCT_ID,
            "subscriber_account_id": CONSUMER_ACCOUNT,
            "subscription_id": sub_id,
            "status": "APPROVED",
            "requested_columns": ["order_id"],
            "created_at": "2026-04-01T00:00:00Z",
            "updated_at": "2026-04-01T00:00:00Z",
            "subscriber_domain": DOMAIN,
            "provisioning_steps": {},
        })

        event = _api_event({"subscription_id": sub_id})
        event["headers"]["x-mesh-owner-token"] = OWNER_EMAIL
        result = handle_approve(event, None)
        assert result["statusCode"] == 409

    def test_approve_subscription_not_found(self, populated_tables):
        from subscription_request import handle_approve

        event = _api_event({"subscription_id": "nonexistent-uuid"})
        result = handle_approve(event, None)
        assert result["statusCode"] == 404


# ── handle_revoke tests ────────────────────────────────────────────────────────

class TestHandleRevoke:

    def _seed_active(self, ddb, sub_id: str) -> None:
        ddb.Table(SUBSCRIPTIONS_TABLE).put_item(Item={
            "product_id": PRODUCT_ID,
            "subscriber_account_id": CONSUMER_ACCOUNT,
            "subscription_id": sub_id,
            "status": "ACTIVE",
            "requested_columns": ["order_id"],
            "created_at": "2026-04-01T00:00:00Z",
            "updated_at": "2026-04-01T00:00:00Z",
            "subscriber_domain": DOMAIN,
            "provisioning_steps": {"lf_grant": "DONE", "kms_grant": "DONE", "resource_link": "DONE"},
        })

    @patch("subscription_request._emit_event")
    def test_owner_can_revoke(self, mock_emit, populated_tables):
        from subscription_request import handle_revoke

        sub_id = str(uuid.uuid4())
        self._seed_active(populated_tables, sub_id)

        with patch("subscription_compensator.compensate") as mock_comp:
            mock_comp.return_value = {"compensation_status": "REVOKED"}

            event = _api_event(
                {"subscription_id": sub_id},
                caller_arn=f"arn:aws:iam::{PRODUCER_ACCOUNT}:user/{OWNER_EMAIL}",
            )
            result = handle_revoke(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["status"] == "REVOKED"
        mock_emit.assert_called_with("SubscriptionRevoked", pytest.approx({
            "subscription_id": sub_id,
            "product_id": PRODUCT_ID,
            "consumer_account_id": CONSUMER_ACCOUNT,
        }))

    @patch("subscription_request._emit_event")
    def test_platform_admin_can_revoke(self, mock_emit, populated_tables):
        from subscription_request import handle_revoke

        sub_id = str(uuid.uuid4())
        self._seed_active(populated_tables, sub_id)

        with patch("subscription_compensator.compensate") as mock_comp:
            mock_comp.return_value = {"compensation_status": "REVOKED"}

            event = _api_event(
                {"subscription_id": sub_id},
                caller_arn="arn:aws:iam::111111111111:assumed-role/MeshAdminRole/session",
            )
            result = handle_revoke(event, None)

        assert result["statusCode"] == 200

    def test_non_owner_non_admin_rejected(self, populated_tables):
        from subscription_request import handle_revoke

        sub_id = str(uuid.uuid4())
        self._seed_active(populated_tables, sub_id)

        event = _api_event(
            {"subscription_id": sub_id},
            caller_arn="arn:aws:iam::333333333333:user/random@example.com",
        )
        result = handle_revoke(event, None)
        assert result["statusCode"] == 403


# ── handle_list tests ──────────────────────────────────────────────────────────

class TestHandleList:

    def _seed_subscription(self, ddb, sub_id: str, status: str = "ACTIVE") -> None:
        ddb.Table(SUBSCRIPTIONS_TABLE).put_item(Item={
            "product_id": PRODUCT_ID,
            "subscriber_account_id": CONSUMER_ACCOUNT,
            "subscription_id": sub_id,
            "status": status,
            "requested_columns": ["order_id"],
            "created_at": "2026-04-01T00:00:00Z",
            "updated_at": "2026-04-01T00:00:00Z",
            "subscriber_domain": DOMAIN,
            "provisioning_steps": {},
        })

    def test_list_by_product_id(self, populated_tables):
        from subscription_request import handle_list

        sub_id = str(uuid.uuid4())
        self._seed_subscription(populated_tables, sub_id)

        event = {
            "requestContext": {"accountId": PRODUCER_ACCOUNT},
            "queryStringParameters": {"product_id": PRODUCT_ID},
        }
        result = handle_list(event, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert len(body["subscriptions"]) == 1
        assert body["subscriptions"][0]["subscription_id"] == sub_id
        assert "status" in body["subscriptions"][0]
        assert "requested_columns" in body["subscriptions"][0]

    def test_list_by_subscriber_domain(self, populated_tables):
        from subscription_request import handle_list

        sub_id = str(uuid.uuid4())
        self._seed_subscription(populated_tables, sub_id)

        event = {
            "requestContext": {"accountId": CONSUMER_ACCOUNT},
            "queryStringParameters": {"subscriber_domain": DOMAIN},
        }
        result = handle_list(event, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert any(s["subscription_id"] == sub_id for s in body["subscriptions"])

    def test_list_missing_params_returns_400(self, populated_tables):
        from subscription_request import handle_list

        event = {
            "requestContext": {"accountId": CONSUMER_ACCOUNT},
            "queryStringParameters": {},
        }
        result = handle_list(event, None)
        assert result["statusCode"] == 400

    def test_list_response_shape(self, populated_tables):
        """Response items must include required fields for Stream 3 contract."""
        from subscription_request import handle_list

        sub_id = str(uuid.uuid4())
        self._seed_subscription(populated_tables, sub_id)

        event = {
            "requestContext": {"accountId": PRODUCER_ACCOUNT},
            "queryStringParameters": {"product_id": PRODUCT_ID},
        }
        result = handle_list(event, None)
        body = json.loads(result["body"])
        item = body["subscriptions"][0]

        required_fields = {"subscription_id", "product_id", "status", "requested_columns", "created_at"}
        assert required_fields.issubset(item.keys())
