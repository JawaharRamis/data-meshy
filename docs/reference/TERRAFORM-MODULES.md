# Reference: Terraform Modules

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03

## Navigation
<- [Docs home](../README.md)

---

Data Meshy uses four Terraform modules located in `infra/modules/`. Each module has a well-defined interface of input variables and output values. This reference documents every variable and output for all four modules.

Module source paths:
- `infra/modules/governance/`
- `infra/modules/domain-account/`
- `infra/modules/data-product/`
- `infra/modules/monitoring/`

---

## Module: governance

**Path**: `infra/modules/governance/`

**Purpose**: Central governance account infrastructure -- DynamoDB tables, EventBridge central bus, Schema Registry, decomposed IAM roles, SNS topics, SQS DLQs, KMS, GitHub Actions OIDC.

### Input Variables

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `environment` | string | Yes | - | Deployment environment label (e.g., portfolio, staging, prod). |
| `aws_region` | string | No | `us-east-1` | AWS region for all resources. |
| `domain_account_ids` | list(string) | No | `[]` | List of domain AWS account IDs allowed to put events on the central EventBridge bus. |
| `github_org` | string | No | `""` | GitHub organisation or user name that owns the repository. |
| `github_repo` | string | No | `data-meshy` | GitHub repository name (without the org prefix). |
| `alert_email` | string | No | `""` | Email address for SNS alert subscriptions. Leave blank to skip. |

### Outputs

| Name | Description |
|---|---|
| `central_event_bus_arn` | ARN of the central EventBridge bus (`mesh-central-bus`). |
| `central_event_bus_name` | Name of the central EventBridge bus. |
| `schema_registry_name` | EventBridge Schema Registry name for mesh events. |
| `mesh_products_table_name` | DynamoDB table name for mesh product catalog. |
| `mesh_domains_table_name` | DynamoDB table name for registered mesh domains. |
| `mesh_subscriptions_table_name` | DynamoDB table name for product subscriptions. |
| `mesh_quality_scores_table_name` | DynamoDB table name for quality score history. |
| `mesh_audit_log_table_name` | DynamoDB table name for the append-only audit log. |
| `mesh_event_dedup_table_name` | DynamoDB table name for event deduplication (TTL 24h). |
| `mesh_pipeline_locks_table_name` | DynamoDB table name for pipeline run locks. |
| `mesh_products_table_arn` | ARN of the `mesh-products` DynamoDB table. |
| `mesh_domains_table_arn` | ARN of the `mesh-domains` DynamoDB table. |
| `mesh_subscriptions_table_arn` | ARN of the `mesh-subscriptions` DynamoDB table. |
| `mesh_quality_scores_table_arn` | ARN of the `mesh-quality-scores` DynamoDB table. |
| `mesh_audit_log_table_arn` | ARN of the `mesh-audit-log` DynamoDB table. |
| `mesh_event_dedup_table_arn` | ARN of the `mesh-event-dedup` DynamoDB table. |
| `mesh_pipeline_locks_table_arn` | ARN of the `mesh-pipeline-locks` DynamoDB table. |
| `mesh_lf_grantor_role_arn` | ARN of `MeshLFGrantorRole` (LF SELECT grants on gold tables only). |
| `mesh_catalog_writer_role_arn` | ARN of `MeshCatalogWriterRole` (DynamoDB writes to catalog tables only). |
| `mesh_audit_writer_role_arn` | ARN of `MeshAuditWriterRole` (append-only PutItem on audit log). |
| `governance_read_role_arn` | ARN of `GovernanceReadRole` (read-only on all tables + Glue catalog). |
| `mesh_admin_role_arn` | ARN of `MeshAdminRole` (break-glass, MFA required, 1h session). |
| `terraform_plan_role_arn` | ARN of `TerraformPlanRole` (GitHub Actions OIDC, read-only, any branch). |
| `terraform_apply_role_arn` | ARN of `TerraformApplyRole` (GitHub Actions OIDC, write, main branch only). |
| `quality_alert_sns_topic_arn` | ARN of the `mesh-quality-alerts` SNS topic. |
| `pipeline_failure_sns_topic_arn` | ARN of the `mesh-pipeline-failures` SNS topic. |
| `freshness_violation_sns_topic_arn` | ARN of the `mesh-freshness-violations` SNS topic. |
| `subscription_requests_sns_topic_arn` | ARN of the `mesh-subscription-requests` SNS topic. |
| `catalog_dlq_arn` | ARN of the `mesh-catalog-dlq` SQS queue. |
| `audit_dlq_arn` | ARN of the `mesh-audit-dlq` SQS queue. |
| `subscription_dlq_arn` | ARN of the `mesh-subscription-dlq` SQS queue. |
| `central_kms_key_arn` | ARN of the central KMS CMK (`alias/mesh-central`). |
| `central_kms_key_id` | Key ID of the central KMS CMK. |
| `central_kms_alias_arn` | ARN of the `alias/mesh-central` KMS alias. |
| `github_actions_oidc_provider_arn` | ARN of the GitHub Actions OIDC provider. |

