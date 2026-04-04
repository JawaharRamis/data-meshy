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
