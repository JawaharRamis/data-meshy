# Account Structure

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
← [Security](SECURITY.md) | [Next →](MEDIATION-PIPELINE.md) | [↑ Docs home](../README.md)

---

## Overview

Data Meshy uses a multi-account AWS layout: one management account for Organizations, one central governance account for the mesh control plane, and one account per domain. Each domain account is provisioned from the same Terraform module, making onboarding repeatable.

## Organization Layout

```
+---------------------------------------------------------------------+
| AWS ORGANIZATION                                                     |
| Management Account (orgs management, SCPs, IAM Identity Center)     |
|                                                                      |
|   Platform OU                         Domain OU                     |
|   +-- Central Governance Acct         +-- Sales Account             |
|   |   (mesh control plane)            +-- Marketing Account         |
|   |                                  +-- (future domains ...)       |
|   +-- SCP: DataMeshyPlatformGuardrails  SCP: DataMeshyDomainGuardrails|
+---------------------------------------------------------------------+
```

### Organizational Units

| OU | SCP | Accounts | Purpose |
|---|---|---|---|
| **Platform OU** | `DataMeshyPlatformGuardrails` (MFA for MeshAdminRole) | Central Governance | Catalog, events, governance, subscription workflows |
| **Domain OU** | `DataMeshyDomainGuardrails` (7 controls) | Sales, Marketing, future domains | Domain-owned data products |

SCP definitions: `infra/environments/central/scps.tf`

## Central Governance Account

The mesh control plane. All catalog state, event routing, governance policies, and subscription workflows live here. Domain accounts never have direct access to central resources.

### Resources

| Resource | Name / Convention | Purpose | Source |
|---|---|---|---|
| **DynamoDB Tables (7)** | | | `infra/modules/governance/dynamodb.tf` |
| | `mesh-domains` | Domain registry (PK: `domain_name`) | line 8 |
| | `mesh-products` | Product catalog (PK: `domain#product_name`, GSI1: `tag`, GSI2: `classification`, GSI3: `domain`) | line 34 |
| | `mesh-subscriptions` | Active subscriptions (PK: `product_id`, SK: `subscriber_account_id`, GSI1: `subscriber_domain`) | line 93 |
| | `mesh-quality-scores` | Quality score history (PK: `product_id`, SK: `timestamp`) | line 136 |
| | `mesh-audit-log` | Append-only audit trail (PK: `event_id`, SK: `timestamp`, GSI1: `domain`, GSI2: `event_type`) | line 168 |
| | `mesh-event-dedup` | Idempotency (PK: `event_id`, TTL: 24h) | line 224 |
| | `mesh-pipeline-locks` | Concurrent run prevention (PK: `product_id`, SK: `lock_key`, TTL: configurable) | line 255 |
| **EventBridge** | | | `infra/modules/governance/eventbridge.tf` |
| | `mesh-central-bus` | Central event bus with explicit domain account allow-list | line 7 |
| | `mesh-events` (Schema Registry) | JSON Schema definitions for all 10 event types | line 40 |
| | 5 routing rules | Catalog update, subscription workflow, quality alerts, pipeline failures, all-events audit | lines 142-260 |
| **SQS DLQs (3)** | | | `infra/modules/governance/eventbridge.tf` |
| | `mesh-catalog-dlq` | Failed catalog update targets (14-day retention, KMS) | line 50 |
| | `mesh-audit-dlq` | Failed audit/alert targets | line 61 |
| | `mesh-subscription-dlq` | Failed subscription workflow targets | line 72 |
| **SNS Topics (4)** | | | `infra/modules/governance/outputs.tf` |
| | `mesh-quality-alerts` | Quality, freshness, schema alerts | line 142 |
| | `mesh-pipeline-failures` | Pipeline failures, DLQ alarms | line 148 |
| | `mesh-freshness-violations` | SLA breach notifications | line 154 |
| | `mesh-subscription-requests` | Subscription request notifications | line 160 |
| **KMS** | `alias/mesh-central` | Central account CMK for DynamoDB, SQS, SNS encryption | `infra/modules/governance/outputs.tf:182` |
| **IAM Roles (7)** | See [SECURITY.md](SECURITY.md) for full detail | Decomposed, no god-roles | `infra/modules/governance/iam.tf` |
| **API Gateway** | IAM Auth | Mesh Control Plane API (CLI backend) | `plan/ARCHITECTURE.md` |
| **Step Functions** | Subscription approval workflow | Cross-account LF grant saga | Phase 2 |

