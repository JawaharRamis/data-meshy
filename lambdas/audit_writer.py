"""
audit_writer.py — Appends ALL events to the mesh-audit-log.

Responsibilities:
- Append-only: uses PutItem only, never UpdateItem or DeleteItem
- Record includes: event_id, event_type, domain, timestamp, source_account, full event payload
- Triggered by ALL events on the central EventBridge bus
- Uses MeshAuditWriterRole for DynamoDB access (PutItem only policy)

Triggered by: EventBridge rule matching ALL datameshy events
Runtime: Python 3.12
"""

import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Table name defaults (matching governance module outputs)
DEFAULT_AUDIT_TABLE = "mesh-audit-log"


def _get_table(env_key: str, default: str) -> str:
    return os.environ.get(env_key, default)


def handler(event, context):
    """
    Lambda handler that appends every event to the audit log.

    Args:
        event: EventBridge event envelope
        context: Lambda context (unused)

    Returns:
        dict with status
    """
    audit_table = _get_table("MESH_AUDIT_TABLE", DEFAULT_AUDIT_TABLE)
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(audit_table)

    detail = event.get("detail", {})
    event_type = event.get("detail-type", "Unknown")
    event_id = detail.get("event_id", "unknown")
    domain = detail.get("domain", "unknown")
    timestamp = detail.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    source_account = event.get("account", "unknown")

    # Build the audit record
    audit_record = {
        "event_id": event_id,
        "timestamp": timestamp,
        "event_type": event_type,
        "domain": domain,
        "source_account": source_account,
        "event_payload": json.dumps(event, default=str),
    }

    try:
        # PutItem only — never UpdateItem (append-only policy enforced by IAM)
        table.put_item(Item=audit_record)
        logger.info("Audit record appended", extra={
            "event_id": event_id, "event_type": event_type, "domain": domain
        })
    except ClientError as exc:
        logger.error("Failed to write audit record", extra={
            "event_id": event_id, "error": str(exc)
        })
        raise

    return {"status": "success", "event_id": event_id}
