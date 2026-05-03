"""Tests for CLI product import command — Issue #15."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from typer.testing import CliRunner

from datameshy.cli import app

runner = CliRunner()

SAMPLE_PRODUCT_YAML = textwrap.dedent("""\
    product:
      name: revenue_daily
      domain: sales
      description: "Daily revenue aggregated by region"
      owner: sales-data@company.com

    schema_version: 1

    schema:
      format: iceberg
      columns:
        - name: date
          type: date
          pii: false
          nullable: false
        - name: region
          type: string
          pii: false
          nullable: false
        - name: revenue
          type: decimal(14,2)
          pii: false
          nullable: false

    quality:
      rules:
        - name: date_complete
          rule: "IsComplete 'date'"

    sla:
      refresh_frequency: daily

    classification: internal
""")

INVALID_PRODUCT_YAML = textwrap.dedent("""\
    product:
      name: bad_product
    # Missing required fields
""")


def _invoke(*args, **kwargs):
    return runner.invoke(app, *args, **kwargs)


def _setup_mock_session(session):
    from datameshy.lib import aws_client
    return patch.object(aws_client, "get_session", return_value=session)


def _make_products_table(session):
    dynamodb = session.resource("dynamodb")
    table = dynamodb.create_table(
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
    return table


@pytest.fixture
def spec_file(tmp_path):
    p = tmp_path / "product.yaml"
    p.write_text(SAMPLE_PRODUCT_YAML, encoding="utf-8")
    return str(p)


@pytest.fixture
def invalid_spec_file(tmp_path):
    p = tmp_path / "product.yaml"
    p.write_text(INVALID_PRODUCT_YAML, encoding="utf-8")
    return str(p)


class _FakeEntityNotFound(Exception):
    """Stand-in for botocore EntityNotFoundException."""


MOCK_ACCOUNT_ID = "123456789012"
VALID_LOCATION = f"s3://sales-gold-{MOCK_ACCOUNT_ID}/revenue_daily"
INVALID_LOCATION = "s3://other-bucket-999999999999/revenue_daily"


def _make_glue_mock(iceberg=True, exists=True, location=VALID_LOCATION):
    """Return a mock Glue client that returns an Iceberg table."""
    mock_glue = MagicMock()
    # Wire exceptions so isinstance checks work
    mock_glue.exceptions.EntityNotFoundException = _FakeEntityNotFound
    if not exists:
        mock_glue.get_table.side_effect = _FakeEntityNotFound("Table not found")
    else:
        mock_glue.get_table.return_value = {
            "Table": {
                "Name": "revenue_daily",
                "DatabaseName": "sales_domain",
                "Parameters": {"table_type": "ICEBERG" if iceberg else "HIVE"},
                "StorageDescriptor": {
                    "Location": location,
                    "Columns": [],
                },
            }
        }
    return mock_glue


class TestProductImport:
    """Tests for 'datameshy product import' command."""

    @mock_aws
    def test_import_registers_product_as_active(self, spec_file):
        """Successful import writes ACTIVE entry with import_source=glue."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        products_table = _make_products_table(session)

        mock_glue = _make_glue_mock()
        real_client = session.client

        def patched_client(service, **kwargs):
            if service == "glue":
                return mock_glue
            if service == "sts":
                mock_sts = MagicMock()
                mock_sts.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID}
                return mock_sts
            return real_client(service, **kwargs)

        with _setup_mock_session(session), \
             patch.object(session, "client", side_effect=patched_client):
            result = _invoke([
                "product", "import",
                "--spec", spec_file,
                "--glue-database", "sales_domain",
                "--glue-table", "revenue_daily",
            ])

        assert result.exit_code == 0, result.output
        item = products_table.get_item(Key={"product_id": "sales#revenue_daily"}).get("Item", {})
        assert item["status"] == "ACTIVE"
        assert item["import_source"] == "glue"

    @mock_aws
    def test_import_validates_spec_before_proceeding(self, invalid_spec_file):
        """Invalid spec fails before any Glue calls."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_products_table(session)

        mock_glue = _make_glue_mock()
        with _setup_mock_session(session):
            result = _invoke([
                "product", "import",
                "--spec", invalid_spec_file,
                "--glue-database", "sales_domain",
                "--glue-table", "revenue_daily",
            ])

        assert result.exit_code != 0
        mock_glue.get_table.assert_not_called()

    @mock_aws
    def test_import_fails_if_glue_table_not_found(self, spec_file):
        """Clear error if Glue table doesn't exist."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_products_table(session)

        mock_glue = _make_glue_mock(exists=False)
        real_client = session.client

        def patched_client(service, **kwargs):
            if service == "glue":
                return mock_glue
            return real_client(service, **kwargs)

        with _setup_mock_session(session), \
             patch.object(session, "client", side_effect=patched_client):
            result = _invoke([
                "product", "import",
                "--spec", spec_file,
                "--glue-database", "sales_domain",
                "--glue-table", "nonexistent",
            ])

        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "error" in result.output.lower()

    @mock_aws
    def test_import_fails_if_table_not_iceberg(self, spec_file):
        """Clear error if Glue table is not Iceberg format."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_products_table(session)

        mock_glue = _make_glue_mock(iceberg=False)
        real_client = session.client

        def patched_client(service, **kwargs):
            if service == "glue":
                return mock_glue
            if service == "sts":
                mock_sts = MagicMock()
                mock_sts.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID}
                return mock_sts
            return real_client(service, **kwargs)

        with _setup_mock_session(session), \
             patch.object(session, "client", side_effect=patched_client):
            result = _invoke([
                "product", "import",
                "--spec", spec_file,
                "--glue-database", "sales_domain",
                "--glue-table", "revenue_daily",
            ])

        assert result.exit_code != 0
        assert "iceberg" in result.output.lower()

    @mock_aws
    def test_import_duplicate_guard(self, spec_file):
        """Returns clear error if product already registered."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        products_table = _make_products_table(session)
        products_table.put_item(Item={
            "product_id": "sales#revenue_daily",
            "domain": "sales",
            "product_name": "revenue_daily",
            "status": "ACTIVE",
        })

        with _setup_mock_session(session):
            result = _invoke([
                "product", "import",
                "--spec", spec_file,
                "--glue-database", "sales_domain",
                "--glue-table", "revenue_daily",
            ])

        assert result.exit_code != 0
        assert "already" in result.output.lower() or "duplicate" in result.output.lower()

    @mock_aws
    def test_import_emits_product_created_event(self, spec_file):
        """ProductCreated event emitted after successful import."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_products_table(session)

        mock_glue = _make_glue_mock()
        real_client = session.client

        def patched_client(service, **kwargs):
            if service == "glue":
                return mock_glue
            if service == "sts":
                mock_sts = MagicMock()
                mock_sts.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID}
                return mock_sts
            return real_client(service, **kwargs)

        with _setup_mock_session(session), \
             patch.object(session, "client", side_effect=patched_client), \
             patch("datameshy.lib.aws_client.put_mesh_event", return_value="evt-import") as mock_emit:
            result = _invoke([
                "product", "import",
                "--spec", spec_file,
                "--glue-database", "sales_domain",
                "--glue-table", "revenue_daily",
                "--event-bus-arn", "arn:fake:bus",
            ])

        assert result.exit_code == 0, result.output
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args
        assert call_kwargs[1]["event_type"] == "ProductCreated"
        payload = call_kwargs[1]["payload"]
        assert payload.get("import_source") == "glue"

    @mock_aws
    def test_import_catalog_entry_discoverable(self, spec_file):
        """Imported product entry has all expected catalog fields."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        products_table = _make_products_table(session)

        mock_glue = _make_glue_mock()
        real_client = session.client

        def patched_client(service, **kwargs):
            if service == "glue":
                return mock_glue
            if service == "sts":
                mock_sts = MagicMock()
                mock_sts.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID}
                return mock_sts
            return real_client(service, **kwargs)

        with _setup_mock_session(session), \
             patch.object(session, "client", side_effect=patched_client):
            result = _invoke([
                "product", "import",
                "--spec", spec_file,
                "--glue-database", "sales_domain",
                "--glue-table", "revenue_daily",
            ])

        assert result.exit_code == 0, result.output
        item = products_table.get_item(Key={"product_id": "sales#revenue_daily"}).get("Item", {})
        assert item["domain"] == "sales"
        assert item["product_name"] == "revenue_daily"
        assert item["glue_database"] == "sales_domain"
        assert item["glue_table"] == "revenue_daily"
        assert "imported_at" in item
        assert item["status"] == "ACTIVE"
        assert item["import_source"] == "glue"

    @mock_aws
    def test_import_rejects_wrong_s3_prefix(self, spec_file):
        """import rejects table whose S3 location does not match the domain's gold bucket."""
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")
        _make_products_table(session)

        # Table in a different/wrong bucket
        mock_glue = _make_glue_mock(location=INVALID_LOCATION)
        real_client = session.client

        def patched_client(service, **kwargs):
            if service == "glue":
                return mock_glue
            if service == "sts":
                mock_sts = MagicMock()
                mock_sts.get_caller_identity.return_value = {"Account": MOCK_ACCOUNT_ID}
                return mock_sts
            return real_client(service, **kwargs)

        with _setup_mock_session(session), \
             patch.object(session, "client", side_effect=patched_client):
            result = _invoke([
                "product", "import",
                "--spec", spec_file,
                "--glue-database", "sales_domain",
                "--glue-table", "revenue_daily",
            ])

        assert result.exit_code != 0
        assert "gold bucket" in result.output.lower() or "location" in result.output.lower()
