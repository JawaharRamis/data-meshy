# Component: Data Product Module

> **Phase coverage**: Phase 5 | **Last updated**: 2026-05-15

## Navigation

[<- Architecture](../architecture/OVERVIEW.md) | [^ Docs home](../README.md)

---

## What this is

The `data-product` Terraform module provisions all infrastructure for a **single data product** within a domain. It creates the Iceberg table in the gold Glue catalog, attaches a Glue Data Quality ruleset, provisions a Step Functions state machine for the medallion pipeline, and writes an initial catalog entry to DynamoDB.

**Domain teams never invoke this module directly.** The `provision-product.yml` reusable workflow in `JawaharRamis/data-meshy` fetches the module from the template repo at the platform version pinned in `infra.yaml`, converts `infra.yaml` fields to Terraform `-var` arguments, and runs the apply. Domain teams interact only with `product.yaml` and `infra.yaml`. See [ADR-011](../decisions/ADR-011-no-hcl-in-dp-repos.md).

---

## Where to find it

The module lives in the `JawaharRamis/data-meshy-product-template` repository, not in this repo:

```
https://github.com/JawaharRamis/data-meshy-product-template/tree/main/modules/data-product/
```

Files in the module:

```
modules/data-product/
  main.tf              -- provider config and module-level locals
  iceberg.tf           -- Glue Catalog Iceberg table in gold DB + LF-Tags (domain, classification)
  quality.tf           -- Glue Data Quality ruleset attached to the gold table
  step_functions.tf    -- Step Functions state machine + CloudWatch log group
  catalog.tf           -- Initial DynamoDB catalog entry in mesh-products (status=PROVISIONED)
  outputs.tf           -- All product-specific outputs (product_id, ruleset name, SM ARN, table ARN)
  variables.tf         -- Variables derived from infra.yaml fields + domain-account and governance outputs
```

The module is versioned alongside the platform. Each GitHub Release on `JawaharRamis/data-meshy` has a matching tag on `data-meshy-product-template`. The `provision-product.yml` workflow fetches the module using:

```
git::https://github.com/JawaharRamis/data-meshy-product-template//modules/data-product?ref={platform_version}
```

where `{platform_version}` is the value from `infra.yaml` in the domain's DP repo.

---

## How it works

### Module invocation by provision-product.yml

`provision-product.yml` does the following before calling Terraform:

1. Reads `product.yaml` — POSTs it to the catalog registration API (SigV4-signed).
2. Reads `infra.yaml` — converts each field to a Terraform `-var` argument.
3. Writes an ephemeral `main.tf` in `/tmp/product-tf/` that calls the module at the pinned `platform_version` ref.
4. Runs `terraform init` with the central S3 backend:
   - Bucket: `mesh-tf-state`
   - Key: `{domain}/{product_name}/terraform.tfstate`
5. Runs `terraform apply -auto-approve` with the `-var` arguments.
6. On Terraform failure, rolls back the catalog registration (best-effort DELETE to the catalog API).

Domain teams never see or interact with Terraform state. The state lives entirely in the central governance account S3 bucket.

---

### Iceberg table registration

Creates a Glue Catalog table with:

- `table_type = "EXTERNAL_TABLE"`
- Iceberg v2 via `open_table_format_input.iceberg_input` with `metadata_operation = "CREATE"`
- Input/output formats: `HiveIcebergInputFormat`, `HiveIcebergOutputFormat`, `HiveIcebergSerDe`
- S3 location: `s3://{gold_bucket}/{domain}/{product_name}/`
- Schema columns from `var.schema_columns`, derived from the `schema` array in `product.yaml`
- Partition keys from `var.iceberg_partition_keys`, sourced from `infra.yaml iceberg.partition_keys`
- Table parameters: `table_type=ICEBERG`, `metadata_location`, `classification`, `version`, `product_id`, `owner`

---

### LF-Tags

Two LF-Tags are applied to the Iceberg table at provisioning time:

- `domain={domain}` — used by Lake Formation for subscription-based access grants
- `classification={classification}` — one of `public`, `internal`, `confidential`, `restricted`

