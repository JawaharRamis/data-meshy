# ADR-001: Medallion model as the data product pipeline pattern

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03

## Navigation
← [Decisions](../README.md) | [↑ Docs home](../../README.md)

## Status
Accepted

## Context

Data Meshy needs a standard pipeline pattern that every data product follows from source ingestion to published output. The platform must enforce a clear separation of concerns so that:

- Raw data is landed without transformation, preserving the source of truth for replay and audit.
- Validation, deduplication, and schema enforcement happen before any business logic runs, catching data quality issues early.
- Only the final, validated, high-quality layer is registered as a shareable data product in the mesh catalog.

Without a standard pattern, each domain team would invent its own pipeline structure, making it impossible for the platform to enforce quality gates, schema contracts, or consistent metadata across products.

Alternatives considered:

- **Single-layer (direct ingest-to-publish)**: Simpler, but conflates landing, validation, and business logic. No replay capability if a downstream transform has a bug. Quality issues propagate to consumers.
- **Two-layer (raw + curated)**: Better, but conflates validation with business logic. Hard to separate schema enforcement failures from business rule failures.
- **Medallion (raw/silver/gold)**: Three distinct layers with clear responsibilities. Industry-proven pattern from Databricks and Lakehouse architectures. Each layer can be independently monitored and debugged.

## Decision

We adopt a three-layer medallion pattern -- Raw (Bronze), Silver (Validated), and Gold (Data Product) -- as the standard pipeline for every data product. The specifics:

**Raw (Bronze) layer**:
- Source data landed as-is via Glue ETL job (`raw_ingestion.py`).
- No transformation, no deduplication. Preserves source fidelity.
- Stored as Parquet/Iceberg in domain's raw S3 bucket.
- Glue job bookmarking enabled for incremental ingestion.
- Never shared outside the domain account.

**Silver (Validated) layer**:
- Validated, deduplicated, and schema-enforced copy of raw data.
- Written as Apache Iceberg tables via Glue ETL (`silver_transform.py`).
- Glue Data Quality rules evaluated here -- completeness, uniqueness, referential checks.
- This is the quality gate. If silver validation fails, the pipeline stops before gold.
- Never shared outside the domain account.

**Gold (Data Product) layer**:
- Business logic, aggregation, and enrichment applied via Glue ETL (`gold_aggregate.py`).
- Written as Apache Iceberg tables with MERGE INTO (upsert) semantics.
- Schema validation step (Lambda) compares live Iceberg schema against `product.yaml` contract. Undeclared columns or breaking changes without a version bump block publishing.
- Quality check step evaluates the full DQDL ruleset on the gold table.
- Only gold is registered in the mesh catalog, shared via Lake Formation, and visible to consumers.

**Orchestration via Step Functions**:
- The `medallion_pipeline.asl.json` state machine chains: AcquireLock -> RawIngestion -> SilverTransform -> GoldAggregate -> SchemaValidate -> QualityCheck -> (Pass: PublishCatalog + ReleaseLock + IcebergMaintenance | Fail: QualityAlert + ReleaseLock).
- Each Glue step has retry (3x exponential backoff on `Glue.ServiceException`, `Glue.ThrottlingException`), timeout (30 min), and heartbeat (5 min).
- Concurrent run protection via DynamoDB conditional write lock (TTL 3 hours).
- Post-publish Iceberg maintenance (OPTIMIZE + VACUUM) runs after lock release so consumers are not blocked. Non-fatal on failure.
- Catch-all error handler emits `PipelineFailure` event, writes to audit log, releases lock, routes to DLQ.

**Paved road, not mandate** (per ADR-009-A in ARCHITECTURE.md):
- The medallion pipeline with Glue/Iceberg/Step Functions is the platform default.
- Domains may produce their gold Iceberg table via any mechanism provided the publishing contract is met: gold table registered, `product.yaml` present, LF-Tags applied, events emitted, quality metadata written.

## Consequences

### Positive
- **Clear separation of concerns**: Landing, validation, and publishing are distinct stages with independent monitoring and debugging.
- **Quality gates before consumers see data**: Silver validation and gold quality checks catch issues before they propagate to subscribers.
- **Replayability**: Raw layer preserves the source of truth. If silver or gold transforms have bugs, raw can be replayed without re-ingesting from source systems.
- **Consistent metadata**: Every product follows the same pipeline shape, so the platform can automate catalog registration, quality scoring, and freshness tracking uniformly.
- **Iceberg benefits at silver and gold**: Schema evolution, time travel for debugging and rollback (`datameshy product rollback --to-snapshot <id>`), and partition evolution without data rewrite.
- **Concurrent run protection**: DynamoDB lock prevents overlapping pipeline executions that could corrupt Iceberg tables.

### Negative
- **Added complexity for simple use cases**: A straightforward copy-from-A-to-B pipeline gets three layers, a state machine, quality gates, and Iceberg maintenance. Overhead is justified at mesh scale but may feel heavy for a single trivial product.
- **Storage cost**: Data exists in three copies (raw, silver, gold). Mitigated by S3 lifecycle policies on raw (archive after 90 days) and the fact that portfolio-scale costs are minimal (~$0.23/month for 10 GB).
- **More moving parts to debug**: Failures can occur at any of 8+ states in the Step Functions machine. Mitigated by visual Step Functions debugging, structured error routing to DLQ, and the error handler that captures context.
- **Glue job sizing is fixed at 2 DPU**: May cause OOM on datasets over 200 MB with complex transforms. Documented scaling guidance: 100 GB -> 4 DPU, 1 TB -> 8 DPU + Iceberg compaction, 10 TB+ -> EMR Serverless.

## See also
- [Architecture: Medallion Pipeline](../../plan/ARCHITECTURE.md) -- full pipeline diagram and state machine details
- [Architecture: ADR-009-A](../../plan/ARCHITECTURE.md) -- medallion as paved road, not mandate
- [Architecture: ADR-007](../../plan/ARCHITECTURE.md) -- Iceberg on Glue Data Catalog
- [Pipeline ASL](../../templates/step_functions/medallion_pipeline.asl.json) -- Step Functions state machine definition
- [Glue job templates](../../templates/glue_jobs/) -- raw_ingestion, silver_transform, gold_aggregate, iceberg_maintenance
