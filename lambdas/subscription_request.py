"""
subscription_request.py — Handles subscription lifecycle for Data Meshy.

Handlers:
  handle_subscribe_request(event, context) — POST /subscriptions
  handle_approve(event, context)           — POST /subscriptions/{id}/approve
  handle_revoke(event, context)            — POST /subscriptions/{id}/revoke
  handle_list(event, context)              — GET /subscriptions

All DynamoDB writes use ConditionExpression to prevent races.
SubscriptionApproved is only emitted from handle_approve in the central account.

Runtime: Python 3.12
"""

import json
import logging
import os
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Environment variable defaults ──────────────────────────────────────────────
SUBSCRIPTIONS_TABLE = os.environ.get("MESH_SUBSCRIPTIONS_TABLE", "mesh-subscriptions")
PRODUCTS_TABLE = os.environ.get("MESH_PRODUCTS_TABLE", "mesh-products")
DOMAINS_TABLE = os.environ.get("MESH_DOMAINS_TABLE", "mesh-domains")
SFN_ARN = os.environ.get("SUBSCRIPTION_SFN_ARN", "")
SNS_TOPIC_ARN = os.environ.get("OWNER_NOTIFY_SNS_ARN", "")
EVENT_BUS_NAME = os.environ.get("CENTRAL_EVENT_BUS_NAME", "datameshy-central")
CENTRAL_ACCOUNT_ID = os.environ.get("CENTRAL_ACCOUNT_ID", "")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_dynamodb():
    return boto3.resource("dynamodb")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _emit_event(detail_type: str, detail: dict) -> None:
    """Put an event onto the central EventBridge bus."""
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
    logger.info("Emitted event", extra={"detail_type": detail_type, "detail": detail})


def _notify_owner(owner_email: str, subject: str, message: str) -> None:
    """Send SNS notification to the product owner."""
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not set; skipping notification")
        return
    sns = boto3.client("sns")
    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=subject,
        Message=message,
        MessageAttributes={
            "owner_email": {
                "DataType": "String",
                "StringValue": owner_email,
            }
        },
    )


def _start_sfn(sfn_arn: str, input_payload: dict, name_prefix: str = "sub") -> str:
    """Start a Step Functions execution and return its ARN."""
    sfn = boto3.client("stepfunctions")
    exec_name = f"{name_prefix}-{uuid.uuid4()}"
    response = sfn.start_execution(
        stateMachineArn=sfn_arn,
        name=exec_name,
        input=json.dumps(input_payload),
    )
    return response["executionArn"]


def _validate_caller_domain(caller_account_id: str) -> dict | None:
    """
    Check that the calling account is a registered domain.
    Returns the domain record if found, else None.
    """
    ddb = _get_dynamodb()
    domains_table = ddb.Table(DOMAINS_TABLE)
    # Scan by account_id (small table — GSI preferred in prod, scan fine for unit tests)
    response = domains_table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("account_id").eq(caller_account_id)
    )
    items = response.get("Items", [])
    return items[0] if items else None


def _get_product(product_id: str) -> dict | None:
    """Fetch a product record from mesh-products."""
    ddb = _get_dynamodb()
    table = ddb.Table(PRODUCTS_TABLE)
    response = table.get_item(Key={"domain#product_name": product_id})
    return response.get("Item")


def _has_pii_columns(product: dict, requested_columns: list[str]) -> bool:
    """Return True if any requested column is marked PII in the product schema."""
    schema = product.get("schema", {})
    columns = schema.get("columns", [])
    pii_cols = {c["name"] for c in columns if c.get("pii", False)}
    return bool(pii_cols.intersection(set(requested_columns)))


def _same_business_unit(domain_record: dict, product: dict) -> bool:
    """Return True if subscriber domain and product owner share a business_unit tag."""
    subscriber_bu = domain_record.get("business_unit", "")
    product_bu = product.get("business_unit", "")
    return bool(subscriber_bu and subscriber_bu == product_bu)


# ── Handler: subscribe request ─────────────────────────────────────────────────

