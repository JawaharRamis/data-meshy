"""Tests for CLI product rollback command — Issue #14."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from datameshy.cli import app

runner = CliRunner()


def _invoke(*args, **kwargs):
    return runner.invoke(app, *args, **kwargs)


def _setup_mock_session(session):
    from datameshy.lib import aws_client
    return patch.object(aws_client, "get_session", return_value=session)


def _make_tables(session, product_status="ACTIVE"):
    dynamodb = session.resource("dynamodb")

    products_table = dynamodb.create_table(
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

    locks_table = dynamodb.create_table(
        TableName="mesh-pipeline-locks",
        KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    products_table.put_item(Item={
        "product_id": "sales#customer_orders",
        "domain": "sales",
        "product_name": "customer_orders",
        "owner": "sales@company.com",
        "status": product_status,
        "glue_catalog_db_gold": "sales_gold",
    })

    return products_table, locks_table


class TestProductRollback:
    """Tests for 'datameshy product rollback' command."""

    @mock_aws
    def test_rollback_requires_flag(self):
        """rollback without --to-snapshot or --list-snapshots exits with error."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session)

        with _setup_mock_session(session):
            result = _invoke(["product", "rollback", "sales/customer_orders"])

        assert result.exit_code != 0

    @mock_aws
    def test_list_snapshots_shows_athena_query(self):
        """--list-snapshots prints the Athena query hint."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session)

        with _setup_mock_session(session):
            result = _invoke([
                "product", "rollback",
                "sales/customer_orders",
                "--list-snapshots",
            ])

        assert result.exit_code == 0
        assert "snapshot" in result.output.lower()
        # Should show helpful SQL / guidance
        assert "SELECT" in result.output or "snapshots" in result.output

    @mock_aws
    def test_rollback_to_snapshot_acquires_and_releases_lock(self):
        """--to-snapshot acquires pipeline lock, runs rollback, releases lock."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        products_table, locks_table = _make_tables(session)

        with _setup_mock_session(session):
            result = _invoke([
                "product", "rollback",
                "sales/customer_orders",
                "--to-snapshot", "8765309",
            ])

        assert result.exit_code == 0, result.output
        # Lock must be released after rollback
        lock = locks_table.get_item(Key={"product_id": "sales#customer_orders"}).get("Item")
        assert lock is None

    @mock_aws
    def test_rollback_blocked_when_pipeline_locked(self):
        """rollback blocked if pipeline lock is active."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        products_table, locks_table = _make_tables(session)

        locks_table.put_item(Item={"product_id": "sales#customer_orders", "locked": True})

        with _setup_mock_session(session):
            result = _invoke([
                "product", "rollback",
                "sales/customer_orders",
                "--to-snapshot", "1234",
            ])

        assert result.exit_code != 0
        assert "lock" in result.output.lower() or "running" in result.output.lower()

    @mock_aws
    def test_rollback_updates_catalog_metadata(self):
        """After rollback, last_refreshed updated in mesh-products."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        products_table, locks_table = _make_tables(session)

        with _setup_mock_session(session):
            result = _invoke([
                "product", "rollback",
                "sales/customer_orders",
                "--to-snapshot", "9876543",
            ])

        assert result.exit_code == 0, result.output
        item = products_table.get_item(Key={"product_id": "sales#customer_orders"}).get("Item", {})
        assert "last_refreshed" in item
        assert item.get("last_rollback_snapshot") == 9876543

    @mock_aws
    def test_rollback_blocked_for_deprecated_product(self):
        """rollback blocked if product status is DEPRECATED."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session, product_status="DEPRECATED")

        with _setup_mock_session(session):
            result = _invoke([
                "product", "rollback",
                "sales/customer_orders",
                "--to-snapshot", "111",
            ])

        assert result.exit_code != 0
        assert "deprecated" in result.output.lower() or "blocked" in result.output.lower()

    @mock_aws
    def test_rollback_blocked_for_retired_product(self):
        """rollback blocked if product status is RETIRED."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session, product_status="RETIRED")

        with _setup_mock_session(session):
            result = _invoke([
                "product", "rollback",
                "sales/customer_orders",
                "--to-snapshot", "222",
            ])

        assert result.exit_code != 0
        assert "retired" in result.output.lower() or "blocked" in result.output.lower()

    @mock_aws
    def test_rollback_emits_product_refreshed_event(self):
        """ProductRefreshed event emitted after rollback."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session)

        with _setup_mock_session(session), \
             patch("datameshy.lib.aws_client.put_mesh_event", return_value="evt-rollback") as mock_emit:
            result = _invoke([
                "product", "rollback",
                "sales/customer_orders",
                "--to-snapshot", "555",
                "--event-bus-arn", "arn:fake:bus",
            ])

        assert result.exit_code == 0, result.output
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args
        assert call_kwargs[1]["event_type"] == "ProductRefreshed"

    @mock_aws
    def test_rollback_with_glue_job(self):
        """--glue-job-name triggers a Glue job run."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session)

        mock_glue = MagicMock()
        mock_glue.start_job_run.return_value = {"JobRunId": "jr-abc123"}

        # Patch only the glue client call in product.py, keeping other session calls intact
        real_client = session.client

        def patched_client(service, **kwargs):
            if service == "glue":
                return mock_glue
            return real_client(service, **kwargs)

        with _setup_mock_session(session), \
             patch.object(session, "client", side_effect=patched_client):
            result = _invoke([
                "product", "rollback",
                "sales/customer_orders",
                "--to-snapshot", "777",
                "--glue-job-name", "mesh-iceberg-rollback",
            ])

        assert result.exit_code == 0, result.output
        mock_glue.start_job_run.assert_called_once()

    @mock_aws
    def test_rollback_product_not_found(self):
        """rollback on non-existent product returns clear error."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session)

        with _setup_mock_session(session):
            result = _invoke([
                "product", "rollback",
                "sales/nonexistent",
                "--to-snapshot", "999",
            ])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()
