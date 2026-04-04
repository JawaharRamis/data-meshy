"""Silver transform Glue job for customer_orders data product.

Reads from the raw Iceberg table, applies deduplication and type casting,
then writes validated records to the silver Iceberg table.

Transformations:
  - Deduplicate by order_id (keep latest)
  - Cast order_total to Decimal(10,2)
  - Validate order_date is not null (drop invalid rows)
  - Strip whitespace from string columns

This job accepts the following Glue job parameters:
  --domain, --product_name, --raw_bucket, --silver_bucket,
  --raw_db, --silver_db, --table_name, --quality_ruleset_name

Usage (Glue):
  glueetl --script silver_transform.py \
    --job-bookmark-option job-bookmark-enable \
    --domain sales --product_name customer_orders \
    --raw_bucket sales-raw-123456789012 \
    --silver_bucket sales-silver-123456789012 \
    --raw_db sales_raw --silver_db sales_silver \
    --table_name customer_orders \
    --quality_ruleset_name sales_customer_orders_dq
"""

import sys
import json
import logging
from datetime import datetime
from decimal import Decimal

from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("silver_transform")

LOG_BASE = {
    "job": "silver_transform",
    "domain": None,
    "product_name": None,
    "layer": "silver",
}


def _log(event: str, **kwargs):
    entry = {**LOG_BASE, "event": event, "timestamp": datetime.utcnow().isoformat()}
    entry.update(kwargs)
    print(json.dumps(entry))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

args = getResolvedOptions(
    sys.argv,
    [
        "job_name",
        "domain",
        "product_name",
        "raw_bucket",
        "silver_bucket",
        "raw_db",
        "silver_db",
        "table_name",
        "quality_ruleset_name",
    ],
)

LOG_BASE["domain"] = args["domain"]
LOG_BASE["product_name"] = args["product_name"]

_log("job_start", args=args)

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args["job_name"], args)

try:
    # Read from raw Iceberg table
    raw_table = f"{args['raw_db']}.{args['table_name']}"
    _log("reading_raw", table=raw_table)

    df = spark.table(raw_table)
    raw_count = df.count()
    _log("rows_read", count=raw_count)

    # --- Transformation 1: Deduplicate by order_id ---
    # Use window function to keep the latest record per order_id
    from pyspark.sql.window import Window

    window = Window.partitionBy("order_id").orderBy(F.col("_ingested_at").desc())
    df = (
        df.withColumn("_row_num", F.row_number().over(window))
        .filter(F.col("_row_num") == 1)
        .drop("_row_num")
    )
    after_dedup = df.count()
    _log("deduplicated", before=raw_count, after=after_dedup, removed=raw_count - after_dedup)

    # --- Transformation 2: Cast order_total to Decimal(10,2) ---
    df = df.withColumn(
        "order_total",
        F.col("order_total").cast(DecimalType(10, 2)),
    )

    # --- Transformation 3: Validate order_date not null ---
    before_validation = df.count()
    df = df.filter(F.col("order_date").isNotNull())
    after_validation = df.count()
    _log(
        "validated_order_date",
        before=before_validation,
        after=after_validation,
        dropped=before_validation - after_validation,
    )

    # --- Transformation 4: Strip whitespace from string columns ---
    for col_name in ["order_id", "customer_email", "customer_segment"]:
        if col_name in df.columns:
            df = df.withColumn(col_name, F.trim(F.col(col_name)))

    # Drop ingestion metadata columns
    df = df.drop("_ingested_at", "_source_file")

    silver_count = df.count()
    _log("transformed", final_rows=silver_count)

    # Write to silver Iceberg table
    silver_table = f"{args['silver_db']}.{args['table_name']}"
    _log("writing_silver", table=silver_table, rows=silver_count)

    df.writeTo(silver_table).using("iceberg").tableProperty(
        "format-version", "2"
    ).createOrReplace()

    _log("job_complete", rows_written=silver_count)

except Exception as exc:
    _log("job_failed", error=str(exc), error_type=type(exc).__name__)
    raise
finally:
    job.commit()
    _log("job_committed")