---

## Module: domain-account

**Path**: `infra/modules/domain-account/`

**Purpose**: Per-domain AWS account infrastructure -- S3 buckets (raw/silver/gold with bucket policies), KMS key, Glue Catalog databases, scoped IAM roles (Admin, DataEngineer, Consumer, GlueJobExecution, MeshEvent), Lake Formation registration, EventBridge domain bus with forwarding to central.

### Input Variables

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `domain` | string | Yes | - | Domain name (e.g., `sales`, `marketing`). Used in all resource naming. Must be lowercase alphanumeric with hyphens. |
| `environment` | string | No | `dev` | Deployment environment (dev, staging, prod). |
| `aws_region` | string | No | `us-east-1` | AWS region for domain resources. |
| `aws_org_id` | string | Yes | - | AWS Organization ID (`o-xxxxxxxxxx`). Used in bucket policy `aws:PrincipalOrgID` condition. |
| `central_account_id` | string | Yes | - | Account ID of the central governance account. Used in KMS key policy. |
| `central_event_bus_arn` | string | Yes | - | ARN of the central EventBridge bus (from governance module). Events with `source=datameshy` are forwarded here. |
| `mesh_catalog_writer_role_arn` | string | Yes | - | ARN of `MeshCatalogWriterRole` in the central account. Referenced in domain trust policy. |
| `sso_identity_store_id` | string | No | `""` | IAM Identity Center Identity Store ID for SSO trust policies. |
| `tags` | map(string) | No | `{}` | Additional tags to merge with the mandatory tag set. |

### Outputs

| Name | Description |
|---|---|
| `raw_bucket_name` | S3 bucket name for the raw (Bronze) layer. Pattern: `{domain}-raw-{account_id}`. |
| `silver_bucket_name` | S3 bucket name for the silver (Validated) layer. Pattern: `{domain}-silver-{account_id}`. |
| `gold_bucket_name` | S3 bucket name for the gold (Data Product) layer. Pattern: `{domain}-gold-{account_id}`. |
| `glue_catalog_db_raw` | Glue Data Catalog database name for the raw layer. Pattern: `{domain}_raw`. |
| `glue_catalog_db_silver` | Glue Data Catalog database name for the silver layer. Pattern: `{domain}_silver`. |
| `glue_catalog_db_gold` | Glue Data Catalog database name for the gold layer. Pattern: `{domain}_gold`. |
| `glue_job_execution_role_arn` | ARN of `GlueJobExecutionRole` -- passed as execution role for Glue job definitions. |
| `mesh_event_role_arn` | ARN of `MeshEventRole` -- used by Lambda/Step Functions to `PutEvents` on the central bus. |
| `domain_event_bus_arn` | ARN of the domain EventBridge bus (`mesh-domain-bus`). |
| `domain_kms_key_arn` | ARN of the domain KMS CMK (`alias/mesh-{domain}`). |
| `domain_kms_key_id` | Key ID of the domain KMS CMK. |
| `domain_admin_role_arn` | ARN of `DomainAdminRole`. |
| `domain_consumer_role_arn` | ARN of `DomainConsumerRole`. |

---

## Module: data-product

**Path**: `infra/modules/data-product/`

