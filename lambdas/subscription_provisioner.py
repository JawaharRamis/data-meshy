"""
subscription_provisioner.py — Saga Steps A / B / C for subscription provisioning.

  step_a_lf_grant(event, context)       — Grant Lake Formation permissions to consumer
  step_b_kms_grant(event, context)      — Grant KMS key access to consumer GlueConsumerRole
  step_c_resource_link(event, context)  — Create Glue resource link in consumer catalog

Each handler is invoked by the subscription_saga Step Functions state machine.
All steps update DynamoDB provisioning_steps on success.
Steps B and C raise on failure — Step Functions Catch blocks invoke the compensator.

Runtime: Python 3.12
"""

import json
import logging
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Environment variables ──────────────────────────────────────────────────────
SUBSCRIPTIONS_TABLE = os.environ.get("MESH_SUBSCRIPTIONS_TABLE", "mesh-subscriptions")
PRODUCTS_TABLE = os.environ.get("MESH_PRODUCTS_TABLE", "mesh-products")
LF_GRANTOR_ROLE_ARN = os.environ.get("LF_GRANTOR_ROLE_ARN", "")
KMS_GRANTOR_ROLE_ARN = os.environ.get("KMS_GRANTOR_ROLE_ARN", "")
CENTRAL_ACCOUNT_ID = os.environ.get("CENTRAL_ACCOUNT_ID", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
EVENT_BUS_NAME = os.environ.get("CENTRAL_EVENT_BUS_NAME", "datameshy-central")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_dynamodb():
    return boto3.resource("dynamodb")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _assume_role(role_arn: str, session_name: str) -> dict:
    """Assume an IAM role and return temporary credentials."""
    sts = boto3.client("sts")
    response = sts.assume_role(RoleArn=role_arn, RoleSessionName=session_name)
    return response["Credentials"]


def _client_from_creds(service: str, creds: dict, region: str = None) -> Any:
    """Build a boto3 client using assumed-role credentials."""
    return boto3.client(
        service,
        region_name=region or AWS_REGION,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def _get_product(product_id: str) -> dict | None:
    ddb = _get_dynamodb()
    table = ddb.Table(PRODUCTS_TABLE)
    resp = table.get_item(Key={"domain#product_name": product_id})
    return resp.get("Item")


def _get_subscription(product_id: str, consumer_account_id: str) -> dict | None:
    ddb = _get_dynamodb()
    table = ddb.Table(SUBSCRIPTIONS_TABLE)
    resp = table.get_item(
        Key={
            "product_id": product_id,
            "subscriber_account_id": consumer_account_id,
        }
    )
    return resp.get("Item")


def _update_provisioning_step(
    product_id: str,
    consumer_account_id: str,
    step: str,
    value: str,
    set_status: str | None = None,
) -> None:
    """Update provisioning_steps.<step> in DynamoDB."""
    ddb = _get_dynamodb()
    table = ddb.Table(SUBSCRIPTIONS_TABLE)
    update_expr = "SET provisioning_steps.#step = :val, updated_at = :now"
    expr_names = {"#step": step}
    expr_values = {":val": value, ":now": _now_iso()}

    if set_status:
        update_expr += ", #s = :status"
        expr_names["#s"] = "status"
        expr_values[":status"] = set_status

    table.update_item(
        Key={
            "product_id": product_id,
            "subscriber_account_id": consumer_account_id,
        },
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )


def _emit_event(detail_type: str, detail: dict) -> None:
    eb = boto3.client("events")
    eb.put_events(
        Entries=[
            {
                "Source": "datameshy.central",
                "DetailType": detail_type,
                "Detail": json.dumps(detail),
                "EventBusName": EVENT_BUS_NAME,
            }
        ]
    )


def _get_pii_columns(product: dict) -> set:
    """Return set of column names marked pii=true in the product schema."""
    schema = product.get("schema", {})
    columns = schema.get("columns", [])
    return {c["name"] for c in columns if c.get("pii", False)}


def _get_gold_table_arn(product: dict, account_id: str, region: str) -> str:
    """Build the Glue table ARN for the gold layer."""
    domain = product.get("domain", "")
    product_name = product.get("product_name", "")
    database = f"{domain}_gold"
    return f"arn:aws:glue:{region}:{account_id}:table/{database}/{product_name}"


# ── Step A: LF Grant ───────────────────────────────────────────────────────────

def step_a_lf_grant(event: dict, context: Any) -> dict:
    """
    Grant Lake Formation SELECT permissions to the consumer account.

    Uses ColumnWildcard exclusion to filter PII columns not in the approved list.
    Idempotent: duplicate BatchGrantPermissions calls are no-ops.
    """
    subscription_id: str = event["subscription_id"]
    product_id: str = event["product_id"]
    consumer_account_id: str = event["consumer_account_id"]
    requested_columns: list = event.get("requested_columns", [])

    product = _get_product(product_id)
    if product is None:
        raise ValueError(f"Product not found: {product_id}")

    # Determine columns to exclude (PII columns not in approved list)
    pii_cols = _get_pii_columns(product)
    excluded_columns = list(pii_cols - set(requested_columns))

    # Producer account info
    producer_account_id = product.get("account_id", CENTRAL_ACCOUNT_ID)
    domain = product.get("domain", "")
    product_name = product.get("product_name", "")
    gold_database = f"{domain}_gold"

    logger.info("Step A: granting LF permissions", extra={
        "subscription_id": subscription_id,
        "product_id": product_id,
        "excluded_columns": excluded_columns,
    })

    # Assume LF grantor role
    creds = _assume_role(LF_GRANTOR_ROLE_ARN, f"lf-grant-{subscription_id[:8]}")
    lf_client = _client_from_creds("lakeformation", creds)

    # Build column filter
    if excluded_columns:
        column_wildcard = {"ExcludedColumnNames": excluded_columns}
    else:
        column_wildcard = {}

    try:
        lf_client.batch_grant_permissions(
            CatalogId=producer_account_id,
            Entries=[
                {
                    "Id": f"sub-{subscription_id}-lf",
                    "Principal": {"DataLakePrincipalIdentifier": consumer_account_id},
                    "Resource": {
                        "TableWithColumns": {
                            "CatalogId": producer_account_id,
                            "DatabaseName": gold_database,
                            "Name": product_name,
                            "ColumnWildcard": column_wildcard,
                        }
                    },
                    "Permissions": ["SELECT"],
                    "PermissionsWithGrantOption": [],
                }
            ],
        )
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        # ConcurrentModificationException — propagate for SFN retry
        if error_code == "ConcurrentModificationException":
            raise
        # AlreadyExistsException means grant is in place — idempotent
        if error_code not in ("AlreadyExistsException",):
            raise

    # Update DynamoDB
    _update_provisioning_step(product_id, consumer_account_id, "lf_grant", "DONE")
    logger.info("Step A: LF grant done", extra={"subscription_id": subscription_id})

    return {**event, "lf_grant": "DONE"}


# ── Step B: KMS Grant ──────────────────────────────────────────────────────────

def step_b_kms_grant(event: dict, context: Any) -> dict:
    """
    Grant KMS Decrypt + DescribeKey to the consumer account's GlueConsumerRole.

    On failure raises exception → Step Functions invokes compensation.
    """
    subscription_id: str = event["subscription_id"]
    product_id: str = event["product_id"]
    consumer_account_id: str = event["consumer_account_id"]

    product = _get_product(product_id)
    if product is None:
        raise ValueError(f"Product not found: {product_id}")

    kms_key_arn: str = product.get("gold_kms_key_arn", "")
    if not kms_key_arn:
        raise ValueError(f"Product {product_id} has no gold_kms_key_arn")

    # Consumer's GlueConsumerRole ARN
    consumer_glue_role_arn = (
        f"arn:aws:iam::{consumer_account_id}:role/MeshGlueConsumerRole"
    )

    logger.info("Step B: creating KMS grant", extra={
        "subscription_id": subscription_id,
        "key_arn": kms_key_arn,
        "grantee": consumer_glue_role_arn,
    })

    creds = _assume_role(KMS_GRANTOR_ROLE_ARN, f"kms-grant-{subscription_id[:8]}")
    kms_client = _client_from_creds("kms", creds)

    try:
        kms_response = kms_client.create_grant(
            KeyId=kms_key_arn,
            GranteePrincipal=consumer_glue_role_arn,
            Operations=["Decrypt", "DescribeKey"],
            Name=f"mesh-sub-{subscription_id}",
        )
        grant_id = kms_response["GrantId"]
    except ClientError:
        logger.exception("Step B: KMS grant failed", extra={"subscription_id": subscription_id})
        raise

    # Update DynamoDB (store grant_id for future revocation)
    ddb = _get_dynamodb()
    subs_table = ddb.Table(SUBSCRIPTIONS_TABLE)
    subs_table.update_item(
        Key={
            "product_id": product_id,
            "subscriber_account_id": consumer_account_id,
        },
        UpdateExpression=(
            "SET provisioning_steps.#step = :val, "
            "kms_grant_id = :gid, "
            "updated_at = :now"
        ),
        ExpressionAttributeNames={"#step": "kms_grant"},
        ExpressionAttributeValues={
            ":val": "DONE",
            ":gid": grant_id,
            ":now": _now_iso(),
        },
    )

    logger.info("Step B: KMS grant done", extra={
        "subscription_id": subscription_id, "grant_id": grant_id
    })

    return {**event, "kms_grant": "DONE", "kms_grant_id": grant_id}


# ── Step C: Resource Link ──────────────────────────────────────────────────────

def step_c_resource_link(event: dict, context: Any) -> dict:
    """
    Create a Glue resource link in the consumer account's catalog.

    Pre-check: if the resource link already exists, skip creation (idempotent guard).
    On success: marks subscription ACTIVE and emits SubscriptionProvisioned.
    """
    subscription_id: str = event["subscription_id"]
    product_id: str = event["product_id"]
    consumer_account_id: str = event["consumer_account_id"]
    requested_columns: list = event.get("requested_columns", [])

    product = _get_product(product_id)
    if product is None:
        raise ValueError(f"Product not found: {product_id}")

    domain = product.get("domain", "")
    product_name = product.get("product_name", "")
    producer_account_id = product.get("account_id", CENTRAL_ACCOUNT_ID)

    gold_database = f"{domain}_gold"
    consumer_db = f"{domain}_mesh_consumer"
    link_name = f"{domain}_{product_name}_link"

    logger.info("Step C: creating resource link", extra={
        "subscription_id": subscription_id,
        "consumer_db": consumer_db,
        "link_name": link_name,
    })

    # Assume cross-account role in the consumer account
    consumer_role_arn = (
        f"arn:aws:iam::{consumer_account_id}:role/MeshGlueConsumerRole"
    )
    creds = _assume_role(consumer_role_arn, f"rl-{subscription_id[:8]}")
    glue_client = _client_from_creds("glue", creds)

    # Pre-check: does the resource link already exist?
    try:
        glue_client.get_table(DatabaseName=consumer_db, Name=link_name)
        logger.info("Step C: resource link already exists, skipping", extra={
            "link_name": link_name
        })
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "EntityNotFoundException":
            # Table does not exist — proceed with creation
            try:
                glue_client.create_table(
                    DatabaseName=consumer_db,
                    TableInput={
                        "Name": link_name,
                        "TargetTable": {
                            "CatalogId": producer_account_id,
                            "DatabaseName": gold_database,
                            "Name": product_name,
                        },
                    },
                )
            except ClientError:
                logger.exception("Step C: failed to create resource link", extra={
                    "subscription_id": subscription_id
                })
                raise
        else:
            raise

    # Update DynamoDB: provisioning_steps.resource_link = DONE, status = ACTIVE
    _update_provisioning_step(
        product_id,
        consumer_account_id,
        "resource_link",
        "DONE",
        set_status="ACTIVE",
    )

    # Emit SubscriptionProvisioned event
    _emit_event(
        "SubscriptionProvisioned",
        {
            "subscription_id": subscription_id,
            "product_id": product_id,
            "consumer_account_id": consumer_account_id,
            "granted_columns": requested_columns,
            "provisioned": True,
        },
    )

    logger.info("Step C: resource link done, subscription ACTIVE", extra={
        "subscription_id": subscription_id
    })

    return {**event, "resource_link": "DONE", "status": "ACTIVE"}
