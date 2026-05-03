"""Tests for CLI product deprecate command — Issue #13."""

from __future__ import annotations

import os
from decimal import Decimal
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


def _make_tables(session, put_product=True, status="ACTIVE"):
    dynamodb = session.resource("dynamodb")

    products_table = dynamodb.create_table(
        TableName="mesh-products",
        KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "product_id", "AttributeType": "S"},
            {"AttributeName": "domain", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": "domain-index",
                "KeySchema": [{"AttributeName": "domain", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )

    dynamodb.create_table(
        TableName="mesh-subscriptions",
        KeySchema=[{"AttributeName": "subscription_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "subscription_id", "AttributeType": "S"},
            {"AttributeName": "product_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": "product-index",
                "KeySchema": [{"AttributeName": "product_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )

    if put_product:
        products_table.put_item(Item={
            "product_id": "sales#customer_orders",
            "domain": "sales",
            "product_name": "customer_orders",
            "owner": "sales@company.com",
            "status": status,
        })

    return products_table


class TestProductDeprecate:
    """Tests for 'datameshy product deprecate' command."""

    @mock_aws
    def test_deprecate_active_product_marks_deprecated(self):
        """deprecate on ACTIVE product sets status=DEPRECATED and sunset_date."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        products_table = _make_tables(session)

        with _setup_mock_session(session), \
             patch("datameshy.lib.aws_client.put_mesh_event", return_value="evt-123"), \
             patch("boto3.client") as mock_boto_client:
            # Mock scheduler client
            mock_sched = MagicMock()
            mock_sched.create_schedule.return_value = {"ScheduleArn": "arn:aws:scheduler:us-east-1:123:schedule/retire-sales-customer_orders"}
            mock_boto_client.return_value = mock_sched

            result = _invoke([
                "product", "deprecate",
                "sales/customer_orders",
                "--sunset-days", "90",
                "--event-bus-arn", "arn:fake:bus",
                "--retirement-lambda-arn", "arn:fake:lambda",
            ])

        assert result.exit_code == 0, result.output
        assert "deprecated" in result.output.lower() or "DEPRECATED" in result.output

        item = products_table.get_item(Key={"product_id": "sales#customer_orders"}).get("Item", {})
        assert item["status"] == "DEPRECATED"
        assert "sunset_date" in item
        assert item.get("sunset_days") == 90

    @mock_aws
    def test_deprecate_already_deprecated_rejected(self):
        """deprecate on DEPRECATED product returns a clear error."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session, status="DEPRECATED")

        with _setup_mock_session(session):
            result = _invoke([
                "product", "deprecate",
                "sales/customer_orders",
                "--sunset-days", "30",
            ])

        assert result.exit_code != 0
        assert "already" in result.output.lower() or "deprecated" in result.output.lower()

    @mock_aws
    def test_deprecate_retired_product_rejected(self):
        """deprecate on RETIRED product returns a clear error."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session, status="RETIRED")

        with _setup_mock_session(session):
            result = _invoke([
                "product", "deprecate",
                "sales/customer_orders",
                "--sunset-days", "30",
            ])

        assert result.exit_code != 0
        assert "retired" in result.output.lower() or "cannot" in result.output.lower()

    @mock_aws
    def test_deprecate_nonexistent_product(self):
        """deprecate on non-existent product returns clear error."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session, put_product=False)

        with _setup_mock_session(session):
            result = _invoke([
                "product", "deprecate",
                "sales/nonexistent",
                "--sunset-days", "30",
            ])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    @mock_aws
    def test_deprecate_emits_product_deprecated_event(self):
        """ProductDeprecated event emitted with breaking=true and sunset_date."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session)

        with _setup_mock_session(session), \
             patch("datameshy.lib.aws_client.put_mesh_event", return_value="evt-456") as mock_emit, \
             patch("boto3.client") as mock_boto_client:
            mock_boto_client.return_value = MagicMock()

            result = _invoke([
                "product", "deprecate",
                "sales/customer_orders",
                "--sunset-days", "60",
                "--event-bus-arn", "arn:fake:bus",
                "--retirement-lambda-arn", "arn:fake:lambda",
            ])

        assert result.exit_code == 0, result.output
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args
        assert call_kwargs[1]["event_type"] == "ProductDeprecated"
        payload = call_kwargs[1]["payload"]
        assert payload.get("breaking") is True
        assert "sunset_date" in payload

    @mock_aws
    def test_deprecate_creates_scheduler_rule(self):
        """EventBridge Scheduler rule created targeting retirement Lambda at sunset_date."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_tables(session)

        mock_sched = MagicMock()
        mock_sched.create_schedule.return_value = {"ScheduleArn": "arn:aws:scheduler:us-east-1:123:schedule/retire-test"}

        with _setup_mock_session(session), \
             patch("datameshy.lib.aws_client.put_mesh_event", return_value="evt-789"), \
             patch("boto3.client", return_value=mock_sched):
            result = _invoke([
                "product", "deprecate",
                "sales/customer_orders",
                "--sunset-days", "45",
                "--event-bus-arn", "arn:fake:bus",
                "--retirement-lambda-arn", "arn:fake:lambda",
                "--scheduler-role-arn", "arn:fake:role",
            ])

        assert result.exit_code == 0, result.output
        mock_sched.create_schedule.assert_called_once()

    @mock_aws
    def test_deprecate_status_shows_deprecated(self):
        """product status shows status=deprecated and sunset_date after deprecation."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        # _make_tables creates mesh-subscriptions, so don't duplicate
        products_table = _make_tables(session)

        # Manually mark deprecated so we can test status display
        products_table.update_item(
            Key={"product_id": "sales#customer_orders"},
            UpdateExpression="SET #s = :dep, sunset_date = :sd",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":dep": "DEPRECATED", ":sd": "2026-08-01"},
        )

        with _setup_mock_session(session):
            result = _invoke([
                "product", "status",
                "--domain", "sales",
                "--name", "customer_orders",
            ])

        assert result.exit_code == 0
        assert "DEPRECATED" in result.output
        assert "2026-08-01" in result.output
