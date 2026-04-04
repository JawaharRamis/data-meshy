"""
Tests for lambdas/subscription_provisioner.py

Covers:
  - step_a_lf_grant: happy path, excluded PII columns, idempotent on AlreadyExists
  - step_b_kms_grant: happy path, missing key ARN raises, stores grant_id
  - step_c_resource_link: happy path, skips if link exists, raises on create failure
"""
import json
import os
import sys
import uuid
from unittest.mock import MagicMock, patch, call

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Constants ──────────────────────────────────────────────────────────────────
SUBSCRIPTIONS_TABLE = "mesh-subscriptions"
PRODUCTS_TABLE = "mesh-products"

PRODUCER_ACCOUNT = "111111111111"
CONSUMER_ACCOUNT = "222222222222"
PRODUCT_ID = "sales#customer_orders"
DOMAIN = "sales"
PRODUCT_NAME = "customer_orders"
OWNER_EMAIL = "sales-owner@example.com"
KMS_KEY_ARN = f"arn:aws:kms:us-east-1:{PRODUCER_ACCOUNT}:key/test-key-id"
LF_ROLE_ARN = f"arn:aws:iam::{PRODUCER_ACCOUNT}:role/MeshLFGrantorRole"
KMS_ROLE_ARN = f"arn:aws:iam::{PRODUCER_ACCOUNT}:role/MeshKmsGrantorRole"


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
    monkeypatch.setenv("LF_GRANTOR_ROLE_ARN", LF_ROLE_ARN)
    monkeypatch.setenv("KMS_GRANTOR_ROLE_ARN", KMS_ROLE_ARN)
    monkeypatch.setenv("CENTRAL_ACCOUNT_ID", PRODUCER_ACCOUNT)
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
    products_table = ddb_tables.Table(PRODUCTS_TABLE)
    products_table.put_item(Item={
        "domain#product_name": PRODUCT_ID,
        "domain": DOMAIN,
        "product_name": PRODUCT_NAME,
        "account_id": PRODUCER_ACCOUNT,
        "status": "ACTIVE",
        "owner": OWNER_EMAIL,
        "gold_kms_key_arn": KMS_KEY_ARN,
        "schema": {
            "columns": [
                {"name": "order_id", "type": "string", "pii": False},
                {"name": "order_date", "type": "date", "pii": False},
                {"name": "customer_email", "type": "string", "pii": True},
            ]
        },
    })

    subs_table = ddb_tables.Table(SUBSCRIPTIONS_TABLE)
    subs_table.put_item(Item={
        "product_id": PRODUCT_ID,
        "subscriber_account_id": CONSUMER_ACCOUNT,
        "subscription_id": "test-sub-001",
        "status": "APPROVED",
        "requested_columns": ["order_id", "order_date"],
        "created_at": "2026-04-01T00:00:00Z",
        "updated_at": "2026-04-01T00:00:00Z",
        "provisioning_steps": {},
    })

    yield ddb_tables


def _make_sfn_event(sub_id: str = "test-sub-001", columns: list = None) -> dict:
    return {
        "subscription_id": sub_id,
        "product_id": PRODUCT_ID,
        "consumer_account_id": CONSUMER_ACCOUNT,
        "requested_columns": columns or ["order_id", "order_date"],
    }


def _mock_sts_creds():
    return {
        "AccessKeyId": "ASIAMOCKED",
        "SecretAccessKey": "mockedsecret",
        "SessionToken": "mockedsessiontoken",
    }


# ── Step A: LF Grant ───────────────────────────────────────────────────────────