### DynamoDB Table Summary

All tables use `PAY_PER_REQUEST` billing, SSE with AWS-managed KMS, and PITR enabled.

```
mesh-domains
  PK: domain_name (S)
  No GSIs

mesh-products
  PK: domain#product_name (S)
  GSI1: tag (S)             -- search by tag
  GSI2: classification (S)  -- search by classification
  GSI3: domain (S)          -- list products in a domain

mesh-subscriptions
  PK: product_id (S)
  SK: subscriber_account_id (S)
  GSI1: subscriber_domain (S)

mesh-quality-scores
  PK: product_id (S)
  SK: timestamp (S)

mesh-audit-log
  PK: event_id (S)
  SK: timestamp (S)
  GSI1: domain + timestamp  -- query audit by domain
  GSI2: event_type + timestamp  -- query audit by event type

mesh-event-dedup
  PK: event_id (S)
  TTL: expires_at (24 hours)

mesh-pipeline-locks
  PK: product_id (S)
  SK: lock_key (S)
  TTL: expires_at (3 hours)
```

## Domain Account

Provisioned from `infra/modules/domain-account/`. Each domain gets identical infrastructure, parameterized by the `domain` variable (e.g., `sales`, `marketing`).

### Resources

| Resource | Name / Convention | Purpose | Source |
|---|---|---|---|
| **S3 Buckets (3)** | | | `infra/modules/domain-account/s3.tf` |
| | `{domain}-raw-{account_id}` | Bronze layer. Lifecycle: Glacier at 90d, expire at 365d. SSE-KMS + Bucket Keys. Cross-account deny. | line 19 |
| | `{domain}-silver-{account_id}` | Validated layer. SSE-KMS + Bucket Keys. Cross-account deny. | line 131 |
| | `{domain}-gold-{account_id}` | Data product layer. SSE-KMS + Bucket Keys. LF bypass prevention policy. | line 219 |
| **Glue Catalog DBs (3)** | | | `infra/modules/domain-account/outputs.tf` |
| | `{domain}_raw` | Raw layer Iceberg tables | line 28 |
| | `{domain}_silver` | Silver layer Iceberg tables | line 33 |
| | `{domain}_gold` | Gold layer Iceberg tables (the shareable product) | line 38 |
| **KMS** | `alias/mesh-{domain}` | Domain CMK. Used for S3 SSE-KMS. Key policy grants decrypt to: domain roles + LF service. | `infra/modules/domain-account/outputs.tf:59` |
| **EventBridge** | `mesh-domain-bus` | Local event bus. Resource policy: same-account only. Rule forwards to central bus. | `infra/modules/domain-account/outputs.tf:53` |
| **Lake Formation** | | | `infra/modules/domain-account/lakeformation.tf` |
| | LF admins: GlueJobExecutionRole, DomainAdminRole | Data lake admin settings | line 16 |
| | LF-Tags: `domain`, `classification`, `pii` | Tag-based access control | lines 27-46 |
| | LF-Tag on gold DB: `domain={domain}` | Tag binding | line 53 |
| | LF grants to GlueJobExecutionRole | CREATE_TABLE + DESCRIBE on raw/silver, + ALTER/DROP on gold | lines 74-114 |
| | LF grants to DomainDataEngineerRole | DESCRIBE on all 3 DBs (catalog browsing) | lines 120-151 |
| **IAM Roles (5)** | See [SECURITY.md](SECURITY.md) for full detail | DomainAdmin, DataEngineer, Consumer, GlueJobExecution, MeshEvent | `infra/modules/domain-account/iam.tf` |
| **Athena Workgroup** | Consumer workgroup | Query access for subscribers. Result encryption with domain KMS key. | Phase 2 |

