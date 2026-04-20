# Component: Lambdas

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
<-- [Architecture](../architecture/OVERVIEW.md) | [^ Docs home](../README.md)

## What this is

Four central Lambda handlers that run in the governance account and provide event validation, catalog management, audit logging, and SLA freshness monitoring for the data mesh. These are the reactive core of the mesh: they process events from the central EventBridge bus, maintain the product catalog in DynamoDB, write an append-only audit trail, and alert when data products violate their freshness SLA.

## Where to find it

```
lambdas/
  event_validator.py        # Validates event source account + dedup via TTL + out-of-order resilience
  catalog_writer.py         # Handles ProductCreated (PutItem) and ProductRefreshed (UpdateItem + quality write)
  audit_writer.py           # Append-only audit log writer -- PutItem only, never Update or Delete
  freshness_monitor.py      # Daily cron: scans products, checks last_refreshed_at vs SLA, emits violations
  tests/
    conftest.py             # Shared pytest fixtures: moto mocks, DynamoDB tables, test event factory
```

## How it works

### event_validator.py

Shared validation module imported by other handlers (not a standalone Lambda handler). Provides three functions:

- **`validate_event_source(event, domains_table)`**: Extracts the `account` field from the EventBridge envelope (set by AWS, not caller-controlled) and the `domain` from the event detail. Looks up the domain in `mesh-domains` DynamoDB to verify the source account is registered for that domain. Returns `VALID` or `DOMAIN_MISMATCH`. On account mismatch, logs a `SECURITY_ALERT`. On missing account or domain, returns `DOMAIN_MISMATCH` with reason `missing_account_or_domain`.

- **`check_dedup(event_id, dedup_table)`**: Uses a conditional `put_item` with `attribute_not_exists(event_id)` against `mesh-event-dedup` to atomically detect duplicate events within a 24-hour TTL window. Returns `VALID` on first processing, `DUPLICATE_EVENT` on repeat. TTL is set to `current_time + 86400` seconds. DynamoDB TTL must be enabled on the table.

- **`check_product_exists(product_id, products_table)`**: Queries `mesh-products` for a given `domain#product_name` composite key. Returns `VALID` if found, `PRODUCT_NOT_FOUND` otherwise. Used for out-of-order resilience: if a handler receives an event for a product not yet registered, the caller can queue for retry instead of dropping the event.

Status constants: `VALID`, `DUPLICATE_EVENT`, `DOMAIN_MISMATCH`, `PRODUCT_NOT_FOUND`, `SECURITY_ALERT`.

### catalog_writer.py

Lambda handler for `ProductCreated` and `ProductRefreshed` events from EventBridge. Processing pipeline for every invocation:

1. **Validate event source** via `event_validator.validate_event_source()`. Raises `RuntimeError` on failure.
2. **Dedup check** via `event_validator.check_dedup()`. Returns `{status: "duplicate"}` on repeat.
3. **Route by event type**:
   - **ProductCreated**: `PutItem` to `mesh-products` with fields: `domain#product_name` (composite key), `domain`, `product_name`, `status=ACTIVE`, `owner`, `classification`, `description`, `tags`, `schema_version`, `created_at`, `last_refreshed_at`, `quality_score=0`, `sla`.
   - **ProductRefreshed**: `UpdateItem` on `mesh-products` (sets `last_refreshed_at`, `quality_score`, `schema_version`, `rows_written`) + `PutItem` to `mesh-quality-scores` (writes quality score history with `product_id`, `timestamp`, `quality_score`, `rows_written`, `domain`, `pipeline_execution_arn`). Converts float `quality_score` to `Decimal` for DynamoDB compatibility.

### audit_writer.py

Lambda handler triggered by ALL events on the central EventBridge bus. Appends every event to `mesh-audit-log` without any filtering or transformation.

- Uses `PutItem` only -- never `UpdateItem` or `DeleteItem`. This is enforced at the IAM level via `MeshAuditWriterRole` which only grants `dynamodb:PutItem`.
- Record structure: `event_id` (partition key), `timestamp` (sort key), `event_type`, `domain`, `source_account`, `event_payload` (full EventBridge envelope as JSON string).
- Raises on write failure (no silent drops).

