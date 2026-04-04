"""
Tests for lambdas/subscription_compensator.py

Covers:
  - compensate: full rollback (resource_link → kms_grant → lf_grant)
  - compensate: partial (lf_grant only, on Step A failure)
  - compensate: missing KMS grant_id skips kms revoke
  - compensate: DynamoDB marked FAILED with compensation_reason
  - compensate: revoke path does NOT set FAILED
  - compensate: partial errors stored in compensation_reason
  - handler: delegates to compensate
"""
import os
import sys
import uuid
from unittest.mock import MagicMock, patch, call

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
KMS_KEY_ARN = f"arn:aws:kms:us-east-1:{PRODUCER_ACCOUNT}:key/test-key-id"
LF_ROLE_ARN = f"arn:aws:iam::{PRODUCER_ACCOUNT}:role/MeshLFGrantorRole"
KMS_ROLE_ARN = f"arn:aws:iam::{PRODUCER_ACCOUNT}:role/MeshKmsGrantorRole"


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
    monkeypatch.setenv("OWNER_NOTIFY_SNS_ARN", "arn:aws:sns:us-east-1:111111111111:owner-notify")
    monkeypatch.setenv("CENTRAL_ACCOUNT_ID", PRODUCER_ACCOUNT)


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
        "gold_kms_key_arn": KMS_KEY_ARN,
        "schema": {
            "columns": [
                {"name": "order_id", "type": "string", "pii": False},
                {"name": "customer_email", "type": "string", "pii": True},
            ]
        },
    })

    ddb_tables.Table(SUBSCRIPTIONS_TABLE).put_item(Item={
        "product_id": PRODUCT_ID,
        "subscriber_account_id": CONSUMER_ACCOUNT,
        "subscription_id": "test-sub-001",
        "status": "APPROVED",
        "requested_columns": ["order_id"],
        "kms_grant_id": "kms-grant-123",
        "created_at": "2026-04-01T00:00:00Z",
        "updated_at": "2026-04-01T00:00:00Z",
        "provisioning_steps": {"lf_grant": "DONE", "kms_grant": "DONE"},
    })

    yield ddb_tables


def _make_event(steps=None, reason="provisioning_failure", sub_id="test-sub-001"):
    return {
        "subscription_id": sub_id,
        "product_id": PRODUCT_ID,
        "consumer_account_id": CONSUMER_ACCOUNT,
        "compensation_steps": steps or ["resource_link", "kms_grant", "lf_grant"],
        "reason": reason,
    }


def _mock_sts_creds():
    return {
        "AccessKeyId": "ASIAMOCKED",
        "SecretAccessKey": "mockedsecret",
        "SessionToken": "mockedsessiontoken",
    }


