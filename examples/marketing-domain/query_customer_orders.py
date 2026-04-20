"""Query the sales/customer_orders data product from the marketing domain account.

This script demonstrates that a marketing analyst can query non-PII columns
of the sales/customer_orders product via the resource link created by the
subscription workflow.  The PII column (customer_email) is NOT accessible —
Lake Formation column-level filtering enforces this at query time.

Prerequisites:
  - The subscription to sales/customer_orders must be ACTIVE (run subscribe_to_customer_orders.sh first).
  - boto3 installed: pip install boto3
  - AWS credentials configured for the MARKETING account.
  - Environment variables (see below).

Environment variables:
  AWS_PROFILE          — AWS CLI profile for the marketing account (default: default).
  ATHENA_WORKGROUP     — Athena workgroup name (default: primary).
  ATHENA_OUTPUT_BUCKET — S3 bucket for Athena query results.
                         Example: s3://marketing-athena-results-123456789012/
  AWS_REGION           — AWS region (default: us-east-1).

Usage:
  AWS_PROFILE=marketing \\
  ATHENA_OUTPUT_BUCKET=s3://marketing-athena-results-123456789012/ \\
  python query_customer_orders.py
"""

from __future__ import annotations

import os
import time

import boto3


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

AWS_PROFILE = os.environ.get("AWS_PROFILE", "default")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
ATHENA_OUTPUT_BUCKET = os.environ.get("ATHENA_OUTPUT_BUCKET", "")

# The resource link is created in the marketing account's Glue catalog by the
# subscription saga.  It mirrors the sales account's gold Iceberg table.
RESOURCE_LINK_DATABASE = "marketing_catalog"
RESOURCE_LINK_TABLE = "sales_customer_orders"

# Columns available to marketing (non-PII, as defined in the approved subscription).
ALLOWED_QUERY = (
    f"SELECT order_id, order_date, order_total "
    f"FROM {RESOURCE_LINK_DATABASE}.{RESOURCE_LINK_TABLE} "
    f"LIMIT 10"
)

# This query will be rejected by Lake Formation with an Access Denied error
# because customer_email is a PII column excluded from the subscription grant.
# Uncomment and run to observe the expected error.
#
# BLOCKED_PII_QUERY = (
#     f"SELECT order_id, customer_email "
#     f"FROM {RESOURCE_LINK_DATABASE}.{RESOURCE_LINK_TABLE} "
#     f"LIMIT 1"
# )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _poll_query(client: "boto3.client", query_execution_id: str, poll_interval: float = 1.0) -> dict:
    """Poll Athena until the query finishes and return the execution details."""
    terminal_states = {"SUCCEEDED", "FAILED", "CANCELLED"}
    while True:
        response = client.get_query_execution(QueryExecutionId=query_execution_id)
        state = response["QueryExecution"]["Status"]["State"]
        if state in terminal_states:
            return response["QueryExecution"]
        time.sleep(poll_interval)


def _fetch_results(client: "boto3.client", query_execution_id: str) -> list[list[str]]:
    """Return all rows from an Athena query result (header row first)."""
    rows: list[list[str]] = []
    paginator = client.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=query_execution_id):
        for row in page["ResultSet"]["Rows"]:
            rows.append([col.get("VarCharValue", "") for col in row["Data"]])
    return rows


def _print_table(rows: list[list[str]]) -> None:
    """Print rows as a left-aligned text table."""
    if not rows:
        print("(no rows returned)")
        return
    col_widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    separator = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    print(separator)
    for idx, row in enumerate(rows):
        line = "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"
        print(line)
        if idx == 0:
            print(separator)
    print(separator)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not ATHENA_OUTPUT_BUCKET:
        raise SystemExit(
            "Error: ATHENA_OUTPUT_BUCKET environment variable is required.\n"
            "Example: export ATHENA_OUTPUT_BUCKET=s3://marketing-athena-results-123456789012/"
        )

    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    athena = session.client("athena")

    print(f"Running query against resource link: {RESOURCE_LINK_DATABASE}.{RESOURCE_LINK_TABLE}")
    print(f"SQL: {ALLOWED_QUERY}")
    print()

    start = athena.start_query_execution(
        QueryString=ALLOWED_QUERY,
        QueryExecutionContext={"Database": RESOURCE_LINK_DATABASE},
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_BUCKET},
        WorkGroup=ATHENA_WORKGROUP,
    )
    query_id = start["QueryExecutionId"]
    print(f"Query execution ID: {query_id}")

    execution = _poll_query(athena, query_id)
    state = execution["Status"]["State"]

    if state != "SUCCEEDED":
        reason = execution["Status"].get("StateChangeReason", "unknown")
        raise SystemExit(f"Query {state}: {reason}")

    rows = _fetch_results(athena, query_id)
    print(f"Query succeeded. Rows returned (including header): {len(rows)}")
    print()
    _print_table(rows)

    print()
    print("Note: querying customer_email would return an Access Denied error.")
    print("      Lake Formation column-level filtering enforces PII restrictions.")
    print("      Uncomment BLOCKED_PII_QUERY in this script to verify the behaviour.")


if __name__ == "__main__":
    main()
