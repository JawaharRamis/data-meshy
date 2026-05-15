# Guide: Quick Start

> **Phase coverage**: Phase 5 | **Last updated**: 2026-05-15

## Navigation
← [Docs home](../README.md)

---

## Goal

Get a working data mesh with one domain and one data product running end-to-end: from raw ingestion through silver transformation to gold aggregation, with quality checks and catalog registration. After completing this guide you will have data flowing through all three medallion layers and visible in the mesh catalog.

**Who this guide is for:**
- **Platform engineers** — Steps 1–3 cover deploying central governance and onboarding a domain.
- **Domain team engineers** — Steps 4–6 cover authoring YAML files and publishing a first product.

Domain teams never write Terraform or install any CLI tool. Everything is driven by two YAML files and a `git push`.

---

## Prerequisites

| Requirement | Details | Who needs it |
|---|---|---|
| AWS accounts | At minimum: one central governance account + one domain account. | Platform engineer |
| AWS Organizations | Both accounts must be in the same AWS Organization. | Platform engineer |
| IAM Identity Center | Configured with permission sets for platform engineers. | Platform engineer |
| AWS CLI v2 with SSO | For platform engineers running Terraform and catalog queries. | Platform engineer |
| Terraform >= 1.6.0 | For deploying central governance infrastructure. | Platform engineer |
| GitHub org access | Ability to create repos and trigger `workflow_dispatch` in `JawaharRamis/data-meshy`. | Platform engineer |
| DP repo access | Write access to the domain's `{domain}-products` repo. | Domain engineer |

**Domain teams do not install Terraform, do not install any CLI package, and do not run AWS commands locally.** The only tools a domain engineer needs are `git` and a text editor.

---

## Steps

### Step 1: Deploy Central Governance Infrastructure (Platform Engineer)

Clone the platform repo and deploy the shared governance account infrastructure. This only needs to be done once per environment.

```bash
git clone https://github.com/JawaharRamis/data-meshy.git
cd data-meshy
```

Log in using the SSO profile for the central governance account:

```bash
aws sso login --profile central-admin
aws sts get-caller-identity --profile central-admin
```

Initialize and apply the central environment:

```bash
cd infra/environments/central/

terraform init
terraform plan -var="environment=prod"
terraform apply -var="environment=prod"
```

Note the outputs — you will need them when configuring domain secrets:

```bash
terraform output central_event_bus_arn
terraform output mesh_catalog_writer_role_arn
terraform output mesh_api_endpoint
terraform output quality_alert_sns_topic_arn
```

