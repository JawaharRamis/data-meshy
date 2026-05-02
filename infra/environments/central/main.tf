terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "data-meshy"
      ManagedBy   = "terraform"
      Environment = var.environment
    }
  }
}

###############################################################################
# Variables local to the central environment
###############################################################################
variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment label."
  type        = string
  default     = "portfolio"
}

variable "org_id" {
  description = "AWS Organization ID."
  type        = string
}

variable "domain_account_ids" {
  description = "List of domain account IDs allowed to publish events to the central bus."
  type        = list(string)
  default     = []
}

variable "github_org" {
  description = "GitHub organisation or user name."
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "GitHub repository name."
  type        = string
  default     = "data-meshy"
}

variable "alert_email" {
  description = "Email for SNS alert subscriptions (optional)."
  type        = string
  default     = ""
}

# ── Pre-Phase 3: Multi-repo OIDC ──────────────────────────────────────────────

variable "domain_repo_paths" {
  description = "OIDC sub conditions for domain GitHub repos (e.g. 'repo:org/data-meshy-sales:*'). Append-only — adding a domain never removes existing access."
  type        = list(string)
  default     = []
}

# ── Phase 2: Subscription Lambda ARN placeholders ────────────────────────────
# Set to "" until Stream 2 merges. After merge, populate with actual ARNs.

variable "subscription_provisioner_lambda_arn" {
  description = "ARN of the subscription provisioner Lambda (Stream 2). Empty until Stream 2 merges."
  type        = string
  default     = ""
}

variable "subscription_compensator_lambda_arn" {
  description = "ARN of the subscription compensator Lambda (Stream 2). Empty until Stream 2 merges."
  type        = string
  default     = ""
}

variable "subscription_approver_lambda_arn" {
  description = "ARN of the subscription approver Lambda (Stream 2). Empty until Stream 2 merges."
  type        = string
  default     = ""
}

variable "subscription_lister_lambda_arn" {
  description = "ARN of the subscription lister Lambda (Stream 2). Empty until Stream 2 merges."
  type        = string
  default     = ""
}

# ── Phase 2: DataZone variables ───────────────────────────────────────────────

variable "datazone_domain_name" {
  description = "Name of the AWS DataZone domain."
  type        = string
  default     = "data-meshy"
}

variable "datazone_sso_type" {
  description = "SSO type for DataZone (IAM_IDC or DISABLED)."
  type        = string
  default     = "DISABLED"
}

###############################################################################
# Governance module instantiation
###############################################################################
module "governance" {
  source = "../../modules/governance"

  environment        = var.environment
  aws_region         = var.aws_region
  domain_account_ids = var.domain_account_ids
  github_org         = var.github_org
  github_repo        = var.github_repo
  alert_email        = var.alert_email

  # Pre-Phase 3: domain repo OIDC paths
  domain_repo_paths = var.domain_repo_paths

  # Phase 2: DataZone
  datazone_domain_name = var.datazone_domain_name
  datazone_sso_type    = var.datazone_sso_type

  # Phase 2: Subscription API Lambda ARNs (placeholders until Stream 2 merges)
  subscription_provisioner_lambda_arn = var.subscription_provisioner_lambda_arn
  subscription_compensator_lambda_arn = var.subscription_compensator_lambda_arn
  subscription_approver_lambda_arn    = var.subscription_approver_lambda_arn
  subscription_lister_lambda_arn      = var.subscription_lister_lambda_arn
}

###############################################################################
# Outputs (re-export module outputs for use by other Terraform runs / CI)
###############################################################################
output "central_event_bus_arn" {
  value = module.governance.central_event_bus_arn
}

output "mesh_products_table_name" {
  value = module.governance.mesh_products_table_name
}

output "mesh_domains_table_name" {
  value = module.governance.mesh_domains_table_name
}

output "mesh_subscriptions_table_name" {
  value = module.governance.mesh_subscriptions_table_name
}

output "mesh_quality_scores_table_name" {
  value = module.governance.mesh_quality_scores_table_name
}

output "mesh_audit_log_table_name" {
  value = module.governance.mesh_audit_log_table_name
}

output "mesh_event_dedup_table_name" {
  value = module.governance.mesh_event_dedup_table_name
}

output "mesh_pipeline_locks_table_name" {
  value = module.governance.mesh_pipeline_locks_table_name
}

output "mesh_lf_grantor_role_arn" {
  value = module.governance.mesh_lf_grantor_role_arn
}

output "mesh_catalog_writer_role_arn" {
  value = module.governance.mesh_catalog_writer_role_arn
}