**Purpose**: Per-data-product infrastructure -- Iceberg table creation, Glue DQ ruleset, Step Functions medallion pipeline (with retry/catch/timeout/lock + Iceberg maintenance), Secrets Manager secret for source credentials, catalog entry in DynamoDB.

### Input Variables

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `domain` | string | Yes | - | Domain name. Must match the `domain-account` module domain. |
| `product_name` | string | Yes | - | Data product name in snake_case (e.g., `customer_orders`). Must match pattern `^[a-z][a-z0-9_]*$`. |
| `environment` | string | No | `dev` | Deployment environment. |
| `aws_region` | string | No | `us-east-1` | AWS region. |
| `raw_bucket_name` | string | Yes | - | S3 bucket name for the raw layer (from `domain-account` output). |
| `silver_bucket_name` | string | Yes | - | S3 bucket name for the silver layer (from `domain-account` output). |
| `gold_bucket_name` | string | Yes | - | S3 bucket name for the gold layer (from `domain-account` output). |
| `glue_catalog_db_raw` | string | Yes | - | Glue catalog DB name for the raw layer (from `domain-account` output). |
| `glue_catalog_db_silver` | string | Yes | - | Glue catalog DB name for the silver layer (from `domain-account` output). |
| `glue_catalog_db_gold` | string | Yes | - | Glue catalog DB name for the gold layer (from `domain-account` output). |
| `glue_job_execution_role_arn` | string | Yes | - | ARN of `GlueJobExecutionRole` (from `domain-account` output). |
| `mesh_event_role_arn` | string | Yes | - | ARN of `MeshEventRole` (from `domain-account` output). |
| `domain_kms_key_arn` | string | Yes | - | ARN of domain KMS CMK (from `domain-account` output). |
| `domain_event_bus_arn` | string | Yes | - | ARN of domain EventBridge bus (from `domain-account` output). |
| `mesh_products_table_name` | string | No | `mesh-products` | DynamoDB table name for mesh-products catalog (from governance module output). |
| `mesh_pipeline_locks_table_name` | string | No | `mesh-pipeline-locks` | DynamoDB table name for pipeline locks (from governance module output). |
| `mesh_audit_log_table_name` | string | No | `mesh-audit-log` | DynamoDB table name for audit log (from governance module output). |
| `central_event_bus_arn` | string | Yes | - | ARN of the central EventBridge bus (from governance module output). |
| `schema_columns` | list(object({name=string, type=string, comment=optional(string)})) | Yes | - | List of column definitions for the Iceberg table. Each element has `name`, `type`, and optional `comment`. |
| `partition_keys` | list(string) | No | `[]` | List of partition key column names (subset of `schema_columns`). |
| `classification` | string | No | `internal` | LF-Tag classification value. Must be one of: `public`, `internal`, `confidential`, `restricted`. |
| `pii` | bool | No | `false` | Whether this data product contains PII data. Used for LF-Tag `pii=true/false`. |
| `dq_rules` | list(string) | No | `[]` | List of DQDL rule strings from `product.yaml` `quality.rules` section. |
| `owner` | string | Yes | - | Data product owner email or team name. |
| `description` | string | No | `""` | Short description of the data product. |
| `schema_version` | number | No | `1` | Schema version integer, monotonically increasing. |
| `sla_refresh_frequency` | string | No | `daily` | SLA refresh frequency string. |
| `sla_availability` | string | No | `99.9` | SLA availability target as a string. |
| `medallion_pipeline_asl_path` | string | No | `""` | Path to `templates/step_functions/medallion_pipeline.asl.json`. Must be set by the calling environment. |
| `source_name` | string | No | `default` | Identifier for the source system (used in Secrets Manager secret name). |
| `tags` | map(string) | No | `{}` | Additional tags merged with the mandatory set. |

### Outputs

