variable "domain" {
  description = "Domain name (e.g. sales, marketing). Used in all resource naming."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]*$", var.domain))
    error_message = "Domain must be lowercase alphanumeric with hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment (e.g. dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region for domain resources."
  type        = string
  default     = "us-east-1"
}

variable "aws_org_id" {
  description = "AWS Organization ID (o-xxxxxxxxxx). Used in bucket policy OrgID condition."
  type        = string
}

variable "central_account_id" {
  description = "Account ID of the central governance account. Used in KMS key policy for MeshAdminRole break-glass access."
  type        = string
}

variable "central_event_bus_arn" {
  description = "ARN of the central EventBridge bus (from Stream 1 governance module). Events with source=datameshy are forwarded here."
  type        = string
}

variable "mesh_catalog_writer_role_arn" {
  description = "ARN of MeshCatalogWriterRole in the central account (from Stream 1). Referenced in domain trust policy."
  type        = string
}

variable "sso_identity_store_id" {
  description = "IAM Identity Center Identity Store ID, used for SSO trust policies."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags to merge with the mandatory tag set."
  type        = map(string)
  default     = {}
}
