##############################################################################
# domain-sales/main.tf
#
# Sales domain environment — instantiates all 3 modules:
#   1. domain-account  — S3, IAM, Glue Catalog, Lake Formation, EventBridge
#   2. data-product    — Iceberg table, DQ ruleset, Step Functions, catalog entry
#   3. monitoring      — CloudWatch alarms, log groups, AWS Budgets
#
# Cross-account provider configuration: assumes a role in the sales account
# from the orchestrating environment (central account or local dev).
##############################################################################

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.30.0"
    }
  }
}

##############################################################################
# Provider configuration
# For portfolio deployment: use the default provider with the target account
# credentials (via SSO profile, assumed role, or environment variables).
# For production multi-account: configure a provider alias assuming a role
# in the sales account from the central account.
##############################################################################

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "data-meshy"
      ManagedBy   = "terraform"
      Environment = var.environment
      Domain      = var.domain
    }
  }
}

##############################################################################
# Module: domain-account
##############################################################################

module "domain_account" {
  source = "../../modules/domain-account"

  domain                = var.domain
  environment           = var.environment
  aws_org_id            = var.aws_org_id
  central_account_id    = var.central_account_id
  central_event_bus_arn = var.central_event_bus_arn

  mesh_catalog_writer_role_arn = var.mesh_catalog_writer_role_arn

  tags = var.tags
}

##############################################################################
# Module: data-product (customer_orders)
##############################################################################

module "data_product" {
  source = "../../modules/data-product"

  domain        = var.domain
  product_name  = var.product_name
  environment   = var.environment
  aws_region    = var.aws_region

  # Consumed from domain-account module outputs
  raw_bucket_name              = module.domain_account.raw_bucket_name
  silver_bucket_name           = module.domain_account.silver_bucket_name
  gold_bucket_name             = module.domain_account.gold_bucket_name
  glue_catalog_db_raw          = module.domain_account.glue_catalog_db_raw
  glue_catalog_db_silver       = module.domain_account.glue_catalog_db_silver
  glue_catalog_db_gold         = module.domain_account.glue_catalog_db_gold
  glue_job_execution_role_arn  = module.domain_account.glue_job_execution_role_arn
  mesh_event_role_arn          = module.domain_account.mesh_event_role_arn
  domain_kms_key_arn           = module.domain_account.domain_kms_key_arn
  domain_event_bus_arn         = module.domain_account.domain_event_bus_arn

  # Consumed from governance module outputs (Stream 1)
  central_event_bus_arn        = var.central_event_bus_arn
  mesh_products_table_name     = "mesh-products"
  mesh_pipeline_locks_table_name = "mesh-pipeline-locks"
  mesh_audit_log_table_name    = "mesh-audit-log"

  # Product spec
  schema_columns      = var.schema_columns
  partition_keys      = var.partition_keys
  classification      = var.classification
  pii                 = var.pii
  dq_rules            = var.dq_rules
  owner               = var.owner
  description         = var.description
  schema_version      = var.schema_version
  sla_refresh_frequency = var.sla_refresh_frequency
  sla_availability    = var.sla_availability
  source_name         = var.source_name

  tags = var.tags
}

##############################################################################
# Module: monitoring
##############################################################################

module "monitoring" {
  source = "../../modules/monitoring"

  domain                = var.domain
  environment           = var.environment
  aws_region            = var.aws_region
  alarm_notification_arn = var.quality_alert_sns_topic_arn

  lambda_function_names = var.lambda_function_names
  dlq_queue_arns        = var.dlq_queue_arns

  state_machine_arn = module.data_product.state_machine_arn
  glue_job_names    = var.glue_job_names

  budget_thresholds      = var.budget_thresholds
  budget_email_recipients = var.budget_email_recipients

  tags = var.tags
}
