"""
iceberg_rollback.py — Glue 4.0 PySpark Iceberg rollback job template.

Purpose: Restore a gold-layer Iceberg table to a prior snapshot using
         CALL system.rollback_to_snapshot.

Medallion layer: Gold (Data Product)

Required job arguments:
  --domain       : domain name (e.g. "sales")
  --product_name : product name (e.g. "customer_orders")
  --gold_db      : Glue catalog database for the gold layer (e.g. "sales_gold")
  --table_name   : target Iceberg table name (e.g. "customer_orders")
  --snapshot_id  : Iceberg snapshot ID to roll back to (integer string)

Optional job arguments:
  --products_table_name        : DynamoDB table name for mesh-products (default: mesh-products)
  --pipeline_locks_table_name  : DynamoDB table name for pipeline locks (default: mesh-pipeline-locks)
  --central_event_bus_arn      : EventBridge ARN to emit ProductRefreshed event (optional)

Runtime: Glue 4.0, Iceberg connector 3.3.0+, PySpark 3.3

Usage note:
  This job is started by `datameshy product rollback --to-snapshot <id> --glue-job-name <job>`.
  Pipeline lock is acquired/released by the CLI; the Glue job performs only the Iceberg CALL.

Integration note:
  The Iceberg rollback CALL syntax is:
    CALL glue_catalog.system.rollback_to_snapshot('<database>', '<table>', <snapshot_id>)

  To list snapshots (run as an Athena query or interactive session):
    SELECT snapshot_id, committed_at, operation, summary
    FROM glue_catalog.<gold_db>.<table_name>.snapshots
    ORDER BY committed_at DESC;
"""

import sys
import json
import logging
import time

from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext

# ── Bootstrap ─────────────────────────────────────────────────────────────────

REQUIRED_ARGS = [
    "JOB_NAME",
    "domain",
    "product_name",
    "gold_db",
    "table_name",
    "snapshot_id",
]

OPTIONAL_ARGS = {
    "products_table_name": "mesh-products",
    "pipeline_locks_table_name": "mesh-pipeline-locks",
    "central_event_bus_arn": "",
}

args = getResolvedOptions(sys.argv, REQUIRED_ARGS)
for key, default in OPTIONAL_ARGS.items():
    if f"--{key}" in sys.argv:
        idx = sys.argv.index(f"--{key}")
        args[key] = sys.argv[idx + 1]
    else:
        args[key] = default

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

domain = args["domain"]
product_name = args["product_name"]
gold_db = args["gold_db"]
table_name = args["table_name"]
snapshot_id = int(args["snapshot_id"])
product_id = f"{domain}#{product_name}"

sc = SparkContext()
glue_context = GlueContext(sc)
spark = glue_context.spark_session
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

logger.info(
    "Iceberg rollback starting",
    extra={
        "product_id": product_id,
        "gold_db": gold_db,
        "table_name": table_name,
        "snapshot_id": snapshot_id,
    },
)

# ── Step 1: Validate snapshot exists ─────────────────────────────────────────

logger.info("Listing available snapshots before rollback...")
try:
    snapshots_df = spark.sql(
        f"SELECT snapshot_id, committed_at, operation "
        f"FROM glue_catalog.{gold_db}.{table_name}.snapshots "
        f"ORDER BY committed_at DESC"
    )
    snapshot_ids = [row.snapshot_id for row in snapshots_df.collect()]
    logger.info(f"Available snapshots: {snapshot_ids}")

    if snapshot_id not in snapshot_ids:
        raise ValueError(
            f"Snapshot ID {snapshot_id} not found in available snapshots: {snapshot_ids}"
        )
except Exception as exc:
    logger.error(f"Failed to validate snapshot: {exc}")
    raise

# ── Step 2: Execute the Iceberg rollback ─────────────────────────────────────

logger.info(f"Rolling back '{gold_db}.{table_name}' to snapshot {snapshot_id}...")
try:
    spark.sql(
        f"CALL glue_catalog.system.rollback_to_snapshot("
        f"'{gold_db}.{table_name}', {snapshot_id}"
        f")"
    )
    logger.info(f"Rollback to snapshot {snapshot_id} completed successfully.")
except Exception as exc:
    logger.error(f"Iceberg rollback failed: {exc}")
    raise

# ── Step 3: Count rows in rolled-back snapshot ───────────────────────────────

try:
    row_count = spark.sql(
        f"SELECT COUNT(*) as cnt FROM glue_catalog.{gold_db}.{table_name}"
    ).first()["cnt"]
    logger.info(f"Row count after rollback: {row_count}")
except Exception as exc:
    logger.warning(f"Could not count rows after rollback: {exc}")
    row_count = None

# ── Step 4: Update mesh-products catalog metadata ────────────────────────────

import boto3

now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

try:
    dynamodb = boto3.resource("dynamodb")
    products_table = dynamodb.Table(args["products_table_name"])
    update_expr = "SET last_refreshed = :now, last_rollback_snapshot = :snap"
    expr_values = {":now": now_iso, ":snap": snapshot_id}

    if row_count is not None:
        update_expr += ", row_count = :rc"
        expr_values[":rc"] = row_count

    products_table.update_item(
        Key={"product_id": product_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )
    logger.info(f"mesh-products updated: last_refreshed={now_iso}, snapshot={snapshot_id}")
except Exception as exc:
    logger.warning(f"Could not update mesh-products: {exc}")

# ── Step 5: Emit ProductRefreshed event (optional) ───────────────────────────

if args.get("central_event_bus_arn"):
    try:
        events = boto3.client("events")
        events.put_events(
            Entries=[{
                "Source": "datameshy.glue",
                "DetailType": "ProductRefreshed",
                "Detail": json.dumps({
                    "product_id": product_id,
                    "domain": domain,
                    "product_name": product_name,
                    "rollback_snapshot_id": snapshot_id,
                    "row_count": row_count,
                    "refreshed_at": now_iso,
                }),
                "EventBusName": args["central_event_bus_arn"],
            }]
        )
        logger.info("ProductRefreshed event emitted.")
    except Exception as exc:
        logger.warning(f"Could not emit ProductRefreshed event: {exc}")

# ── Finalize ──────────────────────────────────────────────────────────────────

job.commit()
logger.info(
    "Iceberg rollback job complete",
    extra={
        "product_id": product_id,
        "snapshot_id": snapshot_id,
        "row_count": row_count,
    },
)
