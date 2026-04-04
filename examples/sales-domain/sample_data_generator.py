"""Sample data generator for the customer_orders data product.

Generates synthetic CSV data simulating customer orders and optionally
uploads to the raw S3 bucket.

Uses the faker library for realistic data generation.
Deterministic output when --seed is provided.

Usage:
  python sample_data_generator.py --seed 42 --rows 10000 --output orders.csv
  python sample_data_generator.py --seed 42 --rows 10000 --upload --bucket sales-raw-123456789012
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from faker import Faker


def generate_orders(
    num_rows: int = 10000,
    seed: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """Generate synthetic customer order data.

    Args:
        num_rows: Number of rows to generate.
        seed: Random seed for deterministic output.
        start_date: Earliest order date (default: 90 days ago).
        end_date: Latest order date (default: today).

    Returns:
        List of dicts with keys: order_id, customer_email, order_total, order_date
    """
    fake = Faker()
    if seed is not None:
        Faker.seed(seed)
        fake.seed_instance(seed)

    start_date = start_date or (date.today() - timedelta(days=90))
    end_date = end_date or date.today()

    orders = []
    for i in range(num_rows):
        order_id = f"ORD-{seed or 0}-{i + 1:06d}"
        customer_email = fake.email()
        # Generate order_total between $5.00 and $999.99
        raw_total = fake.pyfloat(min_value=5.0, max_value=999.99, right_digits=2)
        order_total = str(Decimal(str(raw_total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        # Random date in range
        order_date = fake.date_between(start_date=start_date, end_date=end_date)

        orders.append({
            "order_id": order_id,
            "customer_email": customer_email,
            "order_total": order_total,
            "order_date": order_date.isoformat(),
        })

    return orders


def write_csv(orders: list[dict], output_path: str) -> None:
    """Write orders to a CSV file.

    Args:
        orders: List of order dicts.
        output_path: Path to the output CSV file.
    """
    fieldnames = ["order_id", "customer_email", "order_total", "order_date"]
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(orders)

    print(f"Wrote {len(orders)} rows to {output_path}")


def upload_to_s3(orders: list[dict], bucket: str, key: str = "customer_orders/data.csv") -> None:
    """Upload orders CSV to an S3 bucket.

    Args:
        orders: List of order dicts.
        bucket: S3 bucket name.
        key: S3 object key.
    """
    import io
    import boto3

    fieldnames = ["order_id", "customer_email", "order_total", "order_date"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(orders)

    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue().encode("utf-8"))
    print(f"Uploaded {len(orders)} rows to s3://{bucket}/{key}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic customer_orders CSV data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate 10,000 rows to stdout with seed 42
  python sample_data_generator.py --seed 42 --rows 10000 --output orders.csv

  # Generate and upload to S3
  python sample_data_generator.py --seed 42 --rows 10000 --upload --bucket sales-raw-123456789012
        """,
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic output (default: 42).",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=10000,
        help="Number of rows to generate (default: 10000).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV file path. If not provided, prints to stdout.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the generated CSV to S3.",
    )
    parser.add_argument(
        "--bucket",
        type=str,
        default=None,
        help="S3 bucket name for upload.",
    )
    parser.add_argument(
        "--key",
        type=str,
        default="customer_orders/data.csv",
        help="S3 object key for upload (default: customer_orders/data.csv).",
    )

    args = parser.parse_args()

    # Generate data
    orders = generate_orders(num_rows=args.rows, seed=args.seed)

    # Output
    if args.output:
        write_csv(orders, args.output)

    if args.upload:
        if not args.bucket:
            print("Error: --bucket is required when --upload is specified.", file=sys.stderr)
            sys.exit(1)
        upload_to_s3(orders, args.bucket, args.key)

    if not args.output and not args.upload:
        # Print to stdout as CSV
        fieldnames = ["order_id", "customer_email", "order_total", "order_date"]
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(orders)


if __name__ == "__main__":
    main()