class TestCompensate:

    def test_full_rollback_reverses_all_steps(self, seeded_tables):
        """All three steps reversed, DynamoDB status=FAILED."""
        from subscription_compensator import compensate

        mock_lf = MagicMock()
        mock_kms = MagicMock()
        mock_glue = MagicMock()

        def _client_factory(service, creds, region=None):
            services = {"lakeformation": mock_lf, "kms": mock_kms, "glue": mock_glue}
            return services.get(service, MagicMock())

        with patch("subscription_compensator._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_compensator._client_from_creds", side_effect=_client_factory), \
             patch("subscription_compensator._notify"):

            result = compensate(_make_event(), None)

        # All three revocation calls made
        mock_lf.batch_revoke_permissions.assert_called_once()
        mock_kms.retire_grant.assert_called_once_with(KeyId=KMS_KEY_ARN, GrantId="kms-grant-123")
        mock_glue.delete_table.assert_called_once()

        # DynamoDB status = FAILED
        sub = seeded_tables.Table(SUBSCRIPTIONS_TABLE).get_item(
            Key={"product_id": PRODUCT_ID, "subscriber_account_id": CONSUMER_ACCOUNT}
        )["Item"]
        assert sub["status"] == "FAILED"
        assert "provisioning_failure" in sub["compensation_reason"]
        assert result["compensation_status"] == "FAILED"

    def test_step_b_failure_triggers_lf_and_kms_revoke_only(self, seeded_tables):
        """Compensating from Step B failure: only lf_grant and kms_grant reversed."""
        from subscription_compensator import compensate

        mock_lf = MagicMock()
        mock_kms = MagicMock()
        mock_glue = MagicMock()

        def _client_factory(service, creds, region=None):
            return {"lakeformation": mock_lf, "kms": mock_kms, "glue": mock_glue}.get(service, MagicMock())

        event = _make_event(steps=["kms_grant", "lf_grant"], reason="kms_grant_failed")

        with patch("subscription_compensator._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_compensator._client_from_creds", side_effect=_client_factory), \
             patch("subscription_compensator._notify"):

            compensate(event, None)

        mock_lf.batch_revoke_permissions.assert_called_once()
        mock_kms.retire_grant.assert_called_once()
        # No resource link to delete
        mock_glue.delete_table.assert_not_called()

    def test_lf_only_compensation(self, seeded_tables):
        """Step A failure: only LF grant revoked."""
        from subscription_compensator import compensate

        mock_lf = MagicMock()

        def _client_factory(service, creds, region=None):
            return mock_lf if service == "lakeformation" else MagicMock()

        event = _make_event(steps=["lf_grant"], reason="lf_grant_failed")

        with patch("subscription_compensator._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_compensator._client_from_creds", side_effect=_client_factory), \
             patch("subscription_compensator._notify"):

            compensate(event, None)

        mock_lf.batch_revoke_permissions.assert_called_once()

    def test_missing_kms_grant_id_skips_kms_revoke(self, seeded_tables):
        """Subscription without kms_grant_id → KMS revoke skipped gracefully."""
        from subscription_compensator import compensate

        # Remove grant_id from DynamoDB
        seeded_tables.Table(SUBSCRIPTIONS_TABLE).update_item(
            Key={"product_id": PRODUCT_ID, "subscriber_account_id": CONSUMER_ACCOUNT},
            UpdateExpression="REMOVE kms_grant_id",
        )

        mock_kms = MagicMock()

        with patch("subscription_compensator._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_compensator._client_from_creds", return_value=mock_kms), \
             patch("subscription_compensator._notify"):

            result = compensate(_make_event(steps=["kms_grant"]), None)

        # KMS retire_grant not called (no grant_id)
        mock_kms.retire_grant.assert_not_called()
        assert result["errors"] == []

    def test_revoke_path_does_not_set_failed_status(self, seeded_tables):
        """Explicit revoke (reason='revoke') should NOT update status to FAILED."""
        from subscription_compensator import compensate

        mock_lf = MagicMock()

        with patch("subscription_compensator._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_compensator._client_from_creds", return_value=mock_lf), \
             patch("subscription_compensator._notify"):

            result = compensate(_make_event(reason="revoke"), None)

        # Status in DynamoDB should remain unchanged (APPROVED)
        sub = seeded_tables.Table(SUBSCRIPTIONS_TABLE).get_item(
            Key={"product_id": PRODUCT_ID, "subscriber_account_id": CONSUMER_ACCOUNT}
        )["Item"]
        assert sub["status"] == "APPROVED"
        assert result["compensation_status"] == "REVOKED"

    def test_compensation_with_partial_error_records_reason(self, seeded_tables):
        """When one revocation sub-step fails, error is captured and DynamoDB reflects it."""
        from subscription_compensator import compensate

        mock_lf = MagicMock()
        mock_lf.batch_revoke_permissions.side_effect = Exception("LF transient error")

        mock_kms = MagicMock()
        mock_glue = MagicMock()

        def _client_factory(service, creds, region=None):
            return {"lakeformation": mock_lf, "kms": mock_kms, "glue": mock_glue}.get(service, MagicMock())

        with patch("subscription_compensator._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_compensator._client_from_creds", side_effect=_client_factory), \
             patch("subscription_compensator._notify"):

            result = compensate(_make_event(), None)

        assert len(result["errors"]) > 0
        assert "lf_grant" in result["errors"][0]

        sub = seeded_tables.Table(SUBSCRIPTIONS_TABLE).get_item(
            Key={"product_id": PRODUCT_ID, "subscriber_account_id": CONSUMER_ACCOUNT}
        )["Item"]
        assert sub["status"] == "FAILED"
        assert "partial errors" in sub["compensation_reason"]

    def test_sns_notification_sent_on_failure(self, seeded_tables):
        """SNS alert is sent to owner on compensation."""
        from subscription_compensator import compensate

        mock_lf = MagicMock()

        with patch("subscription_compensator._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_compensator._client_from_creds", return_value=mock_lf), \
             patch("subscription_compensator._notify") as mock_notify:

            compensate(_make_event(), None)

        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert OWNER_EMAIL in call_args.args or OWNER_EMAIL in str(call_args)

    def test_handler_delegates_to_compensate(self, seeded_tables):
        """handler() calls compensate() with the same event."""
        from subscription_compensator import handler

        with patch("subscription_compensator.compensate") as mock_compensate:
            mock_compensate.return_value = {"compensation_status": "FAILED", "errors": []}
            event = _make_event()
            handler(event, None)
            mock_compensate.assert_called_once_with(event, None)

    def test_lf_invalid_input_ignored_as_already_revoked(self, seeded_tables):
        """InvalidInputException from LF batch_revoke_permissions is swallowed (already revoked)."""
        from subscription_compensator import compensate

        not_found = ClientError(
            {"Error": {"Code": "InvalidInputException", "Message": "no grant"}},
            "BatchRevokePermissions",
        )
        mock_lf = MagicMock()
        mock_lf.batch_revoke_permissions.side_effect = not_found

        with patch("subscription_compensator._assume_role", return_value=_mock_sts_creds()), \
             patch("subscription_compensator._client_from_creds", return_value=mock_lf), \
             patch("subscription_compensator._notify"):

            result = compensate(_make_event(steps=["lf_grant"]), None)

        # Should complete without error in the errors list
        lf_errors = [e for e in result["errors"] if "lf_grant" in e]
        assert lf_errors == []
