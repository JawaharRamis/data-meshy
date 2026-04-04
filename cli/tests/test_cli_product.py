"""Tests for CLI product commands -- create, refresh, status."""

from __future__ import annotations

import json
import os
from decimal import Decimal
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
# product create
# ---------------------------------------------------------------------------


class TestProductCreate:
    """Tests for 'datameshy product create' command."""

    def test_create_dry_run_valid_spec(self, sample_spec_file):
        """Dry run should validate spec and exit successfully."""
        result = _invoke([
            "product", "create",
            "--spec", sample_spec_file,
            "--dry-run",
        ])
        assert result.exit_code == 0
        assert "valid" in result.output.lower() or "customer_orders" in result.output

    def test_create_dry_run_invalid_spec(self, invalid_spec_file):
        """Dry run with invalid spec should fail with validation error."""
        result = _invoke([
            "product", "create",
            "--spec", invalid_spec_file,
            "--dry-run",
        ])
        assert result.exit_code != 0

    def test_create_nonexistent_spec_file(self):
        """Non-existent spec file should fail."""
        result = _invoke([
            "product", "create",
            "--spec", "/nonexistent/product.yaml",
            "--dry-run",
        ])
        assert result.exit_code != 0

    @mock_aws
    def test_create_product_already_exists(self, sample_spec_file):
        """Should error if product already exists in DynamoDB."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        products_table = dynamodb.Table("mesh-products")
        products_table.put_item(Item={
            "product_id": "sales#customer_orders",
            "domain": "sales",
            "product_name": "customer_orders",
            "status": "ACTIVE",
        })

        with _setup_mock_session(session):
            result = _invoke([
                "product", "create",
                "--spec", sample_spec_file,
            ])
            assert result.exit_code != 0 or "already exists" in result.output.lower()

    @mock_aws
    def test_create_full_flow_no_terraform(self, sample_spec_file, tmp_path, monkeypatch):
        """Full create flow when terraform env dir doesn't exist."""
        monkeypatch.chdir(tmp_path)
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        with _setup_mock_session(session):
            result = _invoke([
                "product", "create",
                "--spec", sample_spec_file,
            ])
            # Should succeed even without terraform dir
            assert result.exit_code == 0

    @mock_aws
    def test_create_with_terraform_env(self, sample_spec_file, tmp_path, monkeypatch):
        """Full create flow with terraform environment present."""
        monkeypatch.chdir(tmp_path)

        # Create terraform env dir
        env_dir = tmp_path / "infra" / "environments" / "domain-sales"
        env_dir.mkdir(parents=True)

        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        with _setup_mock_session(session), \
             patch("datameshy.lib.terraform_runner.plan", return_value="Plan: 1 to add"), \
             patch("datameshy.lib.terraform_runner.apply", return_value=True), \
             patch("typer.confirm", return_value=True):
            result = _invoke([
                "product", "create",
                "--spec", sample_spec_file,
            ])
            assert result.exit_code == 0

    @mock_aws
    def test_create_with_event_bus(self, sample_spec_file, tmp_path, monkeypatch):
        """Create with event bus ARN should attempt to emit event."""
        monkeypatch.chdir(tmp_path)
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        with _setup_mock_session(session), \
             patch("datameshy.lib.aws_client.put_mesh_event", return_value="evt-123") as mock_emit:
            result = _invoke([
                "product", "create",
                "--spec", sample_spec_file,
                "--event-bus-arn", "arn:fake:bus",
            ])
            assert result.exit_code == 0
            mock_emit.assert_called_once()


# ---------------------------------------------------------------------------
# product refresh
# ---------------------------------------------------------------------------


