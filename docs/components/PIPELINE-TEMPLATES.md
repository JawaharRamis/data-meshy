# Component: Pipeline Templates

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
<-- [Architecture](../architecture/OVERVIEW.md) | [^ Docs home](../README.md)

## What this is

Reusable Glue 4.0 PySpark job templates, a Step Functions ASL state machine definition, and a product spec YAML template that together form the default pipeline skeleton for every data product in the mesh. Domain engineers copy these templates during `mesh product create` and customise the gold-layer business logic for their domain. No pipeline is deployed from scratch -- everything starts from these templates.

## Where to find it

```
templates/
  glue_jobs/
    raw_ingestion.py           # Bronze layer -- reads source (JDBC or S3), writes Parquet with Hive partitioning
    silver_transform.py        # Silver layer -- validates, deduplicates, enforces schema, writes Iceberg v2
    gold_aggregate.py          # Gold layer -- applies business logic, upserts via Iceberg MERGE INTO
    iceberg_maintenance.py     # Post-publish -- OPTIMIZE, VACUUM, orphan cleanup on gold Iceberg table
  step_functions/
    medallion_pipeline.asl.json  # 12-state Step Functions state machine orchestrating the full medallion flow
  product_spec/
    product.yaml.template      # Product contract template: schema, SLA, quality rules, lineage, classification
```

## How it works

### Glue job templates

**raw_ingestion.py** (Bronze)

Reads source data through either a named Glue JDBC connection or an S3 path. Writes the data as-is to the raw S3 bucket in Parquet format, partitioned by `_ingestion_date` in append mode (immutable). Adds metadata columns `_ingestion_date`, `_ingestion_ts`, and `_job_run_id`. Job bookmarks are enabled for incremental ingestion on subsequent runs. Supports `--max_rows` for testing and `--source_query` for partial JDBC extraction.

**silver_transform.py** (Silver)

Reads raw Parquet from S3. Strips raw-layer metadata columns. Standardises column names (lowercase, whitespace and hyphen removal). Deduplicates by primary key columns using a window function ordered by `_ingestion_ts` (falls back to `dropDuplicates` if no timestamp available). Validates expected columns against the data. Writes to an Iceberg v2 table in the silver Glue catalog DB using `createOrReplace`. Optionally runs Glue Data Quality evaluation (DQDL ruleset) and raises `QualityCheckFailedError` if the score falls below threshold (default score threshold: 1.0). Polls for DQ completion up to 10 minutes.

**gold_aggregate.py** (Gold)

Reads from the silver Iceberg table. Applies domain-specific business logic in the marked `BEGIN DOMAIN-SPECIFIC TRANSFORMS` / `END DOMAIN-SPECIFIC TRANSFORMS` section (identity transform by default -- the domain engineer fills this in). Removes silver metadata, adds gold metadata (`_gold_ts`, `_gold_job_run_id`). Validates output columns against `--declared_columns` from product.yaml; raises `UndeclaredColumnError` if any undeclared columns are found. Writes to the gold Iceberg table using `MERGE INTO` (upsert semantics) when the table already exists, or `createOrReplace` on first run. Emits CloudWatch metrics: `GoldRowsWritten`, `GoldRowsUpdated`, `GoldRowsDeleted`.

**iceberg_maintenance.py** (Post-publish)

Runs after the pipeline lock is released so consumers are never blocked. Three steps, all non-fatal on failure:
1. **OPTIMIZE** (`rewrite_data_files`) -- compacts small files toward the target size (default 128 MB).
2. **VACUUM** (`expire_snapshots`) -- expires snapshots older than the retention window (default 7 days), always retaining at least 5 snapshots.
3. **Orphan file cleanup** (`remove_orphan_files`) -- deletes files not referenced by any snapshot (default 3-day retention).

Emits `IcebergMaintenanceFailure` CloudWatch metric on any step failure.

### Step Functions state machine (medallion_pipeline.asl.json)

The ASL defines 12 states with a 2-hour timeout. Execution flow:

