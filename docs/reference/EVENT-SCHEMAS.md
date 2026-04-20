# Reference: Event Schemas

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03

## Navigation
<- [Docs home](../README.md)

---

All mesh events follow JSON Schema (Draft 7). Schemas are stored in `schemas/events/*.json` and validated in CI on every PR. EventBridge Schema Registry enforces these at runtime.

### Common Required Fields

Every event includes these required fields:

| Field | Type | Description |
|---|---|---|
| `event_id` | string (UUID) | Unique event identifier for deduplication |
| `domain` | string | Domain name this event belongs to |
| `timestamp` | string (date-time) | ISO 8601 event timestamp |
| `version` | string | Event schema version |

### Event Source Values

| Source | Who Emits | Trust Level |
|---|---|---|
| `datameshy` | Domain accounts | Standard -- validated against registered domain |
| `datameshy.central` | Central Step Functions only | Elevated -- triggers LF grants; domain accounts cannot emit this source |

---

## DomainOnboarded

**Description**: Emitted when a new domain is onboarded into the mesh.

**Source**: `datameshy` (domain)

**Trigger**: Domain onboarding complete

**Central Handler**: Lambda -- register in `mesh-domains` DynamoDB

| Property | Type | Required | Description |
|---|---|---|---|
| `event_id` | string (UUID) | Yes | Unique event identifier |
| `domain` | string | Yes | Domain name |
| `account_id` | string | No | AWS account ID of the onboarded domain |
| `owner` | string | No | Domain owner contact email |
| `status` | string | No | Domain status. Values: `ACTIVE`, `INACTIVE` |
| `onboarded_at` | string (date-time) | No | Timestamp when the domain was onboarded |

---

## ProductCreated

**Description**: Emitted when a new data product is created and registered in the catalog.

**Source**: `datameshy` (domain)

**Trigger**: Data product provisioned

**Central Handler**: Lambda -- register in `mesh-products` DynamoDB

| Property | Type | Required | Description |
|---|---|---|---|
| `event_id` | string (UUID) | Yes | Unique event identifier |
| `domain` | string | Yes | Domain name |
| `product_name` | string | No | Product name |
| `product_id` | string | No | Composite key: `domain#product_name` |
| `owner` | string | No | Product owner email |
| `classification` | string | No | Data classification level. Values: `public`, `internal`, `confidential`, `restricted` |
| `description` | string | No | Product description |
| `tags` | array[string] | No | Product tags for catalog discoverability |
| `schema_version` | integer | No | Schema version of the product spec |
| `sla` | object | No | Service Level Agreement |
| `sla.refresh_frequency` | string | No | Refresh frequency |
| `sla.freshness_target` | string | No | Maximum acceptable data age |

---

## ProductRefreshed

**Description**: Emitted when a data product is successfully refreshed and the central catalog is updated.

**Source**: `datameshy` (domain)

**Trigger**: Medallion pipeline completes successfully

**Central Handler**: Lambda -- update freshness in `mesh-products` and `mesh-quality-scores`

| Property | Type | Required | Description |
|---|---|---|---|
| `event_id` | string (UUID) | Yes | Unique event identifier |
| `domain` | string | Yes | Domain name |
| `product_name` | string | No | Product name |
| `product_id` | string | No | Composite key: `domain#product_name` |
| `schema_version` | integer | No | Schema version of the product |
| `quality_score` | number | No | Quality score from latest refresh |
| `rows_written` | integer | No | Number of rows written in latest refresh |
| `pipeline_execution_arn` | string | No | Pipeline execution ARN |

---

## QualityAlert

**Description**: Emitted when a data quality check fails below the quality threshold.

**Source**: `datameshy` (domain)

**Trigger**: Quality score drops below `minimum_quality_score` in product.yaml

**Central Handler**: SNS -- notify owner; mark product as "degraded" in catalog

