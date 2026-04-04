# Component: Governance

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
<- [Architecture](../architecture/OVERVIEW.md) | [^ Docs home](../README.md)

## What this is

The governance module provisions the **central governance account** infrastructure for Data Meshy. It is deployed once into the AWS account that acts as the mesh control plane. Every domain account depends on outputs from this module (EventBridge bus ARN, DynamoDB table names, KMS key, IAM role ARNs).

No data pipelines run in this account. Its job is to provide the shared services that make the mesh work: a product catalog, an audit trail, event routing, and fine-grained IAM roles.

Source: `infra/modules/governance/`

## Where to find it

```
infra/modules/governance/
  main.tf              -- KMS CMK (alias/mesh-central), SNS topics, optional email subscriptions, topic policies
  dynamodb.tf           -- 7 DynamoDB tables (catalog, subscriptions, audit, quality, dedup, locks)
  eventbridge.tf        -- Central bus (mesh-central-bus), schema registry, 6 rules, 3 DLQs, CloudWatch log group
  iam.tf                -- 7 decomposed IAM roles (no god-roles), OIDC provider for GitHub Actions
  outputs.tf            -- All table names, ARNs, SNS topics, SQS DLQs, KMS, OIDC provider
  variables.tf          -- environment, aws_region, domain_account_ids, github_org, github_repo, alert_email
```

## How it works

### DynamoDB tables (7)

All tables use `PAY_PER_REQUEST` billing, server-side encryption with AWS-managed KMS, and point-in-time recovery.

| Table | PK | SK | Purpose |
|---|---|---|---|
| `mesh-domains` | `domain_name` (S) | -- | Registered domains. PK is the domain name. |
| `mesh-products` | `domain#product_name` (S) | -- | Product catalog. GSIs on `tag`, `classification`, `domain`. |
| `mesh-subscriptions` | `product_id` (S) | `subscriber_account_id` (S) | Active subscriptions. GSI on `subscriber_domain`. |
| `mesh-quality-scores` | `product_id` (S) | `timestamp` (S) | Quality score time series per product. |
| `mesh-audit-log` | `event_id` (S) | `timestamp` (S) | Append-only audit trail. GSIs on `domain` and `event_type`. |
| `mesh-event-dedup` | `event_id` (S) | -- | 24-hour TTL dedup window (`expires_at` attribute). |
| `mesh-pipeline-locks` | `product_id` (S) | `lock_key` (S) | Prevents concurrent pipeline runs. TTL via `expires_at`. |

### EventBridge

- **Bus**: `mesh-central-bus`. Resource policy explicitly lists each domain account ID -- no wildcards.
- **Schema registry**: `mesh-events` for JSON Schemas of all mesh event types.
- **Rules** (6 total):
  - `mesh-catalog-update` -- routes `ProductCreated` / `ProductRefreshed` to catalog Lambda (+ DLQ)
  - `mesh-subscription-workflow` -- routes `SubscriptionRequested` to Step Functions (+ DLQ)
  - `mesh-quality-alerts` -- routes `QualityAlert` / `FreshnessViolation` / `SchemaChanged` to SNS
  - `mesh-pipeline-failures` -- routes `PipelineFailure` to SNS
  - `mesh-all-events-audit` -- sends all `datameshy`-prefix events to CloudWatch Logs (`/aws/events/mesh-central-audit`, 90-day retention)
- **DLQs**: `mesh-catalog-dlq`, `mesh-audit-dlq`, `mesh-subscription-dlq` (14-day retention, SSE-KMS). CloudWatch alarms fire on any DLQ depth > 0.

### IAM roles (7)

