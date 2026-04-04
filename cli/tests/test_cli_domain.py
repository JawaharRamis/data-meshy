"""Tests for CLI domain commands — onboard, list, status."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from datameshy.cli import app


runner = CliRunner()


def _invoke(*args, **kwargs):
    """Helper to invoke CLI with the runner."""
    return runner.invoke(app, *args, **kwargs)


def _setup_mock_session(session):
    """Patch aws_client.get_session to return the given session."""
    from datameshy.lib import aws_client
    return patch.object(aws_client, "get_session", return_value=session)


# ---------------------------------------------------------------------------
# domain onboard
# ---------------------------------------------------------------------------


class TestDomainOnboard:
    """Tests for 'datameshy domain onboard' command."""

    def test_onboard_dry_run_scaffolds_directory(self, tmp_path, monkeypatch):
        """Dry run should create the Terraform environment directory and tfvars."""
        monkeypatch.chdir(tmp_path)
        result = _invoke([
            "domain", "onboard",
            "--name", "sales",
            "--account-id", "123456789012",
            "--owner", "sales@company.com",
            "--dry-run",
        ])
        assert result.exit_code == 0
        assert "scaffolded" in result.output.lower() or "sales" in result.output

        env_dir = tmp_path / "infra" / "environments" / "domain-sales"
        assert env_dir.is_dir()
        tfvars = env_dir / "terraform.tfvars"
        assert tfvars.exists()
        content = tfvars.read_text()
        assert 'domain' in content and '"sales"' in content
        assert 'account_id' in content and '"123456789012"' in content
        assert 'owner' in content and '"sales@company.com"' in content

    def test_onboard_invalid_domain_name_special_chars(self):
        """Domain name with special characters should fail."""
        result = _invoke([
            "domain", "onboard",
            "--name", "sales@domain!",
            "--account-id", "123456789012",
            "--owner", "test@example.com",
            "--dry-run",
        ])
        assert result.exit_code != 0

    def test_onboard_domain_name_too_long(self):
        """Domain name over 32 chars should fail."""
        result = _invoke([
            "domain", "onboard",
            "--name", "a" * 33,
            "--account-id", "123456789012",
            "--owner", "test@example.com",
            "--dry-run",
        ])
        assert result.exit_code != 0

    def test_onboard_invalid_account_id(self):
        """Account ID that is not 12 digits should fail."""
        result = _invoke([
            "domain", "onboard",
            "--name", "sales",
            "--account-id", "123",
            "--owner", "test@example.com",
            "--dry-run",
        ])
        assert result.exit_code != 0

    def test_onboard_invalid_owner_email(self):
        """Owner without @ should fail."""
        result = _invoke([
            "domain", "onboard",
            "--name", "sales",
            "--account-id", "123456789012",
            "--owner", "notanemail",
            "--dry-run",
        ])
        assert result.exit_code != 0

    def test_onboard_valid_domain_name_with_hyphens(self, tmp_path, monkeypatch):
        """Domain names with hyphens should be accepted."""
        monkeypatch.chdir(tmp_path)
        result = _invoke([
            "domain", "onboard",
            "--name", "my-sales-domain",
            "--account-id", "123456789012",
            "--owner", "test@example.com",
            "--dry-run",
        ])
        assert result.exit_code == 0

    def test_onboard_single_char_domain_name(self, tmp_path, monkeypatch):
        """Single character domain name should be accepted."""
        monkeypatch.chdir(tmp_path)
        result = _invoke([
            "domain", "onboard",
            "--name", "a",
            "--account-id", "123456789012",
            "--owner", "test@example.com",
            "--dry-run",
        ])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# domain list
# ---------------------------------------------------------------------------


class TestDomainList:
    """Tests for 'datameshy domain list' command."""

    @mock_aws
    def test_list_empty(self):
        """Should show message when no domains exist."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")
        dynamodb.create_table(
            TableName="mesh-domains",
            KeySchema=[{"AttributeName": "domain", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "domain", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        with _setup_mock_session(session):
            result = _invoke(["domain", "list"])
            assert result.exit_code == 0

    @mock_aws
    def test_list_with_domains(self):
        """Should list existing domains."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")
        table = dynamodb.create_table(
            TableName="mesh-domains",
            KeySchema=[{"AttributeName": "domain", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "domain", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        table.put_item(Item={
            "domain": "sales",
            "account_id": "123456789012",
            "owner": "sales@company.com",
            "status": "ACTIVE",
        })

        with _setup_mock_session(session):
            result = _invoke(["domain", "list"])
            assert result.exit_code == 0
            assert "sales" in result.output


# ---------------------------------------------------------------------------
# domain status
# ---------------------------------------------------------------------------


class TestDomainStatus:
    """Tests for 'datameshy domain status' command."""

    @mock_aws
    def test_status_existing_domain(self):
        """Should show domain details and product count."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        domains_table = dynamodb.create_table(
            TableName="mesh-domains",
            KeySchema=[{"AttributeName": "domain", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "domain", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "product_id", "AttributeType": "S"},
                {"AttributeName": "domain", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            GlobalSecondaryIndexes=[{
                "IndexName": "domain-index",
                "KeySchema": [{"AttributeName": "domain", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
        )

        domains_table.put_item(Item={
            "domain": "sales",
            "account_id": "123456789012",
            "owner": "sales@company.com",
            "status": "ACTIVE",
        })

        with _setup_mock_session(session):
            result = _invoke(["domain", "status", "--name", "sales"])
            assert result.exit_code == 0
            assert "sales" in result.output
            assert "123456789012" in result.output

    @mock_aws
    def test_status_nonexistent_domain(self):
        """Should error for a domain that doesn't exist."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")
        dynamodb.create_table(
            TableName="mesh-domains",
            KeySchema=[{"AttributeName": "domain", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "domain", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "product_id", "AttributeType": "S"},
                {"AttributeName": "domain", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            GlobalSecondaryIndexes=[{
                "IndexName": "domain-index",
                "KeySchema": [{"AttributeName": "domain", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
        )

        with _setup_mock_session(session):
            result = _invoke(["domain", "status", "--name", "nonexistent"])
            assert result.exit_code != 0

    def test_status_invalid_domain_name(self):
        """Should fail for invalid domain name format."""
        result = _invoke(["domain", "status", "--name", "bad!name"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# domain onboard — full flow (terraform + events)
# ---------------------------------------------------------------------------


class TestDomainOnboardFullFlow:
    """Tests for the full onboard flow with terraform and event emission."""

    def test_onboard_terraform_plan_not_found(self, tmp_path, monkeypatch):
        """Should handle terraform plan FileNotFoundError gracefully."""
        monkeypatch.chdir(tmp_path)
        with patch("datameshy.lib.terraform_runner.plan", side_effect=FileNotFoundError("terraform not found")):
            result = _invoke([
                "domain", "onboard",
                "--name", "sales",
                "--account-id", "123456789012",
                "--owner", "test@example.com",
            ])
            # Should still succeed (graceful handling)
            assert result.exit_code == 0 or "terraform not found" in result.output.lower() or "skipping" in result.output.lower()

    def test_onboard_terraform_plan_fails(self, tmp_path, monkeypatch):
        """Should exit with error when terraform plan raises TerraformError."""
        monkeypatch.chdir(tmp_path)
        from datameshy.lib.terraform_runner import TerraformError
        with patch("datameshy.lib.terraform_runner.plan", side_effect=TerraformError("plan failed")):
            result = _invoke([
                "domain", "onboard",
                "--name", "sales",
                "--account-id", "123456789012",
                "--owner", "test@example.com",
            ])
            assert result.exit_code != 0

    def test_onboard_terraform_plan_and_apply_success(self, tmp_path, monkeypatch):
        """Should run terraform plan, then apply when confirmed."""
        monkeypatch.chdir(tmp_path)
        with patch("datameshy.lib.terraform_runner.plan", return_value="Plan: 3 to add, 0 to change, 0 to destroy."), \
             patch("datameshy.lib.terraform_runner.apply", return_value=True), \
             patch("typer.confirm", return_value=True):
            result = _invoke([
                "domain", "onboard",
                "--name", "sales",
                "--account-id", "123456789012",
                "--owner", "test@example.com",
            ])
            assert result.exit_code == 0
            assert "onboarded" in result.output.lower()

    def test_onboard_terraform_plan_user_cancels_apply(self, tmp_path, monkeypatch):
        """Should cancel when user declines apply confirmation."""
        monkeypatch.chdir(tmp_path)
        with patch("datameshy.lib.terraform_runner.plan", return_value="Plan: 3 to add"), \
             patch("typer.confirm", return_value=False):
            result = _invoke([
                "domain", "onboard",
                "--name", "sales",
                "--account-id", "123456789012",
                "--owner", "test@example.com",
            ])
            assert result.exit_code == 0
            assert "cancelled" in result.output.lower()

    @mock_aws
    def test_onboard_emits_event(self, tmp_path, monkeypatch):
        """Should emit DomainOnboarded event when event_bus_arn is provided."""
        monkeypatch.chdir(tmp_path)
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        events = session.client("events")
        events.create_event_bus(Name="mesh-events")
        bus_arn = "arn:aws:events:us-east-1:123456789012:event-bus/mesh-events"

        with _setup_mock_session(session), \
             patch("datameshy.lib.terraform_runner.plan", return_value=""), \
             patch("datameshy.lib.aws_client.put_mesh_event", return_value="evt-123") as mock_emit:
            result = _invoke([
                "domain", "onboard",
                "--name", "sales",
                "--account-id", "123456789012",
                "--owner", "test@example.com",
                "--event-bus-arn", bus_arn,
            ])
            assert result.exit_code == 0
            mock_emit.assert_called_once()
            assert "event emitted" in result.output.lower()

    def test_onboard_event_failure_graceful(self, tmp_path, monkeypatch):
        """Should warn but not fail if event emission fails."""
        monkeypatch.chdir(tmp_path)
        with patch("datameshy.lib.terraform_runner.plan", return_value=""), \
             patch("datameshy.lib.aws_client.put_mesh_event", side_effect=Exception("EventBridge down")):
            result = _invoke([
                "domain", "onboard",
                "--name", "sales",
                "--account-id", "123456789012",
                "--owner", "test@example.com",
                "--event-bus-arn", "arn:fake",
            ])
            assert result.exit_code == 0
            assert "warning" in result.output.lower()

    @mock_aws
    def test_list_error_handling(self):
        """Should handle DynamoDB errors gracefully."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        # Don't create the table -- this will cause an error
        mock_session = MagicMock()
        mock_session.resource.return_value.Table.return_value.scan.side_effect = Exception("DynamoDB error")

        from datameshy.lib import aws_client
        with patch.object(aws_client, "get_session", return_value=mock_session):
            result = _invoke(["domain", "list"])
            assert result.exit_code != 0

    @mock_aws
    def test_status_error_handling(self):
        """Should handle DynamoDB errors in status gracefully."""
        mock_session = MagicMock()
        mock_session.resource.return_value.Table.return_value.get_item.side_effect = Exception("DB error")

        from datameshy.lib import aws_client
        with patch.object(aws_client, "get_session", return_value=mock_session):
            result = _invoke(["domain", "status", "--name", "sales"])
            assert result.exit_code != 0