| Property | Type | Required | Description |
|---|---|---|---|
| `event_id` | string (UUID) | Yes | Unique event identifier |
| `domain` | string | Yes | Domain name |
| `product_name` | string | No | Product name |
| `product_id` | string | No | Composite key: `domain#product_name` |
| `failed_rules` | array[string] | No | List of failed quality rule names |
| `quality_score` | number | No | Quality score at evaluation |
| `pipeline_execution_arn` | string | No | Step Functions execution ARN |

---

## SchemaChanged

**Description**: Emitted when a schema change is detected for a product.

**Source**: `datameshy` (domain)

**Trigger**: Schema drift detected between pipeline runs or at publish time

**Central Handler**: SNS -- notify owner + subscribers

| Property | Type | Required | Description |
|---|---|---|---|
| `event_id` | string (UUID) | Yes | Unique event identifier |
| `domain` | string | Yes | Domain name |
| `product_name` | string | No | Product name |
| `product_id` | string | No | Composite key: `domain#product_name` |
| `schema_version` | integer | No | Schema version after change |
| `previous_schema_version` | integer | No | Schema version before change |
| `breaking` | boolean | No | Whether this is a breaking change |
| `added_columns` | array[object] | No | Columns added. Each has `name` (string, required) and `type` (string). |
| `removed_columns` | array[object] | No | Columns removed. Each has `name` (string, required). |
| `changed_columns` | array[object] | No | Columns with type changes. Each has `name` (string, required), `old_type` (string, required), `new_type` (string, required). |

---

## SubscriptionRequested

**Description**: Emitted when a consumer requests access to a data product.

**Source**: `datameshy` (domain)

**Trigger**: Consumer runs `datameshy subscribe request`

**Central Handler**: Step Functions -- approval workflow

| Property | Type | Required | Description |
|---|---|---|---|
| `event_id` | string (UUID) | Yes | Unique event identifier |
| `domain` | string | Yes | Consumer domain name |
| `product_name` | string | No | Product name requested |
| `product_id` | string | No | Composite key: `domain#product_name` |
| `consumer_domain` | string | No | Domain requesting access |
| `consumer_account_id` | string | No | AWS account ID of the consumer |
| `requested_columns` | array[string] | No | Columns the consumer is requesting access to |
| `justification` | string | No | Business justification for the subscription request |

---

## SubscriptionApproved

**Description**: Emitted when a subscription request is approved by the data product owner. Triggers LF cross-account grant.

**Source**: `datameshy.central` (central Step Functions only -- domain accounts cannot emit this source)

**Trigger**: Central Step Functions approves subscription

**Central Handler**: Lambda -- execute LF cross-account grant via `MeshLFGrantorRole`

| Property | Type | Required | Description |
|---|---|---|---|
| `event_id` | string (UUID) | Yes | Unique event identifier |
| `domain` | string | Yes | Producer domain name |
| `product_name` | string | No | Product name |
| `product_id` | string | No | Composite key: `domain#product_name` |
| `subscriber_domain` | string | No | Domain that requested access |
| `subscriber_account_id` | string | No | AWS account ID of the subscriber |
| `subscription_id` | string | No | Subscription identifier |
| `approved_by` | string | No | Who approved the subscription |
| `approved_at` | string (date-time) | No | Timestamp of approval |
| `columns_requested` | array[string] | No | List of columns the subscriber requested access to |
| `columns_excluded` | array[string] | No | Columns excluded from access (e.g., PII columns) |
| `classification` | string | No | Data classification level |

---

## FreshnessViolation

**Description**: Emitted when a product's freshness SLA is breached.

**Source**: `datameshy.central` (central EventBridge Scheduler + Lambda)

**Trigger**: Daily cron detects product not refreshed within `sla.freshness_target`

**Central Handler**: SNS -- notify owner

