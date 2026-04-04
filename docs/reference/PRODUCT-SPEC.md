# Reference: Data Product Specification (product.yaml)

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03

## Navigation
<- [Docs home](../README.md)

---

Every data product is defined by a `product.yaml` file validated against `schemas/product_spec.json` (JSON Schema Draft 7). This document catalogs every field, its type, whether it is required, and its valid values.

---

## Top-Level Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | integer | Yes | Monotonically increasing integer version. Incremented on any change. Minimum: 1. |
| `product` | object | Yes | Product identity block. See below. |
| `sla` | object | Yes | Service Level Agreement block. See below. |
| `schema` | object | Yes | Schema definition block. See below. |
| `quality` | object | Yes | Quality contract block. See below. |
| `tags` | array[string] | No | Tags for catalog discoverability and LF-Tag-based access control. |
| `classification` | string | Yes | Data classification level. Values: `public`, `internal`, `confidential`, `restricted`. |
| `lineage` | object | No | Source systems and pipeline type. See below. |

---

## `product` Block

| Field | Type | Required | Valid Values | Description |
|---|---|---|---|---|
| `name` | string | Yes | Pattern: `^[a-z][a-z0-9_]*$` | Unique product name within the domain (snake_case). Minimum length: 1. |
| `domain` | string | Yes | Pattern: `^[a-z][a-z0-9_-]*$` | Domain name this product belongs to. Must match a registered domain in `mesh-domains`. |
| `description` | string | No | Min length: 1 | Human-readable description of what this product contains. |
| `owner` | string (email) | Yes | Valid email format | Product owner email address. Receives quality alerts, freshness violations, subscription requests. |
| `contact_channel` | string | No | Any string | Slack channel or other support channel for consumer questions. |

---

## `sla` Block

| Field | Type | Required | Valid Values | Description |
|---|---|---|---|---|
| `refresh_frequency` | string | Yes | `hourly`, `daily`, `weekly`, `monthly`, `on_demand` | How often data is refreshed. |
| `freshness_target` | string | No | Duration string (e.g., `24 hours`, `4 hours`, `7 days`) | Maximum acceptable data age. Monitored by EventBridge Scheduler + Lambda. |
| `availability` | string | No | Percentage string (e.g., `99.9%`) | Availability SLA target. |

---

## `schema` Block

| Field | Type | Required | Description |
|---|---|---|---|
| `format` | string | No | Table format. Always `iceberg` for mesh-published products. |
| `columns` | array[object] | Yes | List of columns in the gold (published) layer. Minimum 1 item. |
| `partition_by` | array[object] | No | Iceberg partitioning strategy. |

### `schema.columns[]` Items

| Field | Type | Required | Valid Values | Description |
|---|---|---|---|---|
| `name` | string | Yes | Pattern: `^[a-z][a-z0-9_]*$` | Column name (snake_case). Minimum length: 1. |
| `type` | string | Yes | Spark SQL types (e.g., `string`, `integer`, `bigint`, `decimal(10,2)`, `date`, `timestamp`) | Column data type. |
| `description` | string | No | Any string | Human-readable column description for consumers. |
| `pii` | boolean | Yes | `true`, `false` | Whether this column contains personally identifiable information. Drives LF column-level filtering. |
| `nullable` | boolean | Yes | `true`, `false` | Whether this column can contain null values. |

### `schema.partition_by[]` Items

| Field | Type | Required | Valid Values | Description |
|---|---|---|---|---|
| `column` | string | Yes | Must match a column name in `schema.columns` | Column to partition by. |
| `transform` | string | No | `identity`, `year`, `month`, `day`, `hour`, `bucket`, `truncate` | Iceberg partition transform. Default: `identity`. |

---

## `quality` Block

| Field | Type | Required | Description |
|---|---|---|---|
| `rules` | array[object] | Yes | List of Glue Data Quality (DQDL) rules. Minimum 1 item. |
| `minimum_quality_score` | number | No | Minimum overall quality score (0--100) for the pipeline to publish. If score falls below this, `QualityAlert` is emitted and publish is blocked. |

### `quality.rules[]` Items

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Human-readable rule name (used in alerts and reports). Minimum length: 1. |
| `rule` | string | Yes | DQDL rule expression (e.g., `IsComplete 'order_id'`, `ColumnValues 'order_total' > 0`). Minimum length: 1. |
| `threshold` | number | No | Pass threshold (0.0--1.0). Defaults to Glue DQ default if not set. |

### Common DQDL Rule Patterns

| Rule Type | Example | Description |
|---|---|---|
| Completeness | `IsComplete 'order_id'` | Checks for null values |
| Uniqueness | `IsUnique 'order_id'` | Checks for duplicate values |
| Value Range | `ColumnValues 'order_total' > 0` | Checks column values meet condition |
| Freshness | `Freshness 'order_date' <= 2 days` | Checks data recency |
| Column Length | `ColumnLength 'currency_code' = 3` | Checks string length |
| Value Set | `ColumnValues 'status' IN ("active", "closed")` | Checks values in allowed set |

### DQDL Limitations

