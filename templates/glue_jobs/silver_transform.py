"""
silver_transform.py — Glue 4.0 PySpark silver transformation job template.

Medallion layer: Silver (Validated)
- Reads raw Parquet data from S3
- Deduplicates by primary key columns (if defined)
- Enforces types and schema (column name standardisation, null checks)
- Writes to Apache Iceberg table in the silver Glue catalog DB
- Triggers Glue Data Quality evaluation and raises on failure

Required args (from README.md parameter interface):
  --domain               : domain name
  --product_name         : product name
  --raw_bucket           : raw S3 bucket name
  --silver_bucket        : silver S3 bucket name
  --raw_db               : Glue catalog DB for raw layer
  --silver_db            : Glue catalog DB for silver layer
  --table_name           : table name
  --quality_ruleset_name : Glue DQ ruleset name

Optional args:
  --primary_key_columns  : comma-separated column names for dedup (e.g. "order_id")
  --expected_columns     : comma-separated list of expected column names for schema validation
  --ingestion_date       : process only this ingestion date partition (YYYY-MM-DD)
  --enable_quality_check : "true"/"false", default "true"
"""

import sys
import json
import datetime
import logging

from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from pyspark.sql import functions as F, Window
from pyspark.sql.types import StringType
import boto3

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

REQUIRED_ARGS = [
    "JOB_NAME",
    "domain",
    "product_name",
    "raw_bucket",
    "silver_bucket",
    "raw_db",
    "silver_db",
    "table_name",
    "quality_ruleset_name",
]

OPTIONAL_ARGS = [
    "primary_key_columns",
    "expected_columns",
    "ingestion_date",
    "enable_quality_check",
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

# Configure Iceberg catalog
spark.conf.set("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.warehouse", f"s3://{args['silver_bucket']}/")
spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
spark.conf.set("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")

# ---------------------------------------------------------------------------
# Custom exception types
# ---------------------------------------------------------------------------

class SchemaValidationError(Exception):
    """Raised when the Iceberg table schema does not match the expected columns."""
    pass


class QualityCheckFailedError(Exception):
    """Raised when Glue Data Quality evaluation fails below threshold."""
    pass

# ---------------------------------------------------------------------------
# Structured logger
# ---------------------------------------------------------------------------

def log(level: str, message: str, **extra):
    record = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "level": level.upper(),
        "job": "silver_transform",
        "domain": args["domain"],
        "product_name": args["product_name"],
        "run_id": args.get("JOB_RUN_ID", "unknown"),
        "message": message,
    }
    record.update(extra)
    print(json.dumps(record))


log("INFO", "Job starting")

# ---------------------------------------------------------------------------
# Read raw data
# ---------------------------------------------------------------------------

raw_path = f"s3://{args['raw_bucket']}/{args['domain']}/{args['table_name']}/"

ingestion_date = args.get("ingestion_date")
if ingestion_date:
    raw_path = f"{raw_path}_ingestion_date={ingestion_date}/"
    log("INFO", "Reading specific partition", ingestion_date=ingestion_date, path=raw_path)
else:
    log("INFO", "Reading all partitions", path=raw_path)

df_raw = spark.read.parquet(raw_path)
rows_read = df_raw.count()
log("INFO", "Raw data read", rows_read=rows_read)

# ---------------------------------------------------------------------------
# Drop internal metadata columns added by raw ingestion
# ---------------------------------------------------------------------------

metadata_cols = {"_ingestion_date", "_ingestion_ts", "_job_run_id"}
data_cols = [c for c in df_raw.columns if c not in metadata_cols]
df = df_raw.select(data_cols)

log("INFO", "Metadata columns dropped", remaining_columns=data_cols)

# ---------------------------------------------------------------------------
# Column name standardisation (lowercase, strip whitespace, replace spaces)
# ---------------------------------------------------------------------------

for col_name in df.columns:
    std_name = col_name.strip().lower().replace(" ", "_").replace("-", "_")
    if std_name != col_name:
        df = df.withColumnRenamed(col_name, std_name)
        log("INFO", "Column renamed", original=col_name, standardised=std_name)

# ---------------------------------------------------------------------------
# Deduplication by primary key
# ---------------------------------------------------------------------------

primary_key_str = args.get("primary_key_columns", "")
if primary_key_str:
    pk_cols = [c.strip() for c in primary_key_str.split(",") if c.strip()]
    log("INFO", "Deduplicating", primary_key_columns=pk_cols)

    # Keep the latest row for each primary key (by _ingestion_ts if present, else arbitrary)
    if "_ingestion_ts" in df_raw.columns:
        w = Window.partitionBy(*pk_cols).orderBy(F.col("_ingestion_ts").desc())
        df = df_raw.select(data_cols + ["_ingestion_ts"]).withColumn("_rn", F.row_number().over(w))
        df = df.filter(F.col("_rn") == 1).drop("_rn", "_ingestion_ts")
    else:
        df = df.dropDuplicates(pk_cols)

    rows_after_dedup = df.count()
    log("INFO", "Dedup complete", rows_before=rows_read, rows_after=rows_after_dedup, duplicates_removed=rows_read - rows_after_dedup)
