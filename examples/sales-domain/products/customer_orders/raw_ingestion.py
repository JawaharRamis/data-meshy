"""Raw ingestion Glue job for customer_orders data product.

Reads CSV files from the raw S3 bucket (simulating an orders database extract)
and writes them as Apache Iceberg tables to the raw catalog database.

This job accepts the following Glue job parameters:
  --domain, --product_name, --raw_bucket, --source_connection_name,
  --raw_db, --table_name

Usage (Glue):
  glueetl --script raw_ingestion.py \
    --job-bookmark-option job-bookmark-enable \
    --domain sales --product_name customer_orders \
    --raw_bucket sales-raw-123456789012 \
    --source_connection_name orders-csv \
    --raw_db sales_raw --table_name customer_orders
"""

import sys
import json
import logging
from datetime import datetime

from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from pyspark.sql import functions as F

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("raw_ingestion")

LOG_BASE = {
    "job": "raw_ingestion",
    "domain": None,
    "product_name": None,
    "layer": "raw",
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
        "source_connection_name",
        "raw_db",
        "table_name",
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
    # Read CSV files from raw S3 bucket
    input_path = f"s3://{args['raw_bucket']}/{args['table_name']}/"
    _log("reading_csv", path=input_path)

    df = spark.read.csv(
        input_path,
        header=True,
        inferSchema=True,
    )

    row_count = df.count()
    _log("rows_read", count=row_count)

    # Add ingestion metadata columns
    df = df.withColumn("_ingested_at", F.current_timestamp())
    df = df.withColumn("_source_file", F.input_file_name())

    # Write as Iceberg table to raw catalog
    raw_table = f"{args['raw_db']}.{args['table_name']}"
    _log("writing_iceberg", table=raw_table, rows=row_count)

    df.writeTo(raw_table).using("iceberg").tableProperty(
        "format-version", "2"
    ).createOrReplace()

    _log("job_complete", rows_written=row_count)

except Exception as exc:
    _log("job_failed", error=str(exc), error_type=type(exc).__name__)
    raise
finally:
    job.commit()
    _log("job_committed")