| Role | Trust | Purpose | Key constraint |
|---|---|---|---|
| `MeshLFGrantorRole` | `lambda.amazonaws.com` | Grant/revoke LF SELECT on gold tables | Permission boundary limits to SELECT-only grants |
| `MeshCatalogWriterRole` | `lambda.amazonaws.com` | Write to catalog DynamoDB tables | `dynamodb:LeadingKeys` restricted to caller's domain prefix |
| `MeshAuditWriterRole` | `lambda.amazonaws.com` | Append-only writes to audit log | PutItem only; explicit Deny on UpdateItem/DeleteItem/BatchWriteItem |
| `GovernanceReadRole` | SAML/SSO + `sso.amazonaws.com` | Read-only on all tables + Glue catalog | No write actions permitted |
| `MeshAdminRole` | Account root (MFA required) + `TerraformApplyRole` | Break-glass / Terraform apply | 1h session, MFA required, CloudWatch alarm on any assumption |
| `TerraformPlanRole` | GitHub Actions OIDC (any branch) | `terraform plan` (read-only) | No write actions |
| `TerraformApplyRole` | GitHub Actions OIDC (`main` branch only) | `terraform apply` (write) | Branch-locked to `ref:refs/heads/main` |

### KMS

Single CMK (`alias/mesh-central`) with automatic key rotation. Key policy grants:
- Root account full control
- `MeshAdminRole` full access
- `MeshCatalogWriterRole` / `MeshAuditWriterRole` encrypt+decrypt
- Lambda and DynamoDB service principals decrypt+generate data keys
- SQS service principal decrypt+generate data keys

### SNS topics (4)

| Topic | Purpose |
|---|---|
| `mesh-quality-alerts` | Quality score below threshold |
| `mesh-pipeline-failures` | Medallion pipeline execution failures |
| `mesh-freshness-violations` | SLA refresh window exceeded |
| `mesh-subscription-requests` | New subscription requests |

Optional email subscriptions are created when `alert_email` is set. Topics use `alias/mesh-central` KMS encryption. EventBridge and CloudWatch are granted publish permissions via topic policies.

## Key interactions

1. **Domain accounts** put events on `mesh-central-bus` via the forwarding rule in `domain-account/eventbridge.tf`. The bus policy explicitly authorizes each domain account ID.
2. **Lambdas** (`catalog_writer`, `audit_writer`) assume `MeshCatalogWriterRole` / `MeshAuditWriterRole` to write to DynamoDB.
3. **GitHub Actions** use OIDC to assume `TerraformPlanRole` (any branch) or `TerraformApplyRole` (main only).
4. **CloudWatch alarms** on DLQs and MeshAdminRole assumption route to `mesh-pipeline-failures` SNS topic.

## Configuration

| Variable | Type | Default | Description |
|---|---|---|---|
| `environment` | `string` | required | Deployment environment label (e.g. `dev`, `prod`) |
| `aws_region` | `string` | `us-east-1` | AWS region for all resources |
| `domain_account_ids` | `list(string)` | `[]` | Domain AWS account IDs allowed to PutEvents on the central bus |
| `github_org` | `string` | `""` | GitHub org/user for OIDC roles |
| `github_repo` | `string` | `"data-meshy"` | GitHub repo name for OIDC roles |
| `alert_email` | `string` | `""` | Email for SNS subscriptions; leave blank to skip |

## Gotchas and constraints

- **No wildcards in bus policy.** Each domain account must be explicitly listed in `domain_account_ids`. Adding a new domain requires a governance module `terraform apply`.
- **MeshAuditWriterRole is strictly append-only.** The IAM policy has an explicit Deny on `UpdateItem`, `DeleteItem`, and `BatchWriteItem`. If you need to correct an audit entry, you append a new one.
- **MeshAdminRole triggers an alarm on every assumption.** This is intentional -- it is a break-glass role. The alarm is a CloudWatch metric filter on CloudTrail matching `AssumeRole` on `*MeshAdminRole`.
- **TerraformApplyRole is branch-locked.** It can only be assumed from `refs/heads/main`. Feature branches get read-only access via `TerraformPlanRole`.
- **DLQ alarms are zero-tolerance.** Any message in any DLQ triggers an alarm. There is no "acceptable" DLQ depth.
- **KMS key deletion window is 30 days.** After deletion is scheduled, you have 30 days to cancel before the key is permanently destroyed.

## See also

- [Domain Account](DOMAIN-ACCOUNT.md) -- per-domain infrastructure that connects to this governance plane
- [Data Product](DATA-PRODUCT.md) -- product-level resources that register in the catalog tables
- [Monitoring](MONITORING.md) -- CloudWatch alarms and budgets
- [Lambdas](LAMBDAS.md) -- Lambda handlers that assume the IAM roles defined here
