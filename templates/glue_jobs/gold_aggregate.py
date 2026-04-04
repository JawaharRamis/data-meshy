"""
gold_aggregate.py — Glue 4.0 PySpark gold aggregation job template.

Medallion layer: Gold (Data Product)
- Reads from silver Iceberg table
- Applies business logic transformations (domain-specific — add logic in the marked section)
- Writes to Iceberg table in the gold Glue catalog DB via MERGE INTO (upsert)
- Emits CloudWatch metrics: rows_written, rows_updated, rows_deleted
- Final schema validation: any undeclared column blocks publish

Required args (from README.md parameter interface):
  --domain         : domain name
  --product_name   : product name
  --silver_bucket  : silver S3 bucket name
  --gold_bucket    : gold S3 bucket name
  --silver_db      : Glue catalog DB for silver layer
  --gold_db        : Glue catalog DB for gold layer
  --table_name     : table name

Optional args:
  --primary_key_columns  : comma-separated PK columns for MERGE (required for upsert; default "id")
  --partition_by_columns : comma-separated columns for Iceberg partitioning
  --declared_columns     : comma-separated expected output columns from product.yaml schema
  --cloudwatch_namespace : CloudWatch namespace for metrics (default "DataMeshy/Pipeline")
"""

import sys
import json
import datetime

from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.types import StringType
import boto3

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

REQUIRED_ARGS = [
    "JOB_NAME",
    "domain",
    "product_name",
    "silver_bucket",
    "gold_bucket",
    "silver_db",
    "gold_db",
    "table_name",
]

