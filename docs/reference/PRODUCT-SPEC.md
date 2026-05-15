# Reference: Data Product Specification (product.yaml + infra.yaml)

> **Phase coverage**: Phase 5 | **Last updated**: 2026-05-15

## Navigation

[<- Docs home](../README.md)

---

Every data product is defined by **two** YAML files that live in the same directory inside the domain's DP repo:

| File | Audience | Consumed by | Contains |
|---|---|---|---|
| `product.yaml` | Data product owners, catalog consumers | Catalog registration API, `provision-product.yml` | Schema, SLA, quality rules, classification, owner |
| `infra.yaml` | Domain data engineers | `provision-product.yml` as Terraform variable inputs | Glue DPU, schedule, Iceberg config, S3 source path, platform version pin |

The separation is intentional: `product.yaml` is the public contract that the catalog stores and exposes to consumers. `infra.yaml` is the private implementation spec that only the platform workflow ever reads. Consumers and the governance API never see `infra.yaml` fields. See [ADR-010](../decisions/ADR-010-two-yaml-interface.md) for the rationale.

Both files are validated by `provision-product.yml` on every push to main in the DP repo. Domain teams never run Terraform directly — the reusable workflow does it. See [ADR-011](../decisions/ADR-011-no-hcl-in-dp-repos.md).

---

## product.yaml

`product.yaml` is validated against `schemas/product_spec.json` (JSON Schema Draft 7). Every field at the top level is listed below.

### Top-Level Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Product name in snake_case. Must be unique within the domain. Pattern: `^[a-z][a-z0-9_]*$`. |
| `domain` | string | Yes | Domain this product belongs to. Must match a registered domain in `domains/*.yaml`. Pattern: `^[a-z][a-z0-9_-]*$`. |
| `owner` | string (email) | Yes | Product owner email. Receives quality alerts, freshness violations, and subscription notifications. |
| `description` | string | Yes | Human-readable description of what the product contains. Shown in catalog browse and describe responses. |
| `version` | string | Yes | Incremented on any schema change. String (e.g., `"1"`, `"2"`). Must be monotonically increasing. |
| `sla` | object | Yes | Service Level Agreement block. See below. |
| `schema` | array[object] | Yes | List of columns in the published gold table. Minimum 1 item. |
| `classification` | string | Yes | Data classification level. Values: `public`, `internal`, `confidential`, `restricted`. |
| `tags` | array[string] | No | Tags for catalog discoverability. |
| `quality_rules` | array[object] | Yes | List of Glue Data Quality (DQDL) rules. Minimum 1 item. |
| `deprecated` | boolean | No | Set to `true` when the product is deprecated. Authoritative state lives in DynamoDB; this field documents intent in the YAML. |
| `sunset_date` | string (ISO date) | No | ISO 8601 date (`YYYY-MM-DD`) when the product will be automatically retired and all Lake Formation grants revoked. |
| `superseded_by` | string | No | Name of the replacement product, if any. Shown in catalog describe for deprecated products. |

---

### `sla` Block

| Field | Type | Required | Valid Values | Description |
|---|---|---|---|---|
| `freshness_hours` | integer | Yes | Any positive integer | Maximum acceptable data age in hours. Monitored by EventBridge Scheduler. |
| `tier` | string | Yes | `gold` | Lake formation tier. Always `gold` for published products — no bronze or silver products are shared. |

---

### `schema` Array

Each item in `schema` defines one column in the published gold Iceberg table.

| Field | Type | Required | Valid Values | Description |
|---|---|---|---|---|
| `name` | string | Yes | Pattern: `^[a-z][a-z0-9_]*$` | Column name (snake_case). |
| `type` | string | Yes | Spark SQL types | Column data type. Common values: `string`, `integer`, `bigint`, `decimal`, `date`, `timestamp`, `boolean`, `double`. |

The schema array maps directly to the Iceberg table columns created in the gold Glue catalog database. The column list is the complete published surface area: anything not in this list is not visible to consumers.

---

### `quality_rules` Array

Each item specifies one Glue Data Quality Definition Language (DQDL) rule.

| Field | Type | Required | Description |
|---|---|---|---|
| `rule` | string | Yes | DQDL rule expression. Evaluated against the gold table after the pipeline run. |

#### Common DQDL Rule Patterns