class TestStepALFGrant:

    def test_happy_path_grants_lf_permissions(self, seeded_tables):
        """Step A calls BatchGrantPermissions and marks lf_grant=DONE."""
        from subscription_provisioner import step_a_lf_grant

        mock_lf = MagicMock()
        mock_lf.batch_grant_permissions.return_value = {"Failures": []}

        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_lf):

            event = _make_sfn_event()
            result = step_a_lf_grant(event, None)

        assert result["lf_grant"] == "DONE"
        mock_lf.batch_grant_permissions.assert_called_once()

        # Verify DynamoDB was updated
        sub = seeded_tables.Table(SUBSCRIPTIONS_TABLE).get_item(
            Key={"product_id": PRODUCT_ID, "subscriber_account_id": CONSUMER_ACCOUNT}
        )["Item"]
        assert sub["provisioning_steps"]["lf_grant"] == "DONE"

    def test_pii_columns_excluded_from_grant(self, seeded_tables):
        """Customer_email (PII) should appear in ExcludedColumnNames."""
        from subscription_provisioner import step_a_lf_grant

        mock_lf = MagicMock()
        mock_lf.batch_grant_permissions.return_value = {"Failures": []}

        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_lf):

            # Request only non-PII columns
            event = _make_sfn_event(columns=["order_id", "order_date"])
            step_a_lf_grant(event, None)

        call_kwargs = mock_lf.batch_grant_permissions.call_args[1]
        entry = call_kwargs["Entries"][0]
        col_wildcard = entry["Resource"]["TableWithColumns"]["ColumnWildcard"]
        # customer_email is PII and not requested → should be excluded
        assert "customer_email" in col_wildcard["ExcludedColumnNames"]

    def test_idempotent_on_already_exists(self, seeded_tables):
        """AlreadyExistsException is treated as success (idempotent)."""
        from subscription_provisioner import step_a_lf_grant

        error_response = {
            "Error": {"Code": "AlreadyExistsException", "Message": "Grant already exists"}
        }
        mock_lf = MagicMock()
        mock_lf.batch_grant_permissions.side_effect = ClientError(error_response, "BatchGrantPermissions")

        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_lf):

            event = _make_sfn_event()
            result = step_a_lf_grant(event, None)

        assert result["lf_grant"] == "DONE"

    def test_concurrent_modification_propagates(self, seeded_tables):
        """ConcurrentModificationException propagates (SFN will retry)."""
        from subscription_provisioner import step_a_lf_grant

        error_response = {
            "Error": {"Code": "ConcurrentModificationException", "Message": "Concurrent"}
        }
        mock_lf = MagicMock()
        mock_lf.batch_grant_permissions.side_effect = ClientError(error_response, "BatchGrantPermissions")

        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_lf):

            with pytest.raises(ClientError):
                step_a_lf_grant(_make_sfn_event(), None)

    def test_product_not_found_raises(self, seeded_tables):
        from subscription_provisioner import step_a_lf_grant

        event = {**_make_sfn_event(), "product_id": "nonexistent#product"}
        with pytest.raises(ValueError, match="Product not found"):
            step_a_lf_grant(event, None)


# ── Step B: KMS Grant ──────────────────────────────────────────────────────────

class TestStepBKMSGrant:

    def test_happy_path_creates_kms_grant(self, seeded_tables):
        """Step B calls create_grant and stores grant_id in DynamoDB."""
        from subscription_provisioner import step_b_kms_grant

        mock_kms = MagicMock()
        mock_kms.create_grant.return_value = {"GrantId": "test-grant-id-001", "GrantToken": "token"}

        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_kms):

            result = step_b_kms_grant(_make_sfn_event(), None)

        assert result["kms_grant"] == "DONE"
        assert result["kms_grant_id"] == "test-grant-id-001"
        mock_kms.create_grant.assert_called_once()

        # Verify grant_id stored in DynamoDB
        sub = seeded_tables.Table(SUBSCRIPTIONS_TABLE).get_item(
            Key={"product_id": PRODUCT_ID, "subscriber_account_id": CONSUMER_ACCOUNT}
        )["Item"]
        assert sub["kms_grant_id"] == "test-grant-id-001"
        assert sub["provisioning_steps"]["kms_grant"] == "DONE"

    def test_kms_grant_uses_correct_grantee(self, seeded_tables):
        """Grantee must be consumer account's MeshGlueConsumerRole."""
        from subscription_provisioner import step_b_kms_grant

        mock_kms = MagicMock()
        mock_kms.create_grant.return_value = {"GrantId": "gid", "GrantToken": "tok"}

        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_kms):

            step_b_kms_grant(_make_sfn_event(), None)

        call_kwargs = mock_kms.create_grant.call_args[1]
        assert CONSUMER_ACCOUNT in call_kwargs["GranteePrincipal"]
        assert "MeshGlueConsumerRole" in call_kwargs["GranteePrincipal"]
        assert "Decrypt" in call_kwargs["Operations"]
        assert "DescribeKey" in call_kwargs["Operations"]

    def test_missing_kms_key_raises(self, seeded_tables):
        """Product without gold_kms_key_arn should raise ValueError."""
        from subscription_provisioner import step_b_kms_grant

        # Remove KMS key from product
        seeded_tables.Table(PRODUCTS_TABLE).update_item(
            Key={"domain#product_name": PRODUCT_ID},
            UpdateExpression="REMOVE gold_kms_key_arn",
        )

        with pytest.raises(ValueError, match="gold_kms_key_arn"):
            step_b_kms_grant(_make_sfn_event(), None)

    def test_kms_client_error_propagates(self, seeded_tables):
        """ClientError from KMS propagates → SFN triggers compensation."""
        from subscription_provisioner import step_b_kms_grant

        error_response = {
            "Error": {"Code": "DisabledException", "Message": "KMS key disabled"}
        }
        mock_kms = MagicMock()
        mock_kms.create_grant.side_effect = ClientError(error_response, "CreateGrant")

        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_kms):

            with pytest.raises(ClientError):
                step_b_kms_grant(_make_sfn_event(), None)


