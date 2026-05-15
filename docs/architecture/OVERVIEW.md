# System Overview

> **Phase coverage**: Phase 5 | **Last updated**: 2026-05-15 | **Stale check**: Phase 6

## Navigation
← [Docs home](../README.md) | [Next →](MEDIATION-PIPELINE.md) | [↑ Docs home](../README.md)

---

## What Is Data Meshy

Data Meshy is an AWS platform for running a data mesh. Domain teams get infrastructure to own, produce, share, and consume data products. A central governance account enforces policies, maintains the catalog, and routes events. Domain accounts run independent medallion pipelines that produce Iceberg tables as shareable products.

The system is opinionated: Apache Iceberg for storage, Glue for ETL, Step Functions for orchestration, Lake Formation for cross-account sharing, EventBridge for mesh events. Domain teams follow the paved road (YAML files + GitHub Actions) and the platform handles all infrastructure provisioning on their behalf. The publishing contract — a compliant gold Iceberg table described by a `product.yaml` — is the real boundary.

## Design Principles

| Principle | How Data Meshy Enforces It |
|---|---|
| **Domain ownership** | One AWS account per domain. Domain teams own their S3 buckets, Glue catalog, pipelines, and IAM roles. No cross-domain access to raw or silver layers. |
| **Data-as-product** | Every data product has a `product.yaml` spec with schema, quality rules, SLA, and version. Only gold-layer tables are shared externally. |
| **Self-serve** | Domain teams author `product.yaml` (public contract) and `infra.yaml` (private infra config). Pushing to `main` in their DP repo is the entire interface. Platform reusable GitHub Actions workflows handle all Terraform provisioning. No pip install, no local Terraform for domain teams. |
| **Federated governance** | Central account defines SCPs, LF-Tags, and catalog schema. Domain accounts have autonomy within those guardrails. No god-roles. |
| **Event-driven** | All state changes emit events on EventBridge. Central catalog, audit log, and alerting are all subscribers — never called directly by domains. |

## System Diagram

```
+---------------------------------------------------------------------+
|                    AWS ORGANIZATION (Management Account)             |
|  Platform OU                    Domain OU                           |
|  +-- Central Governance Acct    +-- Sales Account                   |
|                                 +-- Marketing Account               |
+---------------------------------------------------------------------+

+---------------------------------------------------------------------+
|                   CENTRAL GOVERNANCE ACCOUNT                         |
|                                                                      |
|  +--------------+  +--------------+  +---------------+              |
|  | Lake         |  | EventBridge  |  | DynamoDB      |              |
|  | Formation    |  | Central Bus  |  | (Mesh State:  |              |
|  | (LF-Tags,    |  | + Schema     |  |  domains,     |              |
|  |  Cross-Acct  |  |   Registry   |  |  products,    |              |
|  |  Grants)     |  |              |  |  subscriptions|              |
|  +--------------+  +--------------+  |  quality,     |              |
|                                       |  audit)       |              |
|  +--------------+  +--------------+  +---------------+              |
|  | Step         |  | Glue Data    |                                 |
|  | Functions    |  | Catalog      |  +---------------+              |
|  | (Subscription|  | (Central     |  | SNS + SES     |              |
|  |  Workflows)  |  |  Gold Reg.)  |  | (Alerts)      |              |
|  +--------------+  +--------------+  +---------------+              |
|                                                                      |
|  +--------------------------------------------------------------+   |
|  | API Gateway (IAM Auth) + Lambda (Mesh Control Plane)         |   |
|  +--------------------------------------------------------------+   |
|                                                                      |
|  +--------------+  +--------------+  +---------------+              |
|  | IAM Identity |  | Secrets      |  | KMS           |              |
|  | Center (SSO) |  | Manager      |  | (Per-Domain   |              |
|  |              |  |              |  |  Keys)        |              |
|  +--------------+  +--------------+  +---------------+              |
+---------------------------------------------------------------------+
          |                    |                    |
          |    Cross-Account: LF Grants, EventBridge, IAM AssumeRole
          v                    v                    v
+----------------------+              +----------------------+
|  DOMAIN ACCOUNT      |              |  DOMAIN ACCOUNT      |
|  (e.g., Sales)       |              |  (e.g., Marketing)   |
|                      |              |                      |
|  S3: raw/silver/gold |              |  S3: raw/silver/gold |
|  Glue Catalog (local)|              |  Glue Catalog (local)|
|  Glue ETL Jobs       |              |  + Resource Links    |
|  Step Functions      |              |  Athena Workgroup    |
|  Glue Data Quality   |              |                      |
|  EventBridge (domain)|              |  EventBridge (domain)|
|  IAM Roles           |              |  IAM Roles           |
|  SQS DLQs            |              |  SQS DLQs            |
+----------------------+              +----------------------+
```