| Rule Type | Example | Description |
|---|---|---|
| Completeness | `IsComplete("order_id")` | Fails if any value is null |
| Uniqueness | `IsUnique("order_id")` | Fails if any duplicates exist |
| Value Range | `ColumnValues("order_total") > 0` | Fails if any value does not satisfy the condition |
| Freshness | `Freshness("order_date") <= 2 days` | Fails if data is older than the threshold |
| Column Length | `ColumnLength("currency_code") = 3` | Fails if string length does not match |
| Value Set | `ColumnValues("status") IN ("active", "closed")` | Fails if values are outside the allowed set |

#### DQDL Limitations

- No cross-column comparisons (e.g., `end_date > start_date`)
- No referential integrity checks across tables
- No statistical distribution checks (standard deviation, percentile thresholds)
- No custom Python rule functions
- No row-level failure output (only aggregate pass/fail per rule)

---

### `classification` Field

Controls the subscription approval flow and Lake Formation tag assignment.

| Value | Access Policy | Subscription Approval |
|---|---|---|
| `public` | No restriction. Anyone in the org can subscribe. | Auto-approved |
| `internal` | Requires subscription request. Auto-approved for same-BU consumers. | Auto-approve or manual depending on consumer BU |
| `confidential` | Manual approval required. Use for PII data. | Manual approval by product owner |
| `restricted` | Explicit governance team sign-off required. | Governance sign-off |

---

### `tags` Field

Type: `array[string]`

Tags are used for catalog discoverability (search by keyword). Recommended tag categories:

| Category | Examples |
|---|---|
| Business area | `finance`, `marketing`, `operations` |
| Data type | `transactions`, `events`, `dimensions`, `reference` |
| Regulatory | `pii`, `gdpr`, `sox` |
| Domain | `revenue`, `customers`, `inventory` |

---

### Lifecycle Fields

These optional fields document lifecycle state in the YAML alongside the product definition. The authoritative lifecycle state always lives in DynamoDB (set by the governance API). The YAML fields are documentation and are committed alongside product changes for traceability.

| Field | When to set | Example |
|---|---|---|
| `deprecated: true` | After triggering `deprecate.yml` | `deprecated: true` |
| `sunset_date` | Computed by deprecation workflow, then committed | `sunset_date: "2026-09-01"` |
| `superseded_by` | When a replacement product exists | `superseded_by: revenue_daily_v2` |

See [DEPRECATE-PRODUCT.md](../guides/DEPRECATE-PRODUCT.md) for the full deprecation process.

---

## infra.yaml

`infra.yaml` is the private infrastructure specification for a data product. It is consumed exclusively by the `provision-product.yml` reusable workflow, which converts each field to a Terraform `-var` argument. The catalog API never reads or stores `infra.yaml` fields.

Domain teams author this file. Platform engineers define which fields are available (i.e., the set of fields is bounded by what `provision-product.yml` and the platform module support).

### Top-Level Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `platform_version` | string | Yes | Which version of the platform module to use. The `provision-product.yml` workflow fetches the module at `git::https://github.com/JawaharRamis/data-meshy-product-template//modules/data-product?ref={platform_version}`. Controls Terraform behavior. Bump this to upgrade. |
| `glue` | object | Yes | Glue job configuration. See below. |
| `iceberg` | object | Yes | Iceberg table and maintenance configuration. See below. |
| `s3` | object | Yes | Source data location. See below. |

---

### `glue` Block

| Field | Type | Required | Valid Values | Description |
|---|---|---|---|---|
| `dpu` | integer | Yes | `1`–`4` | Glue Data Processing Units allocated to the job. An SCP cap in the domain account enforces the maximum of 4. Higher DPU reduces runtime but increases cost. Start at 2 for most daily batch products. |
| `schedule` | string | Yes | Valid EventBridge cron expression | Schedule for the pipeline trigger. Format: `cron(minutes hours day-of-month month day-of-week year)`. Example: `cron(0 3 * * ? *)` runs at 03:00 UTC daily. |
| `worker_type` | string | Yes | `G.1X`, `G.2X`, `Standard` | Glue worker type. `G.1X` is standard for most products. `G.2X` for memory-intensive transforms. `Standard` for legacy Python shell jobs. |

---

### `iceberg` Block

