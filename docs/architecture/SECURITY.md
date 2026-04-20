# Security Architecture

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
← [Event Mesh](EVENT-MESH.md) | [Next →](ACCOUNT-STRUCTURE.md) | [↑ Docs home](../README.md)

---

## Overview

Security in Data Meshy is built on decomposed IAM roles (no god-roles), Lake Formation for data access control, per-domain KMS encryption, SCPs for guardrails, and S3 bucket policies that prevent Lake Formation bypass. Every layer has an enforcement point.

## IAM Roles

### Central Account Roles (7 Roles)

| Role | Trust | Permissions | Cannot Do | Source |
|---|---|---|---|---|
| `MeshLFGrantorRole` | `lambda.amazonaws.com` | LF `GrantPermissions`/`RevokePermissions` on `gold_*` tables only. Permission boundary restricts to SELECT-only grants. | Write to DynamoDB, read audit logs, grant non-SELECT permissions | `infra/modules/governance/iam.tf:73` |
| `MeshCatalogWriterRole` | `lambda.amazonaws.com` | `dynamodb:PutItem`/`UpdateItem`/`GetItem`/`Query` on catalog tables. IAM condition `dynamodb:LeadingKeys` restricts writes to caller's domain prefix. | Call Lake Formation APIs, read/write audit log | `infra/modules/governance/iam.tf:151` |
| `MeshAuditWriterRole` | `lambda.amazonaws.com` | `dynamodb:PutItem` ONLY on `mesh-audit-log`. Explicit Deny on `UpdateItem`, `DeleteItem`, `BatchWriteItem`. | Modify or delete any audit record, access any other table | `infra/modules/governance/iam.tf:233` |
| `GovernanceReadRole` | SSO (SAML) + `sso.amazonaws.com` | Read-only on all 7 DynamoDB tables + Glue Catalog `GetTable`/`GetDatabase`/`GetPartitions` | Write to any resource | `infra/modules/governance/iam.tf:306` |
| `MeshAdminRole` | Account root (MFA required) + `TerraformApplyRole` | AdministratorAccess (break-glass + Terraform apply only). Session: 1 hour max. CloudWatch alarm on any assumption. | Use without MFA | `infra/modules/governance/iam.tf:401` |
| `TerraformPlanRole` | GitHub Actions OIDC (any branch) | Read-only: `s3:GetObject`, `dynamodb:GetItem`, `kms:Decrypt`, `glue:Get*`, `lakeformation:Get*`, `events:Describe*` | Write to any resource | `infra/modules/governance/iam.tf:496` |
| `TerraformApplyRole` | GitHub Actions OIDC (main branch only) | AdministratorAccess. OIDC subject condition: `ref:refs/heads/main` | Use from non-main branches | `infra/modules/governance/iam.tf:566` |

### Domain Account Roles (5 Roles per Domain)

| Role | Trust | Permissions | Cannot Do | Source |
|---|---|---|---|---|
| `DomainAdminRole` | SSO (SAML) | Full S3 access (own buckets), Glue catalog R/W (own DBs), Step Functions R/W, Secrets Manager read, KMS decrypt | Modify LF permissions, access other domains' buckets | `infra/modules/domain-account/iam.tf:56` |
| `DomainDataEngineerRole` | SSO (SAML) | S3 R/W (own buckets), Glue jobs/catalog, Step Functions start/describe, KMS decrypt | Modify LF permissions, access outside own domain S3 (enforced by permission boundary) | `infra/modules/domain-account/iam.tf:193` |
| `DomainConsumerRole` | SSO (SAML) | Athena query access, S3 access to Athena results bucket, Glue catalog read, KMS decrypt | Write to any data bucket, start Glue jobs | `infra/modules/domain-account/iam.tf:305` |
| `GlueJobExecutionRole` | `glue.amazonaws.com` | S3 R/W on all 3 medallion buckets, Glue catalog access, Secrets Manager (domain-scoped), KMS decrypt, LF `GetDataAccess` | Access buckets outside own domain (enforced by permission boundary) | `infra/modules/domain-account/iam.tf:400` |
| `MeshEventRole` | `lambda.amazonaws.com` + `states.amazonaws.com` | `events:PutEvents` on central bus + domain bus. Deny: `datameshy.central` source. Glue job start/stop. | Emit central-only events, access DynamoDB | `infra/modules/domain-account/iam.tf:515` |

