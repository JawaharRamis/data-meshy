##############################################################################
# domain-marketing/terraform.tfvars
#
# Variable values for the Marketing domain environment.
# Instantiates: domain-account, data-product, monitoring modules.
# Consumer-side: Athena workgroup + MarketingGlueConsumerRole for
# cross-account access to Sales gold tables via resource links.
##############################################################################

# ── Domain configuration ──────────────────────────────────────────────────────

domain      = "marketing"
environment = "dev"
aws_region  = "us-east-1"

# ── Cross-account references (from governance module) ────────────────────────
# These values must be updated with actual account IDs and ARNs after
# the central governance account is provisioned.

aws_org_id                   = "o-xxxxxxxxxx"         # Replace with actual Organization ID
central_account_id           = "000000000000"         # Replace with actual central account ID
central_event_bus_arn        = "arn:aws:events:us-east-1:000000000000:event-bus/mesh-central-bus"
mesh_catalog_writer_role_arn = "arn:aws:iam::000000000000:role/MeshCatalogWriterRole"
quality_alert_sns_topic_arn  = "arn:aws:sns:us-east-1:000000000000:mesh-quality-alerts"

# ── Consumer configuration ────────────────────────────────────────────────────
# Set after sales domain is provisioned and subscription is approved.

producer_account_id   = "111111111111"    # Replace with actual sales account ID
producer_gold_db_name = "gold_sales"

# ── Data Product: campaign_performance ───────────────────────────────────────

product_name        = "campaign_performance"
description         = "Marketing domain campaign performance data product — ad spend, impressions, clicks, and conversions by campaign."
owner               = "marketing-data-team@example.com"
schema_version      = 1
classification      = "internal"
pii                 = false

sla_refresh_frequency = "daily"
sla_availability      = "99.9"

source_name = "marketing_platform"

# Schema columns for campaign_performance Iceberg table
schema_columns = [
  { name = "campaign_id",       type = "bigint",        comment = "Unique campaign identifier (PK)" },
  { name = "campaign_name",     type = "string",        comment = "Campaign display name" },
  { name = "channel",           type = "string",        comment = "Marketing channel: paid_search/social/email/display" },
  { name = "campaign_date",     type = "date",          comment = "Date of campaign activity" },
  { name = "impressions",       type = "bigint",        comment = "Number of ad impressions served" },
  { name = "clicks",            type = "bigint",        comment = "Number of clicks" },
  { name = "spend_usd",         type = "decimal(12,2)", comment = "Total spend in USD" },
  { name = "conversions",       type = "int",           comment = "Number of attributed conversions" },
  { name = "revenue_usd",       type = "decimal(12,2)", comment = "Attributed revenue in USD" },
  { name = "ctr",               type = "decimal(8,4)",  comment = "Click-through rate (clicks/impressions)" },
  { name = "cpa_usd",           type = "decimal(10,2)", comment = "Cost per acquisition (spend/conversions)" },
  { name = "roas",              type = "decimal(8,4)",  comment = "Return on ad spend (revenue/spend)" },
  { name = "region",            type = "string",        comment = "Target region" },
  { name = "created_at",        type = "timestamp",     comment = "Record creation timestamp" },
  { name = "updated_at",        type = "timestamp",     comment = "Record last update timestamp" }
]

partition_keys = ["channel", "region"]

# Data Quality rules (DQDL) for campaign_performance
dq_rules = [
  "Rules = [",
  "  ColumnValues \"campaign_id\" > 0",
  "  ColumnValues \"impressions\" >= 0",
  "  ColumnValues \"clicks\" >= 0",
  "  ColumnValues \"spend_usd\" >= 0",
  "  ColumnValues \"conversions\" >= 0",
  "  ColumnValues \"ctr\" >= 0",
  "  ColumnValues \"ctr\" <= 1",
  "  Completeness \"campaign_id\" > 0.99",
  "  Completeness \"campaign_date\" > 0.99",
  "  Completeness \"channel\" > 0.99",
  "  Uniqueness \"campaign_id\" > 0.99",
  "  ColumnValues \"channel\" IN (\"paid_search\", \"social\", \"email\", \"display\")",
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

budget_thresholds       = [20, 50, 100]
budget_email_recipients = []

# ── Additional tags ───────────────────────────────────────────────────────────

tags = {
  Layer = "domain"
}