### S3 Bucket Naming Convention

```
{domain}-raw-{account_id}       e.g., sales-raw-123456789012
{domain}-silver-{account_id}    e.g., sales-silver-123456789012
{domain}-gold-{account_id}      e.g., sales-gold-123456789012
```

The account ID suffix guarantees global uniqueness and prevents bucket name collisions across domains.

### Glue Catalog Database Naming Convention

```
{domain}_raw       e.g., sales_raw
{domain}_silver    e.g., sales_silver
{domain}_gold      e.g., sales_gold
```

## Cross-Account Trust Patterns

### EventBridge (Domain -> Central)

```
Domain Account                          Central Account
+-----------------+  PutEvents          +------------------+
| MeshEventRole   |------------------->| mesh-central-bus  |
| (trust: lambda, |  (cross-account)   | (resource policy: |
|  states)        |                    |  explicit list of |
|                 |                    |  domain account   |
|                 |                    |  IDs, no wildcards)|
+-----------------+                    +------------------+
```

- Domain's `MeshEventRole` has `events:PutEvents` on the central bus ARN
- Central bus resource policy explicitly lists each domain account ID
- Domain bus rule auto-forwards `source: datameshy` events to central
- `MeshEventRole` cannot emit `source: datameshy.central` (explicit Deny)

### Lake Formation (Central -> Domain Gold)

```
Consumer Account        Central Account              Producer Account
+------------------+   +--------------------+   +------------------+
| Resource Link    |   | MeshLFGrantorRole  |   | Gold S3 Bucket   |
| (in consumer     |<--| grants SELECT on   |-->| (LF bypass deny  |
|  Glue Catalog)   |   | gold_* tables      |   |  policy)         |
|                  |   | (LF V3+ grants,    |   |                  |
| Athena queries   |   |  LF-Tag policies,  |   | LF SLR reads    |
| via LF grant     |   |  BatchGrantPerms)  |   | on behalf of    |
+------------------+   +--------------------+   | consumer        |
                                                  +------------------+
```

- Cross-account LF grant version: V3+ (single RAM share per account pair, not one per table)
- LF-Tag-based policies scale linearly; named resource grants do not
- `BatchGrantPermissions` used to prevent `ConcurrentModificationException`
- Gold bucket policy allows S3 reads only from LF service-linked role and GlueJobExecutionRole

### IAM AssumeRole (SSO -> Domain Roles)

```
IAM Identity Center (Organization-level)
|
+-- Permission Set: DomainDataEngineer
|   Inline Policy: sts:AssumeRole -> DomainDataEngineerRole
|
+-- Group: sales-engineers
    Assigned to: Sales account
    Permission Set: DomainDataEngineer
```

Users authenticate via SSO (`aws sso login`), get temporary credentials mapped to domain-specific IAM roles. No long-lived access keys.

## OIDC Federation for GitHub Actions

GitHub Actions authenticates via OIDC token exchange -- no stored AWS credentials.

```
GitHub Actions Runner
  |
  +-- OIDC token (JWT from token.actions.githubusercontent.com)
  |     |
  |     +-- sts:AssumeRoleWithWebIdentity
  |           |
  |           +-- TerraformPlanRole (any branch, read-only)
  |           +-- TerraformApplyRole (main branch only, write)
  |
  +-- .github/workflows/infra-plan.yml  -> TerraformPlanRole
  +-- .github/workflows/infra-apply.yml -> TerraformApplyRole
```

### OIDC Configuration

| Parameter | Value | Source |
|---|---|---|
| Provider URL | `https://token.actions.githubusercontent.com` | `infra/modules/governance/iam.tf:481` |
| Client ID | `sts.amazonaws.com` | line 485 |
| Audience condition | `token.actions.githubusercontent.com:aud = sts.amazonaws.com` | line 512 |
| Subject condition (Plan) | `repo:{org}/{repo}:*` (any branch) | line 516 |
| Subject condition (Apply) | `repo:{org}/{repo}:ref:refs/heads/main` (main only) | line 584 |
| Session duration | 1 hour max | line 569 |

All GitHub Actions are pinned to commit SHAs (not version tags). Dependabot keeps them current.

