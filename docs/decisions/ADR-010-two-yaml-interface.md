# ADR-010: Two-YAML interface for data products (product.yaml + infra.yaml)

> **Phase coverage**: Phase 4 refactor | **Last updated**: 2026-05-15

## Navigation
← [Decisions](../README.md) | [↑ Docs home](../../README.md)

## Status
Accepted

## Context

Domain teams need to express two distinct things about a data product:

1. **What the product is** — schema, SLA, quality rules, classification, owner, tags. This is the public contract that consumers and the catalog rely on. It changes when business requirements change.
2. **How the product is built** — Glue DPU, schedule, Iceberg partition keys, S3 source path, platform version pin. This is the implementation detail that only the domain data engineer cares about. It changes when infrastructure is tuned.

Previously, `product.yaml` conflated both concerns. This created two problems:

- **Consumer-facing vs. private**: Consumers browsing the catalog saw infrastructure details (Glue DPU, S3 paths) that are irrelevant and potentially sensitive. The catalog registration Lambda had to filter fields manually.
- **Different change rates, same file**: Tuning a Glue DPU from 2 to 4 and changing a column schema are fundamentally different operations — one is a catalog event, one is a private infrastructure change. Conflating them meant every infrastructure tweak triggered a catalog update.

Alternatives considered:

- **Single YAML with a top-level `infra:` block**: Preserves one file but still sends infrastructure details to the catalog unless filtered. Filtering logic lives in the Lambda and drifts over time.
- **Terraform variables file (`.tfvars`)**: Familiar to platform engineers but requires Terraform knowledge from domain teams. Inconsistent with the goal of keeping HCL out of DP repos (see ADR-011).
- **Environment variables / GitHub Actions inputs**: Ad-hoc, not versionable alongside the product definition.

## Decision

Every data product repo defines exactly two YAML files:

**`product.yaml`** — the public contract. Consumed by the platform catalog registration API. Never contains infrastructure details. This is the file the central catalog reads, stores, and exposes to consumers.

```yaml
name: revenue_daily
domain: sales
owner: jawahar@acme.com
description: Daily revenue aggregated by region
version: "2"
sla:
  freshness_hours: 24
  tier: gold
schema:
  - name: date,         type: date
  - name: gross_revenue, type: decimal
  - name: net_revenue,   type: decimal
classification: internal
tags: [finance, revenue]
quality_rules:
  - rule: IsComplete("gross_revenue")
  - rule: ColumnValues("date").isBetween(...)
```

**`infra.yaml`** — the implementation spec. Consumed by the platform-provided GitHub Actions reusable workflow as Terraform variable inputs. Never surfaces to the catalog or consumers.

```yaml
platform_version: v1.2        # domain team controls upgrade timing
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

**Rules:**
- `platform_version` lives in `infra.yaml`. Domain teams control when they upgrade. The platform never force-pushes a version bump.
- `product.yaml` is the only file the catalog API ever reads. The catalog Lambda must reject any request that passes `infra.yaml` fields.
- The platform-provided reusable workflow reads both files: `product.yaml` goes to the catalog registration call; `infra.yaml` provides Terraform input variables.
- Changes to `product.yaml` are governance events (catalog updated, subscribers notified if schema changes). Changes to `infra.yaml` are infrastructure events (Terraform apply, no catalog update).

## Consequences

### Positive
- **Clean catalog**: The central catalog only stores business-meaningful fields. No infrastructure leakage.
- **Independent change rates**: Infrastructure tuning (DPU, schedule) does not touch the public contract and does not generate catalog noise.
- **Audience alignment**: Data product owners author `product.yaml`; data engineers author `infra.yaml`. Neither needs to understand the other's file.
- **Simpler catalog Lambda**: No field filtering — the Lambda accepts `product.yaml` as-is.

### Negative
- **Two files to keep in sync**: `product.yaml` schema version and `infra.yaml` platform version are independently versioned. A domain team that upgrades `platform_version` in `infra.yaml` to a version that changes the schema format must also update `product.yaml`. The reusable workflow should validate compatibility between the two on every run.
- **Onboarding friction**: New domain teams must understand two files instead of one. Mitigated by the template repo providing pre-populated stubs with inline comments.

## See also
- [ADR-011](./ADR-011-no-hcl-in-dp-repos.md) — why infra.yaml replaces HCL in DP repos
- [ADR-012](./ADR-012-platform-managed-onboarding.md) — how platform_version in infra.yaml integrates with onboarding
- [Product spec reference](../reference/PRODUCT-SPEC.md) — canonical field definitions for product.yaml
