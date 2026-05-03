variable "domain" {
  description = "Domain name (e.g. sales). Must match the domain-account module domain."
  type        = string
}

variable "product_name" {
  description = "Data product name in snake_case (e.g. customer_orders)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]*$", var.product_name))
    error_message = "product_name must be lowercase snake_case starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

# ── Consumed from domain-account module outputs ──────────────────────────────

variable "raw_bucket_name" {
  description = "S3 bucket name for the raw layer (from domain-account output)."
  type        = string
}

variable "silver_bucket_name" {
  description = "S3 bucket name for the silver layer (from domain-account output)."
  type        = string
}

variable "gold_bucket_name" {
  description = "S3 bucket name for the gold layer (from domain-account output)."
  type        = string
}

variable "glue_catalog_db_raw" {
  description = "Glue catalog DB name for the raw layer (from domain-account output)."
  type        = string
}

variable "glue_catalog_db_silver" {
  description = "Glue catalog DB name for the silver layer (from domain-account output)."
  type        = string
}

variable "glue_catalog_db_gold" {
  description = "Glue catalog DB name for the gold layer (from domain-account output)."
  type        = string
}

variable "glue_job_execution_role_arn" {
  description = "ARN of GlueJobExecutionRole (from domain-account output)."
  type        = string
}

variable "mesh_event_role_arn" {
  description = "ARN of MeshEventRole — Step Functions execution role (from domain-account output)."
  type        = string
}

variable "domain_kms_key_arn" {
  description = "ARN of domain KMS CMK (from domain-account output)."
  type        = string
}

variable "domain_event_bus_arn" {
  description = "ARN of domain EventBridge bus (from domain-account output)."
  type        = string
}

# ── Consumed from governance module outputs (Stream 1) ───────────────────────

variable "mesh_products_table_name" {
  description = "DynamoDB table name for mesh-products catalog (from governance module output)."
  type        = string
  default     = "mesh-products"
}

variable "mesh_pipeline_locks_table_name" {
  description = "DynamoDB table name for pipeline locks (from governance module output)."
  type        = string
  default     = "mesh-pipeline-locks"
}

variable "mesh_audit_log_table_name" {
  description = "DynamoDB table name for audit log (from governance module output)."
  type        = string
  default     = "mesh-audit-log"
}

variable "central_event_bus_arn" {
  description = "ARN of the central EventBridge bus (from governance module output)."
  type        = string
}

# ── Iceberg table schema (from product.yaml) ─────────────────────────────────

variable "schema_columns" {
  description = "List of column definitions for the Iceberg table. Each element: {name, type, comment}."
  type = list(object({
    name    = string
    type    = string
    comment = optional(string, "")
  }))
}

variable "partition_keys" {
  description = "List of partition key column names (subset of schema_columns)."
  type        = list(string)
  default     = []
}

variable "classification" {
  description = "LF-Tag classification value (public/internal/confidential/restricted)."
  type        = string
  default     = "internal"

  validation {
    condition     = contains(["public", "internal", "confidential", "restricted"], var.classification)
    error_message = "classification must be one of: public, internal, confidential, restricted."
  }
}

variable "pii" {
  description = "Whether this data product contains PII data. Used for LF-Tag pii=true/false."
  type        = bool
  default     = false
}

# ── Data quality (from product.yaml quality.rules) ───────────────────────────

variable "dq_rules" {
  description = "List of DQDL rule strings from product.yaml quality.rules section."
  type        = list(string)
  default     = []
}

# ── Product metadata (from product.yaml) ─────────────────────────────────────

variable "owner" {
  description = "Data product owner email or team name."
  type        = string
}

variable "description" {
  description = "Short description of the data product."
  type        = string
  default     = ""
}

variable "schema_version" {
  description = "Schema version integer, monotonically increasing."
  type        = number
  default     = 1
}

variable "sla_refresh_frequency" {
  description = "SLA refresh frequency string (e.g. daily, hourly, weekly)."
  type        = string
  default     = "daily"
}

variable "sla_availability" {
  description = "SLA availability target as a string (e.g. 99.9)."
  type        = string
  default     = "99.9"
}

# ── Step Functions ASL path ───────────────────────────────────────────────────

variable "medallion_pipeline_asl_path" {
  description = "Absolute or relative path to templates/step_functions/medallion_pipeline.asl.json (written by Stream 3). Must be set by the calling environment."
  type        = string
  default     = ""
}

# ── Source credentials secret ─────────────────────────────────────────────────

variable "source_name" {
  description = "Identifier for the source system (used in Secrets Manager secret name)."
  type        = string
  default     = "default"
}

variable "tags" {
  description = "Additional tags merged with the mandatory set."
  type        = map(string)
  default     = {}
}

# ── Lifecycle variables (Phase 3 Stream 2) ────────────────────────────────────

variable "product_deprecation_lambda_arn" {
  description = "ARN of the product_deprecation Lambda (handles ProductDeprecated events). Leave empty to skip EventBridge target."
  type        = string
  default     = ""
}

variable "retirement_lambda_arn" {
  description = "ARN of the retirement Lambda (triggered by EventBridge Scheduler at sunset_date). Required — Terraform plan fails if not set, preventing wildcard Lambda permissions."
  type        = string
}

variable "rollback_glue_script_s3_path" {
  description = "S3 path to the iceberg_rollback.py Glue script. Leave empty to skip Glue job creation."
  type        = string
  default     = ""
}

variable "mesh_subscriptions_table_name" {
  description = "DynamoDB table name for mesh-subscriptions (from governance module output)."
  type        = string
  default     = "mesh-subscriptions"
}