### freshness_monitor.py

Lambda handler triggered by EventBridge Scheduler on a daily cron (`cron(0 6 * * ? *)` -- 6 AM UTC). Scans the `mesh-products` table for all `ACTIVE` products and checks whether each product's `last_refreshed_at` exceeds the SLA `freshness_target`.

Processing steps:
1. **Scan mesh-products** with pagination (`LastEvaluatedKey` loop).
2. **Skip inactive products** (only `status == "ACTIVE"` is checked).
3. **Parse freshness_target**: supports `"N hours"`, `"N days"`, and plain numbers (assumed hours). Defaults to 24 hours if unparseable.
4. **Check SLA**: computes `age_hours = (now - last_refreshed_at)`. Products with no `last_refreshed_at` are flagged as `never_refreshed`.
5. **Emit FreshnessViolation event** to the central EventBridge bus for each breach. Event source is `datameshy.central`, detail type is `FreshnessViolation`. Includes `product_id`, `domain`, `product_name`, `violation_reason`, `age_hours`, `sla_target_hours`.
6. **Send SNS alert** to `FRESHNESS_SNS_TOPIC_ARN` for each violation (if configured).

Returns a summary: `{status, products_checked, violations, violation_details}`.

### Test fixtures (conftest.py)

Uses `moto` (`mock_aws`) to mock all AWS services. Key fixtures:

- **`aws_mock`**: Activates moto context manager with `us-east-1` region.
- **`dynamodb` / `ddb_resource`**: Pre-configured DynamoDB client/resource within the moto mock.
- **`setup_tables`**: Creates all six mesh DynamoDB tables with correct key schemas: `mesh-domains` (PK: `domain_name`), `mesh-products` (PK: `domain#product_name`), `mesh-quality-scores` (PK: `product_id`, SK: `timestamp`), `mesh-audit-log` (PK: `event_id`, SK: `timestamp`), `mesh-event-dedup` (PK: `event_id`, TTL enabled), `mesh-pipeline-locks` (PK: `product_id`, SK: `lock_key`).
- **`register_domain`**: Seeds `mesh-domains` with test domain (`sales`, account `111111111111`).
- **`register_product`**: Seeds `mesh-products` with test product (`sales#customer_orders`, SLA: daily/24 hours, quality_score: 98.5).
- **`make_event`**: Factory fixture that builds EventBridge event envelopes with configurable `event_type`, `detail`, `account`, and `source`.

## Key interactions

- **catalog_writer** is invoked by EventBridge rules matching `ProductCreated` and `ProductRefreshed` detail types. It imports `event_validator` for source validation and dedup.
- **audit_writer** is invoked by an EventBridge rule matching ALL `datameshy` source events. It receives the same events as catalog_writer (and any other mesh event). It does not import `event_validator` -- it logs everything unconditionally.
- **freshness_monitor** is triggered by EventBridge Scheduler (not by mesh events). It reads from `mesh-products` and writes to EventBridge and SNS.
- **event_validator** is not a standalone handler. It is imported as a library by `catalog_writer` and can be imported by any other handler that needs event validation.
- All handlers use `MeshCatalogWriterRole`, `MeshAuditWriterRole`, or `MeshAuditWriterRole` IAM roles with least-privilege DynamoDB permissions scoped to specific tables and actions.

## Configuration

### Environment variables