class TestProductRefresh:
    """Tests for 'datameshy product refresh' command."""

    @mock_aws
    def test_refresh_product_not_found(self):
        """Should error if product not found in DynamoDB."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        dynamodb.create_table(
            TableName="mesh-pipeline-locks",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        with _setup_mock_session(session):
            result = _invoke([
                "product", "refresh",
                "--domain", "sales",
                "--name", "nonexistent",
            ])
            assert result.exit_code != 0

    @mock_aws
    def test_refresh_product_locked(self):
        """Should show message when pipeline is already running."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        locks_table = dynamodb.create_table(
            TableName="mesh-pipeline-locks",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        products_table = dynamodb.Table("mesh-products")
        products_table.put_item(Item={
            "product_id": "sales#orders",
            "domain": "sales",
            "product_name": "orders",
            "status": "ACTIVE",
            "state_machine_arn": "arn:some-sm",
        })

        locks_table.put_item(Item={
            "product_id": "sales#orders",
            "locked": True,
            "execution_arn": "arn:some-exec",
        })

        with _setup_mock_session(session):
            result = _invoke([
                "product", "refresh",
                "--domain", "sales",
                "--name", "orders",
            ])
            assert "already running" in result.output.lower() or "locked" in result.output.lower()

    @mock_aws
    def test_refresh_full_success(self):
        """Full refresh flow with successful pipeline execution."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        dynamodb.create_table(
            TableName="mesh-pipeline-locks",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        products_table = dynamodb.Table("mesh-products")
        products_table.put_item(Item={
            "product_id": "sales#orders",
            "domain": "sales",
            "product_name": "orders",
            "status": "ACTIVE",
            "state_machine_arn": "arn:some-sm",
        })

        with _setup_mock_session(session), \
             patch("datameshy.lib.aws_client.start_pipeline", return_value="arn:exec:123"), \
             patch("datameshy.lib.aws_client.wait_pipeline", return_value="SUCCEEDED"):
            result = _invoke([
                "product", "refresh",
                "--domain", "sales",
                "--name", "orders",
            ])
            assert result.exit_code == 0
            assert "succeeded" in result.output.lower()

    @mock_aws
    def test_refresh_pipeline_failure(self):
        """Should error when pipeline execution fails."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        dynamodb.create_table(
            TableName="mesh-pipeline-locks",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        products_table = dynamodb.Table("mesh-products")
        products_table.put_item(Item={
            "product_id": "sales#orders",
            "domain": "sales",
            "product_name": "orders",
            "status": "ACTIVE",
            "state_machine_arn": "arn:some-sm",
        })

        from datameshy.lib.aws_client import PipelineError
        with _setup_mock_session(session), \
             patch("datameshy.lib.aws_client.start_pipeline", return_value="arn:exec:123"), \
             patch("datameshy.lib.aws_client.wait_pipeline",
                   side_effect=PipelineError("Failed", status="FAILED", cause="Timeout")):
            result = _invoke([
                "product", "refresh",
                "--domain", "sales",
                "--name", "orders",
            ])
            assert result.exit_code != 0

    @mock_aws
    def test_refresh_missing_state_machine_arn(self):
        """Should error when product record has no state machine ARN."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        dynamodb.create_table(
            TableName="mesh-pipeline-locks",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        products_table = dynamodb.Table("mesh-products")
        products_table.put_item(Item={
            "product_id": "sales#orders",
            "domain": "sales",
            "product_name": "orders",
            "status": "ACTIVE",
            # No state_machine_arn
        })

        with _setup_mock_session(session):
            result = _invoke([
                "product", "refresh",
                "--domain", "sales",
                "--name", "orders",
            ])
            assert result.exit_code != 0

    @mock_aws
    def test_refresh_invalid_product_status(self):
        """Should error when product is not in a refreshable state."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        dynamodb.create_table(
            TableName="mesh-pipeline-locks",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        products_table = dynamodb.Table("mesh-products")
        products_table.put_item(Item={
            "product_id": "sales#orders",
            "domain": "sales",
            "product_name": "orders",
            "status": "DELETING",
        })

        with _setup_mock_session(session):
            result = _invoke([
                "product", "refresh",
                "--domain", "sales",
                "--name", "orders",
            ])
            assert result.exit_code != 0


# ---------------------------------------------------------------------------
# product status
# ---------------------------------------------------------------------------


class TestProductStatus:
    """Tests for 'datameshy product status' command."""

    @mock_aws
    def test_status_existing_product(self):
        """Should display product details for an existing product."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        dynamodb.create_table(
            TableName="mesh-subscriptions",
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

        products_table = dynamodb.Table("mesh-products")
        products_table.put_item(Item={
            "product_id": "sales#customer_orders",
            "domain": "sales",
            "product_name": "customer_orders",
            "owner": "sales@company.com",
            "status": "ACTIVE",
            "schema_version": 1,
            "last_refresh_at": "2026-04-01T00:00:00Z",
            "last_quality_score": Decimal("98.5"),
            "last_rows_written": 10000,
        })

        with _setup_mock_session(session):
            result = _invoke([
                "product", "status",
                "--domain", "sales",
                "--name", "customer_orders",
            ])
            assert result.exit_code == 0
            assert "customer_orders" in result.output
            assert "ACTIVE" in result.output

    @mock_aws
    def test_status_nonexistent_product(self):
        """Should error for a product that doesn't exist."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        dynamodb = session.resource("dynamodb")

        dynamodb.create_table(
            TableName="mesh-products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        dynamodb.create_table(
            TableName="mesh-subscriptions",
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

        with _setup_mock_session(session):
            result = _invoke([
                "product", "status",
                "--domain", "sales",
                "--name", "nonexistent",
            ])
            assert result.exit_code != 0