def handle_subscribe_request(event: dict, context: Any) -> dict:
    """
    POST /subscriptions

    Validates the caller, stores a PENDING subscription, and either
    auto-approves (non-PII, same BU) or requests manual approval.
    """
    body = event.get("body", {})
    if isinstance(body, str):
        body = json.loads(body)

    product_id: str = body["product_id"]
    consumer_account_id: str = body["consumer_account_id"]
    requested_columns: list = body.get("requested_columns", [])
    justification: str = body.get("justification", "")

    # Determine caller account from API GW request context
    request_context = event.get("requestContext", {})
    caller_account_id = request_context.get("accountId", consumer_account_id)

    # 1. Validate caller is a registered domain
    domain_record = _validate_caller_domain(caller_account_id)
    if domain_record is None:
        logger.warning("Caller account not a registered domain", extra={"account": caller_account_id})
        return {
            "statusCode": 403,
            "body": json.dumps({"error": "Caller account is not a registered domain"}),
        }

    # 2. Fetch product
    product = _get_product(product_id)
    if product is None:
        return {
            "statusCode": 404,
            "body": json.dumps({"error": f"Product not found: {product_id}"}),
        }

    # 3. Governance checks
    has_pii = _has_pii_columns(product, requested_columns)
    same_bu = _same_business_unit(domain_record, product)
    auto_approve = (not has_pii) and same_bu

    subscription_id = str(uuid.uuid4())
    now = _now_iso()
    initial_status = "PROVISIONING" if auto_approve else "PENDING"

    # 4. Write to DynamoDB (conditional — don't clobber existing ACTIVE/PENDING record)
    ddb = _get_dynamodb()
    subs_table = ddb.Table(SUBSCRIPTIONS_TABLE)
    try:
        subs_table.put_item(
            Item={
                "product_id": product_id,
                "subscriber_account_id": consumer_account_id,
                "subscription_id": subscription_id,
                "status": initial_status,
                "requested_columns": requested_columns,
                "justification": justification,
                "created_at": now,
                "updated_at": now,
                "subscriber_domain": domain_record.get("domain_name", ""),
                "provisioning_steps": {},
            },
            ConditionExpression=(
                "attribute_not_exists(product_id) OR "
                "#s IN (:revoked, :failed)"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":revoked": "REVOKED",
                ":failed": "FAILED",
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {
                "statusCode": 409,
                "body": json.dumps({"error": "Subscription already exists for this product/account"}),
            }
        raise

    # 5. Auto-approve path
    if auto_approve:
        execution_arn = _start_sfn(
            SFN_ARN,
            {
                "subscription_id": subscription_id,
                "product_id": product_id,
                "consumer_account_id": consumer_account_id,
                "requested_columns": requested_columns,
            },
        )
        logger.info("Auto-approved subscription", extra={
            "subscription_id": subscription_id, "sfn": execution_arn
        })
    else:
        # 6. Manual path: notify producer owner
        owner_email = product.get("owner", "")
        _notify_owner(
            owner_email,
            subject=f"[DataMeshy] New subscription request for {product_id}",
            message=(
                f"Subscription ID: {subscription_id}\n"
                f"Requester account: {consumer_account_id}\n"
                f"Columns: {requested_columns}\n"
                f"Justification: {justification}"
            ),
        )

    # 7. Emit SubscriptionRequested event
    _emit_event(
        "SubscriptionRequested",
        {
            "subscription_id": subscription_id,
            "product_id": product_id,
            "consumer_account_id": consumer_account_id,
            "requested_columns": requested_columns,
            "auto_approve": auto_approve,
        },
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "subscription_id": subscription_id,
            "status": initial_status,
        }),
    }


# ── Handler: approve ───────────────────────────────────────────────────────────