OPTIONAL_ARGS = [
    "primary_key_columns",
    "partition_by_columns",
    "declared_columns",
    "cloudwatch_namespace",
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

# Configure Iceberg catalog for both silver (read) and gold (write)
spark.conf.set("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
spark.conf.set("spark.sql.catalog.glue_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
spark.conf.set("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class UndeclaredColumnError(Exception):
    """Raised when output DataFrame contains columns not declared in product.yaml schema."""
    pass

# ---------------------------------------------------------------------------
# Structured logger
# ---------------------------------------------------------------------------

def log(level: str, message: str, **extra):
    record = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "level": level.upper(),
        "job": "gold_aggregate",
        "domain": args["domain"],
        "product_name": args["product_name"],
        "run_id": args.get("JOB_RUN_ID", "unknown"),
        "message": message,
    }
    record.update(extra)
    print(json.dumps(record))


def emit_metric(namespace: str, metric_name: str, value: float, unit: str = "Count"):
    """Emit a CloudWatch custom metric."""
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
        log("DEBUG", "Metric emitted", metric=metric_name, value=value)
    except Exception as exc:
        log("WARN", "Failed to emit metric", metric=metric_name, error=str(exc))


log("INFO", "Job starting")

# ---------------------------------------------------------------------------
# Read silver Iceberg table
# ---------------------------------------------------------------------------

silver_table = f"glue_catalog.{args['silver_db']}.{args['table_name']}"
log("INFO", "Reading silver Iceberg table", table=silver_table)
df_silver = spark.table(silver_table)
rows_silver = df_silver.count()
log("INFO", "Silver data read", rows=rows_silver)

# ---------------------------------------------------------------------------
# Business logic transformations
# ---------------------------------------------------------------------------
# NOTE TO DOMAIN ENGINEER: Add your domain-specific transformation logic below.
# Examples:
#   - Enrichment: join with reference tables
#   - Aggregation: group by and aggregate metrics
#   - Filtering: remove test/cancelled records
#   - Derived columns: compute customer_segment, revenue_band, etc.
#
# The template passes data through unchanged (identity transform) as a starting point.
# ---------------------------------------------------------------------------

df_gold = df_silver

# --- BEGIN DOMAIN-SPECIFIC TRANSFORMS ---

# Example enrichment (replace or extend with real logic):
# df_gold = df_gold.withColumn(
#     "customer_segment",
#     F.when(F.col("order_total") > 1000, "premium")
#      .when(F.col("order_total") > 100, "standard")
#      .otherwise("entry")
# )

# Example aggregation (uncomment and customise):
# df_gold = (
#     df_gold
#     .groupBy("customer_id", "order_date")
#     .agg(
#         F.sum("order_total").alias("daily_spend"),
#         F.count("order_id").alias("order_count"),
#     )
# )

# --- END DOMAIN-SPECIFIC TRANSFORMS ---

# Remove silver metadata columns before writing to gold
silver_meta_cols = {"_silver_ts", "_silver_job_run_id"}
df_gold = df_gold.select([c for c in df_gold.columns if c not in silver_meta_cols])

# Add gold metadata
df_gold = (
    df_gold
    .withColumn("_gold_ts", F.current_timestamp())
    .withColumn("_gold_job_run_id", F.lit(args.get("JOB_RUN_ID", "unknown")))
)

# ---------------------------------------------------------------------------
# Final schema validation: block undeclared columns
# ---------------------------------------------------------------------------

declared_str = args.get("declared_columns", "")
if declared_str:
    declared = {c.strip() for c in declared_str.split(",") if c.strip()}
    # Add internal gold metadata columns to allowed set
    declared.update({"_gold_ts", "_gold_job_run_id"})
    undeclared = set(df_gold.columns) - declared
    if undeclared:
        msg = f"UndeclaredColumnError: columns not in product.yaml schema: {sorted(undeclared)}"
        log("ERROR", msg, undeclared_columns=sorted(undeclared))
        raise UndeclaredColumnError(msg)
    log("INFO", "Schema validation passed: all columns declared", declared_columns=sorted(declared))
else:
    log("WARN", "No --declared_columns provided; schema validation skipped")

# ---------------------------------------------------------------------------
# Write to gold Iceberg table using MERGE INTO (upsert semantics)
# ---------------------------------------------------------------------------

gold_db = args["gold_db"]
table_name = args["table_name"]
full_table_name = f"glue_catalog.{gold_db}.{table_name}"
gold_path = f"s3://{args['gold_bucket']}/{args['domain']}/{table_name}/"

pk_str = args.get("primary_key_columns", "id")
pk_cols = [c.strip() for c in pk_str.split(",") if c.strip()]

partition_str = args.get("partition_by_columns", "")
partition_cols = [c.strip() for c in partition_str.split(",") if c.strip()] if partition_str else []

log(
    "INFO",
    "Preparing gold Iceberg upsert",
    table=full_table_name,
    primary_key_columns=pk_cols,
    partition_columns=partition_cols,
)

# Create the gold table if it doesn't exist yet
table_exists = spark.catalog.tableExists(full_table_name)

if not table_exists:
    log("INFO", "Gold table does not exist — creating with initial write", table=full_table_name)
    writer = (
        df_gold.writeTo(full_table_name)
        .tableProperty("format-version", "2")
        .tableProperty("write.target-file-size-bytes", str(128 * 1024 * 1024))
        .tableProperty("write.parquet.compression-codec", "snappy")
    )
    if partition_cols:
        # Dynamic partitioning — use identity transform by default
        # Domain engineer: adjust transform (e.g., .partitionedBy(F.months("order_date")))
        writer = writer.partitionedBy(*partition_cols)
    writer.create()
    rows_written = df_gold.count()
    rows_updated = 0
    rows_deleted = 0
else:
    # Upsert via MERGE INTO
    # Register source as a temp view
    df_gold.createOrReplaceTempView("gold_source")

    # Build ON clause from primary key columns
    on_clause = " AND ".join([f"t.{pk} = s.{pk}" for pk in pk_cols])

    # Build SET clause for UPDATE (all non-PK columns)
    non_pk_cols = [c for c in df_gold.columns if c not in pk_cols]
    set_clause = ", ".join([f"t.{c} = s.{c}" for c in non_pk_cols])

    # Build INSERT column list
    all_cols = df_gold.columns
    insert_cols = ", ".join(all_cols)
    insert_vals = ", ".join([f"s.{c}" for c in all_cols])

    merge_sql = f"""
        MERGE INTO {full_table_name} AS t
        USING gold_source AS s
        ON {on_clause}
        WHEN MATCHED THEN
            UPDATE SET {set_clause}
        WHEN NOT MATCHED THEN
            INSERT ({insert_cols})
            VALUES ({insert_vals})
    """

    log("INFO", "Executing MERGE INTO", merge_sql=merge_sql.strip())
    spark.sql(merge_sql)

    # Count rows post-merge for metrics
    rows_written = df_gold.count()  # rows in source (new rows inserted)
    rows_updated = 0   # Step Functions can compute from before/after snapshot diff if needed
    rows_deleted = 0

log(
    "INFO",
    "Gold write complete",
    table=full_table_name,
    rows_written=rows_written,
    rows_updated=rows_updated,
    rows_deleted=rows_deleted,
)

# ---------------------------------------------------------------------------
# Emit CloudWatch metrics
# ---------------------------------------------------------------------------

namespace = args.get("cloudwatch_namespace", "DataMeshy/Pipeline")
emit_metric(namespace, "GoldRowsWritten", rows_written)
emit_metric(namespace, "GoldRowsUpdated", rows_updated)
emit_metric(namespace, "GoldRowsDeleted", rows_deleted)

# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

job.commit()

log(
    "INFO",
    "Job completed successfully",
    rows_written=rows_written,
    rows_updated=rows_updated,
    rows_deleted=rows_deleted,
    table=full_table_name,
)