| Name | Description |
|---|---|
| `raw_bucket_name` | S3 bucket name for the raw layer (pass-through from input). |
| `silver_bucket_name` | S3 bucket name for the silver layer (pass-through from input). |
| `gold_bucket_name` | S3 bucket name for the gold layer (pass-through from input). |
| `glue_catalog_db_raw` | Glue catalog DB for the raw layer (pass-through from input). |
| `glue_catalog_db_silver` | Glue catalog DB for the silver layer (pass-through from input). |
| `glue_catalog_db_gold` | Glue catalog DB for the gold layer (pass-through from input). |
| `glue_job_execution_role_arn` | ARN of `GlueJobExecutionRole` (pass-through from input). |
| `mesh_event_role_arn` | ARN of `MeshEventRole` (pass-through from input). |
| `domain_event_bus_arn` | ARN of the domain EventBridge bus (pass-through from input). |
| `domain_kms_key_arn` | ARN of the domain KMS CMK (pass-through from input). |
| `product_id` | Canonical product ID (`{domain}#{product_name}`) used as DynamoDB PK. |
| `quality_ruleset_name` | Glue Data Quality ruleset name (`{domain}_{product_name}_dq`). |
| `state_machine_arn` | ARN of the Step Functions medallion pipeline state machine. |
| `state_machine_name` | Name of the Step Functions medallion pipeline state machine. |
| `source_credentials_secret_arn` | ARN of the Secrets Manager secret holding source DB credentials. |
| `iceberg_table_arn` | ARN of the Glue Catalog table (Iceberg) for this data product. |

---

## Module: monitoring

**Path**: `infra/modules/monitoring/`

**Purpose**: Per-domain monitoring -- CloudWatch alarms, log groups, AWS Budgets alerts. Monitors Lambda errors, DLQ message counts, Step Functions execution failures, Glue job failures, and monthly spend.

### Input Variables

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `domain` | string | Yes | - | Domain name. Used in alarm naming and log group paths. |
| `environment` | string | No | `dev` | Deployment environment. |
| `aws_region` | string | No | `us-east-1` | AWS region for monitoring resources. |
| `alarm_notification_arn` | string | Yes | - | SNS topic ARN for alarm notifications (from governance module `quality_alert_sns_topic_arn` or a dedicated ops topic). |
| `lambda_function_names` | list(string) | No | `[]` | List of Lambda function names in this domain to monitor for errors. |
| `dlq_queue_arns` | map(string) | No | `{}` | Map of DLQ queue name to ARN to monitor for message count > 0. |
| `state_machine_arn` | string | No | `""` | Step Functions state machine ARN to monitor for execution failures. |
| `glue_job_names` | list(string) | No | `[]` | List of Glue job names to monitor for failures. |
| `budget_thresholds` | list(number) | No | `[20, 50, 100]` | List of monthly budget threshold amounts (USD) for AWS Budgets alerts. |
| `budget_email_recipients` | list(string) | No | `[]` | List of email addresses for AWS Budgets alerts. |
| `tags` | map(string) | No | `{}` | Additional tags to merge with the mandatory set. |

This module has no outputs. It creates CloudWatch alarms and AWS Budgets that send notifications directly.

---

## Module Wiring: domain-sales Example

The `infra/environments/domain-sales/main.tf` shows how the modules connect:

```
governance (central account)
    --> domain-account (sales account)
        --> data-product (uses domain-account outputs + governance outputs)
        --> monitoring (uses governance SNS topic + data-product state machine ARN)
```

Key wiring pattern -- `domain-account` outputs feed into `data-product` inputs:

| domain-account Output | data-product Input |
|---|---|
| `raw_bucket_name` | `raw_bucket_name` |
| `silver_bucket_name` | `silver_bucket_name` |
| `gold_bucket_name` | `gold_bucket_name` |
| `glue_catalog_db_raw` | `glue_catalog_db_raw` |
| `glue_catalog_db_silver` | `glue_catalog_db_silver` |
| `glue_catalog_db_gold` | `glue_catalog_db_gold` |
| `glue_job_execution_role_arn` | `glue_job_execution_role_arn` |
| `mesh_event_role_arn` | `mesh_event_role_arn` |
| `domain_kms_key_arn` | `domain_kms_key_arn` |
| `domain_event_bus_arn` | `domain_event_bus_arn` |

---

## See Also

- [Resource Naming Reference](RESOURCE-NAMING.md) -- naming conventions produced by these modules
- [Add a Domain Guide](../guides/ADD-DOMAIN.md) -- step-by-step domain onboarding using these modules
- [Terraform Environments](../../infra/environments/domain-sales/) -- example environment wiring
