"""Shared fixtures for Data Meshy CLI tests.

Provides moto-based AWS mocks and sample product.yaml content.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Sample product.yaml content (customer_orders from PRD Section 8)
# ---------------------------------------------------------------------------

SAMPLE_PRODUCT_YAML = textwrap.dedent("""\
    product:
      name: customer_orders
      domain: sales
      description: "Daily customer order transactions enriched with customer segments"
      owner: sales-data-team@company.com
      contact_channel: "#sales-data-support"

    schema_version: 1

    schema:
      format: iceberg
      columns:
        - name: order_id
          type: string
          description: "Unique order identifier"
          pii: false
          nullable: false
        - name: customer_email
          type: string
          description: "Customer email address"
          pii: true
          nullable: false
        - name: order_total
          type: decimal(10,2)
          description: "Order total in USD"
          pii: false
          nullable: false
        - name: order_date
          type: date
          description: "Date the order was placed"
          pii: false
          nullable: false
        - name: customer_segment
          type: string
          description: "Customer segment (enriched in gold layer)"
          pii: false
          nullable: true
      partition_by:
        - column: order_date
          transform: month

    quality:
      rules:
        - name: order_id_complete
          rule: "IsComplete 'order_id'"
          threshold: 1.0
        - name: order_id_unique
          rule: "IsUnique 'order_id'"
        - name: order_total_positive
          rule: "ColumnValues 'order_total' > 0"
        - name: order_date_recent
          rule: "Freshness 'order_date' <= 2 days"
      minimum_quality_score: 95

    sla:
      refresh_frequency: daily
      freshness_target: "24 hours"
      availability: "99.9%"

    tags:
      - ecommerce
      - customer-data
      - pii

    classification: confidential

    lineage:
      sources:
        - system: "orders-postgres-db"
          table: "public.orders"
        - system: "crm-api"
          endpoint: "/customers/segments"
""")

# Minimal valid spec (just enough to pass validation)
MINIMAL_PRODUCT_YAML = textwrap.dedent("""\
    product:
      name: test_product
      domain: test
      description: "A test product"
      owner: test@example.com

    schema_version: 1

    schema:
      columns:
        - name: id
          type: string

    quality:
      rules:
        - name: id_complete
          rule: "IsComplete 'id'"

    sla:
      refresh_frequency: daily
""")

# Invalid spec (missing required fields)
INVALID_PRODUCT_YAML = textwrap.dedent("""\
    product:
      name: bad_product
    # Missing domain, description, owner
    # Missing schema entirely
    # Missing quality entirely
    # Missing sla entirely
""")


@pytest.fixture
def sample_spec_file(tmp_path):
    """Write SAMPLE_PRODUCT_YAML to a temp file and return its path."""
    spec_path = tmp_path / "product.yaml"
    spec_path.write_text(SAMPLE_PRODUCT_YAML, encoding="utf-8")
    return str(spec_path)


@pytest.fixture
def minimal_spec_file(tmp_path):
    """Write MINIMAL_PRODUCT_YAML to a temp file and return its path."""
    spec_path = tmp_path / "product.yaml"
    spec_path.write_text(MINIMAL_PRODUCT_YAML, encoding="utf-8")
    return str(spec_path)


@pytest.fixture
def invalid_spec_file(tmp_path):
    """Write INVALID_PRODUCT_YAML to a temp file and return its path."""
    spec_path = tmp_path / "product.yaml"
    spec_path.write_text(INVALID_PRODUCT_YAML, encoding="utf-8")
    return str(spec_path)


@pytest.fixture
def parsed_spec():
    """Return the parsed SAMPLE_PRODUCT_YAML as a dict."""
    import yaml
    return yaml.safe_load(SAMPLE_PRODUCT_YAML)


# ---------------------------------------------------------------------------
# AWS Mock Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def aws_mock_env():
    """Set up a mocked AWS environment using moto.

    Creates:
    - STS (caller identity)
    - EventBridge event bus
    - DynamoDB tables: mesh-domains, mesh-products, mesh-subscriptions, mesh-pipeline-locks
    - Step Functions state machine
    - S3 bucket
    - IAM role for cross-account assume

    Yields a boto3 session and the mock context.
    """
    with mock_aws():
        # Set fake credentials so boto3 doesn't complain
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_SECURITY_TOKEN"] = "testing"
        os.environ["AWS_SESSION_TOKEN"] = "testing"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        session = boto3.Session(region_name="us-east-1")

        # Create EventBridge event bus
        events = session.client("events")
        events.create_event_bus(Name="mesh-event-bus")
        event_bus_arn = (
            "arn:aws:events:us-east-1:123456789012:event-bus/mesh-event-bus"
        )

        # Create DynamoDB tables
        dynamodb = session.resource("dynamodb")

        # mesh-domains
        domains_table = dynamodb.create_table(
            TableName="mesh-domains",
            KeySchema=[{"AttributeName": "domain", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "domain", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # mesh-products
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

        # mesh-subscriptions
        subs_table = dynamodb.create_table(
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

        # mesh-pipeline-locks
        locks_table = dynamodb.create_table(
            TableName="mesh-pipeline-locks",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create IAM role for Step Functions
        iam = session.client("iam")
        iam.create_role(
            RoleName="TestSFNRole",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "states.us-east-1.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }),
            Path="/",
        )
        role_arn = "arn:aws:iam::123456789012:role/TestSFNRole"

        # Create Step Functions state machine
        sfn = session.client("stepfunctions")
        sfn.create_state_machine(
            name="sales-customer_orders-pipeline",
            definition=json.dumps({
                "Comment": "Test state machine",
                "StartAt": "Pass",
                "States": {"Pass": {"Type": "Pass", "End": True}},
            }),
            roleArn=role_arn,
        )
        # moto returns state machine ARN
        sm_list = sfn.list_state_machines()
        state_machine_arn = sm_list["stateMachines"][0]["stateMachineArn"]

        # Create S3 bucket
        s3 = session.client("s3")
        s3.create_bucket(Bucket="sales-raw-123456789012")

        yield {
            "session": session,
            "event_bus_arn": event_bus_arn,
            "state_machine_arn": state_machine_arn,
            "domains_table": domains_table,
            "products_table": products_table,
            "subs_table": subs_table,
            "locks_table": locks_table,
        }

        # Cleanup env vars
        for key in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SECURITY_TOKEN",
            "AWS_SESSION_TOKEN",
            "AWS_DEFAULT_REGION",
        ):
            os.environ.pop(key, None)


@pytest.fixture
def mock_get_session(aws_mock_env):
    """Patch aws_client.get_session to return the mocked session."""
    from datameshy.lib import aws_client

    with patch.object(aws_client, "get_session", return_value=aws_mock_env["session"]):
        yield aws_mock_env
