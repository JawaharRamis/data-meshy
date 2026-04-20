# Component: Domain Account

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
<- [Architecture](../architecture/OVERVIEW.md) | [^ Docs home](../README.md)

## What this is

The domain-account module provisions all infrastructure for a **single domain account** in the data mesh. It is instantiated once per domain (e.g. `sales`, `marketing`, `finance`) and creates the S3 storage layers, Glue catalog databases, IAM roles, Lake Formation registrations, EventBridge forwarding, and KMS encryption key for that domain.

The module consumes outputs from the governance module (central EventBridge bus ARN, MeshCatalogWriterRole ARN) and produces outputs consumed by the data-product module (bucket names, catalog DB names, role ARNs).

Source: `infra/modules/domain-account/`

## Where to find it

```
infra/modules/domain-account/
  main.tf              -- KMS domain CMK, Glue catalog databases (raw/silver/gold), Lake Formation S3 registrations
  s3.tf                -- 3 S3 buckets (raw/silver/gold) with SSE-KMS, versioning, lifecycle, bucket policies
  iam.tf               -- 5 IAM roles + permission boundary
  lakeformation.tf      -- LF admin settings, LF-Tags (domain/classification/pii), LF grants
  eventbridge.tf        -- Domain event bus, forwarding rule to central bus, DLQ
  outputs.tf            -- Bucket names, catalog DBs, role ARNs, KMS, event bus
  variables.tf          -- domain, environment, aws_region, aws_org_id, central_account_id, etc.
```

## How it works

### S3 buckets (3)

Naming convention: `{domain}-{layer}-{account_id}` (e.g. `sales-raw-123456789012`).

All buckets share: SSE-KMS with domain CMK, `bucket_key_enabled = true`, versioning enabled, full public access block, HTTPS-only deny, OrgID deny.

| Bucket | Lifecycle | Cross-account | Special |
|---|---|---|---|
| `raw` | Glacier after 90d, expire after 365d, noncurrent version expire after 30d | **Denied** -- domain-internal only | Append-only by design |
| `silver` | None | **Denied** -- domain-internal only | -- |
| `gold` | None | **Restricted** -- see LF-bypass prevention below | Only shareable layer |

**Gold bucket LF-bypass prevention**: The gold bucket policy denies `s3:GetObject` to ALL principals EXCEPT `GlueJobExecutionRole` and the `lakeformation.amazonaws.com` service-linked role. This means no IAM role -- even one with `s3:GetObject` on the bucket -- can bypass Lake Formation and read gold data directly. All consumer access goes through LF governed sharing.

### IAM roles (5)

| Role | Trust | Boundary | Purpose |
|---|---|---|---|
| `DomainAdminRole` | SSO (SAML) | None | Full domain resource access. Explicit Deny on LF permission management. |
| `DomainDataEngineerRole` | SSO (SAML) | `DomainS3PermissionBoundary` | S3+Glue access, Step Functions, CloudWatch Logs. Cannot manage LF grants. |
| `DomainConsumerRole` | SSO (SAML) | None | Athena query access, Glue catalog read, KMS decrypt. Access controlled by LF grants. |
| `GlueJobExecutionRole` | `glue.amazonaws.com` | `DomainS3PermissionBoundary` | Service role for ETL. S3 R/W on all 3 layers, Glue catalog, Secrets Manager (domain-scoped), KMS, LF GetDataAccess. |
| `MeshEventRole` | `lambda.amazonaws.com` + `states.amazonaws.com` | None | PutEvents on central + domain bus. Denies `source=datameshy.central`. Glue start job run. X-Ray tracing. |

**Permission boundary** (`{domain}-DomainS3PermissionBoundary`): Restricts S3 actions to the domain's own 3 buckets. Allows all non-S3 actions. Applied to `GlueJobExecutionRole` and `DomainDataEngineerRole`.

### Glue catalog databases (3)

| Database | Name pattern |
|---|---|
| Raw | `{domain}_raw` |
| Silver | `{domain}_silver` |
| Gold | `{domain}_gold` |

### Lake Formation

