# Data Meshy Documentation

> Phase 1 complete | Last full refresh: 2026-04-03

## Start Here
→ [Quick Start Guide](guides/QUICK-START.md) — get running in 15 minutes

## Architecture
| Doc | What it covers |
|-----|---------------|
| [System Overview](architecture/OVERVIEW.md) | Big picture, design principles, component map |
| [Medallion Pipeline](architecture/MEDIATION-PIPELINE.md) | Raw → Silver → Gold data flow |
| [Event Mesh](architecture/EVENT-MESH.md) | EventBridge topology, event schemas |
| [Security](architecture/SECURITY.md) | IAM, Lake Formation, KMS, SCPs |
| [Account Structure](architecture/ACCOUNT-STRUCTURE.md) | Multi-account setup, cross-account trust |

## Components
| Component | Directory | Doc |
|-----------|-----------|-----|
| Governance | `infra/modules/governance/` | [GOVERNANCE.md](components/GOVERNANCE.md) |
| Domain Account | `infra/modules/domain-account/` | [DOMAIN-ACCOUNT.md](components/DOMAIN-ACCOUNT.md) |
| Data Product | `infra/modules/data-product/` | [DATA-PRODUCT.md](components/DATA-PRODUCT.md) |
| Monitoring | `infra/modules/monitoring/` | [MONITORING.md](components/MONITORING.md) |
| Pipeline Templates | `templates/` | [PIPELINE-TEMPLATES.md](components/PIPELINE-TEMPLATES.md) |
| CLI Tool | `cli/` | [CLI.md](components/CLI.md) |
| Lambda Handlers | `lambdas/` | [LAMBDAS.md](components/LAMBDAS.md) |

## Guides
| Guide | What you'll learn |
|-------|-------------------|
| [Quick Start](guides/QUICK-START.md) | Set up the platform end-to-end |
| [Add a Domain](guides/ADD-DOMAIN.md) | Onboard a new domain team |
| [Add a Product](guides/ADD-PRODUCT.md) | Create a new data product |
| [Customize Pipeline](guides/CUSTOMIZE-PIPELINE.md) | Customize Glue jobs for your domain |

## Reference
| Reference | What it contains |
|-----------|-----------------|
| [Resource Naming](reference/RESOURCE-NAMING.md) | Naming conventions for all AWS resources |
| [Event Schemas](reference/EVENT-SCHEMAS.md) | All event types and their payloads |
| [Product Spec](reference/PRODUCT-SPEC.md) | product.yaml field reference |
| [Terraform Modules](reference/TERRAFORM-MODULES.md) | Module inputs, outputs, variables |

## Architecture Decisions
| ADR | Title | Status |
|-----|-------|--------|
| [ADR-001](decisions/ADR-001-medallion-model.md) | Medallion model as data product pattern | Accepted |
| [ADR-002](decisions/ADR-002-decomposed-iam.md) | Decomposed IAM roles over god-roles | Accepted |
| [ADR Template](decisions/ADR-TEMPLATE.md) | Template for new ADRs | Template |
