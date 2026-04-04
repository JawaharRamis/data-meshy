"""
iceberg_maintenance.py — Glue 4.0 PySpark Iceberg maintenance job template.

Runs AFTER ReleaseLock in the state machine — maintenance does NOT block consumers.
Failures are non-fatal: log the error and emit a CloudWatch metric; do NOT raise.

Steps:
  1. OPTIMIZE (RewriteDataFiles) — compact small files to target size
  2. VACUUM (ExpireSnapshots) — expire old snapshots, keep minimum 5
  3. Delete orphan files — remove files not referenced by any snapshot

Required args (from README.md parameter interface):
  --domain                   : domain name
  --product_name             : product name
  --gold_bucket              : gold S3 bucket name
  --gold_db                  : Glue catalog DB for gold layer
  --table_name               : table name
  --target_file_size_mb      : target file size in MB after compaction (default: 128)
  --snapshot_retention_days  : expire snapshots older than N days (default: 7)

Optional args:
  --orphan_file_retention_days: delete orphan files older than N days (default: 3)
  --cloudwatch_namespace     : CloudWatch namespace (default: "DataMeshy/Maintenance")
  --min_snapshots_to_keep    : minimum snapshots to retain regardless of age (default: 5)
"""

import sys
import json
import datetime
import time

from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from pyspark.sql import functions as F
import boto3

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

REQUIRED_ARGS = [
    "JOB_NAME",
    "domain",
    "product_name",
    "gold_bucket",
    "gold_db",
    "table_name",
    "target_file_size_mb",
    "snapshot_retention_days",
]

OPTIONAL_ARGS = [
    "orphan_file_retention_days",
    "cloudwatch_namespace",
    "min_snapshots_to_keep",
]

args = getResolvedOptions(sys.argv, REQUIRED_ARGS)
for opt in OPTIONAL_ARGS:
    if f"--{opt}" in sys.argv:
        args.update(getResolvedOptions(sys.argv, [opt]))

sc = SparkContext()
glue_context = GlueContext(sc)
spark = glue_context.spark_session
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

# Iceberg catalog configuration
spark.conf.set("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
spark.conf.set("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

target_file_size_mb = int(args.get("target_file_size_mb", "128"))
snapshot_retention_days = int(args.get("snapshot_retention_days", "7"))
orphan_retention_days = int(args.get("orphan_file_retention_days", "3"))
min_snapshots = int(args.get("min_snapshots_to_keep", "5"))
namespace = args.get("cloudwatch_namespace", "DataMeshy/Maintenance")

full_table_name = f"glue_catalog.{args['gold_db']}.{args['table_name']}"

# ---------------------------------------------------------------------------
# Structured logger
# ---------------------------------------------------------------------------

def log(level: str, message: str, **extra):
    record = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "level": level.upper(),
        "job": "iceberg_maintenance",
        "domain": args["domain"],
        "product_name": args["product_name"],
        "run_id": args.get("JOB_RUN_ID", "unknown"),
        "message": message,
    }
    record.update(extra)
    print(json.dumps(record))


def emit_metric(metric_name: str, value: float, unit: str = "Count"):
    """Emit CloudWatch metric. Non-fatal if it fails."""
    try:
        cw = boto3.client("cloudwatch")
        cw.put_metric_data(
            Namespace=namespace,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": unit,
                    "Dimensions": [
                        {"Name": "Domain", "Value": args["domain"]},
                        {"Name": "ProductName", "Value": args["product_name"]},
                    ],
                }
            ],
        )
    except Exception as exc:
        log("WARN", "Failed to emit CloudWatch metric", metric=metric_name, error=str(exc))


log("INFO", "Iceberg maintenance starting", table=full_table_name)

# ---------------------------------------------------------------------------
# Capture pre-maintenance file count for reporting
# ---------------------------------------------------------------------------

files_before = 0
try:
    files_df = spark.sql(f"SELECT COUNT(*) as cnt FROM {full_table_name}.files")
    files_before = files_df.collect()[0]["cnt"]
    log("INFO", "Pre-maintenance file count", files_before=files_before)
except Exception as exc:
    log("WARN", "Could not count files before maintenance", error=str(exc))

# ---------------------------------------------------------------------------
# Step 1: OPTIMIZE (RewriteDataFiles) — compact small files
# ---------------------------------------------------------------------------

