##############################################################################
# data-product/outputs.tf
#
# Output names match the shared contract in plan/phases/phase-1/README.md.
# Consumed by Stream 3 (pipeline-events) and Stream 4 (cli-ci-example).
# DO NOT rename without updating the README contract.
##############################################################################

# ── S3 bucket names ──────────────────────────────────────────────────────────

output "raw_bucket_name" {
  description = "S3 bucket name for the raw (Bronze) layer."
  value       = var.raw_bucket_name
}

output "silver_bucket_name" {
  description = "S3 bucket name for the silver (Validated) layer."
  value       = var.silver_bucket_name
}

output "gold_bucket_name" {
  description = "S3 bucket name for the gold (Data Product) layer."
  value       = var.gold_bucket_name
}

# ── Glue catalog database names ──────────────────────────────────────────────

output "glue_catalog_db_raw" {
  description = "Glue Data Catalog database name for the raw layer."
  value       = var.glue_catalog_db_raw
}

output "glue_catalog_db_silver" {
  description = "Glue Data Catalog database name for the silver layer."
  value       = var.glue_catalog_db_silver
}

output "glue_catalog_db_gold" {
  description = "Glue Data Catalog database name for the gold layer."
  value       = var.glue_catalog_db_gold
}

# ── IAM role ARNs ────────────────────────────────────────────────────────────

output "glue_job_execution_role_arn" {
  description = "ARN of GlueJobExecutionRole — passed as execution role for Glue jobs."
  value       = var.glue_job_execution_role_arn
}

output "mesh_event_role_arn" {
  description = "ARN of MeshEventRole — used by Lambda/Step Functions to PutEvents on the central bus."
  value       = var.mesh_event_role_arn
}

# ── EventBridge ──────────────────────────────────────────────────────────────

output "domain_event_bus_arn" {
  description = "ARN of the domain EventBridge bus (mesh-domain-bus)."
  value       = var.domain_event_bus_arn
}

# ── KMS ──────────────────────────────────────────────────────────────────────

output "domain_kms_key_arn" {
  description = "ARN of the domain KMS CMK (alias/mesh-{domain}). Used for S3 encryption context."
  value       = var.domain_kms_key_arn
}

# ── Product-specific outputs ─────────────────────────────────────────────────

output "product_id" {
  description = "Canonical product ID ({domain}#{product_name}) used as DynamoDB PK."
  value       = local.product_id
}

output "quality_ruleset_name" {
  description = "Glue Data Quality ruleset name ({domain}_{product_name}_dq)."
  value       = aws_glue_data_quality_ruleset.product.name
}

output "state_machine_arn" {
  description = "ARN of the Step Functions medallion pipeline state machine."
  value       = aws_sfn_state_machine.medallion_pipeline.arn
}

output "state_machine_name" {
  description = "Name of the Step Functions medallion pipeline state machine."
  value       = aws_sfn_state_machine.medallion_pipeline.name
}

output "source_credentials_secret_arn" {
  description = "ARN of the Secrets Manager secret holding source DB credentials."
  value       = aws_secretsmanager_secret.source_credentials.arn
}

output "iceberg_table_arn" {
  description = "ARN of the Glue Catalog table (Iceberg) for this data product."
  value       = aws_glue_catalog_table.product.arn
}
