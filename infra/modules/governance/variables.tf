variable "environment" {
  description = "Deployment environment label (e.g. portfolio, staging, prod)."
  type        = string
}

variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "domain_account_ids" {
  description = "List of domain AWS account IDs allowed to put events on the central EventBridge bus."
  type        = list(string)
  default     = []
}

variable "github_org" {
  description = "GitHub organisation or user name that owns the repository."
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "GitHub repository name (without the org prefix)."
  type        = string
  default     = "data-meshy"
}

variable "alert_email" {
  description = "Email address for SNS alert subscriptions (optional — leave blank to skip)."
  type        = string
  default     = ""
}

# ── DataZone variables (Phase 2) ─────────────────────────────────────────────

variable "datazone_domain_name" {
  description = "Name of the AWS DataZone domain for the mesh catalog."
  type        = string
  default     = "data-meshy"
}

variable "datazone_domain_description" {
  description = "Description of the AWS DataZone domain."
  type        = string
  default     = "Data Meshy — self-serve data mesh platform. Domain teams publish data products; consumers discover and subscribe via DataZone."
}

variable "datazone_sso_type" {
  description = "Single sign-on type for DataZone (IAM_IDC for IAM Identity Center, DISABLED to skip SSO)."
  type        = string
  default     = "DISABLED"
}

# ── Subscription API variables (Phase 2) ─────────────────────────────────────

variable "subscription_provisioner_lambda_arn" {
  description = "ARN of the subscription provisioner Lambda (Stream 2). Empty string until Stream 2 merges."
  type        = string
  default     = ""
}

variable "subscription_compensator_lambda_arn" {
  description = "ARN of the subscription compensator Lambda (Stream 2). Empty string until Stream 2 merges."
  type        = string
  default     = ""
}

variable "subscription_approver_lambda_arn" {
  description = "ARN of the subscription approver Lambda (Stream 2). Empty string until Stream 2 merges."
  type        = string
  default     = ""
}

variable "subscription_lister_lambda_arn" {
  description = "ARN of the subscription lister Lambda (Stream 2). Empty string until Stream 2 merges."
  type        = string
  default     = ""
}

locals {
  mandatory_tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}
