# infra/modules/governance/outputs.tf
#
# SHARED CONTRACTS — These output names are the interface for all other streams.
# DO NOT rename without updating plan/phases/phase-1/README.md.

###############################################################################
# EventBridge
###############################################################################
output "central_event_bus_arn" {
  description = "ARN of the central EventBridge bus (mesh-central-bus)."
  value       = aws_cloudwatch_event_bus.mesh_central.arn
}

output "central_event_bus_name" {
  description = "Name of the central EventBridge bus."
  value       = aws_cloudwatch_event_bus.mesh_central.name
}

output "schema_registry_name" {
  description = "EventBridge Schema Registry name for mesh events."
  value       = aws_schemas_registry.mesh_events.name
}

###############################################################################
# DynamoDB table names (exact strings, no ARNs — match contract in README.md)
###############################################################################
output "mesh_products_table_name" {
  description = "DynamoDB table name for mesh product catalog."
  value       = aws_dynamodb_table.mesh_products.name
}

output "mesh_domains_table_name" {
  description = "DynamoDB table name for registered mesh domains."
  value       = aws_dynamodb_table.mesh_domains.name
}

output "mesh_subscriptions_table_name" {
  description = "DynamoDB table name for product subscriptions."
  value       = aws_dynamodb_table.mesh_subscriptions.name
}

output "mesh_quality_scores_table_name" {
  description = "DynamoDB table name for quality score history."
  value       = aws_dynamodb_table.mesh_quality_scores.name
}

output "mesh_audit_log_table_name" {
  description = "DynamoDB table name for the append-only audit log."
  value       = aws_dynamodb_table.mesh_audit_log.name
}

output "mesh_event_dedup_table_name" {
  description = "DynamoDB table name for event deduplication (TTL 24h)."
  value       = aws_dynamodb_table.mesh_event_dedup.name
}

output "mesh_pipeline_locks_table_name" {
  description = "DynamoDB table name for pipeline run locks."
  value       = aws_dynamodb_table.mesh_pipeline_locks.name
}

###############################################################################
# DynamoDB table ARNs (for IAM policies in other modules)
###############################################################################
output "mesh_products_table_arn" {
  description = "ARN of the mesh-products DynamoDB table."
  value       = aws_dynamodb_table.mesh_products.arn
}

output "mesh_domains_table_arn" {
  description = "ARN of the mesh-domains DynamoDB table."
  value       = aws_dynamodb_table.mesh_domains.arn
}

output "mesh_subscriptions_table_arn" {
  description = "ARN of the mesh-subscriptions DynamoDB table."
  value       = aws_dynamodb_table.mesh_subscriptions.arn
}

output "mesh_quality_scores_table_arn" {
  description = "ARN of the mesh-quality-scores DynamoDB table."
  value       = aws_dynamodb_table.mesh_quality_scores.arn
}

output "mesh_audit_log_table_arn" {
  description = "ARN of the mesh-audit-log DynamoDB table."
  value       = aws_dynamodb_table.mesh_audit_log.arn
}

output "mesh_event_dedup_table_arn" {
  description = "ARN of the mesh-event-dedup DynamoDB table."
  value       = aws_dynamodb_table.mesh_event_dedup.arn
}

output "mesh_pipeline_locks_table_arn" {
  description = "ARN of the mesh-pipeline-locks DynamoDB table."
  value       = aws_dynamodb_table.mesh_pipeline_locks.arn
}

###############################################################################
# IAM role ARNs
###############################################################################
output "mesh_lf_grantor_role_arn" {
  description = "ARN of MeshLFGrantorRole (LF SELECT grants on gold tables only)."
  value       = aws_iam_role.mesh_lf_grantor.arn
}

output "mesh_kms_grantor_role_arn" {
  description = "ARN of MeshKmsGrantorRole (creates KMS grants for consumer roles on domain keys)."
  value       = aws_iam_role.mesh_kms_grantor.arn
}