### Permission Boundaries

Two permission boundaries enforce S3 isolation:

1. **MeshLFGrantorBoundary** (`infra/modules/governance/iam.tf:19`): Restricts LFGrantorRole to SELECT-only grants. Even if the role policy is misconfigured, the boundary prevents granting ALTER, DROP, or INSERT.

2. **DomainS3PermissionBoundary** (`infra/modules/domain-account/iam.tf:17`): Applied to `GlueJobExecutionRole` and `DomainDataEngineerRole`. Restricts S3 access to `{domain}-raw-*`, `{domain}-silver-*`, `{domain}-gold-*` buckets only. All non-S3 actions are unrestricted (boundary only constrains S3 scope).

## Lake Formation

### LF-Tags

Three LF-Tag dimensions are defined per domain account in `infra/modules/domain-account/lakeformation.tf:27-46`:

| Tag Key | Values | Purpose |
|---|---|---|
| `domain` | `sales`, `marketing`, ... | Scope access by domain |
| `classification` | `public`, `internal`, `confidential`, `restricted` | Drive auto-approve vs manual review for subscriptions |
| `pii` | `true`, `false` | Column-level PII flag; PII columns excluded from LF grants unless explicitly approved |

The `domain` LF-Tag is applied to the entire gold Glue catalog database. Individual tables get `classification` + `pii` tags from the `data-product` module.

### LF Cross-Account Requirements

From ADR-004 (`plan/ARCHITECTURE.md:213-221`):

- **V3+ grant mode**: Must use Version 3+ (`UpdateLakeFormationIdentityCenterConfiguration`). V1/V2 created one RAM resource share per grant, exhausting the 50-share limit. V3+ uses a single consolidated share per account pair.
- **LF-Tag-based policies**: Scale linearly. Named resource grants require one `GrantPermissions` call per table per consumer and cause `ConcurrentModificationException` at scale.
- **Batch operations**: Use `BatchGrantPermissions`/`BatchRevokePermissions` to reduce contention.
- **Resource link schema propagation**: AWS does not document automatic schema propagation through resource links. Phase 2 integration tests must validate this explicitly.

### LF Grants Within Domain

`GlueJobExecutionRole` gets database-level permissions in the domain account:

- **Raw DB**: `CREATE_TABLE`, `DESCRIBE`
- **Silver DB**: `CREATE_TABLE`, `DESCRIBE`
- **Gold DB**: `CREATE_TABLE`, `DESCRIBE`, `ALTER`, `DROP`

`DomainDataEngineerRole` gets `DESCRIBE` on all three databases (catalog browsing).

Source: `infra/modules/domain-account/lakeformation.tf:74-151`

### LF Admin Monitoring

- Central account is LF admin only during Terraform apply (provisioning time)
- Runtime Lambdas use `MeshLFGrantorRole` (SELECT-only grants)
- CloudTrail data events capture LF API calls
- Alert on any `GrantPermissions` call NOT from `MeshLFGrantorRole`

## S3 Bucket Policies

### Common Policies (All Buckets)

Applied to raw, silver, and gold buckets in `infra/modules/domain-account/s3.tf`:

| Policy | Effect | Condition |
|---|---|---|
| `DenyNonTLS` | Deny all S3 actions if not HTTPS | `aws:SecureTransport = false` |
| `DenyNonOrgAccess` | Deny all S3 actions from outside the Organization | `aws:PrincipalOrgID != {org_id}` |
| Public access block | Block all public ACLs and policies | All four public access settings enabled |
| SSE-KMS + Bucket Keys | Server-side encryption with domain CMK, `bucket_key_enabled = true` | -- |

### Raw and Silver Buckets: Cross-Account Deny

An additional policy statement denies ALL cross-account access:

```
Effect: Deny
Action: s3:*
Condition: aws:PrincipalAccount != {own_account_id}
```

