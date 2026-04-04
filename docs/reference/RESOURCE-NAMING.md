# Reference: Resource Naming Conventions

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03

## Navigation
<- [Docs home](../README.md)

---

All AWS resources provisioned by Data Meshy follow deterministic naming conventions. Names are derived from the domain name, layer, account ID, or product name so that any resource can be located without a lookup.

---

## S3 Buckets

| Layer | Pattern | Example |
|---|---|---|
| Raw (Bronze) | `{domain}-raw-{account_id}` | `sales-raw-123456789012` |
| Silver (Validated) | `{domain}-silver-{account_id}` | `sales-silver-123456789012` |
| Gold (Data Product) | `{domain}-gold-{account_id}` | `sales-gold-123456789012` |
| Terraform state | `data-meshy-tfstate-{env}-{account_id}` | `data-meshy-tfstate-domain-sales-123456789012` |

Notes:
- `account_id` is the 12-digit AWS account ID of the domain account.
- All buckets enable S3 Bucket Keys and SSE-KMS encryption.
- Gold buckets have explicit deny policies preventing direct `s3:GetObject` (all reads go through Lake Formation).

---

## Glue Data Catalog Databases

| Layer | Pattern | Example |
|---|---|---|
| Raw | `{domain}_raw` | `sales_raw` |
| Silver | `{domain}_silver` | `sales_silver` |
| Gold | `{domain}_gold` | `sales_gold` |

Notes:
- Underscore separator (not hyphen) because Glue database names do not allow hyphens.
- One database per layer per domain.

---

## Glue Data Catalog Tables

| Pattern | Example |
|---|---|
| `{product_name}` (in the layer-specific database) | `customer_orders` in `sales_gold` |

Full qualified name: `{domain}_{layer}.{product_name}` (e.g., `sales_gold.customer_orders`).

---

## DynamoDB Tables (Central Governance Account)

| Table | PK | SK | Purpose |
|---|---|---|---|
| `mesh-domains` | `domain_name` | - | Domain registry |
| `mesh-products` | `domain#product_name` | - | Product catalog + metadata |
| `mesh-subscriptions` | `product_id` | `subscriber_account_id` | Active subscriptions |
| `mesh-quality-scores` | `product_id` | `timestamp` | Quality score history |
| `mesh-audit-log` | `event_id` | `timestamp` | Append-only audit trail |
| `mesh-event-dedup` | `event_id` | - | Event idempotency (TTL 24h) |
| `mesh-pipeline-locks` | `product_id` | `LOCK` | Concurrent run prevention (TTL 3h) |

All tables have PITR enabled. No domain account has direct DynamoDB access; all writes go through central Lambdas.

---

## IAM Roles

### Central Governance Account

| Role Name (PascalCase) | Scope | Trust Policy |
|---|---|---|
| `MeshLFGrantorRole` | LF `GrantPermissions` / `RevokePermissions` on gold tables only. Can only grant `SELECT`. | Lambda service |
| `MeshCatalogWriterRole` | `PutItem` / `UpdateItem` on `mesh-products`, `mesh-domains`, `mesh-subscriptions`, `mesh-quality-scores`. Row-level isolation via `dynamodb:LeadingKeys`. | Lambda service |
| `MeshAuditWriterRole` | `PutItem` only on `mesh-audit-log`. No `UpdateItem`, `DeleteItem`, or `BatchWriteItem`. | Lambda service |
| `GovernanceReadRole` | Read-only on all mesh DynamoDB tables + Glue Catalog `DESCRIBE` across accounts. | SSO |
| `MeshAdminRole` | Full CRUD (break-glass only). MFA required. 1-hour max session. CloudWatch alarm on any assumption. | SSO + MFA |
| `TerraformPlanRole` | Read-only + `terraform plan`. Any branch. | GitHub Actions OIDC |
| `TerraformApplyRole` | Write. Main branch only. | GitHub Actions OIDC |

### Domain Account

| Role Name (PascalCase) | Scope | Trust Policy |
|---|---|---|
| `DomainAdminRole` | Full domain resource access. For domain product owners. | SSO |
| `DomainDataEngineerRole` | Read/write S3, create/run Glue jobs, read catalog. Cannot modify LF permissions. | SSO |
| `DomainConsumerRole` | Read-only on subscribed tables via LF grants. Athena query access. | SSO |
| `GlueJobExecutionRole` | S3 read/write for medallion layers, Glue Catalog access. Permission boundary restricts S3 to domain buckets only. | Glue service |
| `MeshEventRole` | `PutEvents` on central EventBridge bus only. | Lambda / Step Functions service |

