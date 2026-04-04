##############################################################################
# domain-account/outputs.tf
#
# Output names are defined by the shared contract in plan/phases/phase-1/README.md.
# These names are consumed by Stream 3 (pipeline-events) and Stream 4 (cli-ci).
# DO NOT rename these outputs without updating the README contract.
##############################################################################

# S3 bucket names — consumed by Glue job parameters and Step Functions input
output "raw_bucket_name" {
  description = "S3 bucket name for the raw (Bronze) layer."
  value       = aws_s3_bucket.raw.bucket
}

output "silver_bucket_name" {
  description = "S3 bucket name for the silver (Validated) layer."
  value       = aws_s3_bucket.silver.bucket
}

output "gold_bucket_name" {
  description = "S3 bucket name for the gold (Data Product) layer."
  value       = aws_s3_bucket.gold.bucket
}

# Glue catalog database names — consumed by Glue jobs, Step Functions input
output "glue_catalog_db_raw" {
  description = "Glue Data Catalog database name for the raw layer."
  value       = aws_glue_catalog_database.raw.name
}

output "glue_catalog_db_silver" {
  description = "Glue Data Catalog database name for the silver layer."
  value       = aws_glue_catalog_database.silver.name
}

output "glue_catalog_db_gold" {
  description = "Glue Data Catalog database name for the gold layer."
  value       = aws_glue_catalog_database.gold.name
}

# IAM role ARNs — consumed by Step Functions task role + event publishers
output "glue_job_execution_role_arn" {
  description = "ARN of GlueJobExecutionRole — passed as execution role for Glue job definitions."
  value       = aws_iam_role.glue_job_execution.arn
}

output "mesh_event_role_arn" {
  description = "ARN of MeshEventRole — used by Lambda/Step Functions to PutEvents on the central bus."
  value       = aws_iam_role.mesh_event.arn
}

# EventBridge
output "domain_event_bus_arn" {
  description = "ARN of the domain EventBridge bus (mesh-domain-bus)."
  value       = aws_cloudwatch_event_bus.domain.arn
}

# KMS
output "domain_kms_key_arn" {
  description = "ARN of the domain KMS CMK (alias/mesh-{domain}). Used for S3 encryption context."
  value       = aws_kms_key.domain.arn
}

# Additional convenience outputs (not in shared contract but useful for environment wiring)
output "domain_kms_key_id" {
  description = "Key ID of the domain KMS CMK."
  value       = aws_kms_key.domain.key_id
}

output "domain_admin_role_arn" {
  description = "ARN of DomainAdminRole."
  value       = aws_iam_role.domain_admin.arn
}

output "domain_consumer_role_arn" {
  description = "ARN of DomainConsumerRole."
  value       = aws_iam_role.domain_consumer.arn
}