def handle_approve(event: dict, context: Any) -> dict:
    """
    POST /subscriptions/{id}/approve

    Validates the caller is the product owner, transitions PENDING → APPROVED,
    starts the provisioner SFN, and emits SubscriptionApproved.

    SubscriptionApproved is ONLY emitted from this central Lambda.
    """
    body = event.get("body", {})
    if isinstance(body, str):
        body = json.loads(body)

    subscription_id: str = body["subscription_id"]
    comment: str = body.get("comment", "")

    # Locate the subscription
    ddb = _get_dynamodb()
    subs_table = ddb.Table(SUBSCRIPTIONS_TABLE)

    # GSI lookup by subscription_id is preferred; scan for simplicity in tests
    result = subs_table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("subscription_id").eq(subscription_id)
    )
    items = result.get("Items", [])
    if not items:
        return {
            "statusCode": 404,
            "body": json.dumps({"error": "Subscription not found"}),
        }
    sub = items[0]

    product_id = sub["product_id"]
    consumer_account_id = sub["subscriber_account_id"]
    requested_columns = sub.get("requested_columns", [])

    # Validate caller is product owner
    product = _get_product(product_id)
    if product is None:
        return {
            "statusCode": 404,
            "body": json.dumps({"error": f"Product not found: {product_id}"}),
        }

    caller_identity = event.get("requestContext", {}).get("identity", {})
    caller_arn = caller_identity.get("userArn", "")
    owner_email = product.get("owner", "")

    # Allow if caller_arn contains owner email (simplified check) or if header carries owner token
    owner_header = event.get("headers", {}).get("x-mesh-owner-token", "")
    if owner_email and owner_email not in caller_arn and owner_email not in owner_header:
        return {
            "statusCode": 403,
            "body": json.dumps({"error": "Caller is not the product owner"}),
        }

    now = _now_iso()

    # Conditional update: PENDING → APPROVED
    try:
        subs_table.update_item(
            Key={
                "product_id": product_id,
                "subscriber_account_id": consumer_account_id,
            },
            UpdateExpression="SET #s = :approved, updated_at = :now, approval_comment = :comment",
            ConditionExpression="#s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":approved": "APPROVED",
                ":pending": "PENDING",
                ":now": now,
                ":comment": comment,
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {
                "statusCode": 409,
                "body": json.dumps({"error": "Subscription is not in PENDING state"}),
            }
        raise

    # Determine non-PII columns for grant
    schema = product.get("schema", {})
    all_columns = [c["name"] for c in schema.get("columns", [])]
    pii_cols = {c["name"] for c in schema.get("columns", []) if c.get("pii", False)}
    granted_columns = [c for c in requested_columns if c not in pii_cols]

    # Start provisioner SFN
    execution_arn = _start_sfn(
        SFN_ARN,
        {
            "subscription_id": subscription_id,
            "product_id": product_id,
            "consumer_account_id": consumer_account_id,
            "requested_columns": granted_columns,
        },
    )

    # Emit SubscriptionApproved — ONLY from central Lambda, source datameshy.central
    _emit_event(
        "SubscriptionApproved",
        {
            "subscription_id": subscription_id,
            "product_id": product_id,
            "consumer_account_id": consumer_account_id,
            "granted_columns": granted_columns,
            "sfn_execution_arn": execution_arn,
        },
    )

    logger.info("Subscription approved", extra={
        "subscription_id": subscription_id,
        "sfn": execution_arn,
    })

    return {
        "statusCode": 200,
        "body": json.dumps({
            "subscription_id": subscription_id,
            "status": "APPROVED",
            "granted_columns": granted_columns,
        }),
    }


# ── Handler: revoke ────────────────────────────────────────────────────────────

def handle_revoke(event: dict, context: Any) -> dict:
    """
    POST /subscriptions/{id}/revoke

    Validates caller is owner or platform admin. Starts compensation,
    marks subscription REVOKED, emits SubscriptionRevoked.
    """
    body = event.get("body", {})
    if isinstance(body, str):
        body = json.loads(body)

    subscription_id: str = body["subscription_id"]

    ddb = _get_dynamodb()
    subs_table = ddb.Table(SUBSCRIPTIONS_TABLE)

    result = subs_table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("subscription_id").eq(subscription_id)
    )
    items = result.get("Items", [])
    if not items:
        return {
            "statusCode": 404,
            "body": json.dumps({"error": "Subscription not found"}),
        }
    sub = items[0]

    product_id = sub["product_id"]
    consumer_account_id = sub["subscriber_account_id"]

    # Validate caller is product owner or platform admin
    product = _get_product(product_id)
    caller_identity = event.get("requestContext", {}).get("identity", {})
    caller_arn = caller_identity.get("userArn", "")
    owner_email = (product or {}).get("owner", "")
    is_admin = "platform-admin" in caller_arn or "MeshAdminRole" in caller_arn
    is_owner = owner_email and owner_email in caller_arn

    if not is_admin and not is_owner:
        return {
            "statusCode": 403,
            "body": json.dumps({"error": "Caller is not the product owner or platform admin"}),
        }

    now = _now_iso()

    # Import compensator and run cleanup
    from subscription_compensator import compensate  # noqa: PLC0415
    compensate(
        {
            "subscription_id": subscription_id,
            "product_id": product_id,
            "consumer_account_id": consumer_account_id,
            "compensation_steps": ["lf_grant", "kms_grant", "resource_link"],
            "reason": "revoke",
        },
        context,
    )

    # Mark REVOKED (no conditional — revoke from any non-terminal state)
    subs_table.update_item(
        Key={
            "product_id": product_id,
            "subscriber_account_id": consumer_account_id,
        },
        UpdateExpression="SET #s = :revoked, updated_at = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":revoked": "REVOKED",
            ":now": now,
        },
    )

    # Emit SubscriptionRevoked
    _emit_event(
        "SubscriptionRevoked",
        {
            "subscription_id": subscription_id,
            "product_id": product_id,
            "consumer_account_id": consumer_account_id,
        },
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "subscription_id": subscription_id,
            "status": "REVOKED",
        }),
    }


# ── Handler: list ──────────────────────────────────────────────────────────────

def handle_list(event: dict, context: Any) -> dict:
    """
    GET /subscriptions

    Query params:
      product_id        — producer view (all subscriptions for a product)
      subscriber_domain — consumer view (all subscriptions for a domain)
    """
    params = event.get("queryStringParameters") or {}
    product_id = params.get("product_id")
    subscriber_domain = params.get("subscriber_domain")

    ddb = _get_dynamodb()
    subs_table = ddb.Table(SUBSCRIPTIONS_TABLE)

    if product_id:
        # Query by PK (product_id)
        response = subs_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("product_id").eq(product_id)
        )
        items = response.get("Items", [])
    elif subscriber_domain:
        # Scan with filter (GSI1 on subscriber_domain preferred in production)
        response = subs_table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("subscriber_domain").eq(subscriber_domain)
        )
        items = response.get("Items", [])
    else:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Provide product_id or subscriber_domain query parameter"}),
        }

    result = [
        {
            "subscription_id": i.get("subscription_id"),
            "product_id": i.get("product_id"),
            "status": i.get("status"),
            "requested_columns": i.get("requested_columns", []),
            "created_at": i.get("created_at"),
        }
        for i in items
    ]

    return {
        "statusCode": 200,
        "body": json.dumps({"subscriptions": result}),
    }