# ── Step C: Resource Link ──────────────────────────────────────────────────────

class TestStepCResourceLink:

    def test_happy_path_creates_resource_link(self, seeded_tables):
        """Step C creates Glue resource link and marks subscription ACTIVE."""
        from subscription_provisioner import step_c_resource_link

        mock_glue = MagicMock()
        # get_table raises EntityNotFoundException → link does not exist
        not_found = ClientError(
            {"Error": {"Code": "EntityNotFoundException", "Message": "not found"}},
            "GetTable",
        )
        mock_glue.get_table.side_effect = not_found
        mock_glue.create_table.return_value = {}

        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_glue), \
             patch("subscription_provisioner._emit_event") as mock_emit:

            result = step_c_resource_link(_make_sfn_event(), None)

        assert result["resource_link"] == "DONE"
        assert result["status"] == "ACTIVE"
        mock_glue.create_table.assert_called_once()
        mock_emit.assert_called_once()
        assert mock_emit.call_args.args[0] == "SubscriptionProvisioned"
        assert mock_emit.call_args.args[1]["subscription_id"] == "test-sub-001"

        # DynamoDB status = ACTIVE
        sub = seeded_tables.Table(SUBSCRIPTIONS_TABLE).get_item(
            Key={"product_id": PRODUCT_ID, "subscriber_account_id": CONSUMER_ACCOUNT}
        )["Item"]
        assert sub["status"] == "ACTIVE"
        assert sub["provisioning_steps"]["resource_link"] == "DONE"

    def test_resource_link_already_exists_skips_creation(self, seeded_tables):
        """If resource link exists, get_table succeeds → create_table not called."""
        from subscription_provisioner import step_c_resource_link

        mock_glue = MagicMock()
        # get_table succeeds → link already exists
        mock_glue.get_table.return_value = {"Table": {"Name": "sales_customer_orders_link"}}

        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_glue), \
             patch("subscription_provisioner._emit_event"):

            result = step_c_resource_link(_make_sfn_event(), None)

        assert result["resource_link"] == "DONE"
        mock_glue.create_table.assert_not_called()

    def test_resource_link_create_failure_propagates(self, seeded_tables):
        """Failure in create_table propagates → SFN triggers CompensateAll."""
        from subscription_provisioner import step_c_resource_link

        not_found = ClientError(
            {"Error": {"Code": "EntityNotFoundException", "Message": "not found"}},
            "GetTable",
        )
        create_error = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "CreateTable",
        )
        mock_glue = MagicMock()
        mock_glue.get_table.side_effect = not_found
        mock_glue.create_table.side_effect = create_error

        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_glue):

            with pytest.raises(ClientError):
                step_c_resource_link(_make_sfn_event(), None)

    def test_step_c_emits_provisioned_event(self, seeded_tables):
        """SubscriptionProvisioned event includes granted_columns."""
        from subscription_provisioner import step_c_resource_link

        not_found = ClientError(
            {"Error": {"Code": "EntityNotFoundException", "Message": "not found"}},
            "GetTable",
        )
        mock_glue = MagicMock()
        mock_glue.get_table.side_effect = not_found

        emitted = []
        with patch("subscription_provisioner._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_provisioner._client_from_creds", return_value=mock_glue), \
             patch("subscription_provisioner._emit_event", side_effect=lambda dt, d: emitted.append((dt, d))):

            step_c_resource_link(_make_sfn_event(columns=["order_id"]), None)

        assert len(emitted) == 1
        detail_type, detail = emitted[0]
        assert detail_type == "SubscriptionProvisioned"
        assert detail["granted_columns"] == ["order_id"]
        assert detail["provisioned"] is True