output "mesh_audit_writer_role_arn" {
  value = module.governance.mesh_audit_writer_role_arn
}

output "quality_alert_sns_topic_arn" {
  value = module.governance.quality_alert_sns_topic_arn
}

output "pipeline_failure_sns_topic_arn" {
  value = module.governance.pipeline_failure_sns_topic_arn
}

output "catalog_dlq_arn" {
  value = module.governance.catalog_dlq_arn
}

output "central_kms_key_arn" {
  value = module.governance.central_kms_key_arn
}

output "terraform_plan_role_arn" {
  value = module.governance.terraform_plan_role_arn
}

output "terraform_apply_role_arn" {
  value = module.governance.terraform_apply_role_arn
}

output "mesh_kms_grantor_role_arn" {
  value = module.governance.mesh_kms_grantor_role_arn
}

output "api_endpoint_url" {
  description = "Base URL of the mesh governance API (Stream 3 CLI uses this)."
  value       = module.governance.api_endpoint_url
}

###############################################################################
# Phase 2: DataZone domain
# Associates the central Glue catalog (registered in LF in Phase 1) with
# DataZone so product owners can approve subscriptions via the DataZone web UI.
#
# Note: DataZone requires the Glue catalog to be registered in Lake Formation
# before associating. This was completed in Phase 1 (domain-account module).
#
# The domain_execution_role is a service-linked role that DataZone assumes
# to interact with the Glue catalog and Lake Formation.
###############################################################################

data "aws_caller_identity" "central" {}

resource "aws_iam_role" "datazone_domain_execution" {
  name        = "DataZoneDomainExecutionRole"
  description = "Service role assumed by DataZone to manage catalog assets and LF permissions."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "datazone.amazonaws.com"
        }
        Action = [
          "sts:AssumeRole",
          "sts:TagSession"
        ]
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.central.account_id
          }
        }
      }
    ]
  })

  tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy_attachment" "datazone_execution_managed" {
  role       = aws_iam_role.datazone_domain_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonDataZoneFullAccess"
}

resource "aws_datazone_domain" "mesh" {
  name        = var.datazone_domain_name
  description = "Data Meshy — self-serve data mesh platform. Domain teams publish products; consumers discover and subscribe via the DataZone web UI."

  domain_execution_role = aws_iam_role.datazone_domain_execution.arn

  # SSO configuration — default DISABLED for portfolio/dev deployments.
  # Set datazone_sso_type = "IAM_IDC" in terraform.tfvars to enable
  # IAM Identity Center integration for non-technical product owner approval.
  single_sign_on {
    type = var.datazone_sso_type
  }

  tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
    Purpose     = "data-catalog-and-governance"
  }
}

output "datazone_domain_id" {
  description = "ID of the DataZone domain (consumed by Stream 4 examples and CLI)."
  value       = aws_datazone_domain.mesh.id
}

output "datazone_domain_arn" {
  description = "ARN of the DataZone domain."
  value       = aws_datazone_domain.mesh.arn
}

output "datazone_portal_url" {
  description = "DataZone web portal URL for product owner self-serve approval."
  value       = aws_datazone_domain.mesh.portal_url
}

###############################################################################
# Phase 2: Subscription module instantiation
# Lambda ARN variables default to "" until Stream 2 merges.
# The SFN is created with placeholder Lambda ARNs — it will work structurally
# but execution requires real Lambdas from Stream 2.
###############################################################################

module "subscription" {
  source = "../../modules/subscription"

  environment        = var.environment
  aws_region         = var.aws_region
  central_account_id = data.aws_caller_identity.central.account_id

  central_event_bus_arn = module.governance.central_event_bus_arn

  # Lambda ARNs — populated after Stream 2 merges
  provisioner_lambda_arn = var.subscription_provisioner_lambda_arn
  compensator_lambda_arn = var.subscription_compensator_lambda_arn

  # IAM roles from governance module (shared contract)
  lf_grantor_role_arn  = module.governance.mesh_lf_grantor_role_arn
  kms_grantor_role_arn = module.governance.mesh_kms_grantor_role_arn

  # DynamoDB (already exists from Phase 1)
  subscriptions_table_name = module.governance.mesh_subscriptions_table_name
  subscriptions_table_arn  = module.governance.mesh_subscriptions_table_arn
  central_kms_key_arn      = module.governance.central_kms_key_arn
}

output "subscription_sfn_arn" {
  description = "ARN of the subscription-provisioner SFN (shared contract for Stream 2)."
  value       = module.subscription.sfn_arn
}