Outputs for CI configuration: `infra/environments/central/oidc.tf`

## Terraform Backend Isolation

Each environment has its own S3 backend config. No shared state between environments.

```
infra/environments/
  central/          -> S3 backend: datameshy-tfstate-central-{account}
```

Domain team infrastructure lives in isolated domain repos (generated by
`datameshy domain init`). Each domain repo manages its own Terraform state
in a bucket named `datameshy-tfstate-{domain}-{account}`.

Backend configuration (defined in `infra/shared/backend.tf`):
- SSE-KMS encryption with dedicated CMK
- Versioning enabled
- DynamoDB state locking
- Access restricted to `TerraformApplyRole` only

Provider versions are pinned to exact versions in `infra/shared/versions.tf`.

## SSO / IAM Identity Center

### Permission Sets

| Permission Set | Maps To | Session | Account Scope | Source |
|---|---|---|---|---|
| `MeshPlatformAdmin` | Central: `MeshAdminRole` | 1 hour | Central account only | `infra/environments/central/identity_center.tf:21` |
| `DomainAdmin` | Domain: `DomainAdminRole` | 8 hours | Assigned domain account | line 52 |
| `DomainDataEngineer` | Domain: `DomainDataEngineerRole` | 8 hours | Assigned domain account | line 72 |
| `DomainConsumer` | Domain: `DomainConsumerRole` | 8 hours | Assigned domain account | line 86 |
| `GovernanceViewer` | Central: `GovernanceReadRole` | 8 hours | Central account only | line 100 |

### Identity Store Groups

| Group | Permission Set | Account | Source |
|---|---|---|---|
| `platform-engineers` | `MeshPlatformAdmin` | Central | `identity_center.tf:134` |
| `sales-engineers` | `DomainDataEngineer` | Sales | line 139 |
| `sales-admins` | `DomainAdmin` | Sales | line 146 |
| `marketing-analysts` | `DomainConsumer` | Marketing | line 152 |
| `governance-team` | `GovernanceViewer` | Central | line 159 |

MFA is required for all permission sets, enforced at the Identity Center level.

### CLI Authentication Flow

1. User runs `aws sso login --profile <domain-profile>`
2. Browser-based SSO login with MFA challenge
3. Temporary credentials cached locally (session duration per permission set)
4. CLI commands use the SSO profile: `datameshy --profile sales-engineer product create ...`
5. For cross-account operations, the CLI assumes a cross-account role via `sts:AssumeRole` using the SSO session as source credential

## Onboarding a New Domain

To add a new domain (e.g., `finance`):

1. Create AWS account under Domain OU in Organizations
2. Create `infra/environments/domain-finance/` with backend.tf pointing to a new S3 state bucket
3. Instantiate the `domain-account` module with `domain = "finance"`
4. Add the new account ID to the governance module's `domain_account_ids` variable (updates central bus resource policy)
5. Create Identity Store group and permission set assignment
6. Run `datameshy domain onboard` to emit `DomainOnboarded` event

## Related Files

| File | Purpose |
|---|---|
| `infra/modules/governance/dynamodb.tf` | 7 DynamoDB tables |
| `infra/modules/governance/iam.tf` | 7 central IAM roles, OIDC provider |
| `infra/modules/governance/eventbridge.tf` | Central bus, Schema Registry, rules, DLQs |
| `infra/modules/domain-account/s3.tf` | 3 S3 buckets with policies |
| `infra/modules/domain-account/iam.tf` | 5 domain IAM roles |
| `infra/modules/domain-account/lakeformation.tf` | LF admin, LF-Tags, LF grants |
| `infra/modules/domain-account/outputs.tf` | Domain account outputs (shared contract) |
| `infra/modules/governance/outputs.tf` | Governance outputs (shared contract) |
| `infra/environments/central/scps.tf` | Domain OU and Platform OU SCPs |
| `infra/environments/central/oidc.tf` | OIDC role outputs for CI |
| `infra/environments/central/identity_center.tf` | SSO permission sets and groups |
| `plan/ARCHITECTURE.md:276` | Authentication model specification |
