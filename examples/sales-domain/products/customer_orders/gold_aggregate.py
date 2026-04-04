"""Gold aggregate Glue job for customer_orders data product.

Reads from the silver Iceberg table, enriches records with a customer_segment
lookup (hardcoded in this example since there is no live CRM), and writes
the final data product to the gold Iceberg table.

Customer segment rules:
  - order_total >= 500  => "Gold"
  - order_total >= 200  => "Silver"
  - order_total < 200   => "Bronze"

This job accepts the following Glue job parameters:
  --domain, --product_name, --silver_bucket, --gold_bucket,
  --silver_db, --gold_db, --table_name

Usage (Glue):
  glueetl --script gold_aggregate.py \
    --job-bookmark-option job-bookmark-enable \
    --domain sales --product_name customer_orders \
    --silver_bucket sales-silver-123456789012 \
    --gold_bucket sales-gold-123456789012 \
    --silver_db sales_silver --gold_db sales_gold \
    --table_name customer_orders
"""

import sys
import json
import logging
from datetime import datetime

from awsglue.utils import getResolvedOptions
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.context import SparkContext
from pyspark.sql import functions as F

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gold_aggregate")

LOG_BASE = {
    "job": "gold_aggregate",
    "domain": None,
    "product_name": None,
    "layer": "gold",
}


def _log(event: str, **kwargs):
    entry = {**LOG_BASE, "event": event, "timestamp": datetime.utcnow().isoformat()}
    entry.update(kwargs)
    print(json.dumps(entry))


# ---------------------------------------------------------------------------
# Customer segment enrichment logic
# ---------------------------------------------------------------------------

def assign_customer_segment(order_total):
    """Assign customer segment based on order_total.

    Since there is no live CRM in the example, we use a simple
    order_total-based lookup:
      - >= 500 => Gold
      - >= 200 => Silver
      - < 200  => Bronze
    """
    return (
        F.when(F.col("order_total") >= 500, F.lit("Gold"))
        .when(F.col("order_total") >= 200, F.lit("Silver"))
        .otherwise(F.lit("Bronze"))
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

args = getResolvedOptions(
    sys.argv,
    [
        "job_name",
        "domain",
        "product_name",
        "silver_bucket",
        "gold_bucket",
        "silver_db",
        "gold_db",
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
    # Read from silver Iceberg table
    silver_table = f"{args['silver_db']}.{args['table_name']}"
    _log("reading_silver", table=silver_table)

    df = spark.table(silver_table)
    silver_count = df.count()
    _log("rows_read", count=silver_count)

    # --- Enrichment: Assign customer_segment ---
    # Override the customer_segment column with the enriched value
    df = df.withColumn("customer_segment", assign_customer_segment(F.col("order_total")))

    enriched_count = df.count()
    _log("enriched", rows=enriched_count)

    # Add gold layer metadata
    df = df.withColumn("_gold_processed_at", F.current_timestamp())

    # Write to gold Iceberg table (this IS the data product)
    gold_table = f"{args['gold_db']}.{args['table_name']}"
    _log("writing_gold", table=gold_table, rows=enriched_count)

    df.writeTo(gold_table).using("iceberg").tableProperty(
        "format-version", "2"
    ).partitionedBy(F.months("order_date")).createOrReplace()

    _log("job_complete", rows_written=enriched_count)

except Exception as exc:
    _log("job_failed", error=str(exc), error_type=type(exc).__name__)
    raise
finally:
    job.commit()
    _log("job_committed")
