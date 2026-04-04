# ADR-002: Decomposed IAM roles over god-roles

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03

## Navigation
← [Decisions](../README.md) | [↑ Docs home](../../README.md)

## Status
Accepted

## Context

Data Meshy is a multi-account data mesh with a central governance account and multiple domain accounts. Each account runs Lambda functions, Step Functions, and Glue jobs that need specific AWS permissions. The platform must enforce least-privilege access because:

- Subscription approval Lambdas grant Lake Formation permissions. A compromised role here could expose any domain's data to any account.
- Catalog writer Lambdas update DynamoDB tables (products, subscriptions, quality scores). A compromised role here could corrupt catalog metadata for every domain.
- Audit log writes must be tamper-proof. No role should be able to modify or delete audit records.
- Domain data engineers need S3 and Glue access within their domain but must never cross into another domain's resources.
- Glue ETL jobs need S3 read/write and catalog access but should never be able to escalate to IAM or LF permission management.

The original design used a single `MeshControlPlaneRole` with broad permissions for all central Lambda functions. This created a single point of compromise: any vulnerability in any Lambda handler gave an attacker the union of all permissions (LF grants, DynamoDB writes, audit log access).

Alternatives considered:

- **Single god-role per account**: Simple to manage. One `MeshControlPlaneRole` in central, one `DomainAdminRole` in each domain. Any compromise exposes all capabilities.
- **Role per Lambda function**: Maximum isolation but role explosion (15+ roles in central alone). Hard to audit and maintain.
- **Decomposed roles by function**: Group capabilities into a small number of scoped roles (grant, catalog write, audit write, read-only). Each role has the minimum permissions for its function. Permission boundaries add a second enforcement layer.

## Decision

We decompose IAM into function-scoped roles with permission boundaries. No god-roles exist at runtime.

### Central governance account (7 roles)

| Role | Trust | Permissions | Cannot Do |
|------|-------|-------------|-----------|
| `MeshLFGrantorRole` | Lambda | `lakeformation:GrantPermissions`, `RevokePermissions`, `BatchGrantPermissions`, `BatchRevokePermissions` on gold tables only. Permission boundary restricts to SELECT-only grants. IAM condition: `lakeformation:ResourceArn` must match `table/*/gold_*`. | Write to DynamoDB, read audit logs, grant ALTER/DROP/INSERT |
| `MeshCatalogWriterRole` | Lambda | `dynamodb:PutItem`, `UpdateItem`, `GetItem`, `Query` on `mesh-products`, `mesh-domains`, `mesh-subscriptions`, `mesh-quality-scores`. IAM condition: `dynamodb:LeadingKeys` restricts writes to the caller's domain prefix. | Call Lake Formation APIs, read/write audit log |
| `MeshAuditWriterRole` | Lambda | `dynamodb:PutItem` only on `mesh-audit-log`. Explicit deny on `UpdateItem`, `DeleteItem`, `BatchWriteItem`. | Modify or delete any audit record, access any other table |
| `GovernanceReadRole` | SSO (SAML) | Read-only on all DynamoDB tables + Glue `GetTable`/`GetDatabase` across accounts. KMS decrypt. | Write to any resource |
| `MeshAdminRole` | MFA-required + OIDC | AdministratorAccess. CloudWatch alarm fires on any assumption. 1-hour max session. Used only for Terraform apply and break-glass. | Nothing (by design -- but alarmed and time-limited) |
| `TerraformPlanRole` | GitHub Actions OIDC | Read-only: S3 Get/List, DynamoDB Get/Describe, IAM Get/List, Glue/LF Get/List. Any branch. | Write to any resource |
| `TerraformApplyRole` | GitHub Actions OIDC | AdministratorAccess. Restricted to main branch only via OIDC subject condition. | N/A (provisioning role) |

### Domain account (5 roles)

