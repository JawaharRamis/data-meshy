# Marketing Domain — Example Data Products

This directory contains example data products for the **marketing** domain.

## Products

| Product | Schema Version | Status | Classification | Description |
|---|---|---|---|---|
| [campaign_metrics](products/campaign_metrics/) | 1 | ACTIVE | internal | Daily campaign performance metrics by channel |

## About this domain

The marketing domain owns data products related to campaign performance, audience
segments, and channel analytics. All products in this domain are `internal` — no PII
is captured, and data is available to any authenticated domain team via the mesh catalog.

## Browsing the catalog

```bash
datameshy catalog search --domain marketing
datameshy catalog describe --name campaign_metrics
```
