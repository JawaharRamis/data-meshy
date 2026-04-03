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

locals {
  mandatory_tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}