output "mesh_catalog_writer_role_arn" {
  description = "ARN of MeshCatalogWriterRole (DynamoDB writes to catalog tables only)."
  value       = aws_iam_role.mesh_catalog_writer.arn
}

output "mesh_audit_writer_role_arn" {
  description = "ARN of MeshAuditWriterRole (append-only PutItem on audit log)."
  value       = aws_iam_role.mesh_audit_writer.arn
}

output "governance_read_role_arn" {
  description = "ARN of GovernanceReadRole (read-only on all tables + Glue catalog)."
  value       = aws_iam_role.governance_read.arn
}

output "mesh_admin_role_arn" {
  description = "ARN of MeshAdminRole (break-glass, MFA required, 1h session)."
  value       = aws_iam_role.mesh_admin.arn
}

output "terraform_plan_role_arn" {
  description = "ARN of TerraformPlanRole (GitHub Actions OIDC, read-only, any branch)."
  value       = aws_iam_role.terraform_plan.arn
}

output "terraform_apply_role_arn" {
  description = "ARN of TerraformApplyRole (GitHub Actions OIDC, write, main branch only)."
  value       = aws_iam_role.terraform_apply.arn
}

###############################################################################
# SNS topic ARNs
###############################################################################
output "quality_alert_sns_topic_arn" {
  description = "ARN of the mesh-quality-alerts SNS topic."
  value       = aws_sns_topic.mesh_quality_alerts.arn
}

output "pipeline_failure_sns_topic_arn" {
  description = "ARN of the mesh-pipeline-failures SNS topic."
  value       = aws_sns_topic.mesh_pipeline_failures.arn
}

output "freshness_violation_sns_topic_arn" {
  description = "ARN of the mesh-freshness-violations SNS topic."
  value       = aws_sns_topic.mesh_freshness_violations.arn
}

output "subscription_requests_sns_topic_arn" {
  description = "ARN of the mesh-subscription-requests SNS topic."
  value       = aws_sns_topic.mesh_subscription_requests.arn
}

###############################################################################
# SQS DLQ ARNs
###############################################################################
output "catalog_dlq_arn" {
  description = "ARN of the mesh-catalog-dlq SQS queue."
  value       = aws_sqs_queue.mesh_catalog_dlq.arn
}

output "audit_dlq_arn" {
  description = "ARN of the mesh-audit-dlq SQS queue."
  value       = aws_sqs_queue.mesh_audit_dlq.arn
}

output "subscription_dlq_arn" {
  description = "ARN of the mesh-subscription-dlq SQS queue."
  value       = aws_sqs_queue.mesh_subscription_dlq.arn
}

###############################################################################
# KMS
###############################################################################
output "central_kms_key_arn" {
  description = "ARN of the central KMS CMK (alias/mesh-central)."
  value       = aws_kms_key.mesh_central.arn
}

output "central_kms_key_id" {
  description = "Key ID of the central KMS CMK."
  value       = aws_kms_key.mesh_central.key_id
}

output "central_kms_alias_arn" {
  description = "ARN of the alias/mesh-central KMS alias."
  value       = aws_kms_alias.mesh_central.arn
}

###############################################################################
# OIDC
###############################################################################
output "github_actions_oidc_provider_arn" {
  description = "ARN of the GitHub Actions OIDC provider."
  value       = aws_iam_openid_connect_provider.github_actions.arn
}

###############################################################################
# API Gateway (Phase 2)
###############################################################################
output "api_endpoint_url" {
  description = "Base URL of the mesh governance HTTP API. CLI appends route paths (e.g. /subscriptions)."
  value       = aws_apigatewayv2_api.mesh_api.api_endpoint
}

output "api_id" {
  description = "ID of the mesh governance HTTP API Gateway."
  value       = aws_apigatewayv2_api.mesh_api.id
}

output "api_execution_arn" {
  description = "Execution ARN of the mesh governance API (used for Lambda permission source_arn)."
  value       = aws_apigatewayv2_api.mesh_api.execution_arn
}
