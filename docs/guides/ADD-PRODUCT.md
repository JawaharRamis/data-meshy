# Guide: Add a Data Product

> **Phase coverage**: Phase 5 | **Last updated**: 2026-05-15

## Navigation

<- [Docs home](../README.md) | [Add a Domain](ADD-DOMAIN.md) | [Subscription Flow](subscription-flow.md)

---

## Goal

Publish a new data product within your domain. This guide is for **domain team members**. You will author two YAML files, push them to your DP repo, and let the platform handle the rest. There is no Terraform to write, no HCL, and no pip CLI to install.

---

## How it works

Your DP repo contains a single GitHub Actions workflow — `on-push.yml` — that fires whenever you push to `main`. It calls `provision-product.yml` in the platform repo (data-meshy), which runs four steps:

1. **Validate** — checks `product.yaml` against the platform schema. Fails fast on any invalid field.
2. **Register** — SigV4-signed POST to the governance API registers the product in the central DynamoDB catalog.
3. **Terraform** — writes an ephemeral `main.tf` pointing to the platform module, runs `terraform init` and `terraform apply` against the central S3 state backend, and provisions the Glue table, Step Functions state machine, and supporting resources.
4. **Rollback** — if Terraform fails, the catalog entry is deleted (best-effort) to keep catalog and infrastructure consistent.

You never run Terraform. You never call the governance API directly. You push YAML and watch Actions.

---

## Prerequisites

| Requirement | How to verify |
|---|---|
| Domain is onboarded | `domains/{your_domain}.yaml` exists in data-meshy main. If not, contact the platform team (see [Add a Domain](ADD-DOMAIN.md)). |
| DP repo exists and is cloned | Your repo was created by the platform team during domain onboarding. Clone it: `git clone https://github.com/{org}/{your-repo}`. |
| Three repo secrets are set | In your DP repo → Settings → Secrets → Actions: `AWS_ROLE_ARN`, `MESH_API_ENDPOINT`, `MESH_DOMAIN_NAME` must all be present. These are set by the platform team during onboarding. If any are missing, contact the platform team. |
| Source data is accessible | The S3 path or JDBC source your Glue job will read from must be reachable from your domain account. |

---

## Step 1: Create the product directory

In your DP repo, create a directory for the new product under `products/`:

```
products/
  revenue_daily/
    product.yaml
    infra.yaml
```

The directory name must match the `name` field in `product.yaml`. Use snake_case.

---

## Step 2: Write `product.yaml`

`product.yaml` is the **public contract** for your data product. It is what consumers discover, what the governance API validates, and what the platform deploys Iceberg table metadata from. Every field is visible to consumers, so be accurate and deliberate.

Copy this example and fill in your values:

```yaml
name: revenue_daily
domain: sales
owner: jawahar@acme.com
description: Daily revenue aggregated by region and product line, sourced from the ERP system.
version: "1"
sla:
  freshness_hours: 24
  tier: gold
schema:
  - name: date
    type: date
  - name: region
    type: string
  - name: product_line
    type: string
  - name: gross_revenue
    type: decimal
  - name: net_revenue
    type: decimal
  - name: transaction_count
    type: long
classification: internal
tags: [finance, revenue, erp]
quality_rules:
  - rule: IsComplete("gross_revenue")
  - rule: IsComplete("date")
  - rule: ColumnValues("gross_revenue") >= 0
```

### Field reference

**`name`** (required)

The product identifier. Snake_case. Must be unique within your domain. This becomes the Glue table name (`{domain}_gold.{name}`), the Step Functions state machine name, and the key in the central catalog.

- Example: `revenue_daily`, `customer_orders`, `inventory_snapshot`
- Cannot contain hyphens. Cannot start with a digit.
- Cannot be changed after first publish — the Terraform state key is `{domain}/{name}/terraform.tfstate`.

**`domain`** (required)

Your domain name. Must exactly match the `domain_name` registered in `domains/{name}.yaml`. You can find your domain name in the `MESH_DOMAIN_NAME` secret in your DP repo.