| # | State | Type | Purpose |
|---|-------|------|---------|
| 1 | AcquireLock | Task (DynamoDB updateItem) | Conditional write to prevent concurrent pipeline runs per product. TTL of 3 hours. Fails fast to `ConcurrentRunDetected`. |
| 2 | ConcurrentRunDetected | Fail | Terminal failure -- another execution holds the lock. |
| 3 | RawIngestion | Task (Glue startJobRun.sync) | Triggers `raw_ingestion` Glue job. 30-min timeout, 5-min heartbeat. Retries on Glue throttling (3x, exponential backoff). |
| 4 | SilverTransform | Task (Glue startJobRun.sync) | Triggers `silver_transform` Glue job. Same timeout/retry policy. |
| 5 | GoldAggregate | Task (Glue startJobRun.sync) | Triggers `gold_aggregate` Glue job. Same timeout/retry policy. |
| 6 | SchemaValidate | Task (Lambda invoke) | Compares live Iceberg gold schema against product.yaml declared columns. Blocks publish on undeclared columns. |
| 7 | QualityCheck | Task (Lambda invoke) | Runs Glue Data Quality evaluation on gold table. Returns pass/fail + score. |
| 8 | QualityPass | Choice | Branches: `passed == true` goes to `PublishCatalog`; otherwise goes to `QualityAlert`. |
| 9 | PublishCatalog | Task (Lambda invoke) | Updates mesh-products DynamoDB, emits `ProductRefreshed` event to central EventBridge bus. |
| 10 | QualityAlert | Task (Lambda invoke) | Marks product as degraded, emits `QualityAlert` event, sends SNS notification. Still releases lock. |
| 11 | ReleaseLock / ReleaseLockAfterAlert | Task (DynamoDB deleteItem) | Removes the pipeline lock. Two separate states for the publish and alert paths. |
| 12 | IcebergMaintenance | Task (Glue startJobRun.sync) | Runs `iceberg_maintenance` Glue job. 20-min timeout. On failure routes to `MaintenanceFailure` (not `ErrorHandler`). |
| 13 | MaintenanceFailure | Task (Lambda invoke) | Logs the error and emits a metric. Pipeline is still considered successful. |
| 14 | ErrorHandler | Task (Lambda invoke) | Catch-all: emits `PipelineFailure` event, writes to audit log, releases lock, routes event to DLQ. |

All Glue job states retry on `Glue.ServiceException` and `Glue.ThrottlingException` (3 attempts, 30s interval, 2x backoff), except IcebergMaintenance which retries 2x. Every state except `AcquireLock` and `ConcurrentRunDetected` catches `States.ALL` and routes to `ErrorHandler`.

### product.yaml template

Defines the data product contract with these top-level sections:

- **product**: identity (`name`, `domain`, `owner`, `description`, `contact_channel`)
- **sla**: `refresh_frequency` (hourly/daily/weekly/monthly/on_demand), `freshness_target` (duration string like "24 hours"), `availability` (percentage)
- **schema**: `format` (always `iceberg`), `columns` list (each with `name`, `type`, `description`, `pii`, `nullable`), `partition_by` with Iceberg transform
- **quality**: `rules` list (DQDL syntax with `name`, `rule`, `threshold`), `minimum_quality_score` (0-100)
- **tags**: for catalog discoverability and LF-Tag-based access control
- **classification**: one of `public`, `internal`, `confidential`, `restricted`
- **lineage**: `sources` list (system, table, optional credentials_secret_arn), `pipeline_type`

## Key interactions

- **Step Functions orchestrates Glue jobs in strict sequence**: AcquireLock -> RawIngestion -> SilverTransform -> GoldAggregate -> SchemaValidate -> QualityCheck -> (PublishCatalog | QualityAlert) -> ReleaseLock -> IcebergMaintenance.
- **CLI copies templates**: `mesh product create` copies the four Glue job scripts and the ASL to the domain's S3 artifact bucket, substitutes parameters, and deploys the state machine.
- **product.yaml drives validation**: `--declared_columns` in gold_aggregate and the quality ruleset name in silver_transform are both derived from the product spec.
- **CloudWatch metrics**: gold_aggregate emits `GoldRowsWritten`, `GoldRowsUpdated`, `GoldRowsDeleted` to `DataMeshy/Pipeline`. iceberg_maintenance emits `IcebergMaintenanceFailure` to `DataMeshy/Maintenance`.
- **EventBridge events**: PublishCatalog emits `ProductRefreshed`. QualityAlert emits `QualityAlert`. ErrorHandler emits `PipelineFailure`.

## Configuration

### Common Glue job parameters (passed by Step Functions)

