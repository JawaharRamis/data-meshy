##############################################################################
# data-product/main.tf
#
# Orchestration file for the data-product module.
# Creates: Secrets Manager placeholder for source credentials.
# Iceberg table -> iceberg.tf | DQ ruleset -> quality.tf
# Step Functions SM -> step_functions.tf | Catalog entry -> catalog.tf
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

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  mandatory_tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
    Domain      = var.domain
  }

  tags = merge(local.mandatory_tags, var.tags)

  # Canonical product ID used as DynamoDB PK
  product_id = "${var.domain}#${var.product_name}"
}

##############################################################################
# Secrets Manager — Placeholder for source database credentials
# The ARN is referenced in product.yaml under lineage.sources[].credentials_secret_arn
# Rotation: 90 days
##############################################################################

resource "aws_secretsmanager_secret" "source_credentials" {
  name        = "${var.domain}/${var.product_name}/${var.source_name}-credentials"
  description = "Source database credentials for ${var.domain}/${var.product_name} Glue ingestion job."
  kms_key_id  = var.domain_kms_key_arn

  recovery_window_in_days = 30

  tags = merge(local.tags, { ProductName = var.product_name })
}

resource "aws_secretsmanager_secret_rotation" "source_credentials" {
  secret_id           = aws_secretsmanager_secret.source_credentials.id
  rotation_lambda_arn = null # Placeholder: populate with rotation Lambda ARN in production

  rotation_rules {
    automatically_after_days = 90
  }

  # lifecycle ignore since rotation Lambda ARN is not wired in Phase 1
  lifecycle {
    ignore_changes = [rotation_lambda_arn]
  }
}