These tags are the mechanism through which the subscription workflow grants Lake Formation permissions to subscriber domains. Subscriptions attach LF-Tag policies to subscriber roles; the tags on the table determine what those policies reach.

The `pii` tag from the old Phase 1 model has been removed from the module. PII handling is now expressed via `classification=confidential` in `product.yaml`.

---

### Data quality ruleset

A Glue Data Quality ruleset named `{domain}_{product_name}_dq` is attached to the gold table. Rules are sourced from `var.dq_rules`, which the workflow populates from the `quality_rules[*].rule` strings in `product.yaml`. The ruleset is evaluated during the gold aggregation pipeline step.

If a quality check produces a score below the configured threshold, the pipeline emits a `QualityAlert` event to the central EventBridge bus and blocks the publish step.

---

### Step Functions state machine

- **Name**: `{domain}-{product_name}-pipeline`
- **Execution role**: `MeshEventRole` (from domain-account module output)
- **Definition**: The ASL is loaded from the template repo at `step_functions/medallion_pipeline.asl.json` via the pinned `platform_version`. Not bundled in this repo — `subscription_saga.asl.json` is still in `data-meshy`, but the product pipeline ASL lives in the template repo.
- **Logging**: ALL level with execution data, to `/data-meshy/{domain}/{product_name}/pipeline` CloudWatch log group (30-day retention).
- **Tracing**: X-Ray enabled.
- **Timeout**: 7200 seconds (2 hours).

The ASL orchestrates: AcquireLock → RawIngestion → SilverTransform → GoldAggregate → SchemaValidate → QualityCheck → PublishCatalog/QualityAlert → ReleaseLock → IcebergMaintenance. See [Pipeline Templates](PIPELINE-TEMPLATES.md) for the full state machine spec.

---

### DynamoDB catalog entry

Writes an initial item to `mesh-products` with:

- PK: `{domain}#{product_name}`
- Status: `PROVISIONED`
- Product metadata: version, owner, description, classification, SLA, gold_bucket, gold_db, quality_ruleset_name

This item is written from the central governance account. The Terraform apply runs as `MeshOnboardingRole` → but it uses the central account credentials inherited from the `provision-product.yml` OIDC session via `DomainGitHubActionsRole`. The catalog Lambda (via API Gateway) is the authoritative writer for subsequent state changes (ACTIVE, DEPRECATED, RETIRED) — Terraform only writes the initial PROVISIONED entry.

At runtime, the `catalog_writer` Lambda updates this entry on `ProductCreated` and `ProductRefreshed` events. The Terraform-managed entry is the bootstrap; the Lambda-managed entry is the source of truth after first pipeline run.

---

## Module variables

These variables are set by `provision-product.yml` from `infra.yaml` fields and injected governance/domain-account outputs. Domain teams do not set these directly.

| Variable | Source | Description |
|---|---|---|
| `domain` | `MESH_DOMAIN_NAME` secret | Domain name |
| `product_name` | Directory name of `infra.yaml` | Product name (snake_case) |
| `platform_version` | `infra.yaml: platform_version` | Module version pin |
| `glue_dpu` | `infra.yaml: glue.dpu` | Glue DPU allocation (1–4) |
| `glue_schedule` | `infra.yaml: glue.schedule` | EventBridge cron expression |
| `glue_worker_type` | `infra.yaml: glue.worker_type` | `G.1X`, `G.2X`, or `Standard` |
| `iceberg_partition_keys` | `infra.yaml: iceberg.partition_keys` | List of column names for partitioning |
| `iceberg_compaction` | `infra.yaml: iceberg.compaction` | Whether to run OPTIMIZE |
| `iceberg_retention_days` | `infra.yaml: iceberg.retention_days` | Snapshot retention in days |
| `s3_source_prefix` | `infra.yaml: s3.source_prefix` | Raw source S3 URI |
| `schema_columns` | `product.yaml: schema` | Column definitions |
| `classification` | `product.yaml: classification` | LF-Tag value |
| `dq_rules` | `product.yaml: quality_rules[*].rule` | DQDL rule strings |
| `owner` | `product.yaml: owner` | Product owner email |
| `description` | `product.yaml: description` | Short description |
| `version` | `product.yaml: version` | Schema version |
| `sla_freshness_hours` | `product.yaml: sla.freshness_hours` | Freshness SLA in hours |
| `raw_bucket_name` | Domain-account module output | Raw S3 bucket name |
| `silver_bucket_name` | Domain-account module output | Silver S3 bucket name |
| `gold_bucket_name` | Domain-account module output | Gold S3 bucket name |
| `glue_catalog_db_raw/silver/gold` | Domain-account module outputs | Glue database names |
| `glue_job_execution_role_arn` | Domain-account module output | Glue execution role ARN |
| `mesh_event_role_arn` | Domain-account module output | Step Functions execution role ARN |
| `domain_kms_key_arn` | Domain-account module output | Domain CMK ARN |
| `domain_event_bus_arn` | Domain-account module output | Domain EventBridge bus ARN |
| `mesh_products_table_name` | Governance module output | DynamoDB table name |
| `central_event_bus_arn` | Governance module output | Central EventBridge bus ARN |

