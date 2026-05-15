# revenue_daily_v1

**Domain**: sales
**Schema version**: 1
**Classification**: internal
**Status**: DEPRECATED
**Sunset date**: 2026-09-01
**Superseded by**: [revenue_daily_v2](../revenue_daily_v2/README.md)

> **DEPRECATED** — This product will be retired on 2026-09-01.
> Existing queries continue to work until then, but no new subscriptions are accepted.
> Migrate to `revenue_daily_v2` before the sunset date.

## Overview

Daily revenue aggregated by region and product line (schema version 1).
This was the original revenue data product for the sales domain.

## Schema

| Column | Type | Nullable | Description |
|---|---|---|---|
| date | date | NOT NULL | Reporting date |
| region | string | yes | Sales region |
| product_line | string | yes | Product line name |
| revenue | decimal(12,2) | yes | Total revenue in USD |
| units_sold | long | yes | Total units sold |

Partition key: `date` (month transform).

## Why it is deprecated

A breaking schema change was required to distinguish gross revenue from net revenue
(after discounts and returns). In v2, the `revenue` column was renamed to `gross_revenue`
and a new `net_revenue` column was added.

Because renaming a column is a breaking change (consumers referencing `revenue` by name
will fail on the new schema), a new product version (`revenue_daily_v2`) was created
rather than mutating the existing schema in place — following ADR-009 (versioned
coexistence).

## Migration path to v2

1. Update your query or ETL job to use `revenue_daily_v2` instead of `revenue_daily_v1`.
2. Replace any reference to the `revenue` column with `gross_revenue`.
3. Optionally consume `net_revenue` for post-discount/post-return figures.
4. Discover `revenue_daily_v2` via the catalog (platform engineers only):
   ```bash
   python tools/catalog.py describe sales revenue_daily_v2
   ```
5. Cancel your subscription to `revenue_daily_v1` after validating the new pipeline.

## Quality Rules

- `date` must be complete (no nulls) — threshold 100 %
- `revenue` must be positive (> 0)

## Sources

- `sales-warehouse` → `public.daily_revenue`