| Role | Trust | Permissions | Cannot Do |
|------|-------|-------------|-----------|
| `DomainAdminRole` | SSO | Full S3/Glue/Step Functions/Secrets/KMS within domain. Explicit deny on LF permission management (`GrantPermissions`, `RevokePermissions`, `BatchGrantPermissions`, `BatchRevokePermissions`). | Modify LF grants, access other domains |
| `DomainDataEngineerRole` | SSO | S3 read/write on domain buckets, Glue jobs and catalog, Step Functions read/start, CloudWatch Logs, KMS. Permission boundary restricts S3 to own domain buckets. | Modify LF grants, access other domain buckets |
| `DomainConsumerRole` | SSO | Athena query access, S3 for Athena results, Glue catalog read, KMS decrypt. Actual data access governed by Lake Formation grants. | Write to any data resource, modify Glue catalog |
| `GlueJobExecutionRole` | Glue service | S3 read/write on all 3 medallion layers, Glue catalog read/write, Secrets Manager (domain-scoped), KMS, CloudWatch Logs, LF `GetDataAccess`. Permission boundary restricts S3 to own domain buckets. | Assume other roles, access other domains, manage IAM/LF |
| `MeshEventRole` | Lambda + Step Functions | `events:PutEvents` on central bus and domain bus. Explicit deny on `events:source = datameshy.central` (reserved for central SFN). Glue `StartJobRun`/`GetJobRun`. CloudWatch Logs. X-Ray. | Emit events as `datameshy.central` source (prevents forged `SubscriptionApproved` events) |

### Enforcement mechanisms

1. **Permission boundaries** on `MeshLFGrantorRole`, `DomainDataEngineerRole`, and `GlueJobExecutionRole` add a second layer beyond inline policies. Even if an attacker modifies the inline policy, the boundary caps effective permissions.
2. **DynamoDB row-level isolation** via `dynamodb:LeadingKeys` IAM condition restricts `MeshCatalogWriterRole` to the caller's domain prefix. Lambda handlers also validate the event source account matches the registered domain.
3. **Audit log is append-only** -- `MeshAuditWriterRole` has `PutItem` only with an explicit deny on `UpdateItem`, `DeleteItem`, `BatchWriteItem`. Only `MeshAdminRole` (break-glass, MFA-required, alarmed) can modify audit records.
4. **Event source restriction** -- `MeshEventRole` in domain accounts cannot emit events with `source: datameshy.central`. The `SubscriptionApproved` event type (which triggers LF grants) is only emitted by central Step Functions.
5. **CloudWatch alarm** on any `MeshAdminRole` assumption -- alerts the platform team via SNS.
6. **SCPs** enforce MFA for admin roles, deny public S3 buckets, require SSE-KMS, restrict regions, cap Glue DPUs at 4, and prevent cross-account role assumption outside the Organization.

## Consequences

### Positive
- **Blast radius contained**: Compromising `MeshLFGrantorRole` gives only SELECT grant capability on gold tables. The attacker cannot read data, modify the catalog, or tamper with audit logs.
- **Audit log integrity**: The append-only enforcement at the IAM level (not just application logic) means no runtime compromise can delete or modify audit records.
- **Domain isolation**: Permission boundaries ensure domain roles cannot access another domain's S3 buckets even if inline policies are misconfigured.
- **Event injection prevention**: The `datameshy.central` source restriction prevents domain accounts from forging subscription approval events.
- **Defense-in-depth**: Permission boundaries, IAM conditions, S3 bucket policies, KMS key policies, and Lake Formation all enforce overlapping restrictions. Any single misconfiguration does not compromise the mesh.
- **Alarmed break-glass**: `MeshAdminRole` has full access but every assumption triggers an SNS alert and is limited to 1 hour.

### Negative
- **More roles to manage**: 12 roles across central and domain accounts (7 governance + 5 domain). Terraform modules abstract the complexity, but debugging permission issues requires understanding which role is used where.
- **IAM policy debugging is hard**: When a Lambda fails with AccessDenied, the engineer must trace which role the Lambda uses, check both the inline policy and the permission boundary, and verify IAM conditions. Mitigated by IAM Access Analyzer and explicit per-role documentation in the architecture plan.
- **Permission boundary confusion**: IAM evaluates both the policy and the boundary -- the effective permission is the intersection. Engineers unfamiliar with boundaries may be confused when a policy grants access but the boundary denies it.
- **Role proliferation at scale**: Each new domain adds 5 roles. At 15+ domains this is 75+ domain roles plus the 7 central roles. The Terraform module pattern keeps this manageable but the raw count is high.

## See also
- [Architecture: Security Architecture](../../plan/ARCHITECTURE.md) -- full security section with S3 bucket policies, encryption, SCPs
- [Architecture: Authentication Model](../../plan/ARCHITECTURE.md) -- SSO, OIDC, CLI authentication flows
- [Governance IAM](../../infra/modules/governance/iam.tf) -- Terraform definitions for central roles
- [Domain IAM](../../infra/modules/domain-account/iam.tf) -- Terraform definitions for domain roles
- [Architecture: Event Validation](../../plan/ARCHITECTURE.md) -- event source validation and injection prevention
