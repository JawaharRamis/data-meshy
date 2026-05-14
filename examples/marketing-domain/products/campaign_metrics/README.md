# campaign_metrics

**Domain**: marketing
**Schema version**: 1
**Classification**: internal
**Status**: ACTIVE

## Overview

Daily aggregated campaign performance metrics by channel. This product tracks
impressions, clicks, spend, and conversion rate per campaign per day.

No PII is captured — all columns are aggregated metrics or non-personal identifiers.
The `internal` classification means the product is available to any team within the
organization via the data mesh catalog, but is not shared externally.

## Schema

| Column | Type | Nullable | Description |
|---|---|---|---|
| campaign_id | string | NOT NULL | Unique campaign identifier |
| channel | string | yes | Marketing channel (email, paid-search, social, …) |
| impressions | long | yes | Total ad impressions for the day |
| clicks | long | yes | Total clicks for the day |
| spend | decimal(10,2) | yes | Total spend in USD for the day |
| conversion_rate | double | yes | Clicks-to-conversion ratio |

Partition key: `channel` (identity transform).

## Quality Rules

- `campaign_id` must be complete (no nulls) — threshold 100 %
- `spend` must be positive (> 0)

## SLA

- Refresh frequency: daily
- Freshness target: 24 hours
- Availability: 99.5 %

## Classification Rationale

This product contains no PII and no commercially sensitive figures at a granularity
that would constitute trade-secret information. `internal` classification is appropriate:
any authenticated domain team can subscribe without additional approval.

## Sources

- `ads-platform-api` → `/campaigns/metrics/daily`

## Catalog

Discover this product:
```bash
datameshy catalog search --domain marketing
datameshy catalog describe --name campaign_metrics
```
