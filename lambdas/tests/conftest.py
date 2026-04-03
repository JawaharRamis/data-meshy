"""
Shared test fixtures for Data Meshy lambda handler tests.

Uses moto to mock AWS services (DynamoDB, EventBridge, SNS, SQS).
"""

import os
from decimal import Decimal
import pytest
import boto3
from moto import mock_aws


# Table names (must match central governance module outputs)
DOMAINS_TABLE = "mesh-domains"
PRODUCTS_TABLE = "mesh-products"
QUALITY_TABLE = "mesh-quality-scores"
AUDIT_TABLE = "mesh-audit-log"
DEDUP_TABLE = "mesh-event-dedup"
LOCKS_TABLE = "mesh-pipeline-locks"

# Test constants
TEST_ACCOUNT_ID = "111111111111"
TEST_DOMAIN = "sales"
TEST_PRODUCT = "customer_orders"
TEST_PRODUCT_ID = "sales#customer_orders"


@pytest.fixture
def aws_mock():
    """Activate moto mock_aws for all services used by the handlers."""
    with mock_aws():
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        os.environ["AWS_REGION"] = "us-east-1"
        yield


@pytest.fixture
def dynamodb(aws_mock):
    """Return a DynamoDB client."""
    return boto3.client("dynamodb", region_name="us-east-1")


@pytest.fixture
def ddb_resource(aws_mock):
    """Return a DynamoDB resource."""
    return boto3.resource("dynamodb", region_name="us-east-1")


def _create_table(client, table_name, pk, sk=None):
    """Helper to create a DynamoDB table."""
    key_schema = [{"AttributeName": pk, "KeyType": "HASH"}]
    attr_defs = [{"AttributeName": pk, "AttributeType": "S"}]
    if sk:
        key_schema.append({"AttributeName": sk, "KeyType": "RANGE"})
        attr_defs.append({"AttributeName": sk, "AttributeType": "S"})

    params = {
        "TableName": table_name,
        "KeySchema": key_schema,
        "AttributeDefinitions": attr_defs,
        "BillingMode": "PAY_PER_REQUEST",
    }
    client.create_table(**params)

    # Enable TTL on dedup table
    if "dedup" in table_name:
        client.update_time_to_live(
            TableName=table_name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )


@pytest.fixture
def setup_tables(dynamodb, ddb_resource):
    """Create all mesh DynamoDB tables for testing."""
    _create_table(dynamodb, DOMAINS_TABLE, "domain_name")
    _create_table(dynamodb, PRODUCTS_TABLE, "domain#product_name")
    _create_table(dynamodb, QUALITY_TABLE, "product_id", sk="timestamp")
    _create_table(dynamodb, AUDIT_TABLE, "event_id", sk="timestamp")
    _create_table(dynamodb, DEDUP_TABLE, "event_id")
    _create_table(dynamodb, LOCKS_TABLE, "product_id", sk="lock_key")
    return ddb_resource


@pytest.fixture
def register_domain(setup_tables, ddb_resource):
    """Register a test domain in mesh-domains table."""
    table = ddb_resource.Table(DOMAINS_TABLE)
    table.put_item(
        Item={
            "domain_name": TEST_DOMAIN,
            "account_id": TEST_ACCOUNT_ID,
            "owner": "sales-team@example.com",
            "status": "ACTIVE",
        }
    )


@pytest.fixture
def register_product(setup_tables, register_domain, ddb_resource):
    """Register a test product in mesh-products table."""
    table = ddb_resource.Table(PRODUCTS_TABLE)
    table.put_item(
        Item={
            "domain#product_name": TEST_PRODUCT_ID,
            "domain": TEST_DOMAIN,
            "product_name": TEST_PRODUCT,
            "status": "ACTIVE",
            "owner": "sales-team@example.com",
            "classification": "internal",
            "sla": {
                "refresh_frequency": "daily",
                "freshness_target": "24 hours",
            },
            "last_refreshed_at": "2026-04-01T10:00:00Z",
            "quality_score": Decimal("98.5"),
            "schema_version": 1,
        }
    )


@pytest.fixture
def make_event():
    """Factory fixture to create EventBridge event envelopes."""
    def _make_event(event_type, detail, account=TEST_ACCOUNT_ID, source="datameshy"):
        return {
            "version": "0",
            "id": "test-eb-id-001",
            "source": source,
            "account": account,
            "time": "2026-04-03T10:00:00Z",
            "region": "us-east-1",
            "resources": [],
            "detail-type": event_type,
            "detail": detail,
        }
    return _make_event