## Account Layout

| Account | Purpose | OU | Key Resources |
|---|---|---|---|
| **Management** | AWS Organizations, SCPs, IAM Identity Center | Root | Org management, SCPs |
| **Central Governance** | Catalog, events, governance, subscription workflows | Platform OU | DynamoDB (7 tables), EventBridge central bus, LF admin, KMS, Step Functions |
| **Domain (e.g., Sales)** | Domain-owned data products | Domain OU | 3x S3 buckets, Glue Catalog DBs, Step Functions pipeline, domain EventBridge bus, IAM roles |

Each domain account is provisioned by a platform engineer triggering `onboard-domain.yml` in the platform repo. The workflow writes a `domains/{domain}.yaml` registry entry, provisions `DomainGitHubActionsRole` and `MeshEventRole` via `MeshOnboardingRole` (OIDC), and instantiates the DP repo from `JawaharRamis/data-meshy-product-template`.

## Component Map

| Component | Location | Key Files | See Also |
|---|---|---|---|
| Central governance | `infra/modules/governance/` (this repo) | `dynamodb.tf`, `iam.tf`, `eventbridge.tf` | [ACCOUNT-STRUCTURE.md](ACCOUNT-STRUCTURE.md) |
| Domain account module | [`data-meshy-product-template: modules/domain-account/`](https://github.com/JawaharRamis/data-meshy-product-template/tree/main/modules/domain-account/) | `s3.tf`, `iam.tf`, `lakeformation.tf` | [ACCOUNT-STRUCTURE.md](ACCOUNT-STRUCTURE.md) |
| Data product module | [`data-meshy-product-template: modules/data-product/`](https://github.com/JawaharRamis/data-meshy-product-template/tree/main/modules/data-product/) | `main.tf`, `outputs.tf` | [MEDIATION-PIPELINE.md](MEDIATION-PIPELINE.md) |
| Medallion pipeline (ASL) | [`data-meshy-product-template: step_functions/`](https://github.com/JawaharRamis/data-meshy-product-template/tree/main/step_functions/) | `medallion_pipeline.asl.json` | [MEDIATION-PIPELINE.md](MEDIATION-PIPELINE.md) |
| Glue job templates | [`data-meshy-product-template: glue_jobs/`](https://github.com/JawaharRamis/data-meshy-product-template/tree/main/glue_jobs/) | `raw_ingestion.py`, `silver_transform.py`, `gold_aggregate.py` | [MEDIATION-PIPELINE.md](MEDIATION-PIPELINE.md) |
| Subscription saga (ASL) | `templates/step_functions/` (this repo) | `subscription_saga.asl.json` | [SECURITY.md](SECURITY.md) |
| Event schemas | `schemas/events/` (this repo) | 10 JSON Schema files | [EVENT-MESH.md](EVENT-MESH.md) |
| Domain registry | `domains/` (this repo) | `{domain_name}.yaml` per domain | [ADD-DOMAIN.md](../guides/ADD-DOMAIN.md) |
| SCPs | `infra/environments/central/` (this repo) | `scps.tf` | [SECURITY.md](SECURITY.md) |
| OIDC federation | `infra/environments/central/` (this repo) | `oidc.tf` | [ACCOUNT-STRUCTURE.md](ACCOUNT-STRUCTURE.md) |
| SSO / Identity Center | `infra/environments/central/` (this repo) | `identity_center.tf` | [SECURITY.md](SECURITY.md) |
| Platform catalog tool | `tools/catalog.py` (this repo) | Standalone script (boto3 + argparse). Platform engineers only — no pip install. Subcommands: `search`, `browse`, `describe`. | -- |
| Subscription module | `infra/modules/subscription/` (this repo) | `step_functions.tf` | [SECURITY.md](SECURITY.md) |
| Monitoring | `infra/modules/monitoring/` (this repo) | -- | -- |

## Technology Stack

| Capability | Technology | Rationale |
|---|---|---|
| **IaC** | Terraform (platform-managed, state in central S3) | First-class multi-account via provider aliases, explicit plan/apply, S3 backend per environment. Domain teams never write or run Terraform. |
| **Storage** | S3 (per medallion layer) | Scalable, Iceberg-compatible, lifecycle rules on raw |
| **Table format** | Apache Iceberg on Glue Catalog | Schema evolution, time travel, partition evolution, native Glue/Athena support |
| **Compute** | Glue ETL (PySpark, Flex mode) | Native Iceberg, serverless, cost-effective |
| **Orchestration** | Step Functions | Pay-per-transition (~$0 at portfolio scale), visual debugging, native Glue/Lambda/DynamoDB integration |
| **Sharing** | Lake Formation cross-account grants | Column-level security, LF-Tag policies, native AWS |
| **Catalog store** | DynamoDB (PAY_PER_REQUEST, GSIs) | Serverless, free tier, GSI for tag/domain/classification search |
| **Quality** | Glue Data Quality (DQDL) | Native Glue integration, no extra infra |
| **Events** | EventBridge + Schema Registry | Cross-account routing, schema enforcement, at-least-once delivery |
| **Audit** | CloudTrail + DynamoDB (append-only) | API audit + structured mesh audit log |
| **Alerts** | SNS | Quality alerts, pipeline failures, freshness violations, subscription requests |
| **Domain interface** | `product.yaml` + `infra.yaml` + GitHub Actions push | Domain teams author two YAML files per product and push to `main`. Platform reusable workflows (`provision-product.yml`) handle all provisioning. No CLI, no local Terraform. |
| **Auth (human)** | IAM Identity Center (SSO) | Centralized, MFA enforcement, temporary credentials |
| **Auth (CI/CD)** | GitHub Actions OIDC federation | No stored keys, branch-scoped roles |
| **Encryption** | KMS (per-domain CMK) | Domain-level key isolation, S3 Bucket Keys to reduce API calls by 99% |
| **Secrets** | Secrets Manager | Source DB credentials for Glue jobs, domain-scoped |
| **Dead letters** | SQS | Capture failed Lambda/EventBridge invocations, CloudWatch alarms |

## Data Flow Summary

1. Domain engineer authors `product.yaml` (public contract) and `infra.yaml` (private infra config, includes `platform_version`) and pushes to `main` in their DP repo.
2. The DP repo's `on-push.yml` stub calls `provision-product.yml` (reusable workflow in this repo) via `workflow_call`.
3. `provision-product.yml` runs three steps in sequence:
   - **Validate**: calls `reusable-product-validate.yml` to check `product.yaml` against `schemas/product_spec.json`.
   - **Register**: POSTs `product.yaml` to the governance catalog API (SigV4-signed) to create or update the DynamoDB `mesh-products` entry.
   - **Terraform apply**: resolves `modules/data-product` from `JawaharRamis/data-meshy-product-template` at the `platform_version` pinned in `infra.yaml`. State is stored in the central `mesh-tf-state` S3 bucket at key `{domain}/{product_name}/terraform.tfstate`. Provisions Iceberg table, Glue DQ ruleset, Step Functions pipeline, and Glue job scripts from the template repo's `glue_jobs/` directory.
4. On pipeline run, Step Functions executes the medallion pipeline (ASL from `medallion_pipeline.asl.json` in the template repo): Raw → Silver → Gold → Validate → Quality → Publish.
5. On publish, a `ProductRefreshed` event hits the domain EventBridge bus, which forwards via `MeshEventRole` to the central bus.
6. Central Lambda updates the DynamoDB catalog and audit log.
7. A consumer domain triggers `request-subscription.yml` `workflow_dispatch` in this repo. The workflow writes a PENDING record, opens an approval issue, and pauses at the `subscription-approval` environment gate. On platform team approval, it provisions an LF cross-account grant and resource link in the consumer account.
8. Consumer queries via Athena in their own account.

## Key Architectural Decisions

| Decision | Choice | Why | Trade-off |
|---|---|---|---|
| ADR-001 | Medallion model as paved road | Domain-owned layers (raw/silver/gold), predictable Iceberg output | Not mandated — any gold Iceberg table works |
| ADR-002 | Decomposed IAM roles (not god-roles) | Least-privilege, explicit deny on LF permission management in domain roles | More roles to manage |
| ADR-010 | Two-YAML interface (`product.yaml` + `infra.yaml`) | Clean separation of public contract from private infra tuning; schema validation gates before Terraform | Domain teams cannot express arbitrary infra |
| ADR-011 | No HCL in DP repos — platform owns all Terraform | Prevents config drift, enables central version management, reduces domain team cognitive load | Platform team is the bottleneck for infra changes |
| ADR-012 | Platform-managed onboarding via `workflow_dispatch` | Repeatable, audited, rollback-safe; removes manual AWS console steps | Requires platform engineer to initiate |

Full ADR details: `docs/decisions/` and `plan/ARCHITECTURE.md`.

## Cost Profile (Portfolio Scale: 2 domains, ~10 GB)

~$12-15/month. Glue Flex mode ($8), KMS ($2), Secrets Manager ($1.20), everything else free tier. Budgets alert at $20/$50/$100. SCP caps Glue at 4 DPU. All Step Functions execution timeouts at 2 hours.

Detailed breakdown: `plan/ARCHITECTURE.md` lines 730-758.