- Example: `sales`

**`owner`** (required)

Email of the person or team responsible for this product. Receives quality alert notifications and subscription approval requests.

- Example: `jawahar@acme.com`, `sales-data-team@acme.com`

**`description`** (required)

Plain-English description visible to consumers in the catalog. Be specific: what data does it contain, what is the source system, what grain/aggregation?

**`version`** (required)

A quoted integer string. Start at `"1"`. Increment when you make a breaking schema change (removing a column, changing a type). Non-breaking additions (new columns) do not require a version bump but you may bump at your discretion.

**`sla.freshness_hours`** (required)

How many hours after the scheduled run time the data is still considered fresh. The governance platform uses this for freshness alerting.

- `24` for daily pipelines
- `1` for hourly pipelines

**`sla.tier`** (required)

Always `gold`. Only gold-layer tables are shared on the data mesh. Bronze and silver are never granted to consumers.

**`schema`** (required)

List of columns in the output Iceberg table. Each entry has:
- `name` — column name (snake_case)
- `type` — Iceberg type: `string`, `long`, `double`, `decimal`, `date`, `timestamp`, `boolean`, `binary`

Only include columns that should be visible to consumers. PII columns should either be excluded entirely or left out of the schema — the platform's Lake Formation column-level filtering will block any column you mark as PII in a future version of the spec. For now, simply do not include columns you do not want shared.

**`classification`** (required)

Data sensitivity classification:
- `internal` — accessible to any verified internal domain
- `confidential` — restricted; subscription approval is required and subject to additional review

**`tags`** (optional)

Free-form list of strings for catalog discoverability. Use consistent terms agreed on with your team.

**`quality_rules`** (required, at least one)

Great Expectations-style rules that run after every pipeline execution. Available rule functions:
- `IsComplete("column")` — no nulls in the column
- `ColumnValues("column") >= value` — all values pass the comparison
- `IsUnique("column")` — no duplicate values
- `RowCount() >= value` — output must have at least N rows

The pipeline fails and an alert fires if any rule does not pass. Start with completeness checks on your primary key and key metric columns.

---

## Step 3: Write `infra.yaml`

`infra.yaml` is the **private infrastructure config** for your product. It is not visible to consumers. It controls the Glue job settings, Iceberg table properties, and the source S3 path that the ingestion job reads from.

Copy this example and fill in your values:

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

### Field reference

**`platform_version`** (required)

The version of the platform module to use. This controls which version of the Terraform module at `JawaharRamis/data-meshy-product-template` is applied to your product. Your domain team controls upgrade timing — the platform team publishes new versions and you opt in by editing this field.

- Format: `v{major}.{minor}` (e.g., `v1.2`)
- Check `upgrade-platform.yml` in your DP repo for the upgrade workflow

**`glue.dpu`** (required)

Number of Glue Data Processing Units to allocate to this product's Glue job. Each DPU is 4 vCPU + 16 GB RAM. Start small and increase if the job is slow.

- Minimum: `2`
- Typical daily aggregation: `2`–`4`
- Large joins or complex transforms: `8`–`16`

**`glue.schedule`** (required)

Cron expression for the Step Functions state machine trigger. Uses AWS EventBridge cron syntax (UTC, 6 fields including year).

- `"cron(0 3 * * ? *)"` — 3:00 AM UTC daily
- `"cron(0 */6 * * ? *)"` — every 6 hours
- `"cron(0 8 ? * MON *)"` — Monday 8:00 AM UTC

**`glue.worker_type`** (required)

Glue worker type. Must match the DPU count:
- `G.1X` — 1 DPU per worker (use with `dpu: 2`–`8`)
- `G.2X` — 2 DPU per worker (use with `dpu: 4`+, better for memory-intensive jobs)

**`iceberg.partition_keys`** (required)

List of column names to partition the Iceberg table by. Choose the column(s) that consumers most commonly filter on. Poor partition choices cause expensive full-table scans.

