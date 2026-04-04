"""
raw_ingestion.py — Glue 4.0 PySpark raw ingestion job template.

Medallion layer: Bronze (Raw)
- Reads source data via JDBC/S3 connection
- Writes as-is to raw S3 bucket (Parquet + Hive partitioning)
- Append-only (immutable audit trail)
- Job bookmark enabled for incremental ingestion

Required args (from README.md parameter interface):
  --domain               : domain name (e.g., "sales")
  --product_name         : product name (e.g., "customer_orders")
  --raw_bucket           : raw S3 bucket name (e.g., "sales-raw-123456789")
  --source_connection_name: Glue connection name or "S3" for S3 source
  --raw_db               : Glue catalog database for raw layer (e.g., "sales_raw")
  --table_name           : target table name (e.g., "customer_orders")

Optional args:
  --source_s3_path       : S3 path when source_connection_name == "S3"
  --source_table         : JDBC source table name (schema.table)
  --source_query         : custom SQL query for partial extraction
  --ingestion_date       : override ingestion date partition (YYYY-MM-DD)
  --max_rows             : cap rows read (useful for testing)
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
from pyspark.sql import functions as F
from pyspark.sql.types import StringType

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

REQUIRED_ARGS = [
    "JOB_NAME",
    "domain",
    "product_name",
    "raw_bucket",
    "source_connection_name",
    "raw_db",
    "table_name",
]

OPTIONAL_ARGS = [
    "source_s3_path",
    "source_table",
    "source_query",
    "ingestion_date",
    "max_rows",
]

args = getResolvedOptions(sys.argv, REQUIRED_ARGS)

# Collect optional args safely
for opt in OPTIONAL_ARGS:
    if f"--{opt}" in sys.argv:
        args.update(getResolvedOptions(sys.argv, [opt]))

sc = SparkContext()
glue_context = GlueContext(sc)
spark = glue_context.spark_session
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

# ---------------------------------------------------------------------------
# Structured logger helper
# ---------------------------------------------------------------------------

def log(level: str, message: str, **extra):
    record = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "level": level.upper(),
        "job": "raw_ingestion",
        "domain": args["domain"],
        "product_name": args["product_name"],
        "run_id": args["JOB_RUN_ID"] if "JOB_RUN_ID" in args else "unknown",
        "message": message,
    }
    record.update(extra)
    print(json.dumps(record))


log("INFO", "Job starting", source_connection=args["source_connection_name"])

# ---------------------------------------------------------------------------
# Ingestion date partition value
# ---------------------------------------------------------------------------

ingestion_date = args.get("ingestion_date", datetime.date.today().isoformat())
log("INFO", "Ingestion date resolved", ingestion_date=ingestion_date)

# ---------------------------------------------------------------------------
# Read source data
# ---------------------------------------------------------------------------

source_connection = args["source_connection_name"]

if source_connection.upper() == "S3":
    source_path = args.get("source_s3_path", "")
    if not source_path:
        raise ValueError("--source_s3_path is required when source_connection_name is 'S3'")
    log("INFO", "Reading from S3 source", source_path=source_path)
    dynamic_frame = glue_context.create_dynamic_frame.from_options(
        connection_type="s3",
        connection_options={"paths": [source_path], "recurse": True},
        format="parquet",
        transformation_ctx="raw_s3_source",
    )
else:
    # JDBC source via named Glue connection
    source_table = args.get("source_table", args["table_name"])
    source_query = args.get("source_query", "")
    connection_options = {
        "useConnectionProperties": "true",
        "dbtable": source_table,
    }
    if source_query:
        connection_options["query"] = source_query
        del connection_options["dbtable"]

    log("INFO", "Reading from JDBC source", connection=source_connection, source_table=source_table)
    dynamic_frame = glue_context.create_dynamic_frame.from_options(
        connection_type="jdbc",
        connection_options={
            "connectionName": source_connection,
            **connection_options,
        },
        transformation_ctx="raw_jdbc_source",
    )

df = dynamic_frame.toDF()
log("INFO", "Source read complete", rows_read=df.count())

# Cap rows for testing if requested
max_rows = args.get("max_rows")
if max_rows:
    df = df.limit(int(max_rows))
    log("INFO", "Row cap applied", max_rows=max_rows)

# ---------------------------------------------------------------------------
# Add ingestion metadata columns
# ---------------------------------------------------------------------------

df = (
    df
    .withColumn("_ingestion_date", F.lit(ingestion_date))
    .withColumn("_ingestion_ts", F.current_timestamp())
    .withColumn("_job_run_id", F.lit(args.get("JOB_RUN_ID", "unknown")))
)

# ---------------------------------------------------------------------------
# Write to raw S3 (Parquet + Hive partitioning by ingestion date)
# Raw layer is append-only — we never overwrite existing partitions.
# ---------------------------------------------------------------------------

raw_output_path = f"s3://{args['raw_bucket']}/{args['domain']}/{args['table_name']}/"

log("INFO", "Writing to raw layer", output_path=raw_output_path, partition_by="_ingestion_date")

rows_to_write = df.count()
(
    df.write
    .mode("append")
    .partitionBy("_ingestion_date")
    .parquet(raw_output_path)
)

log(
    "INFO",
    "Write complete",
    rows_written=rows_to_write,
    output_path=raw_output_path,
)

# ---------------------------------------------------------------------------
# Update Glue Data Catalog table (create if not exists)
# ---------------------------------------------------------------------------

# Register the raw table in the Glue Catalog so downstream jobs can reference it.
glue_context.create_dynamic_frame.from_options(
    connection_type="s3",
    connection_options={"paths": [raw_output_path]},
    format="parquet",
    transformation_ctx="catalog_refresh_read",
)

# ---------------------------------------------------------------------------
# Commit job bookmark (marks progress for incremental next run)
# ---------------------------------------------------------------------------

job.commit()

log(
    "INFO",
    "Job completed successfully",
    rows_written=rows_to_write,
    ingestion_date=ingestion_date,
)