Source: `infra/modules/domain-account/s3.tf:107-120` (raw), `infra/modules/domain-account/s3.tf:195-209` (silver)

### Gold Bucket: LF Bypass Prevention

This is the critical security policy. It prevents direct S3 reads that would bypass Lake Formation:

```
Effect: Deny
Principal: *
Action: s3:GetObject
Resource: {domain}-gold-{account}/*
Condition:
  StringNotLike:
    aws:PrincipalArn:
      - arn:aws:iam::{account}:role/GlueJobExecutionRole
      - arn:aws:iam::{account}:role/aws-service-role/lakeformation.amazonaws.com/*
```

Only two principals can read gold data directly:
1. `GlueJobExecutionRole` -- ETL jobs that write/read gold data
2. Lake Formation service-linked role -- serves cross-account consumers via LF grants

No other IAM role can read gold data from S3, even with `s3:GetObject` permission. Lake Formation becomes the mandatory access path.

Source: `infra/modules/domain-account/s3.tf:250-310`

### Raw Bucket Lifecycle

Raw data transitions to Glacier after 90 days and expires after 365 days. Noncurrent versions expire after 30 days.

Source: `infra/modules/domain-account/s3.tf:50-72`

## KMS Encryption

### Encryption Strategy

| Resource | Encryption | Key |
|---|---|---|
| Domain S3 buckets (all 3) | SSE-KMS | Per-domain CMK (`alias/mesh-{domain}`) |
| DynamoDB tables | SSE with AWS-managed KMS | Default (no extra cost) |
| Terraform state bucket | SSE-KMS | Dedicated CMK |
| Athena results | SSE-KMS | Consumer domain's key |
| SQS DLQs | SSE-KMS | Central CMK (`alias/mesh-central`) |
| SNS topics | KMS encryption at rest | Central CMK |

### S3 Bucket Keys (Mandatory)

All S3 buckets using SSE-KMS have `bucket_key_enabled = true`. Without Bucket Keys, every S3 GET/PUT triggers a KMS API call. At cross-account data mesh scale, this generates millions of KMS requests per month per active product. Bucket Keys reduce KMS API calls by up to 99% by generating a short-lived data key per bucket.

This is a Terraform one-liner with no security trade-off:

```hcl
bucket_key_enabled = true
```

### Key Policy

Domain KMS key grants decrypt to:
- Domain's own roles (`GlueJobExecutionRole`, `DomainAdminRole`, `DomainDataEngineerRole`, `DomainConsumerRole`)
- Lake Formation service principal (for cross-account reads via LF grants)

Cross-account consumers get decrypt access only through LF, never through direct KMS key policy grants.

### Why Not SSE-S3

SSE-S3 provides no independent access control layer. Anyone with `s3:GetObject` can read data. SSE-KMS adds a second enforcement point (KMS key policy) and enables `kms:Decrypt` event logging in CloudTrail.

## Service Control Policies

Two SCPs are defined in `infra/environments/central/scps.tf`.

### Domain OU SCP: `DataMeshyDomainGuardrails`

Source: `infra/environments/central/scps.tf:13-171`

| # | Control | Effect |
|---|---|---|
| 1 | `DenyCloudTrailLogDeletion` | Block `cloudtrail:DeleteTrail`, `cloudtrail:StopLogging`, `logs:DeleteLogGroup`, `logs:DeleteLogStream` |
| 2 | `DenyDisablingLakeFormation` | Block `lakeformation:DeregisterResource`, `RevokePermissions`, `PutDataLakeSettings` -- except from `MeshLFGrantorRole`, `TerraformApplyRole`, `MeshAdminRole` |
| 3 | `RequireS3SSEKMS` | Deny `s3:PutObject` unless `s3:x-amz-server-side-encryption = aws:kms` (not just any encryption) |
| 4 | `DenyPublicS3` + `DenyDisableS3BlockPublicAccess` | Deny public ACLs and turning off Block Public Access settings |
| 5 | `RestrictRegionToUsEast1` | Deny all non-global actions outside `us-east-1` |
| 6 | `DenyCrossOrgAssumeRole` | Deny `sts:AssumeRole` targeting accounts outside the Organization (prevents lateral movement) |
| 7 | `DenyGlueJobsOver4DPU` | Deny `glue:CreateJob`/`glue:UpdateJob` with `MaxCapacity > 4` (cost guardrail) |