| Property | Type | Required | Description |
|---|---|---|---|
| `event_id` | string (UUID) | Yes | Unique event identifier |
| `domain` | string | Yes | Domain name |
| `product_name` | string | No | Product name that breached SLA |
| `product_id` | string | No | Composite key: `domain#product_name` |
| `violation_reason` | string | No | Reason for the SLA breach |
| `age_hours` | number | No | How many hours since last successful refresh |
| `sla_target_hours` | number | No | Maximum allowed hours between refreshes per SLA |
| `last_refreshed_at` | string (date-time) | No | Timestamp of the last successful refresh |

---

## ProductDeprecated

**Description**: Emitted when a product is deprecated by its owner.

**Source**: `datameshy` (domain)

**Trigger**: Owner runs `datameshy product deprecate`

**Central Handler**: SNS -- notify all subscribers; mark product as deprecated in catalog

| Property | Type | Required | Description |
|---|---|---|---|
| `event_id` | string (UUID) | Yes | Unique event identifier |
| `domain` | string | Yes | Domain name |
| `product_name` | string | No | Product name |
| `product_id` | string | No | Composite key: `domain#product_name` |
| `sunset_date` | string (date) | No | Date when the product will be fully retired and access revoked |
| `reason` | string | No | Reason for deprecation |
| `deprecation_initiated_by` | string | No | Who initiated the deprecation |

---

## PipelineFailure

**Description**: Emitted when a Step Functions pipeline fails.

**Source**: `datameshy` (domain)

**Trigger**: Any unhandled error in the medallion pipeline

**Central Handler**: SNS -- notify owner; log to audit; route to DLQ

| Property | Type | Required | Description |
|---|---|---|---|
| `event_id` | string (UUID) | Yes | Unique event identifier |
| `domain` | string | Yes | Domain name |
| `product_name` | string | No | Product name |
| `product_id` | string | No | Composite key: `domain#product_name` |
| `pipeline_execution_arn` | string | No | Step Functions execution ARN |
| `error_message` | string | No | Error message from the failed pipeline step |
| `error_type` | string | No | Error classification |
| `failed_step` | string | No | Name of the failed Step Functions state |

---

## Event Delivery Properties

| Property | Value | Notes |
|---|---|---|
| Delivery guarantee | At-least-once | Handlers must be idempotent |
| Ordering | No guarantee | Handlers must be resilient to out-of-order delivery |
| Dedup | `mesh-event-dedup` table (24h TTL) | Conditional write on `event_id` |
| Step Functions dedup | `event_id` used as execution name | Step Functions rejects duplicate names natively |

---

## Event Routing Summary

| Event | Source | Trigger | Central Handler |
|---|---|---|---|
| `DomainOnboarded` | `datameshy` (domain) | Domain onboarding complete | Lambda: register in DynamoDB |
| `ProductCreated` | `datameshy` (domain) | Data product provisioned | Lambda: register in catalog |
| `ProductRefreshed` | `datameshy` (domain) | Medallion pipeline completes | Lambda: update freshness |
| `QualityAlert` | `datameshy` (domain) | Quality score below threshold | SNS: notify owner |
| `SchemaChanged` | `datameshy` (domain) | Schema drift detected | SNS: notify owner + subscribers |
| `SubscriptionRequested` | `datameshy` (domain) | Consumer requests access | Step Functions: approval workflow |
| `SubscriptionApproved` | `datameshy.central` | Central SFN approves | Lambda: execute LF grant |
| `FreshnessViolation` | `datameshy.central` | SLA breached | SNS: notify owner |
| `ProductDeprecated` | `datameshy` (domain) | Owner deprecates product | SNS: notify all subscribers |
| `PipelineFailure` | `datameshy` (domain) | Step Functions pipeline fails | SNS: notify owner, log to audit |

---

## See Also

- [Product Spec Reference](PRODUCT-SPEC.md) -- quality rules and SLA fields that trigger events
- [Architecture Document](../../plan/ARCHITECTURE.md) -- event architecture, idempotency, and DLQ design
- Schema files: `schemas/events/*.json`