| Field | Type | Required | Description |
|---|---|---|---|
| `partition_keys` | array[string] | Yes | Column names to partition the Iceberg table by. Must reference columns defined in `product.yaml`. Common values: `[date]`, `[date, region]`. Empty list `[]` means unpartitioned. |
| `compaction` | boolean | Yes | Whether to run Iceberg `OPTIMIZE` compaction as the final pipeline step. Set to `true` for products with frequent small writes or daily appends. |
| `retention_days` | integer | Yes | Number of days to retain Iceberg snapshots. Controls snapshot expiry (metadata pruning). Minimum recommended: `365`. `730` (2 years) is the default for financial data. |

---

### `s3` Block

| Field | Type | Required | Description |
|---|---|---|---|
| `source_prefix` | string | Yes | S3 URI prefix for the raw source data that the Glue raw ingestion job reads. Format: `s3://{bucket}/{prefix}/`. Example: `s3://sales-raw/revenue/`. The domain account's Glue execution role must have read access to this path. |

---

## Separation of concerns: why two files

| Concern | product.yaml | infra.yaml |
|---|---|---|
| Change triggers catalog update | Yes — every push re-registers the product | No — infra changes do not touch the catalog |
| Visible to consumers | Yes — returned by catalog API | Never |
| Versioned as public contract | Yes — `version` field incremented on schema changes | No — tuning DPU does not version the product |
| Audience | Data product owner, catalog consumers | Domain data engineer |
| Breaking change detection | Yes — CI detects column removals and type changes | No — infra changes are non-breaking by definition |
| Terraform variable inputs | No | Yes — each field maps to a `-var` argument |

---

## Full example: revenue_daily product

### product.yaml

```yaml
name: revenue_daily
domain: sales
owner: jawahar@acme.com
description: Daily revenue aggregated by region, net of returns and discounts.
version: "2"

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
  - name: net_revenue
    type: decimal
  - name: order_count
    type: integer

classification: internal
tags: [finance, revenue, daily]

quality_rules:
  - rule: IsComplete("gross_revenue")
  - rule: IsComplete("net_revenue")
  - rule: ColumnValues("gross_revenue") > 0
  - rule: IsComplete("date")
  - rule: IsUnique("date")
```

### infra.yaml

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

---

## Validation

`product.yaml` is validated in two places:

1. **CI gate (`reusable-product-validate.yml`)**: On every PR that modifies a `product.yaml`, the workflow validates the file against `schemas/product_spec.json` and checks for breaking changes vs the previous committed version.
2. **`provision-product.yml` (before provisioning)**: Validates the spec before any catalog registration or Terraform apply. A validation failure blocks provisioning.

`infra.yaml` is parsed (not schema-validated) by `provision-product.yml`. Required fields (`platform_version`, `glue.*`, `iceberg.*`, `s3.*`) cause the workflow to fail if absent.

### Breaking Change Detection

| Change Type | Classification | Action Required |
|---|---|---|
| New column added (nullable type) | Non-breaking | Increment `version`. Consumers can ignore new columns. |
| Description or tag updated | Non-breaking | Increment `version`. |
| SLA `freshness_hours` changed | Non-breaking | Increment `version`. |
| Column removed | **Breaking** | Create a new product version (e.g., `revenue_daily_v2`). Deprecate old product with a `sunset_date` giving consumers time to migrate. |
| Column type changed | **Breaking** | Create a new product version. |
| Column renamed | **Breaking** | Create a new product version. Renaming is a remove + add from the consumer's perspective. |
| `classification` changed to more restrictive | **Breaking** | Existing subscribers may lose access. Notify consumers first. |
| `classification` changed to less restrictive | Non-breaking | Increment `version`. |

**Infra changes are never breaking.** Changing `dpu`, `schedule`, `worker_type`, `compaction`, `retention_days`, or `platform_version` in `infra.yaml` does not generate a catalog event and does not affect consumers.

---

## See Also

- [ADR-010: Two-YAML interface](../decisions/ADR-010-two-yaml-interface.md) — rationale for separating product.yaml and infra.yaml
- [ADR-011: No HCL in DP repos](../decisions/ADR-011-no-hcl-in-dp-repos.md) — why infra.yaml replaced Terraform HCL in domain repos
- [JSON Schema](../../schemas/product_spec.json) — authoritative validation schema for product.yaml
- [Add a Product Guide](../guides/ADD-PRODUCT.md) — step-by-step product creation walkthrough
- [Deprecate a Product Guide](../guides/DEPRECATE-PRODUCT.md) — deprecation and retirement process
- [DATA-PRODUCT component](../components/DATA-PRODUCT.md) — the Terraform module that infra.yaml drives