- **Admin settings**: `GlueJobExecutionRole` and `DomainAdminRole` are LF admins.
- **LF-Tags**: `domain={domain}`, `classification=[public,internal,confidential,restricted]`, `pii=[true,false]`.
- **Gold DB tag**: The gold catalog database is tagged with `domain={domain}`.
- **LF grants**: `GlueJobExecutionRole` gets `CREATE_TABLE` + `DESCRIBE` on raw/silver DBs, plus `ALTER`/`DROP` on gold DB. `DomainDataEngineerRole` gets `DESCRIBE` on all 3 DBs.

### EventBridge

- **Domain bus**: `mesh-domain-bus`. Resource policy allows `PutEvents` from same account only; explicit deny on all other accounts.
- **Forwarding rule**: `{domain}-forward-datameshy-events`. Matches `source = "datameshy"` (NOT `datameshy.central` -- that source is reserved for central-originated events). Forwards to the central bus ARN using a dedicated `EventBridgeForwarderRole`.
- **DLQ**: `{domain}-eventbridge-forward-dlq` (14-day retention, SSE-KMS).

### KMS

Domain CMK (`alias/mesh-{domain}`) with automatic key rotation. Key policy grants:
- Root account full control
- `GlueJobExecutionRole` encrypt/decrypt
- `lakeformation.amazonaws.com` service principal decrypt (for cross-account consumers)
- `DomainAdminRole` key administration
- `MeshAdminRole` in central account: read-only (break-glass)

## Key interactions

1. **Data-product module** consumes domain-account outputs: bucket names, catalog DB names, `GlueJobExecutionRole` ARN, `MeshEventRole` ARN, domain KMS key ARN, domain event bus ARN.
2. **EventBridge forwarding** sends all `source=datameshy` events from the domain bus to the central governance bus. The `MeshEventRole` denies publishing with `source=datameshy.central` to prevent source confusion.
3. **Lake Formation** governs all cross-account data access to the gold layer. The S3 bucket policy prevents direct S3 reads, forcing all access through LF.
4. **SSO trust** on human roles (`DomainAdminRole`, `DomainDataEngineerRole`, `DomainConsumerRole`) uses `arn:aws:iam::{account}:saml-provider/AWSSSO` with SAML audience condition.

## Configuration

| Variable | Type | Default | Description |
|---|---|---|---|
| `domain` | `string` | required | Domain name (lowercase alphanumeric + hyphens). Used in all resource naming. |
| `environment` | `string` | `"dev"` | Deployment environment |
| `aws_region` | `string` | `"us-east-1"` | AWS region |
| `aws_org_id` | `string` | required | AWS Organization ID for bucket policy OrgID condition |
| `central_account_id` | `string` | required | Governance account ID for KMS break-glass access |
| `central_event_bus_arn` | `string` | required | ARN from governance module output |
| `mesh_catalog_writer_role_arn` | `string` | required | ARN from governance module output |
| `sso_identity_store_id` | `string` | `""` | IAM Identity Center store ID |
| `tags` | `map(string)` | `{}` | Additional tags |

## Gotchas and constraints

- **Gold bucket deny policy is the security linchpin.** If you relax the `DenyDirectGetObjectExceptLFAndGlue` statement, consumers can bypass Lake Formation and read data directly via S3.
- **Permission boundary is applied, not just inline policy.** Even if someone modifies the inline policy on `GlueJobExecutionRole` to grant broader S3 access, the boundary restricts S3 to domain buckets only.
- **EventBridge forwarding only matches `source=datameshy`.** Events with source `datameshy.central` are NOT forwarded. This prevents loops where central-originated events get re-injected into domain buses.
- **Raw bucket lifecycle is aggressive.** Data transitions to Glacier after 90 days and is deleted after 365 days. Adjust if you need longer raw retention.
- **`MeshEventRole` trusts both Lambda and Step Functions.** The same role is used as the Step Functions execution role for the medallion pipeline and as the Lambda execution role for event-publishing Lambdas.

## See also

- [Governance](GOVERNANCE.md) -- central account module that this domain connects to
- [Data Product](DATA-PRODUCT.md) -- per-product resources created within a domain
- [Pipeline Templates](PIPELINE-TEMPLATES.md) -- Glue jobs and Step Functions that run in the domain
- [Monitoring](MONITORING.md) -- CloudWatch alarms and budgets for the domain account