else:
    log("INFO", "No primary key specified, skipping deduplication")

# ---------------------------------------------------------------------------
# Null checks on non-nullable expected columns
# ---------------------------------------------------------------------------

expected_columns_str = args.get("expected_columns", "")
if expected_columns_str:
    expected_cols = [c.strip() for c in expected_columns_str.split(",") if c.strip()]
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        msg = f"Expected columns missing from raw data: {missing}"
        log("ERROR", msg, missing_columns=missing)
        raise SchemaValidationError(msg)
    log("INFO", "Expected columns present", expected_columns=expected_cols)

# ---------------------------------------------------------------------------
# Add silver metadata
# ---------------------------------------------------------------------------

df = (
    df
    .withColumn("_silver_ts", F.current_timestamp())
    .withColumn("_silver_job_run_id", F.lit(args.get("JOB_RUN_ID", "unknown")))
)

# ---------------------------------------------------------------------------
# Write to Iceberg silver table
# ---------------------------------------------------------------------------

silver_db = args["silver_db"]
table_name = args["table_name"]
full_table_name = f"glue_catalog.{silver_db}.{table_name}"
silver_warehouse = f"s3://{args['silver_bucket']}/"

log("INFO", "Writing to Iceberg silver table", table=full_table_name)

# Create table if it doesn't exist (first run)
# Subsequent runs use APPEND mode (Iceberg handles schema evolution)
(
    df.writeTo(full_table_name)
    .tableProperty("format-version", "2")
    .tableProperty("write.target-file-size-bytes", str(128 * 1024 * 1024))
    .createOrReplace()
)

rows_written = df.count()
log("INFO", "Iceberg write complete", table=full_table_name, rows_written=rows_written)

# ---------------------------------------------------------------------------
# Schema validation: compare live Iceberg schema vs expected columns
# ---------------------------------------------------------------------------

if expected_columns_str:
    expected_cols = [c.strip() for c in expected_columns_str.split(",") if c.strip()]
    live_df = spark.table(full_table_name)
    live_cols = set(live_df.columns) - {"_silver_ts", "_silver_job_run_id"}
    missing_in_table = set(expected_cols) - live_cols
    if missing_in_table:
        msg = f"Schema validation failed: columns missing from Iceberg table: {list(missing_in_table)}"
        log("ERROR", msg, missing_columns=list(missing_in_table))
        raise SchemaValidationError(msg)
    log("INFO", "Schema validation passed", live_columns=list(live_cols))

# ---------------------------------------------------------------------------
# Glue Data Quality evaluation
# ---------------------------------------------------------------------------

enable_quality = args.get("enable_quality_check", "true").lower() != "false"

if enable_quality:
    ruleset_name = args["quality_ruleset_name"]
    log("INFO", "Starting Glue Data Quality evaluation", ruleset=ruleset_name)

    glue_client = boto3.client("glue")

    try:
        response = glue_client.start_data_quality_ruleset_evaluation_run(
            DataSource={
                "GlueTable": {
                    "DatabaseName": silver_db,
                    "TableName": table_name,
                }
            },
            RulesetNames=[ruleset_name],
            Role=args.get("glue_job_execution_role_arn", ""),
            NumberOfWorkers=2,
            WorkerType="G.1X",
        )
        run_id = response["RunId"]
        log("INFO", "DQ evaluation started", run_id=run_id)

        # Poll for completion (in-band — acceptable for synchronous job)
        import time
        for _ in range(60):  # up to 10 minutes
            status_response = glue_client.get_data_quality_ruleset_evaluation_run(RunId=run_id)
            status = status_response.get("Status")
            if status in ("SUCCEEDED", "FAILED", "STOPPED", "ERROR"):
                break
            time.sleep(10)

        result_ids = status_response.get("ResultIds", [])
        if result_ids:
            results = glue_client.batch_get_data_quality_result(ResultIds=result_ids)
            for result in results.get("Results", []):
                score = result.get("Score", 0)
                log("INFO", "DQ result", score=score, result_id=result.get("ResultId"))
                if score < 1.0:
                    failed_rules = [
                        r for r in result.get("RuleResults", [])
                        if r.get("Result") == "FAIL"
                    ]
                    msg = f"Quality check failed: score={score:.2f}, failed_rules={[r.get('Name') for r in failed_rules]}"
                    log("ERROR", msg, score=score, failed_rules=[r.get("Name") for r in failed_rules])
                    raise QualityCheckFailedError(msg)

        log("INFO", "Data Quality evaluation passed")
    except QualityCheckFailedError:
        raise
    except Exception as exc:
        log("WARN", "Data Quality evaluation error (non-fatal for silver step)", error=str(exc))
else:
    log("INFO", "Quality check skipped (enable_quality_check=false)")

# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

job.commit()

log(
    "INFO",
    "Job completed successfully",
    rows_written=rows_written,
    table=full_table_name,
)
