# Sales Domain — Example Data Products

This directory contains example data products for the **sales** domain.

## Products

| Product | Schema Version | Status | Description |
|---|---|---|---|
| [revenue_daily_v1](products/revenue_daily_v1/) | 1 | DEPRECATED (sunset 2026-09-01) | Daily revenue by region and product line (original schema) |
| [revenue_daily_v2](products/revenue_daily_v2/) | 2 | ACTIVE | Daily revenue with gross/net split (breaking change from v1) |

## Schema versioning story

`revenue_daily_v1` and `revenue_daily_v2` illustrate Data Meshy's versioned coexistence
pattern (ADR-009):

- A breaking schema change (renaming `revenue` → `gross_revenue`) required a new product
  version rather than an in-place mutation.
- Both versions live simultaneously in the catalog so existing consumers are not forced
  to migrate immediately.
- `revenue_daily_v1` is marked `deprecated: true` with `sunset_date: 2026-09-01`.
  The authoritative DEPRECATED status is managed in DynamoDB by the `product deprecate` CLI.
- After the sunset date, `revenue_daily_v1` will be RETIRED: Lake Formation grants revoked,
  resource links deleted.

Consumers should migrate to `revenue_daily_v2` before 2026-09-01. See
[revenue_daily_v1/README.md](products/revenue_daily_v1/README.md) for the migration guide.

## Browsing the catalog

Platform engineers can query the catalog directly:

```bash
python tools/catalog.py search --domain sales
python tools/catalog.py describe sales revenue_daily_v2
```
