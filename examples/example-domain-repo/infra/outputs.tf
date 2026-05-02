output "raw_bucket_name" {
  description = "Name of the raw (bronze) S3 bucket."
  value       = module.domain_account.raw_bucket_name
}

output "silver_bucket_name" {
  description = "Name of the silver S3 bucket."
  value       = module.domain_account.silver_bucket_name
}

output "gold_bucket_name" {
  description = "Name of the gold S3 bucket."
  value       = module.domain_account.gold_bucket_name
}

output "pipeline_state_machine_arn" {
  description = "ARN of the Step Functions medallion pipeline state machine."
  value       = module.data_product.pipeline_state_machine_arn
}

output "kms_key_arn" {
  description = "ARN of the domain KMS key."
  value       = module.domain_account.kms_key_arn
}
