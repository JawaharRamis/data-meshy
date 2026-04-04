##############################################################################
# domain-account/main.tf
#
# Central orchestration file for the domain-account module.
# Creates: KMS key, Glue catalog databases, Lake Formation registrations.
# S3 buckets -> s3.tf | IAM roles -> iam.tf
# LF Tag bindings -> lakeformation.tf | EventBridge -> eventbridge.tf
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

  # Mandatory tags applied to every resource in this module.
  mandatory_tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
    Domain      = var.domain
  }

  tags = merge(local.mandatory_tags, var.tags)
}

##############################################################################
# KMS — Domain Customer-Managed Key
# Key policy grants: GlueJobExecutionRole (encrypt/decrypt),
#   lakeformation service (decrypt for cross-account consumers),
#   DomainAdminRole (admin), MeshAdminRole in central account (break-glass).
# Consumers NEVER get direct KMS decrypt — only through Lake Formation.
##############################################################################

resource "aws_kms_key" "domain" {
  description             = "Domain CMK for ${var.domain} — data-meshy"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Root account admin — required so the key is not orphaned
      {
        Sid    = "RootAccountAdmin"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      # GlueJobExecutionRole — encrypt and decrypt for ETL operations
      {
        Sid    = "GlueJobEncryptDecrypt"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:role/GlueJobExecutionRole"
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = "*"
      },
      # Lake Formation service principal — decrypt on behalf of cross-account consumers
      {
        Sid    = "LakeFormationDecrypt"
        Effect = "Allow"
        Principal = {
          Service = "lakeformation.amazonaws.com"
        }
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = "*"
      },
      # DomainAdminRole — key administration within domain
      {
        Sid    = "DomainAdminKeyAdmin"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:role/DomainAdminRole"
        }
        Action = [
          "kms:Create*",
          "kms:Describe*",
          "kms:Enable*",
          "kms:List*",
          "kms:Put*",
          "kms:Update*",
          "kms:Revoke*",
          "kms:Disable*",
          "kms:Get*",
          "kms:Delete*",
          "kms:ScheduleKeyDeletion",
          "kms:CancelKeyDeletion"
        ]
        Resource = "*"
      },
      # MeshAdminRole in central account — break-glass only
      {
        Sid    = "CentralMeshAdminBreakGlass"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${var.central_account_id}:role/MeshAdminRole"
        }
        Action = [
          "kms:Describe*",
          "kms:List*",
          "kms:Get*"
        ]
        Resource = "*"
      }
    ]
  })

  tags = merge(local.tags, { Name = "mesh-${var.domain}" })
}

resource "aws_kms_alias" "domain" {
  name          = "alias/mesh-${var.domain}"
  target_key_id = aws_kms_key.domain.key_id
}

##############################################################################
# Glue Catalog Databases — raw, silver, gold
##############################################################################

resource "aws_glue_catalog_database" "raw" {
  name        = "${var.domain}_raw"
  description = "Raw (Bronze) layer for ${var.domain} domain — data-meshy"

  tags = local.tags
}

resource "aws_glue_catalog_database" "silver" {
  name        = "${var.domain}_silver"
  description = "Silver (Validated) layer for ${var.domain} domain — data-meshy"

  tags = local.tags
}

resource "aws_glue_catalog_database" "gold" {
  name        = "${var.domain}_gold"
  description = "Gold (Data Product) layer for ${var.domain} domain — data-meshy"

  tags = local.tags
}

##############################################################################
# Lake Formation — Register S3 locations as data lake locations
# The GlueJobExecutionRole is granted USE_LOCATION on each registered path.
##############################################################################

resource "aws_lakeformation_resource" "raw" {
  arn      = "arn:aws:s3:::${var.domain}-raw-${local.account_id}"
  role_arn = aws_iam_role.glue_job_execution.arn

  depends_on = [aws_s3_bucket.raw]
}

resource "aws_lakeformation_resource" "silver" {
  arn      = "arn:aws:s3:::${var.domain}-silver-${local.account_id}"
  role_arn = aws_iam_role.glue_job_execution.arn

  depends_on = [aws_s3_bucket.silver]
}

resource "aws_lakeformation_resource" "gold" {
  arn      = "arn:aws:s3:::${var.domain}-gold-${local.account_id}"
  role_arn = aws_iam_role.glue_job_execution.arn

  depends_on = [aws_s3_bucket.gold]
}
