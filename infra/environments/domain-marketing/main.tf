##############################################################################
# domain-marketing/main.tf
#
# Marketing domain environment — instantiates all 3 core modules plus
# consumer-side resources for cross-account data access:
#   1. domain-account  — S3, IAM, Glue Catalog, Lake Formation, EventBridge
#   2. data-product    — Iceberg table, DQ ruleset, Step Functions, catalog entry
#   3. monitoring      — CloudWatch alarms, log groups, AWS Budgets
#   4. Consumer resources:
#      - MarketingGlueConsumerRole (read on resource link DBs from Sales)
#      - Athena workgroup with SSE-KMS output to marketing S3
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
# in the marketing account from the central account.
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

locals {
  account_id = data.aws_caller_identity.current.account_id
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
# Module: data-product (campaign_performance)
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
  central_event_bus_arn          = var.central_event_bus_arn
  mesh_products_table_name       = "mesh-products"
  mesh_pipeline_locks_table_name = "mesh-pipeline-locks"
  mesh_audit_log_table_name      = "mesh-audit-log"

  # Product spec
  schema_columns        = var.schema_columns
  partition_keys        = var.partition_keys
  classification        = var.classification
  pii                   = var.pii
  dq_rules              = var.dq_rules
  owner                 = var.owner
  description           = var.description
  schema_version        = var.schema_version
  sla_refresh_frequency = var.sla_refresh_frequency
  sla_availability      = var.sla_availability
  source_name           = var.source_name

  tags = var.tags
}

##############################################################################
# Module: monitoring
##############################################################################

module "monitoring" {
  source = "../../modules/monitoring"

  domain                 = var.domain
  environment            = var.environment
  aws_region             = var.aws_region
  alarm_notification_arn = var.quality_alert_sns_topic_arn

  lambda_function_names = var.lambda_function_names
  dlq_queue_arns        = var.dlq_queue_arns

  state_machine_arn = module.data_product.state_machine_arn
  glue_job_names    = var.glue_job_names

  budget_thresholds       = var.budget_thresholds
  budget_email_recipients = var.budget_email_recipients

  tags = var.tags
}

##############################################################################
# Consumer: MarketingGlueConsumerRole
# Allows the marketing Glue jobs and Athena to read resource link databases
# (cross-account shared tables from the Sales domain gold layer).
# LF permissions are granted at approval time by the subscription Lambda
# (MeshLFGrantorRole in central account via boto3 batch_grant_permissions).
##############################################################################

resource "aws_iam_role" "marketing_glue_consumer" {
  name        = "MarketingGlueConsumerRole"
  description = "Allows Marketing Glue jobs and Athena to query resource-linked Sales gold tables via Lake Formation."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = [
            "glue.amazonaws.com",
            "athena.amazonaws.com"
          ]
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(var.tags, {
    Purpose = "consumer-role"
    Domain  = var.domain
  })
}

resource "aws_iam_role_policy" "marketing_glue_consumer_policy" {
  name = "MarketingGlueConsumerPolicy"
  role = aws_iam_role.marketing_glue_consumer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "GlueCatalogReadForResourceLinks"
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetPartition",
          "glue:GetPartitions",
          "glue:BatchGetPartition"
        ]
        # Restricts to resource link databases in this account only.
        # Actual cross-account table access is controlled by LF grants.
        Resource = [
          "arn:aws:glue:${var.aws_region}:${local.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${local.account_id}:database/rl_*",
          "arn:aws:glue:${var.aws_region}:${local.account_id}:table/rl_*/*"
        ]
      },
      {
        Sid    = "LakeFormationGetWorkUnits"
        Effect = "Allow"
        Action = [
          "lakeformation:GetDataAccess",
          "lakeformation:StartQueryPlanning",
          "lakeformation:GetQueryState",
          "lakeformation:GetWorkUnits",
          "lakeformation:GetWorkUnitResults",
          "lakeformation:StartTransaction",
          "lakeformation:CommitTransaction",
          "lakeformation:CancelTransaction",
          "lakeformation:ExtendTransaction"
        ]
        Resource = "*"
      },
      {
        Sid    = "AthenaQueryExecution"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
          "athena:GetWorkGroup",
          "athena:ListWorkGroups"
        ]
        Resource = [
          "arn:aws:athena:${var.aws_region}:${local.account_id}:workgroup/marketing-consumer"
        ]
      },
      {
        Sid    = "S3AthenaResultsBucket"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:GetBucketLocation",
          "s3:ListBucket",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts"
        ]
        Resource = [
          "${module.domain_account.gold_bucket_arn}/athena-results/*",
          module.domain_account.gold_bucket_arn
        ]
      },
      {
        Sid    = "KMSDecryptForDomainBuckets"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = module.domain_account.domain_kms_key_arn
      },
      {
        Sid    = "GlueBasicExecution"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/glue/*"
      }
    ]
  })
}

##############################################################################
# Consumer: Athena Workgroup for Marketing analysts
# S3 output → gold bucket athena-results prefix, SSE-KMS with domain key.
##############################################################################

resource "aws_athena_workgroup" "marketing_consumer" {
  name        = "marketing-consumer"
  description = "Athena workgroup for Marketing analysts querying cross-account Sales gold tables via resource links."
  state       = "ENABLED"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 10737418240 # 10 GB safety limit

    result_configuration {
      output_location = "s3://${module.domain_account.gold_bucket_name}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = module.domain_account.domain_kms_key_arn
      }
    }
  }

  tags = merge(var.tags, {
    Purpose = "consumer-athena"
    Domain  = var.domain
  })
}

##############################################################################
# Outputs
##############################################################################

output "domain_name" {
  description = "Marketing domain name."
  value       = var.domain
}

output "raw_bucket_name" {
  description = "Raw (bronze) S3 bucket name."
  value       = module.domain_account.raw_bucket_name
}

output "silver_bucket_name" {
  description = "Silver S3 bucket name."
  value       = module.domain_account.silver_bucket_name
}

output "gold_bucket_name" {
  description = "Gold S3 bucket name (shareable data product layer)."
  value       = module.domain_account.gold_bucket_name
}

output "domain_kms_key_arn" {
  description = "ARN of the marketing domain KMS key."
  value       = module.domain_account.domain_kms_key_arn
}

output "glue_catalog_db_gold" {
  description = "Glue catalog database name for the gold layer."
  value       = module.domain_account.glue_catalog_db_gold
}

output "marketing_glue_consumer_role_arn" {
  description = "ARN of MarketingGlueConsumerRole (read access on resource link DBs from Sales)."
  value       = aws_iam_role.marketing_glue_consumer.arn
}

output "athena_workgroup_name" {
  description = "Athena workgroup name for Marketing consumer queries."
  value       = aws_athena_workgroup.marketing_consumer.name
}

output "state_machine_arn" {
  description = "ARN of the data product pipeline state machine."
  value       = module.data_product.state_machine_arn
}
