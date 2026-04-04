##############################################################################
# domain-sales/terraform.tfvars
#
# Variable values for the Sales domain environment.
# Instantiates: domain-account, data-product, monitoring modules.
##############################################################################

# ── Domain configuration ──────────────────────────────────────────────────────

domain      = "sales"
environment = "dev"
aws_region  = "us-east-1"

# ── Cross-account references (from governance module / Stream 1) ─────────────
# These values must be updated with actual account IDs and ARNs after
# the central governance account is provisioned (Stream 1).

aws_org_id                = "o-xxxxxxxxxx"    # Replace with actual Organization ID
central_account_id        = "000000000000"    # Replace with actual central account ID
central_event_bus_arn     = "arn:aws:events:us-east-1:000000000000:event-bus/mesh-central-bus"
mesh_catalog_writer_role_arn = "arn:aws:iam::000000000000:role/MeshCatalogWriterRole"
quality_alert_sns_topic_arn  = "arn:aws:sns:us-east-1:000000000000:mesh-quality-alerts"

# ── Data Product: customer_orders ─────────────────────────────────────────────

product_name        = "customer_orders"
description         = "Sales domain customer orders data product — order line items with product and customer details."
owner               = "sales-data-team@example.com"
schema_version      = 1
classification      = "internal"
pii                 = true

sla_refresh_frequency = "daily"
sla_availability      = "99.9"

source_name = "erp_orders"

# Schema columns for customer_orders Iceberg table
schema_columns = [
  { name = "order_id",          type = "bigint",        comment = "Unique order identifier (PK)" },
  { name = "order_line_id",     type = "int",           comment = "Line item number within the order" },
  { name = "customer_id",       type = "bigint",        comment = "Customer identifier (FK)" },
  { name = "product_id",        type = "bigint",        comment = "Product identifier (FK)" },
  { name = "order_date",        type = "timestamp",     comment = "Date and time the order was placed" },
  { name = "ship_date",         type = "timestamp",     comment = "Date the order was shipped" },
  { name = "quantity",          type = "int",           comment = "Quantity ordered" },
  { name = "unit_price",        type = "decimal(10,2)", comment = "Price per unit at time of sale" },
  { name = "discount_pct",      type = "decimal(5,2)",  comment = "Discount percentage applied" },
  { name = "total_amount",      type = "decimal(12,2)", comment = "Line total after discount" },
  { name = "currency_code",     type = "string",        comment = "ISO 4217 currency code" },
  { name = "order_status",      type = "string",        comment = "Order status: pending/processed/shipped/delivered/cancelled" },
  { name = "payment_method",    type = "string",        comment = "Payment method: card/bank_transfer/invoice" },
  { name = "shipping_address",  type = "string",        comment = "Shipping address (PII)" },
  { name = "customer_email",    type = "string",        comment = "Customer email address (PII)" },
  { name = "region",            type = "string",        comment = "Sales region" },
  { name = "sales_rep_id",      type = "bigint",        comment = "Sales representative identifier" },
  { name = "created_at",        type = "timestamp",     comment = "Record creation timestamp" },
  { name = "updated_at",        type = "timestamp",     comment = "Record last update timestamp" }
]

partition_keys = ["region"]

# Data Quality rules (DQDL) for customer_orders
dq_rules = [
  "Rules = [",
  "  ColumnValues \"order_id\" > 0",
  "  ColumnValues \"customer_id\" > 0",
  "  ColumnValues \"product_id\" > 0",
  "  ColumnValues \"quantity\" > 0",
  "  ColumnValues \"unit_price\" >= 0",
  "  ColumnValues \"total_amount\" >= 0",
  "  ColumnValues \"discount_pct\" >= 0",
  "  ColumnValues \"discount_pct\" <= 100",
  "  Completeness \"order_id\" > 0.99",
  "  Completeness \"customer_id\" > 0.99",
  "  Completeness \"order_date\" > 0.99",
  "  Completeness \"total_amount\" > 0.99",
  "  Uniqueness \"order_id\" > 0.99",
  "  IsUnique \"order_line_id\" AND \"order_id\"",
  "  ColumnLength \"currency_code\" = 3",
  "  ColumnValues \"order_status\" IN (\"pending\", \"processed\", \"shipped\", \"delivered\", \"cancelled\")",
  "]"
]

# ── Monitoring ────────────────────────────────────────────────────────────────

lambda_function_names = []

dlq_queue_arns = {}

glue_job_names = [
  "raw_ingestion",
  "silver_transform",
  "gold_aggregate",
  "iceberg_maintenance"
]

budget_thresholds    = [20, 50, 100]
budget_email_recipients = []

# ── Additional tags ───────────────────────────────────────────────────────────

tags = {
  Layer = "domain"
}
