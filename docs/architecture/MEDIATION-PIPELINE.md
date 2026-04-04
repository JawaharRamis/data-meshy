# Medallion Pipeline

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
← [Overview](OVERVIEW.md) | [Next →](EVENT-MESH.md) | [↑ Docs home](../README.md)

---

## Overview

Every data product follows the medallion pattern: Raw (Bronze) -> Silver (Validated) -> Gold (Data Product). A Step Functions state machine orchestrates the full flow, including lock acquisition, schema validation, quality checks, catalog publishing, and post-publish Iceberg maintenance.

The state machine is defined in `templates/step_functions/medallion_pipeline.asl.json`. Each domain instantiates its own copy via the `data-product` Terraform module (`infra/modules/data-product/`).

## Flow Diagram

```
+--------------------+
| AcquireLock        |  DynamoDB conditional write: {product_id}/LOCK
| (Prevent Overlap)  |  If lock exists -> ConcurrentRunDetected (fail)
+--------+-----------+
         |
         v
+--------------------+
| RawIngestion       |  Glue Job: land source data as-is into raw S3 layer
| (Bronze)           |  Retry: 3x exponential backoff (30s, 60s, 120s)
|                    |  Timeout: 30 min, Heartbeat: 5 min
+--------+-----------+
         |
         v
+--------------------+
| SilverTransform    |  Glue ETL (PySpark): validate, dedup, enforce schema
| (Validated)        |  Write to Iceberg silver table
|                    |  Retry: 3x on Glue.ServiceException, Glue.ThrottlingException
|                    |  Timeout: 30 min, Heartbeat: 5 min
+--------+-----------+
         |
         v
+--------------------+
| GoldAggregate      |  Glue ETL (PySpark): business logic, enrichment
| (Data Product)     |  Write to Iceberg gold table via MERGE INTO (upsert)
|                    |  Retry: 3x, Timeout: 30 min, Heartbeat: 5 min
+--------+-----------+
         |
         v
+--------------------+
| SchemaValidate     |  Lambda: compare live Iceberg gold schema vs product.yaml
|                    |  If undeclared columns found -> BLOCK publish
|                    |  If breaking change without version bump -> BLOCK publish
+--------+-----------+
         |
         v
+--------------------+
| QualityCheck       |  Lambda triggers Glue Data Quality evaluation on gold table
|                    |  Returns: pass/fail + numeric quality score
|                    |  Custom Lambda supplements DQDL for rules it cannot express
+--------+-----------+
         |
         v
    +----+----+
    | Pass?   |
    +----+----+
    Yes |     | No
    v        v
+----------+  +------------------+
| Publish  |  | QualityAlert     |
| Catalog  |  | Mark product     |
| Update   |  |   "degraded" in  |
| Emit     |  |   catalog        |
| Event    |  | SNS -> Owner     |
| Release  |  | Emit QualityAlert|
| Lock     |  | Release Lock     |
+-----+----+  +------------------+
      |
      v
+--------------------+
| IcebergMaintenance |  Glue job: OPTIMIZE (compaction) + VACUUM (expire snapshots)
| (post-publish)     |  Target: files <32MB -> compact to ~128MB
|                    |  Expire snapshots older than 7 days (keep last 5)
|                    |  Delete orphan files older than 3 days
|                    |  Timeout: 20 min, Non-blocking on failure (alarm only)
+--------------------+

On ANY unhandled error (raw/silver/gold/schema/quality/publish):
+--------------------------+
| ErrorHandler             |
| Emit PipelineFailure     |
| Write to audit log       |
| Release Lock             |
| Route event to DLQ       |
+--------------------------+
```

## State Machine Configuration

Source: `templates/step_functions/medallion_pipeline.asl.json`