| Variable | Used by | Default | Description |
|----------|---------|---------|-------------|
| `MESH_DOMAINS_TABLE` | event_validator | `mesh-domains` | DynamoDB table for domain registrations |
| `MESH_PRODUCTS_TABLE` | catalog_writer, freshness_monitor | `mesh-products` | DynamoDB table for product catalog |
| `MESH_QUALITY_TABLE` | catalog_writer | `mesh-quality-scores` | DynamoDB table for quality score history |
| `MESH_EVENT_DEDUP_TABLE` | catalog_writer (via event_validator) | `mesh-event-dedup` | DynamoDB table for 24h dedup tracking |
| `MESH_AUDIT_TABLE` | audit_writer | `mesh-audit-log` | DynamoDB table for append-only audit log |
| `CENTRAL_EVENT_BUS_ARN` | freshness_monitor | `arn:aws:events:us-east-1:000000000000:event-bus/mesh-central-bus` | Central EventBridge bus ARN for emitting events |
| `FRESHNESS_SNS_TOPIC_ARN` | freshness_monitor | (empty -- no SNS alerts) | SNS topic ARN for freshness violation alerts |

### Lambda runtime configuration

| Setting | Value |
|---------|-------|
| Runtime | Python 3.12 |
| Memory | Default (128 MB) |
| Timeout | Default (3 seconds for handlers; freshness_monitor may need more for large catalogs) |
| Trigger | catalog_writer/audit_writer: EventBridge rules; freshness_monitor: EventBridge Scheduler |

### DynamoDB table key schemas

| Table | Partition key | Sort key |
|-------|---------------|----------|
| `mesh-domains` | `domain_name` (S) | -- |
| `mesh-products` | `domain#product_name` (S) | -- |
| `mesh-quality-scores` | `product_id` (S) | `timestamp` (S) |
| `mesh-audit-log` | `event_id` (S) | `timestamp` (S) |
| `mesh-event-dedup` | `event_id` (S) | -- (TTL on `ttl` attribute) |
| `mesh-pipeline-locks` | `product_id` (S) | `lock_key` (S) |

## Gotchas and constraints

- **audit_writer NEVER updates records**: It uses `PutItem` exclusively. The IAM role (`MeshAuditWriterRole`) only grants `dynamodb:PutItem`. This is a compliance requirement -- the audit trail must be append-only. If an event with the same `event_id` is processed twice (e.g., retry), it will overwrite the previous record with identical content, which is acceptable.
- **event_validator rejects domain mismatches with SECURITY_ALERT**: When the source account does not match the registered account for a domain, the validator logs `SECURITY_ALERT` at ERROR level. This indicates a potential event injection attempt. The calling handler raises `RuntimeError` to prevent further processing.
- **Dedup TTL is 24 hours**: The `mesh-event-dedup` table uses DynamoDB TTL with a 24-hour window. Events processed more than 24 hours apart will not be deduplicated. DynamoDB TTL must be enabled on the table after creation.
- **freshness_monitor scans the entire products table**: It uses `table.scan()` with pagination. For very large catalogs (hundreds of products), this may approach Lambda timeout limits. Consider increasing the Lambda timeout or switching to a parallel scan pattern.
- **freshness_monitor default EventBridge bus ARN is a placeholder**: The default `CENTRAL_EVENT_BUS_ARN` points to account `000000000000` which is a localstack-style placeholder. In production, this must be set to the actual central bus ARN via environment variable.
- **catalog_writer float-to-Decimal conversion**: DynamoDB does not support float types. The `ProductRefreshed` handler converts `quality_score` from float to `Decimal(str(value))` before writing. This avoids `TypeError` but means precision is limited to the string representation.
- **conftest.py enables TTL only on the dedup table**: The `_create_table` helper checks if `"dedup"` is in the table name and only then calls `update_time_to_live`. Other tables do not have TTL enabled, which matches production configuration.
- **make_event fixture sets account to TEST_ACCOUNT_ID**: Tests that validate event source must use `account=TEST_ACCOUNT_ID` (default) and the domain must be registered with that account via the `register_domain` fixture.

## See also

- [EVENT-MESH.md](./EVENT-MESH.md) -- EventBridge bus configuration, rules, and event schemas
- [GOVERNANCE.md](./GOVERNANCE.md) -- central governance module that provisions the DynamoDB tables and IAM roles
- [PIPELINE-TEMPLATES.md](./PIPELINE-TEMPLATES.md) -- pipeline state machine that emits the events these handlers consume
- [MONITORING.md](./MONITORING.md) -- CloudWatch alarms that monitor these Lambda functions for errors
