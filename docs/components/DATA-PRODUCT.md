# Component: Data Product

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
<- [Architecture](../architecture/OVERVIEW.md) | [^ Docs home](../README.md)

## What this is

The data-product module provisions infrastructure for a **single data product** within a domain. It creates the Iceberg table in the gold Glue catalog, attaches a Glue Data Quality ruleset, provisions a Step Functions state machine for the medallion pipeline, writes an initial catalog entry to DynamoDB, and creates a Secrets Manager secret for source credentials.

It is instantiated per product (e.g. `sales/customer_orders`, `marketing/campaign_metrics`) and consumes outputs from both the governance module (DynamoDB table names, central bus ARN) and the domain-account module (buckets, catalog DBs, role ARNs, KMS key).

Source: `infra/modules/data-product/`

## Where to find it

```
infra/modules/data-product/
  main.tf              -- Secrets Manager secret for source credentials (90-day rotation placeholder)
  iceberg.tf           -- Glue Catalog Iceberg table in gold DB + LF-Tags (domain, classification, pii)
  quality.tf           -- Glue Data Quality ruleset attached to the gold table
  step_functions.tf    -- Step Functions state machine + CloudWatch log group
  catalog.tf           -- Initial DynamoDB catalog entry in mesh-products (status=PROVISIONED)
  outputs.tf           -- All product-specific outputs (product_id, ruleset name, SM ARN, secret ARN, table ARN)
  variables.tf         -- 20+ variables from domain-account and governance outputs + product.yaml fields
```

## How it works

### Iceberg table registration

Creates a Glue Catalog table with:
- `table_type = "EXTERNAL_TABLE"`
- Iceberg v2 via `open_table_format_input.iceberg_input` with `metadata_operation = "CREATE"`
- Input/output formats: `HiveIcebergInputFormat`, `HiveIcebergOutputFormat`, `HiveIcebergSerDe`
- S3 location: `s3://{gold_bucket}/{domain}/{product_name}/`
- Schema columns from `var.schema_columns` (dynamic block)
- Partition keys from `var.partition_keys` (dynamic block, all typed as `string`)
- Table parameters: `table_type=ICEBERG`, `metadata_location`, `classification`, `schema_version`, `product_id`, `owner`

### LF-Tags

Three LF-Tags are applied to the Iceberg table:
- `domain={domain}`
- `classification={classification}` (one of: public, internal, confidential, restricted)
- `pii={pii}` (true/false as string)

These tags are used by Lake Formation tag-based access control to determine who can access the product.

### Data quality ruleset

A Glue Data Quality ruleset named `{domain}_{product_name}_dq` is attached to the gold table. Rules are sourced from `var.dq_rules` (a list of DQDL rule strings from `product.yaml`). The ruleset is evaluated during the silver and gold pipeline steps.

### Step Functions state machine

- **Name**: `{domain}-{product_name}-pipeline`
- **Execution role**: `MeshEventRole` (from domain-account output)
- **Definition**: Loaded from `templates/step_functions/medallion_pipeline.asl.json` via `templatefile()`. Falls back to a placeholder `Succeed` state if the file does not exist.
- **Logging**: ALL level with execution data, to `/data-meshy/{domain}/{product_name}/pipeline` CloudWatch log group (30-day retention).
- **Tracing**: X-Ray enabled.
- **Timeout**: 7200 seconds (2 hours).

The ASL definition orchestrates: AcquireLock -> RawIngestion -> SilverTransform -> GoldAggregate -> SchemaValidate -> QualityCheck -> PublishCatalog/QualityAlert -> ReleaseLock -> IcebergMaintenance. See [Pipeline Templates](PIPELINE-TEMPLATES.md) for the full state machine spec.

### DynamoDB catalog entry

Writes an initial item to `mesh-products` with:
- PK: `{domain}#{product_name}`
- Status: `PROVISIONED`
- All product metadata: schema_version, owner, description, classification, pii, SLA, gold_bucket, gold_db, quality_ruleset_name

This Terraform resource creates the entry so the product is visible immediately after provisioning. At runtime, the `catalog_writer` Lambda updates this entry on `ProductCreated`/`ProductRefreshed` events.

### Secrets Manager

Creates a secret at `{domain}/{product_name}/{source_name}-credentials` with:
- KMS encryption using the domain CMK
- 30-day recovery window
- 90-day automatic rotation (rotation Lambda ARN is a placeholder -- populate in production)