| Parameter | Value | Where Defined |
|---|---|---|
| Execution timeout | 7200s (2 hours) | Top-level `TimeoutSeconds` |
| Glue job timeout | 1800s (30 min) each | Per-state `TimeoutSeconds` |
| Glue heartbeat | 300s (5 min) | Per-state `HeartbeatSeconds` |
| Iceberg maintenance timeout | 1200s (20 min) | `IcebergMaintenance` state |
| Glue retry policy | 3 attempts, 30s interval, backoff rate 2.0 | Per-state `Retry` block |
| Maintenance retry | 2 attempts (fewer, non-critical) | `IcebergMaintenance` `Retry` block |
| Retried errors | `Glue.ServiceException`, `Glue.ThrottlingException` | `ErrorEquals` in `Retry` |

## State-by-State Detail

### AcquireLock

Prevents concurrent pipeline runs for the same product. Uses a DynamoDB conditional write to `mesh-pipeline-locks` table:

- **PK**: `product_id` (e.g., `sales#customer_orders`)
- **SK**: `LOCK`
- **Condition**: `attribute_not_exists(sk)` -- fails if another execution holds the lock
- **TTL**: 3 hours (abandoned locks self-expire)

If the conditional write fails (lock already held), the state machine transitions to `ConcurrentRunDetected` and fails immediately.

Table definition: `infra/modules/governance/dynamodb.tf:255`

### RawIngestion

Runs the `raw_ingestion` Glue job. Lands source data (from a JDBC connection, S3 upload, or API) as-is into the raw S3 layer. Job bookmarks are enabled for incremental ingestion.

Arguments passed to the Glue job:
- `--domain`, `--product_name`, `--raw_bucket`, `--source_connection_name`
- `--raw_db`, `--table_name`
- `--job-bookmark-option`: `job-bookmark-enable`
- `--enable-job-insights`: `true` (Glue job observability)

Template: `templates/glue_jobs/raw_ingestion.py`

### SilverTransform

Runs the `silver_transform` Glue job. Reads from the raw Iceberg table, validates and deduplicates rows, enforces the declared schema, and writes to the silver Iceberg table.

Arguments: `--domain`, `--product_name`, `--raw_bucket`, `--silver_bucket`, `--raw_db`, `--silver_db`, `--table_name`, `--quality_ruleset_name`

Template: `templates/glue_jobs/silver_transform.py`

### GoldAggregate

Runs the `gold_aggregate` Glue job. Applies business logic, enrichment, and aggregations. Writes to the gold Iceberg table using MERGE INTO (upsert pattern) to handle incremental updates.

Arguments: `--domain`, `--product_name`, `--silver_bucket`, `--gold_bucket`, `--silver_db`, `--gold_db`, `--table_name`

Template: `templates/glue_jobs/gold_aggregate.py`

### SchemaValidate

Lambda function that compares the live Iceberg gold table schema (from Glue Catalog) against the columns declared in `product.yaml`. This is the enforcement point for ADR-009 (data product versioning):

- Columns in the table but not in the spec -> **block publish**
- Breaking change detected (column removed, type changed, column renamed, nullable -> non-nullable) without a version bump -> **block publish**
- Non-breaking changes (new nullable column) -> allow, increment `schema_version`

The Lambda reads from `mesh-products` DynamoDB table to get the current published schema version and compares against the live Glue Catalog table.

### QualityCheck

Lambda function that triggers Glue Data Quality evaluation using the DQDL ruleset created for this product. Returns `passed: true/false` and a numeric `quality_score`.

Known DQDL limitations (supplemented by custom Lambda where needed):
- No cross-column validation (e.g., `end_date > start_date`)
- No referential integrity across tables
- No statistical distribution checks
- No custom Python rule functions

Quality ruleset naming convention: `{domain}_{product_name}_dq` (see `infra/modules/data-product/outputs.tf:79`)

### PublishCatalog vs QualityAlert

**Quality passes** (`PublishCatalog` state):
1. Lambda updates `mesh-products` DynamoDB entry (freshness timestamp, quality score, row count)
2. Lambda writes to `mesh-audit-log` (append-only)
3. Lambda emits `ProductRefreshed` event to the central EventBridge bus
4. Pipeline lock is released via DynamoDB `DeleteItem`

**Quality fails** (`QualityAlert` state):
1. Lambda marks the product as `degraded` in the catalog
2. Lambda emits `QualityAlert` event to central EventBridge bus
3. SNS notification sent to product owner
4. Pipeline lock is released

