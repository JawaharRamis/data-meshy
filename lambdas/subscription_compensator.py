"""
subscription_compensator.py — Saga compensation for failed subscription provisioning.

compensate(event, context) — Reverses provisioning steps in order:
  1. Revoke Glue resource link (if created)
  2. Revoke KMS grant (if created)
  3. Revoke LF permissions (if granted)
  4. Mark DynamoDB status = FAILED with compensation_reason
  5. Send SNS alerts to requester and product owner

Called by Step Functions Catch blocks (on Steps B or C failure) and directly
by handle_revoke in subscription_request.py.

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
SNS_TOPIC_ARN = os.environ.get("OWNER_NOTIFY_SNS_ARN", "")
CENTRAL_ACCOUNT_ID = os.environ.get("CENTRAL_ACCOUNT_ID", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_dynamodb():
    return boto3.resource("dynamodb")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _assume_role(role_arn: str, session_name: str) -> dict:
    sts = boto3.client("sts")
    resp = sts.assume_role(RoleArn=role_arn, RoleSessionName=session_name)
    return resp["Credentials"]


def _client_from_creds(service: str, creds: dict, region: str = None) -> Any:
    return boto3.client(
        service,
        region_name=region or AWS_REGION,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


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


def _get_product(product_id: str) -> dict | None:
    ddb = _get_dynamodb()
    table = ddb.Table(PRODUCTS_TABLE)
    resp = table.get_item(Key={"domain#product_name": product_id})
    return resp.get("Item")


def _notify(owner_email: str, requester_account: str, subject: str, message: str) -> None:
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not configured; skipping notification")
        return
    sns = boto3.client("sns")
    try:
        sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject, Message=message)
    except ClientError:
        logger.exception("Failed to send SNS notification")


# ── Revocation sub-steps ───────────────────────────────────────────────────────

def _revoke_lf_grant(product: dict, consumer_account_id: str, subscription_id: str) -> None:
    """Revoke Lake Formation permissions. Idempotent."""
    lf_grantor_role_arn = os.environ.get("LF_GRANTOR_ROLE_ARN", LF_GRANTOR_ROLE_ARN)
    if not lf_grantor_role_arn:
        logger.warning("LF_GRANTOR_ROLE_ARN not set; skipping LF revoke")
        return

    producer_account_id = product.get("account_id", CENTRAL_ACCOUNT_ID)
    domain = product.get("domain", "")
    product_name = product.get("product_name", "")
    gold_database = f"{domain}_gold"

    creds = _assume_role(lf_grantor_role_arn, f"lf-revoke-{subscription_id[:8]}")
    lf_client = _client_from_creds("lakeformation", creds)

    try:
        lf_client.batch_revoke_permissions(
            CatalogId=producer_account_id,
            Entries=[
                {
                    "Id": f"sub-{subscription_id}-lf-revoke",
                    "Principal": {"DataLakePrincipalIdentifier": consumer_account_id},
                    "Resource": {
                        "TableWithColumns": {
                            "CatalogId": producer_account_id,
                            "DatabaseName": gold_database,
                            "Name": product_name,
                            "ColumnWildcard": {},
                        }
                    },
                    "Permissions": ["SELECT"],
                    "PermissionsWithGrantOption": [],
                }
            ],
        )
        logger.info("LF grant revoked", extra={"subscription_id": subscription_id})
    except ClientError as exc:
        # InvalidInputException means permission did not exist — safe to ignore
        if exc.response["Error"]["Code"] not in ("InvalidInputException",):
            logger.exception("Failed to revoke LF grant", extra={"subscription_id": subscription_id})
            raise


def _revoke_kms_grant(product: dict, subscription: dict, subscription_id: str) -> None:
    """Revoke the KMS grant using the stored grant_id."""
    kms_grantor_role_arn = os.environ.get("KMS_GRANTOR_ROLE_ARN", KMS_GRANTOR_ROLE_ARN)
    if not kms_grantor_role_arn:
        logger.warning("KMS_GRANTOR_ROLE_ARN not set; skipping KMS revoke")
        return

    grant_id = subscription.get("kms_grant_id")
    kms_key_arn = product.get("gold_kms_key_arn", "")

    if not grant_id or not kms_key_arn:
        logger.info("No KMS grant to revoke", extra={"subscription_id": subscription_id})
        return

    creds = _assume_role(kms_grantor_role_arn, f"kms-revoke-{subscription_id[:8]}")
    kms_client = _client_from_creds("kms", creds)

    try:
        kms_client.retire_grant(KeyId=kms_key_arn, GrantId=grant_id)
        logger.info("KMS grant retired", extra={
            "subscription_id": subscription_id, "grant_id": grant_id
        })
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("NotFoundException",):
            logger.exception("Failed to revoke KMS grant", extra={"subscription_id": subscription_id})
            raise


def _delete_resource_link(product: dict, consumer_account_id: str, subscription_id: str) -> None:
    """Delete the Glue resource link from the consumer catalog. Idempotent."""
    domain = product.get("domain", "")
    product_name = product.get("product_name", "")
    consumer_db = f"{domain}_mesh_consumer"
    link_name = f"{domain}_{product_name}_link"

    consumer_role_arn = (
        f"arn:aws:iam::{consumer_account_id}:role/MeshGlueConsumerRole"
    )

    try:
        creds = _assume_role(consumer_role_arn, f"rl-delete-{subscription_id[:8]}")
        glue_client = _client_from_creds("glue", creds)
        glue_client.delete_table(DatabaseName=consumer_db, Name=link_name)
        logger.info("Resource link deleted", extra={"link_name": link_name})
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("EntityNotFoundException",):
            logger.exception("Failed to delete resource link", extra={
                "subscription_id": subscription_id
            })
            raise


# ── Main compensate handler ────────────────────────────────────────────────────

def compensate(event: dict, context: Any) -> dict:
    """
    Compensation entry point — called by Step Functions Catch and handle_revoke.

    event keys:
      subscription_id     — string
      product_id          — string
      consumer_account_id — string
      compensation_steps  — list of steps to reverse, e.g. ["lf_grant", "kms_grant"]
      reason              — string describing why compensation is running
    """
    subscription_id: str = event["subscription_id"]
    product_id: str = event["product_id"]
    consumer_account_id: str = event["consumer_account_id"]
    steps_to_reverse: list = event.get("compensation_steps", ["lf_grant", "kms_grant", "resource_link"])
    reason: str = event.get("reason", "provisioning_failure")

    logger.info("Compensation started", extra={
        "subscription_id": subscription_id,
        "steps": steps_to_reverse,
        "reason": reason,
    })

    product = _get_product(product_id)
    subscription = _get_subscription(product_id, consumer_account_id)

    errors = []

    # Reverse in order: resource_link → kms_grant → lf_grant
    if "resource_link" in steps_to_reverse and product:
        try:
            _delete_resource_link(product, consumer_account_id, subscription_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"resource_link: {exc}")

    if "kms_grant" in steps_to_reverse and product and subscription:
        try:
            _revoke_kms_grant(product, subscription, subscription_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"kms_grant: {exc}")

    if "lf_grant" in steps_to_reverse and product:
        try:
            _revoke_lf_grant(product, consumer_account_id, subscription_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"lf_grant: {exc}")

    # Update DynamoDB status = FAILED (only if not being called from revoke path)
    if reason != "revoke":
        ddb = _get_dynamodb()
        subs_table = ddb.Table(SUBSCRIPTIONS_TABLE)
        compensation_reason = reason
        if errors:
            compensation_reason += f"; partial errors: {'; '.join(errors)}"

        subs_table.update_item(
            Key={
                "product_id": product_id,
                "subscriber_account_id": consumer_account_id,
            },
            UpdateExpression=(
                "SET #s = :failed, compensation_reason = :reason, updated_at = :now"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":failed": "FAILED",
                ":reason": compensation_reason,
                ":now": _now_iso(),
            },
        )

    # Send SNS alerts
    if product and subscription:
        owner_email = product.get("owner", "")
        _notify(
            owner_email,
            consumer_account_id,
            subject=f"[DataMeshy] Subscription provisioning failed: {product_id}",
            message=(
                f"Subscription ID: {subscription_id}\n"
                f"Product: {product_id}\n"
                f"Consumer: {consumer_account_id}\n"
                f"Reason: {reason}\n"
                f"Steps attempted to reverse: {steps_to_reverse}\n"
                f"Errors during compensation: {errors or 'none'}"
            ),
        )

    logger.info("Compensation complete", extra={
        "subscription_id": subscription_id,
        "errors": errors,
    })

    return {
        "subscription_id": subscription_id,
        "compensation_status": "FAILED" if reason != "revoke" else "REVOKED",
        "errors": errors,
    }


# ── Lambda entry point ─────────────────────────────────────────────────────────

def handler(event: dict, context: Any) -> dict:
    """Lambda entry point when invoked directly by Step Functions Catch."""
    return compensate(event, context)
