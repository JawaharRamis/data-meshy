# revenue_daily_v2

**Domain**: sales
**Schema version**: 2
**Classification**: internal
**Status**: ACTIVE
**Replaces**: [revenue_daily_v1](../revenue_daily_v1/README.md) (deprecated, sunset 2026-09-01)

## Overview

Daily revenue aggregated by region and product line — schema version 2.
This product introduces clearer revenue semantics by separating gross revenue
from net revenue (after discounts and returns).

## Schema

| Column | Type | Nullable | Description |
|---|---|---|---|
| date | date | NOT NULL | Reporting date |
| region | string | yes | Sales region |
| product_line | string | yes | Product line name |
| gross_revenue | decimal(12,2) | yes | Total gross revenue in USD |
| units_sold | long | yes | Total units sold |
| net_revenue | decimal(12,2) | yes | Net revenue after discounts and returns (new in v2) |

Partition key: `date` (month transform).

## Breaking changes from v1

| Change | v1 column | v2 column | Impact |
|---|---|---|---|
| Column renamed | `revenue` | `gross_revenue` | **Breaking** — queries referencing `revenue` by name will fail |
| New column | — | `net_revenue` | Additive — existing SELECT * queries receive an extra column |

Because the rename is a breaking change, v1 and v2 coexist simultaneously in the catalog
(ADR-009). Consumers choose their migration timing. v1 is deprecated and will be retired
on 2026-09-01.

## Quality Rules

- `date` must be complete (no nulls) — threshold 100 %
- `gross_revenue` must be positive (> 0)
- `net_revenue` must be positive (> 0)

## SLA

- Refresh frequency: daily
- Freshness target: 24 hours
- Availability: 99.5 %

## Sources

- `sales-warehouse` → `public.daily_revenue`
- `returns-service` → `/returns/daily-summary`

## Catalog

Platform engineers can discover this product using:
```bash
python tools/catalog.py search --domain sales
python tools/catalog.py describe sales revenue_daily_v2
```

> Note: `python tools/catalog.py search --domain sales` returns both `revenue_daily_v1` and
> `revenue_daily_v2`. This is expected — both are live in the catalog until v1 is retired.
> Integration-level validation of the catalog search results requires a live DynamoDB
> instance and is not covered by the local spec validation script.