- `[date]` — almost always correct for daily time-series data
- `[date, region]` — if most queries filter by both date and region
- `[]` — only for very small reference tables (< 1 million rows)

**`iceberg.compaction`** (required)

Whether to run Iceberg compaction after every write. Set to `true` for most products — it merges small files and improves query performance. Set to `false` only for products with very infrequent writes where compaction overhead is not justified.

**`iceberg.retention_days`** (required)

Number of days to retain Iceberg table snapshots (data history). Older snapshots are expired by the maintenance job.

- `730` (2 years) — standard for finance and compliance data
- `365` (1 year) — typical for operational data
- `90` — for high-volume, low-retention operational logs

**`s3.source_prefix`** (required)

The S3 prefix where your raw source data lands. The Glue ingestion job reads from this location. The prefix must be accessible from your domain account.

- Example: `s3://sales-raw/revenue/`
- The Glue job expects partitioned data under this prefix (e.g., `s3://sales-raw/revenue/year=2026/month=05/`)

---

## Step 4: Push to main and watch Actions

Once both files are written and look correct, push to the `main` branch of your DP repo:

```
git add products/revenue_daily/
git commit -m "add revenue_daily product"
git push origin main
```

`on-push.yml` fires immediately. Navigate to **Actions** in your DP repo to watch the workflow.

The `provision-product.yml` workflow (running in data-meshy) has two jobs:

### Job 1: `validate`

Checks `product.yaml` against `schemas/product_spec.json` in the platform repo. This is a hard gate — if validation fails, nothing is provisioned. Common failures and fixes:

| Error | Fix |
|---|---|
| `"sla.tier" must be "gold"` | Change `tier` to `gold` |
| `"name" does not match pattern` | Remove hyphens from the product name |
| `"domain" does not match registered domain` | Ensure `domain` matches `MESH_DOMAIN_NAME` secret exactly |
| `"quality_rules" must have at least one item` | Add at least one quality rule |
| `"version" must be a quoted string` | Change `version: 1` to `version: "1"` |

### Job 2: `provision`

Four sub-steps run in sequence:

1. **Register** — SigV4-signed POST to `MESH_API_ENDPOINT/products` with your `product.yaml` content. Creates a `PROVISIONING` record in `mesh-products` DynamoDB.
2. **Init** — assumes `DomainGitHubActionsRole` via OIDC. Parses `infra.yaml` into Terraform `-var` flags. Writes an ephemeral `main.tf` pointing to the platform module at the `platform_version` you specified. Runs `terraform init` with the central S3 backend at key `{domain}/{product_name}/terraform.tfstate`.
3. **Apply** — runs `terraform apply`. Provisions: Glue database and Iceberg table in the gold catalog, Glue Data Quality ruleset, Step Functions state machine, IAM execution role for Glue, EventBridge schedule rule.
4. **Finalize** — updates the catalog record to `ACTIVE`. If Terraform fails, deletes the catalog record (best-effort) so the catalog stays consistent with actual infrastructure.

A successful run ends with a green checkmark and a log line like:

```
Product sales/revenue_daily provisioned successfully (version v1.2)
```

---

## Step 5: Verify the product

**Check the Actions tab** in your DP repo — both `validate` and `provision` must be green.

**Check the catalog via the platform catalog tool** (platform engineers):

```
python tools/catalog.py describe sales revenue_daily
```

Expected output:

```
name:        revenue_daily
domain:      sales
status:      ACTIVE
version:     1
tier:        gold
platform:    v1.2
table:       sales_gold.revenue_daily
state_key:   sales/revenue_daily/terraform.tfstate
created_at:  2026-05-15T...
```

**Verify the Glue table exists:**

In the AWS Console for your domain account, navigate to **AWS Glue → Data Catalog → Databases** and look for `{domain}_gold`. The table `revenue_daily` should appear there.

**Query via Athena:**

In the domain account's Athena console, select the `{domain}_gold` database and run:

```sql
SELECT * FROM revenue_daily LIMIT 10;
```

An empty result (0 rows) is expected before the first pipeline run. The table schema (columns) should already be present.