In both cases, the lock is released. The product is never left in a locked state.

### IcebergMaintenance

Runs after lock release so consumers are not blocked during compaction. This is a mandatory step but non-fatal -- if it fails, the pipeline is still considered successful. Failure routes to `MaintenanceFailure` (logs a warning, emits a metric), not `ErrorHandler`.

Parameters: `--target_file_size_mb: 128`, `--snapshot_retention_days: 7`

Template: `templates/glue_jobs/iceberg_maintenance.py`

## Concurrent Run Protection

```
Execution A: AcquireLock -> [running pipeline] -> ReleaseLock -> IcebergMaintenance
Execution B: AcquireLock -> FAIL (ConditionalCheckFailedException) -> ConcurrentRunDetected
```

- Lock record: `mesh-pipeline-locks` table, PK=`product_id`, SK=`LOCK`
- TTL: 3 hours (safety net for abandoned executions)
- Lock is released in three paths: success, quality alert, error handler
- Even if the error handler fails, the TTL ensures the lock expires

## Error Handling

Every state except `IcebergMaintenance` has a `Catch` block routing to `ErrorHandler`. The error handler:

1. Emits a `PipelineFailure` event to the central EventBridge bus
2. Writes the failure to `mesh-audit-log`
3. Releases the pipeline lock
4. The event is routed to the `mesh-pipeline-failures` SNS topic via a central EventBridge rule
5. Failed events that cannot be processed land in the appropriate SQS DLQ

Error handler Lambda ARN is passed as input parameter `$.error_handler_function_arn`.

## Input Payload

The Step Functions state machine expects this input structure (assembled by the CLI or EventBridge Scheduler):

```json
{
  "domain": "sales",
  "product_name": "customer_orders",
  "product_id": "sales#customer_orders",
  "raw_bucket": "sales-raw-123456789012",
  "silver_bucket": "sales-silver-123456789012",
  "gold_bucket": "sales-gold-123456789012",
  "raw_db": "sales_raw",
  "silver_db": "sales_silver",
  "gold_db": "sales_gold",
  "table_name": "customer_orders",
  "quality_ruleset_name": "sales_customer_orders_dq",
  "source_connection_name": "sales-source-jdbc",
  "pipeline_locks_table_name": "mesh-pipeline-locks",
  "products_table_name": "mesh-products",
  "audit_log_table_name": "mesh-audit-log",
  "central_event_bus_arn": "arn:aws:events:us-east-1:111111111111:event-bus/mesh-central-bus",
  "schema_validate_function_arn": "arn:aws:lambda:...",
  "quality_check_function_arn": "arn:aws:lambda:...",
  "catalog_writer_function_arn": "arn:aws:lambda:...",
  "quality_alert_function_arn": "arn:aws:lambda:...",
  "error_handler_function_arn": "arn:aws:lambda:...",
  "maintenance_alert_function_arn": "arn:aws:lambda:..."
}
```

## Triggering

| Method | How |
|---|---|
| CLI | `datameshy product refresh` -- CLI calls `states:StartExecution` via SSO profile |
| Schedule | EventBridge Scheduler rule with cron expression (defined per product in `product.yaml` sla.refresh_frequency) |
| Manual | AWS Console -> Step Functions -> Start Execution |

## Related Files

| File | Purpose |
|---|---|
| `templates/step_functions/medallion_pipeline.asl.json` | Full ASL definition (439 lines) |
| `templates/glue_jobs/raw_ingestion.py` | Bronze layer Glue job template |
| `templates/glue_jobs/silver_transform.py` | Silver layer Glue job template |
| `templates/glue_jobs/gold_aggregate.py` | Gold layer Glue job template |
| `templates/glue_jobs/iceberg_maintenance.py` | Post-publish OPTIMIZE + VACUUM |
| `infra/modules/governance/dynamodb.tf:255` | `mesh-pipeline-locks` table definition |
| `infra/modules/data-product/outputs.tf:83` | State machine ARN output |
| `plan/ARCHITECTURE.md:532` | Architecture spec for the medallion pipeline |
