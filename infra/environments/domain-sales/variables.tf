##############################################################################
# domain-sales/variables.tf
#
# Variable declarations for the Sales domain environment.
# Values are set in terraform.tfvars.
##############################################################################

variable "domain" {
  description = "Domain name."
  type        = string
}

variable "environment" {
  description = "Deployment environment."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

# ── Cross-account references ─────────────────────────────────────────────────

variable "aws_org_id" {
  description = "AWS Organization ID."
  type        = string
}

variable "central_account_id" {
  description = "Account ID of the central governance account."
  type        = string
}

variable "central_event_bus_arn" {
  description = "ARN of the central EventBridge bus."
  type        = string
}

variable "mesh_catalog_writer_role_arn" {
  description = "ARN of MeshCatalogWriterRole in central account."
  type        = string
}

variable "quality_alert_sns_topic_arn" {
  description = "ARN of the quality alert SNS topic in central account."
  type        = string
}

# ── Data Product: customer_orders ─────────────────────────────────────────────

variable "product_name" {
  description = "Data product name."
  type        = string
}

variable "description" {
  description = "Product description."
  type        = string
  default     = ""
}

variable "owner" {
  description = "Product owner email or team."
  type        = string
}

variable "schema_version" {
  description = "Schema version (monotonically increasing)."
  type        = number
  default     = 1
}

variable "classification" {
  description = "LF-Tag classification."
  type        = string
  default     = "internal"
}

variable "pii" {
  description = "Whether the product contains PII."
  type        = bool
  default     = false
}

variable "sla_refresh_frequency" {
  description = "SLA refresh frequency."
  type        = string
  default     = "daily"
}

variable "sla_availability" {
  description = "SLA availability target."
  type        = string
  default     = "99.9"
}

variable "source_name" {
  description = "Source system identifier for Secrets Manager."
  type        = string
  default     = "default"
}

variable "schema_columns" {
  description = "Iceberg table column definitions."
  type = list(object({
    name    = string
    type    = string
    comment = optional(string, "")
  }))
}

variable "partition_keys" {
  description = "Partition key column names."
  type        = list(string)
  default     = []
}

variable "dq_rules" {
  description = "DQDL quality rule strings."
  type        = list(string)
  default     = []
}

# ── Monitoring ────────────────────────────────────────────────────────────────

variable "lambda_function_names" {
  description = "Lambda function names to monitor."
  type        = list(string)
  default     = []
}

variable "dlq_queue_arns" {
  description = "DLQ queue name -> ARN mapping."
  type        = map(string)
  default     = {}
}

variable "glue_job_names" {
  description = "Glue job names to monitor."
  type        = list(string)
  default     = []
}

variable "budget_thresholds" {
  description = "Budget threshold amounts (USD)."
  type        = list(number)
  default     = [20, 50, 100]
}

variable "budget_email_recipients" {
  description = "Email addresses for budget alerts."
  type        = list(string)
  default     = []
}

# ── Tags ──────────────────────────────────────────────────────────────────────

variable "tags" {
  description = "Additional tags."
  type        = map(string)
  default     = {}
}