- No cross-column validation (e.g., `end_date > start_date`)
- No referential integrity across tables
- No statistical distribution checks
- No custom Python rule functions
- No row-level failure output

For rules DQDL cannot express, a custom Lambda quality step runs after Glue DQ.

---

## `tags` Field

Type: `array[string]`

Tags serve two purposes:
1. Catalog discoverability (search by keyword)
2. LF-Tag-based access control (`classification`, `pii`, `domain` tags are mandatory)

Recommended tag categories:

| Category | Examples |
|---|---|
| Business area | `ecommerce`, `finance`, `marketing` |
| Data type | `transactions`, `events`, `dimensions` |
| Sensitivity | `pii` (required if any column has `pii: true`) |

---

## `classification` Field

Type: `string` (required)

| Value | Access Policy | Subscription Flow |
|---|---|---|
| `public` | No restriction. Anyone in the org can subscribe without approval. | Auto-approved |
| `internal` | Requires subscription request. Auto-approved for same-BU consumers. | Auto-approve or manual |
| `confidential` | Manual approval required. PII data goes here. | Manual approval |
| `restricted` | Explicit governance team sign-off required. Highly sensitive data. | Governance sign-off |

---

## `lineage` Block

| Field | Type | Required | Description |
|---|---|---|---|
| `sources` | array[object] | No | List of source systems that feed this product. |
| `pipeline_type` | string | No | Pipeline technology. Values: `glue_step_functions`, `dbt`, `lambda`, `emr`, `flink`, `external`. Default: `glue_step_functions`. |

### `lineage.sources[]` Items

| Field | Type | Required | Description |
|---|---|---|---|
| `system` | string | Yes | Name of the source system (e.g., `orders-postgres-db`). |
| `table` | string | No | Source table name in `schema.table` format for JDBC sources. |
| `endpoint` | string | No | API endpoint path for API sources. |
| `credentials_secret_arn` | string | No | AWS Secrets Manager ARN for source credentials. Pattern: `^arn:aws:secretsmanager:`. |

---

## Example: customer_orders product.yaml

```yaml
# product.yaml -- customer_orders data product (PRD Section 8)
# Domain: sales
# Schema version: 1

product:
  name: customer_orders
  domain: sales
  description: "Daily customer order transactions enriched with customer segments"
  owner: sales-data-team@company.com
  contact_channel: "#sales-data-support"

schema_version: 1

schema:
  format: iceberg
  columns:
    - name: order_id
      type: string
      description: "Unique order identifier"
      pii: false
      nullable: false
    - name: customer_email
      type: string
      description: "Customer email address"
      pii: true
      nullable: false
    - name: order_total
      type: decimal(10,2)
      description: "Order total in USD"
      pii: false
      nullable: false
    - name: order_date
      type: date
      description: "Date the order was placed"
      pii: false
      nullable: false
    - name: customer_segment
      type: string
      description: "Customer segment (enriched in gold layer)"
      pii: false
      nullable: true
  partition_by:
    - column: order_date
      transform: month

quality:
  rules:
    - name: order_id_complete
      rule: "IsComplete 'order_id'"
      threshold: 1.0
    - name: order_id_unique
      rule: "IsUnique 'order_id'"
    - name: order_total_positive
      rule: "ColumnValues 'order_total' > 0"
    - name: order_date_recent
      rule: "Freshness 'order_date' <= 2 days"
  minimum_quality_score: 95

sla:
  refresh_frequency: daily
  freshness_target: "24 hours"
  availability: "99.9%"

tags:
  - ecommerce
  - customer-data
  - pii

classification: confidential

lineage:
  sources:
    - system: "orders-postgres-db"
      table: "public.orders"
    - system: "crm-api"
      endpoint: "/customers/segments"
```

---

## Validation

The `product.yaml` is validated in two places:

1. **CI (GitHub Actions)**: On every PR that modifies a `product.yaml`, the CI pipeline validates the file against `schemas/product_spec.json`, checks all referenced columns exist in the schema, and detects breaking changes vs. the previous committed version.
2. **CLI**: `datameshy product create --spec product.yaml` validates the spec before provisioning any resources.

### Breaking Change Detection

| Change Type | Classification | Action Required |
|---|---|---|
| New nullable column added | Non-breaking (patch) | `schema_version` increments. No consumer impact. |
| Description updated | Non-breaking (patch) | `schema_version` increments. |
| Tag added | Non-breaking (patch) | `schema_version` increments. |
| Column removed | Breaking (major) | New product version required (e.g., `customer_orders_v2`). 90-day deprecation period. |
| Column type changed | Breaking (major) | New product version required. |
| Column renamed | Breaking (major) | New product version required. |
| `nullable: false` changed to `true` | Non-breaking (patch) | `schema_version` increments. |
| `nullable: true` changed to `false` | Breaking (major) | New product version required. |

---

## See Also

- [JSON Schema](../../schemas/product_spec.json) -- the authoritative validation schema
- [Product Template](../../templates/product_spec/product.yaml.template) -- starter template with inline documentation
- [Add a Product Guide](../guides/ADD-PRODUCT.md) -- step-by-step product creation