---

## Step 6: Customize your Glue jobs

The template repo pre-populates `glue_jobs/` in your DP repo with stub Python scripts. These stubs read from `s3.source_prefix` and write Iceberg output to the gold table, but the transformation logic is intentionally minimal.

Edit the files in `glue_jobs/` in your DP repo to add your business logic:

```
glue_jobs/
  raw_ingestion.py     ← customize: source format, parsing, schema mapping
  gold_aggregate.py    ← customize: aggregation logic, column derivations
```

**Key things to customize in `raw_ingestion.py`:**
- File format reader (`csv`, `json`, `parquet`, `orc`)
- Column renames to match your `product.yaml` schema
- Any type casting

**Key things to customize in `gold_aggregate.py`:**
- Aggregation expressions (SUM, AVG, COUNT)
- Window functions if needed
- Any business-rule filters

After editing, push the changes to `main`. `on-push.yml` triggers again — but since `product.yaml` and `infra.yaml` are unchanged, the Terraform `plan` will show no infrastructure changes, and the run completes quickly. The updated Glue job scripts are uploaded to S3 as part of the provision job.

Do not modify `step_functions/` unless instructed by the platform team — the state machine definition is managed by Terraform.

---

## Step 7: Run the first pipeline

The Step Functions state machine is provisioned but has not run yet. Trigger it manually for the first run:

1. In the domain account AWS Console, navigate to **Step Functions → State machines**
2. Search for `{domain}-{product_name}-pipeline` (e.g., `sales-revenue_daily-pipeline`)
3. Click **Start execution**
4. Leave the input as the default `{}` or pass `{"manual_trigger": true}`
5. Click **Start execution**

Watch the execution graph. The standard pipeline runs:

```
Ingest (Glue) → Transform (Glue) → Quality Check → Notify → Done
```

A successful first execution produces rows in the Iceberg table. Verify:

```sql
SELECT COUNT(*) FROM revenue_daily;
```

After the first successful manual run, the EventBridge schedule (from `glue.schedule` in `infra.yaml`) will take over and run automatically.

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `validate` job fails with schema errors | Invalid `product.yaml` fields | Fix the flagged fields and push again. |
| `provision` fails with `CONFLICT: product already exists` | A product with this name already exists in the catalog | Use a different `name`, or contact the platform team to delete the existing record if it is stale. |
| `provision` fails with `AccessDenied` on `sts:AssumeRoleWithWebIdentity` | `DomainGitHubActionsRole` trust policy does not match this repo/branch | Confirm the `repo_pattern` set during domain onboarding covers this repo. Contact the platform team to update the role. |
| `provision` fails at `terraform init` | Central S3 backend unreachable or state key conflict | Check platform team — the `mesh-tf-state` bucket or DynamoDB lock table may need attention. |
| `provision` fails at `terraform apply` | Glue or LF resource conflict | Check the Terraform error in the logs. Usually a naming conflict or LF quota. |
| Glue job fails with `S3 access denied` | `DomainGitHubActionsRole` cannot read `s3.source_prefix` | Confirm the bucket policy allows the role, or contact the platform team to add the prefix to the role policy. |
| Step Functions execution fails at Quality Check | Quality rules not met | Review which rules failed in the execution output. Fix the source data or adjust the rules in `product.yaml` and push. |
| Athena query returns no rows | First pipeline has not run yet | Trigger the Step Functions state machine manually (Step 7). |
| `UndeclaredColumnError` in Glue job | Glue job outputs a column not in `product.yaml` schema | Either add the column to `product.yaml` schema or remove it from the Glue job output. |

---

## See Also

- [Add a Domain Guide](ADD-DOMAIN.md) — domain onboarding (platform engineers)
- [Subscription Flow](subscription-flow.md) — how consumers subscribe to this product
- [Product Spec Reference](../reference/PRODUCT-SPEC.md) — complete field documentation
- [Architecture Document](../../plan/ARCHITECTURE.md) — gold-layer-only sharing, Lake Formation model