---

## Key interactions

1. **`provision-product.yml`** fetches this module from `data-meshy-product-template` at the pinned `platform_version`, constructs an ephemeral `main.tf`, and applies it.
2. **Domain-account module** (`modules/domain-account/` in `data-meshy-product-template`) provides all domain infrastructure references: bucket names, catalog DB names, role ARNs, KMS key, EventBridge bus ARN.
3. **Governance module** (`infra/modules/governance/` in `data-meshy`) provides DynamoDB table names and the central EventBridge bus ARN. These are not Terraform module outputs at apply time — they are known constants (e.g., `mesh-products`) passed as `-var` arguments.
4. **Lambdas** (`catalog_writer`, `audit_writer`) update the DynamoDB entries this module initially creates, transitioning the product from `PROVISIONED` to `ACTIVE` on first successful pipeline run.
5. **Subscription workflow** uses the LF-Tags applied by this module to grant Lake Formation access to subscriber roles.

---

## Gotchas and constraints

- **Domain teams never invoke this module.** If a domain engineer asks how to call this module locally, direct them to the [Add a Product Guide](../guides/ADD-PRODUCT.md) — they trigger the workflow, not Terraform.
- **Partition keys must reference columns in product.yaml.** The module creates partition key objects for each entry in `iceberg.partition_keys`. If a partition key name does not match a column in `schema`, the Glue table creation will fail.
- **Partition keys are all typed as `string` in the Glue catalog DDL.** The Terraform dynamic block creates partition key objects with `type = "string"` regardless of the actual column type. Iceberg handles the type mapping at write time via the table metadata.
- **Step Functions ASL falls back to a placeholder.** If the template repo does not contain the ASL at the expected path for the given `platform_version`, the state machine is created with a single `Succeed` state. The pipeline will not run correctly. Verify the template repo release matches the `platform_version`.
- **Terraform state in the central account.** The S3 backend is in `mesh-tf-state` in the governance account. Domain teams cannot inspect or modify state. Platform engineers with governance account access can run `terraform state list` or `terraform state show` for debugging.
- **Gold layer only.** This module creates Iceberg tables and LF-Tags for the gold layer exclusively. Never use this module or grant Lake Formation permissions on bronze or silver tables. See [CLAUDE.md](../../CLAUDE.md) — this is a platform-wide constraint.

---

## See Also

- [Domain Account](DOMAIN-ACCOUNT.md) — sibling module that provides all domain infrastructure references
- [Governance](GOVERNANCE.md) — central account module with DynamoDB tables and EventBridge bus
- [Pipeline Templates](PIPELINE-TEMPLATES.md) — Glue jobs and ASL state machine that this module orchestrates
- [Lambdas](LAMBDAS.md) — handlers that update the catalog entries this module creates
- [Product Spec Reference](../reference/PRODUCT-SPEC.md) — field definitions for `product.yaml` and `infra.yaml`
- [ADR-011](../decisions/ADR-011-no-hcl-in-dp-repos.md) — why domain teams do not invoke this module directly
- `provision-product.yml` — the reusable workflow that invokes this module
- `data-meshy-product-template` — `https://github.com/JawaharRamis/data-meshy-product-template`
