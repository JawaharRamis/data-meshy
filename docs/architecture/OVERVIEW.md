# System Overview

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
← [Parent](../README.md) | [Next →](MEDIATION-PIPELINE.md) | [↑ Docs home](../README.md)

---

## What Is Data Meshy

Data Meshy is an AWS template for running a data mesh. Domain teams get infrastructure to own, produce, share, and consume data products. A central governance account enforces policies, maintains the catalog, and routes events. Domain accounts run independent medallion pipelines that produce Iceberg tables as shareable products.

The system is opinionated: Apache Iceberg for storage, Glue for ETL, Step Functions for orchestration, Lake Formation for cross-account sharing, EventBridge for mesh events. Domain teams follow the paved road (templates provided) but the publishing contract is the real boundary -- any mechanism that lands a compliant gold Iceberg table works.

## Design Principles

| Principle | How Data Meshy Enforces It |
|---|---|
| **Domain ownership** | One AWS account per domain. Domain teams own their S3 buckets, Glue catalog, pipelines, and IAM roles. No cross-domain access to raw/silver layers. |
| **Data-as-product** | Every data product has a `product.yaml` spec with schema, quality rules, SLA, and version. Only gold-layer tables are shared externally. |
| **Self-serve** | CLI (`datameshy`) wraps Terraform and AWS SDK. Domain engineers create products, trigger refreshes, and manage subscriptions without platform team tickets. |
| **Federated governance** | Central account defines SCPs, LF-Tags, and catalog schema. Domain accounts have autonomy within those guardrails. No god-roles. |
| **Event-driven** | All state changes emit events on EventBridge. Central catalog, audit log, and alerting are all subscribers -- never called directly by domains. |

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
| **Domain (e.g., Sales)** | Domain-owned data products | Domain OU | 3x S3 buckets, Glue Catalog DBs, Step Functions pipeline, domain EventBridge bus, 5x IAM roles |

Each domain account is provisioned from the same Terraform module (`infra/modules/domain-account/`). Adding a new domain means instantiating that module with a different `domain` variable.

## Component Map

| Component | Directory | Key Files | See Also |
|---|---|---|---|
| Central governance | `infra/modules/governance/` | `dynamodb.tf`, `iam.tf`, `eventbridge.tf` | [ACCOUNT-STRUCTURE.md](ACCOUNT-STRUCTURE.md) |
| Domain account | `infra/modules/domain-account/` | `s3.tf`, `iam.tf`, `lakeformation.tf` | [ACCOUNT-STRUCTURE.md](ACCOUNT-STRUCTURE.md) |
| Data product | `infra/modules/data-product/` | `outputs.tf`, main.tf | [MEDIATION-PIPELINE.md](MEDIATION-PIPELINE.md) |
| Medallion pipeline (ASL) | `templates/step_functions/` | `medallion_pipeline.asl.json` | [MEDIATION-PIPELINE.md](MEDIATION-PIPELINE.md) |
| Glue job templates | `templates/glue_jobs/` | `raw_ingestion.py`, `silver_transform.py`, `gold_aggregate.py` | [MEDIATION-PIPELINE.md](MEDIATION-PIPELINE.md) |
| Event schemas | `schemas/events/` | 10 JSON Schema files | [EVENT-MESH.md](EVENT-MESH.md) |
| SCPs | `infra/environments/central/` | `scps.tf` | [SECURITY.md](SECURITY.md) |
| OIDC federation | `infra/environments/central/` | `oidc.tf` | [ACCOUNT-STRUCTURE.md](ACCOUNT-STRUCTURE.md) |
| SSO / Identity Center | `infra/environments/central/` | `identity_center.tf` | [SECURITY.md](SECURITY.md) |
| CLI | `cli/datameshy/` | `cli.py`, `commands/`, `lib/` | -- |
| Environment configs | `infra/environments/` | `central/` | [ACCOUNT-STRUCTURE.md](ACCOUNT-STRUCTURE.md) |
| Subscription module | `infra/modules/subscription/` | -- | [SECURITY.md](SECURITY.md) |
| Monitoring | `infra/modules/monitoring/` | -- | -- |

## Technology Stack

| Capability | Technology | Rationale |
|---|---|---|
| **IaC** | Terraform (mono-repo) | First-class multi-account via provider aliases, explicit plan/apply, S3 backend per environment |
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
| **CLI** | Python + Typer + Boto3 | Modern CLI, wraps Terraform + AWS SDK |
| **Auth (human)** | IAM Identity Center (SSO) | Centralized, MFA enforcement, temporary credentials |
| **Auth (CI/CD)** | GitHub Actions OIDC federation | No stored keys, branch-scoped roles |
| **Encryption** | KMS (per-domain CMK) | Domain-level key isolation, S3 Bucket Keys to reduce API calls by 99% |
| **Secrets** | Secrets Manager | Source DB credentials for Glue jobs, domain-scoped |
| **Dead letters** | SQS | Capture failed Lambda/EventBridge invocations, CloudWatch alarms |

## Data Flow Summary

1. Domain engineer writes `product.yaml` and runs `datameshy product create`
2. Terraform provisions: S3 paths, Iceberg table, Glue DQ ruleset, Step Functions pipeline
3. On refresh (`datameshy product refresh`), Step Functions runs the medallion pipeline: Raw -> Silver -> Gold -> Validate -> Quality -> Publish
4. On publish, a `ProductRefreshed` event hits the domain EventBridge bus, which forwards to the central bus
5. Central Lambda updates the DynamoDB catalog and audit log
6. Consumer runs `datameshy subscribe request` -> approval workflow -> LF cross-account grant -> resource link in consumer account
7. Consumer queries via Athena in their own account

## Key Architectural Decisions

| Decision | Choice | Why | Trade-off |
|---|---|---|---|
| ADR-001 | Multi-account (1/domain + central) | Blast radius isolation, cost attribution, enterprise-realistic | More complex IaC |
| ADR-002 | Terraform over CDK | Multi-account first-class, explicit plan/apply, industry standard | Less Pythonic |
| ADR-003 | Step Functions over MWAA | $0 vs $350/mo minimum, visual debugging | Less industry-standard for data teams |
| ADR-004 | Lake Formation over DataZone | Direct LF control, column-level security, DataZone as UI layer later | No web portal initially |
| ADR-005 | Glue DQ over Great Expectations | Native integration, no extra infra | Less flexible DSL |
| ADR-006 | DynamoDB over DataZone/OpenSearch | Serverless, free tier, GSI search | No full-text search |
| ADR-007 | Iceberg on Glue Catalog | Schema evolution, time travel, partition evolution | Relatively new Glue support |
| ADR-009 | Medallion as paved road, not mandate | Domain ownership principle, any gold Iceberg table works | Requires documenting publishing contract |

Full ADR details: `plan/ARCHITECTURE.md` lines 196-260.

## Cost Profile (Portfolio Scale: 2 domains, ~10 GB)

~$12-15/month. Glue Flex mode ($8), KMS ($2), Secrets Manager ($1.20), everything else free tier. Budgets alert at $20/$50/$100. SCP caps Glue at 4 DPU. All Step Functions execution timeouts at 2 hours.

Detailed breakdown: `plan/ARCHITECTURE.md` lines 730-758.