| Parameter | Required | Used by | Description |
|-----------|----------|---------|-------------|
| `--domain` | Yes | All 4 jobs | Domain name (e.g., `sales`) |
| `--product_name` | Yes | All 4 jobs | Product name (e.g., `customer_orders`) |
| `--raw_bucket` | Yes | raw, silver | Raw layer S3 bucket name |
| `--silver_bucket` | Yes | silver, gold | Silver layer S3 bucket name |
| `--gold_bucket` | Yes | gold, maintenance | Gold layer S3 bucket name |
| `--raw_db` | Yes | raw, silver | Glue catalog database for raw layer |
| `--silver_db` | Yes | silver, gold | Glue catalog database for silver layer |
| `--gold_db` | Yes | gold, maintenance | Glue catalog database for gold layer |
| `--table_name` | Yes | All 4 jobs | Table name |
| `--source_connection_name` | Yes | raw | Glue connection name or `S3` |
| `--quality_ruleset_name` | Yes | silver | Glue DQ ruleset name |
| `--primary_key_columns` | No | silver, gold | Comma-separated PK columns for dedup/MERGE |
| `--declared_columns` | No | gold | Comma-separated expected output columns from product.yaml |
| `--partition_by_columns` | No | gold | Comma-separated partition columns for gold Iceberg table |
| `--target_file_size_mb` | No | maintenance | Target file size after compaction (default: `128`) |
| `--snapshot_retention_days` | No | maintenance | Expire snapshots older than N days (default: `7`) |
| `--min_snapshots_to_keep` | No | maintenance | Minimum snapshots to retain (default: `5`) |
| `--orphan_file_retention_days` | No | maintenance | Delete orphan files older than N days (default: `3`) |
| `--ingestion_date` | No | raw, silver | Override/process a specific partition (YYYY-MM-DD) |
| `--max_rows` | No | raw | Cap rows read (testing) |
| `--enable_quality_check` | No | silver | `true`/`false` (default: `true`) |
| `--cloudwatch_namespace` | No | gold, maintenance | CloudWatch namespace (default: `DataMeshy/Pipeline`) |

### Step Functions state machine configuration

| Setting | Value |
|---------|-------|
| Timeout | 7200 seconds (2 hours) |
| Glue job timeout | 1800 seconds (30 min) per job |
| Maintenance job timeout | 1200 seconds (20 min) |
| Heartbeat | 300 seconds (5 min) for all Glue jobs |
| Lock TTL | 10800 seconds (3 hours) from execution start |
| Retry | 3x for pipeline jobs, 2x for maintenance (30s base, 2x backoff) |

## Gotchas and constraints

- **Glue 4.0 is required**: All four jobs use Iceberg Spark extensions (`org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions`) and the Glue Iceberg catalog implementation. Glue 3.0 does not support these.
- **Job bookmarks for incremental ingestion**: `raw_ingestion` passes `--job-bookmark-option: job-bookmark-enable`. This only works if the Glue job is configured with bookmark support in Terraform/CDK. Without bookmarks, every run re-reads all source data.
- **Maintenance runs after lock release**: IcebergMaintenance is intentionally placed after ReleaseLock so that OPTIMIZE/VACUUM does not block consumer queries. Failures are non-fatal -- the pipeline is still considered successful.
- **`createOrReplace` on first silver run**: Silver uses `createOrReplace` which replaces the table if it exists. Subsequent runs must not change the schema incompatibly or Iceberg will reject the write.
- **MERGE INTO requires primary key columns**: The gold_aggregate upsert builds a `MERGE INTO` statement using `--primary_key_columns`. If not provided, it defaults to `id`. Forgetting to set this on a table without an `id` column will cause the MERGE to fail.
- **UndeclaredColumnError blocks publish**: If `--declared_columns` is set and the gold DataFrame contains columns not in that list, the job raises and the pipeline fails. This is intentional to prevent accidental schema drift.
- **Quality check polling is in-band**: silver_transform polls Glue DQ evaluation for up to 10 minutes within the Spark job. For large tables this may need a longer timeout or an async approach.
- **product.yaml placeholders must be replaced**: The template uses `{{placeholder}}` syntax. The CLI substitutes these during `mesh product create`, but if a placeholder is left in place, CI validation will catch it.

## See also

- [MEDIATION-PIPELINE.md](./MEDIATION-PIPELINE.md) -- end-to-end pipeline walkthrough
- [CLI.md](./CLI.md) -- how templates are copied and deployed during product creation
- [GOVERNANCE.md](./GOVERNANCE.md) -- central governance tables referenced by the state machine
- [CUSTOMIZE-PIPELINE.md](../guides/CUSTOMIZE-PIPELINE.md) -- guide for domain engineers on customising the gold layer
- [DATA-PRODUCT.md](./DATA-PRODUCT.md) -- data product lifecycle and spec reference