## Key interactions

1. **Domain-account module** provides all infrastructure references: bucket names, catalog DB names, role ARNs, KMS key, event bus ARN.
2. **Governance module** provides DynamoDB table names (`mesh-products`, `mesh-pipeline-locks`, `mesh-audit-log`) and the central EventBridge bus ARN.
3. **Step Functions** executes the medallion pipeline by invoking Glue jobs, Lambdas, and DynamoDB operations using the ASL definition from `templates/step_functions/medallion_pipeline.asl.json`.
4. **Lambdas** (`catalog_writer`, `audit_writer`) update the DynamoDB entries that this module initially creates.
5. **CLI** (`datameshy product create`) triggers terraform plan/apply for this module and emits a `ProductCreated` event.

## Configuration

| Variable | Type | Default | Description |
|---|---|---|---|
| `domain` | `string` | required | Domain name |
| `product_name` | `string` | required | Product name (snake_case, validated) |
| `environment` | `string` | `"dev"` | Deployment environment |
| `schema_columns` | `list(object({name,type,comment}))` | required | Column definitions for Iceberg table |
| `partition_keys` | `list(string)` | `[]` | Partition key column names |
| `classification` | `string` | `"internal"` | One of: public, internal, confidential, restricted |
| `pii` | `bool` | `false` | Whether product contains PII |
| `dq_rules` | `list(string)` | `[]` | DQDL rule strings from product.yaml |
| `owner` | `string` | required | Product owner email or team |
| `description` | `string` | `""` | Short description |
| `schema_version` | `number` | `1` | Schema version (monotonically increasing) |
| `sla_refresh_frequency` | `string` | `"daily"` | SLA refresh frequency |
| `sla_availability` | `string` | `"99.9"` | SLA availability target |
| `medallion_pipeline_asl_path` | `string` | `""` | Path to ASL JSON template |
| `source_name` | `string` | `"default"` | Source system identifier for secret naming |
| `raw_bucket_name` | `string` | required | From domain-account output |
| `silver_bucket_name` | `string` | required | From domain-account output |
| `gold_bucket_name` | `string` | required | From domain-account output |
| `glue_catalog_db_raw/silver/gold` | `string` | required | From domain-account outputs |
| `glue_job_execution_role_arn` | `string` | required | From domain-account output |
| `mesh_event_role_arn` | `string` | required | From domain-account output |
| `domain_kms_key_arn` | `string` | required | From domain-account output |
| `domain_event_bus_arn` | `string` | required | From domain-account output |
| `mesh_products_table_name` | `string` | `"mesh-products"` | From governance output |
| `mesh_pipeline_locks_table_name` | `string` | `"mesh-pipeline-locks"` | From governance output |
| `central_event_bus_arn` | `string` | required | From governance output |

## Gotchas and constraints

- **The Step Functions definition falls back to a placeholder.** If `medallion_pipeline_asl_path` is empty or the file does not exist, the state machine is created with a single `Succeed` state. You must set the path correctly for a working pipeline.
- **DynamoDB catalog entry is cross-account.** The `aws_dynamodb_table_item` resource writes to the governance account's DynamoDB table. In Phase 1, this works because the same Terraform state manages both accounts. For production, the Lambda handler is the authoritative writer.
- **Secret rotation Lambda is not wired.** The `rotation_lambda_arn` is `null` with `lifecycle { ignore_changes }`. You must deploy a rotation Lambda and update the ARN.
- **Partition keys are all typed as `string`.** The Terraform dynamic block creates partition key objects with `type = "string"` regardless of the actual column type. Iceberg handles the type mapping at write time.
- **Quality ruleset is attached to the gold table.** The silver step also evaluates it, but the ruleset target is the gold table in Glue. Ensure the rules reference columns that exist at the silver stage or use separate rulesets.

## See also

- [Domain Account](DOMAIN-ACCOUNT.md) -- parent module that provides all infrastructure references
- [Governance](GOVERNANCE.md) -- central account with the DynamoDB tables and EventBridge bus
- [Pipeline Templates](PIPELINE-TEMPLATES.md) -- the Glue jobs and ASL state machine that this module orchestrates
- [Lambdas](LAMBDAS.md) -- handlers that update the catalog entries this module creates
- [CLI](CLI.md) -- `datameshy product create` command that drives this module