This provisions: DynamoDB tables, EventBridge central bus, Lake Formation admin, KMS keys, `MeshOnboardingRole` (OIDC-trusted for this repo's `main` branch), governance API Gateway + Lambda, and the `mesh-tf-state` S3 bucket for all product Terraform state.

---

### Step 2: Set Repository-Level Secrets and Variables (Platform Engineer)

Before onboarding any domain, the platform repo needs one repository variable:

| Name | Where | Value |
|---|---|---|
| `MESH_API_ENDPOINT` | `data-meshy` repo → Settings → Variables | Base URL of the governance API (no trailing slash). From `terraform output mesh_api_endpoint`. |
| `AWS_ACCOUNT_ID` | `data-meshy` repo → Settings → Secrets | Central account ID (12 digits). Used by `onboard-domain.yml` when constructing the `MeshOnboardingRole` ARN. |
| `ORG_PAT` | `data-meshy` repo → Settings → Secrets | GitHub org-scoped PAT with `repo` + `issues:write` scope. Used to create DP repos and open issues in external repos. |

Also create the `subscription-approval` GitHub environment in this repo (Settings → Environments) and add your platform team as required reviewers. This gates the `provision` job in `request-subscription.yml`.

---

### Step 3: Onboard a Domain (Platform Engineer)

Domain onboarding is fully automated via `onboard-domain.yml`. A platform engineer triggers it with `workflow_dispatch` — there are no manual Terraform or AWS console steps.

Navigate to: **Actions → Onboard Domain → Run workflow**

Fill in the following inputs:

| Input | Description | Example |
|---|---|---|
| `domain_name` | Domain identifier. Must match `^[a-z][a-z0-9_]*$`. | `sales` |
| `account_id` | AWS account ID for the domain (12 digits). | `123456789012` |
| `aws_region` | AWS region for domain infrastructure. | `ap-southeast-2` |
| `owner_email` | Domain owner or on-call email. | `sales-team@acme.com` |
| `github_repo` | Full repo slug of the domain DP repo (will be created). | `JawaharRamis/sales-products` |
| `repo_pattern` | Glob pattern for domain product repos trusted by OIDC. | `JawaharRamis/sales-*` |

**What the workflow does (4 jobs):**

1. **write-registry** — Validates `domain_name` format, generates `domains/sales.yaml`, validates it against `schemas/domain.json`, and commits to `main` in this repo. This file is the source of truth for all subsequent subscription and upgrade workflows.

2. **provision-iam** — Assumes `MeshOnboardingRole` (central account, OIDC), then cross-account assumes `OrganizationAccountAccessRole` in the domain account. Idempotently creates:
   - GitHub OIDC provider in the domain account
   - `DomainGitHubActionsRole` — trusted by the domain DP repo's `main` branch. Has S3/Glue/Step Functions permissions scoped to the domain, plus `events:PutEvents` to the central bus. **Explicit deny on all Lake Formation permission management.**
   - `MeshEventRole` — trusted by EventBridge. Allows only `events:PutEvents` to the central bus.
   - EventBridge cross-account forwarding rule from domain bus to central bus.
   - Lake Formation S3 location registration for `mesh-{domain}-gold` (best-effort).

3. **rollback-on-failure** — If job 2 fails, reverts the `domains/` commit from job 1. Ensures registry and IAM stay in sync.

4. **setup-repo** — Creates `JawaharRamis/sales-products` from `JawaharRamis/data-meshy-product-template` via the GitHub template API. Sets three secrets on the new repo (`AWS_ROLE_ARN`, `MESH_API_ENDPOINT`, `MESH_DOMAIN_NAME`). Opens a quickstart checklist issue in the new repo.

When the workflow completes successfully, the domain team receives the quickstart issue and can move to Step 4.

---

### Step 4: Author product.yaml and infra.yaml (Domain Team)

Clone the auto-created DP repo:

```bash
git clone https://github.com/JawaharRamis/sales-products.git
cd sales-products
```

The repo was generated from `data-meshy-product-template` and contains:
- Example product files at `products/example/`
- Workflow stubs (`on-push.yml`, `deprecate.yml`, `rollback.yml`, `upgrade-platform.yml`) that call back into platform reusable workflows
- `README.md` with quickstart instructions

Create a directory for your product and copy the examples:

```bash
mkdir -p products/revenue_daily
cp products/example/product.yaml products/revenue_daily/product.yaml
cp products/example/infra.yaml products/revenue_daily/infra.yaml
```

**Edit `products/revenue_daily/product.yaml`** (the public contract — visible in the catalog):

```yaml
name: revenue_daily
domain: sales
owner: jawahar@acme.com
description: Daily revenue aggregated by region
version: "1"
sla:
  freshness_hours: 24
  tier: gold
schema:
  - name: date
    type: date
  - name: region
    type: string
  - name: gross_revenue
    type: decimal
classification: internal
tags: [finance, revenue]
quality_rules:
  - rule: IsComplete("gross_revenue")
  - rule: IsComplete("date")
```

Key rules:
- `name` must be unique within your domain (snake_case).
- `classification` must be one of: `public`, `internal`, `confidential`, `restricted`.
- `sla.tier` must be `gold` — only gold-layer products are shared.
- `quality_rules` use DQDL syntax (Glue Data Quality rule language).

**Edit `products/revenue_daily/infra.yaml`** (private infra config — not in the catalog):

```yaml
platform_version: v1.2
glue:
  dpu: 2
  schedule: "cron(0 3 * * ? *)"
  worker_type: G.1X
iceberg:
  partition_keys: [date]
  compaction: true
  retention_days: 730
s3:
  source_prefix: s3://sales-raw/revenue/
```

Key rules:
- `platform_version` is required and must match a release tag in `JawaharRamis/data-meshy-product-template`. The platform team communicates the current stable version.
- `glue.dpu` is capped at 4 by SCP.
- `s3.source_prefix` is the S3 path where your raw source data lands.
- Do not include any Terraform, HCL, or AWS resource definitions in this file or anywhere else in the DP repo.

---

### Step 5: Push to main and Watch the Workflow (Domain Team)

Commit and push:

```bash
git add products/revenue_daily/
git commit -m "feat: add revenue_daily data product"
git push origin main
```

The push triggers `on-push.yml` in your DP repo, which calls `provision-product.yml` in the platform repo via `workflow_call`. Navigate to the **Actions** tab in your DP repo to monitor progress.

**What the workflow does (2 jobs):**

1. **validate (gate)** — Calls `reusable-product-validate.yml`. Validates `product.yaml` against `schemas/product_spec.json`. The workflow fails and stops here if validation fails — no AWS resources are touched.

2. **provision** — Runs sequentially:
   - Assumes `DomainGitHubActionsRole` via OIDC.
   - Registers the product in the catalog by POSTing `product.yaml` to the governance API (SigV4-signed). A `mesh-products` DynamoDB record is created with status `PENDING`.
   - Parses `infra.yaml` into Terraform `-var` flags and extracts `platform_version`.
   - Writes an ephemeral `main.tf` that sources `modules/data-product` from `JawaharRamis/data-meshy-product-template` at the pinned `platform_version`.
   - Runs `terraform init` with the backend configured to the central `mesh-tf-state` bucket at key `sales/revenue_daily/terraform.tfstate`.
   - Runs `terraform apply`. This provisions: Iceberg table in `sales_gold` Glue database, Glue DQ ruleset, Step Functions pipeline state machine (sourced from `medallion_pipeline.asl.json` in the template repo), and uploads Glue job scripts from the template's `glue_jobs/` directory.
   - On Terraform failure: best-effort rollback deletes the catalog entry.

Typical workflow runtime: 3–5 minutes.

---

### Step 6: Verify (Domain Team + Platform Engineer)

Once the GitHub Actions workflow is green, verify the product is live:

**GitHub Actions (domain team):**
- Both the `validate` and `provision` jobs show green checkmarks in the Actions tab.

**DynamoDB `mesh-products` (platform engineer — AWS Console or catalog tool):**

```bash
# Platform engineers only — run from data-meshy/ with SSO credentials:
python tools/catalog.py --profile central-admin describe sales revenue_daily
```

Expected: record with `status: ACTIVE`, `domain: sales`, `name: revenue_daily`.

**DynamoDB `mesh-domains` (platform engineer):**

```bash
python tools/catalog.py --profile central-admin browse --domain sales
```

Expected: `sales` domain with `status: ACTIVE`.

**Athena (domain team — from the sales account AWS Console):**

```sql
SELECT * FROM sales_gold.revenue_daily LIMIT 10;
```

Note: the Athena query returns data only after the first pipeline run. The provisioning step creates the table structure; the medallion pipeline must execute at least once to populate data. Trigger the pipeline from the Step Functions console (`sales-revenue_daily-pipeline`) or wait for the scheduled run.

**Verification checklist:**

| Check | Expected Result |
|---|---|
| GitHub Actions workflow | Both `validate` and `provision` jobs green |
| `mesh-domains` DynamoDB table | Shows `sales` domain with `status: ACTIVE` |
| `mesh-products` DynamoDB table | Shows `sales/revenue_daily` with `status: ACTIVE` |
| `mesh-tf-state` S3 bucket | Object at `sales/revenue_daily/terraform.tfstate` exists |
| Athena query on `sales_gold.revenue_daily` | Returns rows (after first pipeline run) |
| `ProductRefreshed` event in EventBridge | Visible in central bus metrics (after first pipeline run) |
| No messages in DLQs | `mesh-catalog-dlq`, `mesh-audit-dlq` are empty |

---

## Troubleshooting

| Problem | Likely Cause | Resolution |
|---|---|---|
| `onboard-domain.yml` fails at `provision-iam` with `AccessDenied` | `MeshOnboardingRole` is not configured, or OIDC trust condition does not match the platform repo's main branch | Confirm `MeshOnboardingRole` exists in the central account and its trust policy allows `repo:JawaharRamis/data-meshy:ref:refs/heads/main`. Check `infra/environments/central/oidc.tf`. |
| `onboard-domain.yml` fails at `provision-iam` with `OrganizationAccountAccessRole` error | The domain account was not created via AWS Organizations, or the role was renamed | Verify `OrganizationAccountAccessRole` exists in the domain account. It is created automatically by AWS Organizations when you create member accounts. |
| `validate` job fails with `Spec validation failed` | `product.yaml` does not match `schemas/product_spec.json` | Check the error message for the failing field. Required top-level fields: `name`, `domain`, `owner`, `description`, `version`, `sla`, `schema`, `classification`. All `schema` entries need `name` and `type`. |
| `validate` job fails with `No product.yaml found` | Files are not in the expected directory layout | Ensure your product files are at `products/{product_name}/product.yaml` and `products/{product_name}/infra.yaml`. The `product_dir` input defaults to `products/`. |
| `provision` job fails with `platform_version is required` | `infra.yaml` is missing `platform_version` | Add `platform_version: vX.Y` to `infra.yaml`. Check the platform repo releases for the current stable tag. |
| `provision` job fails with `Module not found` or Terraform init error | `platform_version` tag does not exist in `JawaharRamis/data-meshy-product-template` | Confirm the tag exists: `https://github.com/JawaharRamis/data-meshy-product-template/releases`. Use an existing release tag. |
| `provision` job fails with backend S3 error | `mesh-tf-state` bucket not yet created, or `DomainGitHubActionsRole` lacks S3 access | Confirm the central governance Terraform was applied and the `mesh-tf-state` bucket exists. Confirm `DomainGitHubActionsRole` has S3 permissions scoped to the domain prefix. |
| `provision` job fails at catalog registration (`401` or `403`) | OIDC credentials not assumed, or API endpoint is wrong | Check that `AWS_ROLE_ARN` secret on the DP repo matches the `DomainGitHubActionsRole` ARN. Check `MESH_API_ENDPOINT` does not have a trailing slash. |
| Athena query returns `Table not found` | Terraform applied but Glue table name differs from expected | Confirm the Glue database `sales_gold` and table `revenue_daily` exist in the domain account's Glue console. The table name is derived from `product.yaml: name`. |
| Athena query returns no rows | Medallion pipeline has not run yet | Trigger the Step Functions state machine `sales-revenue_daily-pipeline` manually from the AWS Console. |
| Glue job OOM | Dataset too large for configured DPU | Increase `glue.dpu` in `infra.yaml` (max 4, enforced by SCP) and push. |
| `request-subscription.yml` pauses indefinitely | `subscription-approval` environment has no reviewers configured | In `JawaharRamis/data-meshy` Settings → Environments → `subscription-approval`, add required reviewers from the platform team. |

---

## Next Steps

- [Add a Domain](ADD-DOMAIN.md) — detailed walkthrough of the onboarding workflow and registry schema
- [Add a Product](ADD-PRODUCT.md) — full `product.yaml` and `infra.yaml` field reference with examples
- [Deprecate a Product](DEPRECATE-PRODUCT.md) — retire a product using the `deprecate-product.yml` reusable workflow
- [Upgrade Platform](UPGRADE-PLATFORM.md) — respond to upgrade notifications and pin a new `platform_version`
- [Resource Naming Reference](../reference/RESOURCE-NAMING.md) — naming conventions for all AWS resources
- [Product Spec Reference](../reference/PRODUCT-SPEC.md) — full `product.yaml` field documentation
- [Subscription Flow](subscription-flow.md) — detailed walkthrough of cross-domain data access requests
