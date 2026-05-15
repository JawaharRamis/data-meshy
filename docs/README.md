# Data Meshy Documentation

> Phase 5 complete | Last full refresh: 2026-05-15

## Start Here
→ [Quick Start Guide](guides/QUICK-START.md) — end-to-end walkthrough for platform engineers and domain teams

## Architecture
| Doc | What it covers |
|-----|----------------|
| [System Overview](architecture/OVERVIEW.md) | Big picture, design principles, component map |
| [Medallion Pipeline](architecture/MEDIATION-PIPELINE.md) | Raw → Silver → Gold data flow |
| [Event Mesh](architecture/EVENT-MESH.md) | EventBridge topology, event schemas |
| [Security](architecture/SECURITY.md) | IAM, Lake Formation, KMS, SCPs |
| [Account Structure](architecture/ACCOUNT-STRUCTURE.md) | Multi-account setup, cross-account trust |

## Components
| Component | Location | Doc |
|-----------|----------|-----|
| Governance | `infra/modules/governance/` (this repo) | [GOVERNANCE.md](components/GOVERNANCE.md) |
| Domain Account module | [`data-meshy-product-template: modules/domain-account/`](https://github.com/JawaharRamis/data-meshy-product-template/tree/main/modules/domain-account/) | [DOMAIN-ACCOUNT.md](components/DOMAIN-ACCOUNT.md) |
| Data Product module | [`data-meshy-product-template: modules/data-product/`](https://github.com/JawaharRamis/data-meshy-product-template/tree/main/modules/data-product/) | [DATA-PRODUCT.md](components/DATA-PRODUCT.md) |
| Monitoring | `infra/modules/monitoring/` (this repo) | [MONITORING.md](components/MONITORING.md) |
| Pipeline Templates (ASL + Glue) | [`data-meshy-product-template: step_functions/ + glue_jobs/`](https://github.com/JawaharRamis/data-meshy-product-template) | [PIPELINE-TEMPLATES.md](components/PIPELINE-TEMPLATES.md) |
| Template Repo | [`JawaharRamis/data-meshy-product-template`](https://github.com/JawaharRamis/data-meshy-product-template) | TF modules, Glue jobs, Step Functions templates, DP repo stubs |
| Lambda Handlers | `lambdas/` (this repo) | [LAMBDAS.md](components/LAMBDAS.md) |

## Guides
| Guide | What you'll learn |
|-------|-------------------|
| [Quick Start](guides/QUICK-START.md) | Set up the platform and publish a first data product end-to-end |
| [Add a Domain](guides/ADD-DOMAIN.md) | Onboard a new domain team via `onboard-domain.yml` |
| [Add a Product](guides/ADD-PRODUCT.md) | Author `product.yaml` + `infra.yaml` and trigger provisioning |
| [Deprecate a Product](guides/DEPRECATE-PRODUCT.md) | Retire a data product using `deprecate-product.yml` |
| [Upgrade Platform](guides/UPGRADE-PLATFORM.md) | Respond to a platform upgrade notification and pin a new version |

## Reference
| Reference | What it contains |
|-----------|-----------------|
| [Resource Naming](reference/RESOURCE-NAMING.md) | Naming conventions for all AWS resources |
| [Event Schemas](reference/EVENT-SCHEMAS.md) | All event types and their payloads |
| [Product Spec](reference/PRODUCT-SPEC.md) | `product.yaml` field reference |
| [Terraform Modules](reference/TERRAFORM-MODULES.md) | Module inputs, outputs, variables (template repo) |

## Architecture Decisions
| ADR | Title | Status |
|-----|-------|--------|
| [ADR-001](decisions/ADR-001-medallion-model.md) | Medallion model as data product pattern | Accepted |
| [ADR-002](decisions/ADR-002-decomposed-iam.md) | Decomposed IAM roles over god-roles | Accepted |
| [ADR-010](decisions/ADR-010-two-yaml-interface.md) | Two-YAML interface for data products (`product.yaml` + `infra.yaml`) | Accepted |
| [ADR-011](decisions/ADR-011-no-hcl-in-dp-repos.md) | No HCL in data product repos — platform reusable workflows own all Terraform | Accepted |
| [ADR-012](decisions/ADR-012-platform-managed-onboarding.md) | Platform-managed domain onboarding via `workflow_dispatch` | Accepted |
| [ADR Template](decisions/ADR-TEMPLATE.md) | Template for new ADRs | Template |