### Platform OU SCP: `DataMeshyPlatformGuardrails`

Source: `infra/environments/central/scps.tf:176-203`

| Control | Effect |
|---|---|
| `RequireMFAForAdminRole` | Deny `sts:AssumeRole` on `MeshAdminRole` unless MFA is present |

SCP attachment to OUs requires OU IDs (one-time manual step or managed in management account Terraform). See commented-out `aws_organizations_policy_attachment` resources in `scps.tf:228-238`.

## MeshAdminRole Assumption Alarm

A CloudWatch metric filter watches for `AssumeRole` calls targeting `*MeshAdminRole` in the CloudTrail log group. An alarm triggers on any assumption, routing to the `mesh-pipeline-failures` SNS topic.

Source: `infra/modules/governance/iam.tf:446-475`

## DynamoDB Row-Level Isolation

Domain accounts never have direct DynamoDB access. All writes go through central Lambdas. Each Lambda validates:

1. **Event source validation**: Extract `account` from EventBridge event envelope (set by AWS, not caller-controlled). Verify it matches the registered `account_id` for the domain. Reject mismatches and write `SECURITY_ALERT` to audit log.

2. **Partition key enforcement**: `MeshCatalogWriterRole` IAM policy includes condition `dynamodb:LeadingKeys` that restricts writes to keys matching the caller's domain prefix:
   ```json
   "ForAllValues:StringLike": {
     "dynamodb:LeadingKeys": ["${aws:PrincipalTag/domain}*"]
   }
   ```

3. **Audit log is append-only**: `MeshAuditWriterRole` has `PutItem` only. No role except `MeshAdminRole` (break-glass, MFA, alarmed) can `UpdateItem` or `DeleteItem` on the audit table.

## PII Protection (Defense-in-Depth)

Column-level LF filtering is the primary control, but multiple layers provide defense-in-depth:

| Layer | Control | What It Catches |
|---|---|---|
| Product Spec | `pii: true/false` per column in `product.yaml` | Explicit PII declaration |
| Schema Validation | Pipeline Lambda: undeclared columns block publish | Accidental PII in undeclared columns |
| Lake Formation | Cross-account grants exclude PII columns | Unauthorized PII access via Athena |
| S3 Bucket Policy | Gold bucket denies direct `s3:GetObject` | Bypassing LF by reading files directly |
| KMS Key Policy | Domain key decrypt only for domain roles + LF service | Data unreadable without key access |
| Athena Workgroup | Consumer workgroup enforces result encryption | PII leaking through unencrypted query results |
| Macie (Phase 4) | Weekly PII scan on gold S3 buckets | Undeclared PII |
| Subscription Review | Approval flags quasi-identifier combinations | Re-identification risk |

## Secrets Management

- Source database credentials stored in Secrets Manager in each domain account
- Secret ARN referenced in `product.yaml` under `lineage.sources[].credentials_secret_arn`
- `GlueJobExecutionRole` has `secretsmanager:GetSecretValue` on the specific secret ARN only (`{domain}/*` path)
- Secrets rotated on a 90-day schedule
- No secrets in Terraform variables, environment variables, or Glue job scripts

## Related Files

| File | Purpose |
|---|---|
| `infra/modules/governance/iam.tf` | 7 central IAM roles, OIDC provider, MeshAdmin alarm |
| `infra/modules/domain-account/iam.tf` | 5 domain IAM roles, permission boundaries |
| `infra/modules/domain-account/s3.tf` | Bucket policies (LF bypass prevention, cross-account deny) |
| `infra/modules/domain-account/lakeformation.tf` | LF-Tags, LF grants, LF admin settings |
| `infra/environments/central/scps.tf` | Domain OU and Platform OU SCPs |
| `plan/ARCHITECTURE.md:343` | Full security architecture specification |