try:
    log("INFO", "Step 1: OPTIMIZE — compacting files", target_file_size_mb=target_file_size_mb)
    target_bytes = target_file_size_mb * 1024 * 1024

    optimize_result = spark.sql(
        f"""
        CALL glue_catalog.system.rewrite_data_files(
            table => '{args['gold_db']}.{args['table_name']}',
            options => map(
                'target-file-size-bytes', '{target_bytes}',
                'min-file-size-bytes', '{target_bytes // 4}',
                'max-concurrent-file-group-rewrites', '10'
            )
        )
        """
    )
    optimize_df = optimize_result.collect()
    if optimize_df:
        row = optimize_df[0]
        log(
            "INFO",
            "OPTIMIZE complete",
            rewritten_data_files_count=int(row["rewritten_data_files_count"]) if "rewritten_data_files_count" in row else 0,
            added_data_files_count=int(row["added_data_files_count"]) if "added_data_files_count" in row else 0,
        )
except Exception as exc:
    log("ERROR", "OPTIMIZE step failed (non-fatal)", error=str(exc))
    emit_metric("IcebergMaintenanceFailure", 1)

# ---------------------------------------------------------------------------
# Step 2: VACUUM (ExpireSnapshots) — expire old snapshots
# ---------------------------------------------------------------------------

try:
    log(
        "INFO",
        "Step 2: VACUUM — expiring snapshots",
        retention_days=snapshot_retention_days,
        min_snapshots_to_keep=min_snapshots,
    )

    # Calculate the oldest timestamp we can expire to
    retention_cutoff_ms = int(
        (datetime.datetime.utcnow() - datetime.timedelta(days=snapshot_retention_days)).timestamp() * 1000
    )

    # Count snapshots before expiry
    snapshots_before_df = spark.sql(
        f"SELECT COUNT(*) as cnt FROM {full_table_name}.snapshots"
    )
    snapshots_before = snapshots_before_df.collect()[0]["cnt"]

    # Protect minimum snapshot count: skip expiry if we'd go below the minimum
    if snapshots_before <= min_snapshots:
        log(
            "INFO",
            "Skipping snapshot expiry: snapshot count at or below minimum",
            snapshots_before=snapshots_before,
            min_snapshots=min_snapshots,
        )
    else:
        vacuum_result = spark.sql(
            f"""
            CALL glue_catalog.system.expire_snapshots(
                table => '{args['gold_db']}.{args['table_name']}',
                older_than => TIMESTAMP '{datetime.datetime.utcfromtimestamp(retention_cutoff_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")}',
                retain_last => {min_snapshots}
            )
            """
        )
        vacuum_df = vacuum_result.collect()
        snapshots_expired = int(vacuum_df[0]["deleted_manifests_count"]) if vacuum_df and "deleted_manifests_count" in vacuum_df[0] else 0
        log(
            "INFO",
            "VACUUM complete",
            snapshots_before=snapshots_before,
            snapshots_expired=snapshots_expired,
        )
except Exception as exc:
    log("ERROR", "VACUUM step failed (non-fatal)", error=str(exc))
    emit_metric("IcebergMaintenanceFailure", 1)

# ---------------------------------------------------------------------------
# Step 3: Delete orphan files
# ---------------------------------------------------------------------------

try:
    log("INFO", "Step 3: Deleting orphan files", retention_days=orphan_retention_days)

    orphan_cutoff = (
        datetime.datetime.utcnow() - datetime.timedelta(days=orphan_retention_days)
    ).strftime("%Y-%m-%d %H:%M:%S")

    orphan_result = spark.sql(
        f"""
        CALL glue_catalog.system.remove_orphan_files(
            table => '{args['gold_db']}.{args['table_name']}',
            older_than => TIMESTAMP '{orphan_cutoff}'
        )
        """
    )
    orphan_df = orphan_result.collect()
    orphans_deleted = len(orphan_df) if orphan_df else 0
    log("INFO", "Orphan file deletion complete", orphans_deleted=orphans_deleted)
except Exception as exc:
    log("ERROR", "Orphan file deletion failed (non-fatal)", error=str(exc))
    emit_metric("IcebergMaintenanceFailure", 1)

# ---------------------------------------------------------------------------
# Capture post-maintenance file count
# ---------------------------------------------------------------------------

files_after = 0
try:
    files_after_df = spark.sql(f"SELECT COUNT(*) as cnt FROM {full_table_name}.files")
    files_after = files_after_df.collect()[0]["cnt"]
except Exception as exc:
    log("WARN", "Could not count files after maintenance", error=str(exc))

log(
    "INFO",
    "Iceberg maintenance complete",
    files_before=files_before,
    files_after=files_after,
    files_reduced=max(0, files_before - files_after),
)

# ---------------------------------------------------------------------------
# Commit (maintenance job always commits — failures are non-fatal)
# ---------------------------------------------------------------------------

job.commit()

log("INFO", "Job completed (maintenance)")