---

## KMS Keys

| Pattern | Example | Scope |
|---|---|---|
| `alias/mesh-central` | Central governance CMK | Encrypts DynamoDB, Terraform state, central SNS |
| `alias/mesh-{domain}` | `alias/mesh-sales` | Per-domain CMK. Encrypts domain S3 buckets. Key policy grants decrypt to LF service for cross-account reads. |

All keys are customer-managed (CMK), not AWS-managed.

---

## EventBridge Event Buses

| Bus Name | Account | Purpose |
|---|---|---|
| `mesh-central-bus` | Central governance | Receives all `source: datameshy` events from domain buses. Routes to Step Functions, Lambda, SNS, CloudWatch. |
| `mesh-domain-bus` | Each domain account | Local domain bus. Resource policy allows `PutEvents` from same account only. Forwards `source: datameshy` events to central bus. |

---

## SNS Topics

| Topic Name | Account | Purpose |
|---|---|---|
| `mesh-quality-alerts` | Central | Quality score below threshold notifications |
| `mesh-pipeline-failures` | Central | Step Functions pipeline failure notifications |
| `mesh-freshness-violations` | Central | Freshness SLA breach notifications |
| `mesh-subscription-requests` | Central | New subscription request notifications to product owners |

---

## SQS Dead Letter Queues

| Queue Name | Account | Purpose |
|---|---|---|
| `mesh-catalog-dlq` | Central | Failed catalog Lambda invocations |
| `mesh-audit-dlq` | Central | Failed audit Lambda invocations |
| `mesh-subscription-dlq` | Central | Failed subscription Lambda invocations |

CloudWatch alarm triggers on any DLQ message count > 0 (any message is an incident).

---

## Step Functions State Machines

| Pattern | Example |
|---|---|
| `{domain}-{product}-pipeline` | `sales-customer_orders-pipeline` |

Execution timeout: 7200 seconds (2 hours). Each Glue step has a 1800-second (30 min) timeout with 300-second heartbeat.

---

## Glue Data Quality Rulesets

| Pattern | Example |
|---|---|
| `{domain}_{product}_dq` | `sales_customer_orders_dq` |

Defined from `quality.rules` in `product.yaml` and provisioned by the `data-product` Terraform module.

---

## Glue ETL Jobs

| Job | Pattern | Example |
|---|---|---|
| Raw ingestion | `{domain}-{product}-raw-ingestion` | `sales-customer_orders-raw-ingestion` |
| Silver transform | `{domain}-{product}-silver-transform` | `sales-customer_orders-silver-transform` |
| Gold aggregate | `{domain}-{product}-gold-aggregate` | `sales-customer_orders-gold-aggregate` |
| Iceberg maintenance | `{domain}-{product}-iceberg-maintenance` | `sales-customer_orders-iceberg-maintenance` |

---

## Secrets Manager Secrets

| Pattern | Example |
|---|---|
| `mesh/{domain}/{source_name}-credentials` | `mesh/sales/erp_orders-credentials` |

Referenced in `product.yaml` under `lineage.sources[].credentials_secret_arn`. Rotated on a 90-day schedule.

---

## Resource Tagging Strategy

All resources carry these mandatory tags:

| Tag Key | Example Value | Purpose |
|---|---|---|
| `mesh:domain` | `sales` | Cost attribution, filtering |
| `mesh:product` | `customer_orders` | Cost attribution |
| `mesh:environment` | `dev` | Environment identification |
| `mesh:managed-by` | `terraform` | Identify IaC-managed resources |
| `mesh:layer` | `gold` / `silver` / `raw` | Medallion layer identification |

Terraform `default_tags` in the provider block ensures these tags are applied to all resources.

---

## See Also

- [Terraform Modules Reference](TERRAFORM-MODULES.md) -- module inputs and outputs that produce these names
- [Product Spec Reference](PRODUCT-SPEC.md) -- the `product.yaml` fields that feed into resource naming
- [Architecture Document](../../plan/ARCHITECTURE.md) -- full architecture context
