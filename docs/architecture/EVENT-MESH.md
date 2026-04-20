# Event Mesh

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
← [Medallion Pipeline](MEDIATION-PIPELINE.md) | [Next →](SECURITY.md) | [↑ Docs home](../README.md)

---

## Overview

Data Meshy uses a two-tier EventBridge topology: each domain has a local event bus, and a central `mesh-central-bus` aggregates all domain events. Events are the only mechanism by which domain state changes propagate to the central catalog, audit log, and alerting system. No domain account has direct DynamoDB access.

## Event Flow Diagram

```
+-------------------+     +-------------------+
| DOMAIN ACCOUNT    |     | DOMAIN ACCOUNT    |
| (Sales)           |     | (Marketing)       |
|                   |     |                   |
| +---------------+ |     | +---------------+ |
| | mesh-domain-  | |     | | mesh-domain-  | |
| | bus           | |     | | bus           | |
| +-------+-------+ |     | +-------+-------+ |
|         |         |     |         |         |
| Rule: source=     |     | Rule: source=     |
|   datameshy       |     |   datameshy       |
|   -> forward to   |     |   -> forward to   |
|   central bus     |     |   central bus     |
+---------+---------+     +---------+---------+
          |                         |
          | PutEvents (cross-acct)  | PutEvents (cross-acct)
          | via MeshEventRole       | via MeshEventRole
          v                         v
+---------------------------------------------------------+
|               CENTRAL GOVERNANCE ACCOUNT                |
|                                                         |
|  +---------------------------------------------------+ |
|  | mesh-central-bus                                   | |
|  |                                                     | |
|  | Resource Policy:                                    | |
|  |   Explicit list of domain account IDs (no wildcards)|
|  |   Action: events:PutEvents                          | |
|  +---+-------+-------+--------+-----------+----------+ |
|      |       |       |        |           |             |
|  Rules:                                                 |
|      |       |       |        |           |             |
|      v       v       v        v           v             |
|  +------+ +------+ +------+ +------+ +-----------+    |
|  |Catalog| |Subsc.| |Qual. | |Pipe. | |All Events |    |
|  |Update | |Work- | |Alerts| |Fail. | |Audit      |    |
|  |Rule   | |flow  | |Rule  | |Rule  | |Rule       |    |
|  +--+---+ +--+---+ +--+---+ +--+---+ +--+--------+    |
|     |        |        |        |          |             |
|     v        v        v        v          v             |
|  +------+ +------+ +------+ +------+ +-----------+    |
|  |Lambda| |Step   | |SNS   | |SNS   | |CloudWatch |    |
|  |(cat. | |Funcs  | |topic | |topic | |Log Group  |    |
|  |write)| |(subsc.| |(qual.| |(pipe.| |(/aws/     |    |
|  |      | |appr.) | |alert)| |fail) | |events/    |    |
|  |      | |       | |      | |      | |mesh-central|   |
|  |      | |       | |      | |      | | -audit)    |   |
|  +--+---+ +--+---+ +--+---+ +--+---+ +-----------+    |
|     |DLQ     |DLQ     |        |                         |
|     v        v        v        v                         |
|  +------+ +------+                                            |
|  |mesh- | |mesh- |  SQS DLQs (14-day retention, KMS-encrypted)|
|  |catalog| |subsc.|                                            |
|  |dlq   | |dlq   |                                            |
|  +------+ +------+                                            |
+---------------------------------------------------------+
```

## Bus Configuration

### Domain Bus (`mesh-domain-bus`)

- **Name**: `mesh-domain-bus`
- **Resource policy**: Only allows `PutEvents` from within the same account. Prevents cross-domain event injection.
- **Rule**: Forwards all events with `source: datameshy` to the central bus.
- **Defined in**: `infra/modules/domain-account/` (EventBridge resources)

### Central Bus (`mesh-central-bus`)

- **Name**: `mesh-central-bus`
- **Resource policy**: Explicit list of domain account IDs in the Principal. No wildcards. Only known domain accounts can `PutEvents`.
- **Schema Registry**: `mesh-events` -- JSON Schema definitions for all event types. Enforced by EventBridge.
- **Defined in**: `infra/modules/governance/eventbridge.tf:7-13`

Bus resource policy source: `infra/modules/governance/eventbridge.tf:18-35`

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "AllowDomainAccountsPutEvents",
    "Effect": "Allow",
    "Principal": { "AWS": ["arn:aws:iam::{domain_account_id}:root", ...] },
    "Action": "events:PutEvents",
    "Resource": "arn:aws:events:...:event-bus/mesh-central-bus"
  }]
}
```

## Schema Registry

Name: `mesh-events`. Defined in `infra/modules/governance/eventbridge.tf:40-45`.

All 10 event schemas live in `schemas/events/` as JSON Schema (draft-07) files:

| File | Event Type |
|---|---|
| `schemas/events/DomainOnboarded.json` | `DomainOnboarded` |
| `schemas/events/ProductCreated.json` | `ProductCreated` |
| `schemas/events/ProductRefreshed.json` | `ProductRefreshed` |
| `schemas/events/QualityAlert.json` | `QualityAlert` |
| `schemas/events/SchemaChanged.json` | `SchemaChanged` |
| `schemas/events/SubscriptionRequested.json` | `SubscriptionRequested` |
| `schemas/events/SubscriptionApproved.json` | `SubscriptionApproved` |
| `schemas/events/FreshnessViolation.json` | `FreshnessViolation` |
| `schemas/events/ProductDeprecated.json` | `ProductDeprecated` |
| `schemas/events/PipelineFailure.json` | `PipelineFailure` |

Common schema fields across all events: `event_id` (UUID, required), `domain` (string, required), `timestamp` (ISO-8601, required), `version` (string, required).

## Event Types

| # | Event Type | Source | Emitter | Description |
|---|---|---|---|---|
| 1 | `DomainOnboarded` | `datameshy` | Domain Terraform apply | A new domain account has been provisioned and registered in the mesh |
| 2 | `ProductCreated` | `datameshy` | Domain `product create` CLI | A new data product has been provisioned (S3, Iceberg table, DQ ruleset, pipeline) |
| 3 | `ProductRefreshed` | `datameshy` | Step Functions pipeline | A medallion pipeline run completed successfully and catalog was updated |
| 4 | `QualityAlert` | `datameshy` | Step Functions pipeline | A quality check failed below threshold; product marked `degraded` |
| 5 | `SchemaChanged` | `datameshy` | Schema drift Lambda (Phase 4) or pipeline SchemaValidate step | Schema drift detected between live table and product.yaml contract |
| 6 | `SubscriptionRequested` | `datameshy` | Consumer CLI `subscribe request` | A consumer domain requested access to a data product |
| 7 | `SubscriptionApproved` | `datameshy.central` | Central Step Functions workflow | Subscription approved; triggers LF grant saga (NEVER emitted by domain accounts) |
| 8 | `FreshnessViolation` | `datameshy.central` | EventBridge Scheduler + Lambda | Product SLA breached -- last refresh older than `sla.freshness_target` |
| 9 | `ProductDeprecated` | `datameshy` | Domain CLI `product deprecate` | Product owner marked a product as deprecated; subscribers notified |
| 10 | `PipelineFailure` | `datameshy` | Step Functions error handler | An unhandled error occurred during medallion pipeline execution |

## Central Bus Routing Rules

Five rules are defined on `mesh-central-bus` in `infra/modules/governance/eventbridge.tf:142-260`:

### Rule 1: `mesh-catalog-update` (line 142)

- **Matches**: `source: datameshy`, `detail-type: [ProductCreated, ProductRefreshed]`
- **Target**: Lambda (catalog writer) + SQS DLQ (`mesh-catalog-dlq`)
- **Purpose**: Update DynamoDB catalog with product metadata

### Rule 2: `mesh-subscription-workflow` (line 167)

- **Matches**: `source: datameshy`, `detail-type: [SubscriptionRequested]`
- **Target**: Step Functions (subscription approval saga) + SQS DLQ (`mesh-subscription-dlq`)
- **Purpose**: Kick off cross-account subscription workflow

### Rule 3: `mesh-quality-alerts` (line 191)

- **Matches**: `source: [datameshy, datameshy.central]`, `detail-type: [QualityAlert, FreshnessViolation, SchemaChanged]`
- **Target**: SNS topic (`mesh-quality-alerts`) + SQS DLQ (`mesh-audit-dlq`)
- **Purpose**: Notify product owners of quality, freshness, and schema issues

### Rule 4: `mesh-pipeline-failures` (line 217)

- **Matches**: `source: datameshy`, `detail-type: [PipelineFailure]`
- **Target**: SNS topic (`mesh-pipeline-failures`) + SQS DLQ (`mesh-audit-dlq`)
- **Purpose**: Alert on pipeline failures

### Rule 5: `mesh-all-events-audit` (line 242)

- **Matches**: `source: [prefix: datameshy]` (all mesh events)
- **Target**: CloudWatch Log Group (`/aws/events/mesh-central-audit`, 90-day retention)
- **Purpose**: Audit trail of every event on the central bus

## Event Delivery Guarantees

EventBridge provides **at-least-once delivery with no ordering guarantees**. This is a documented AWS characteristic, not an edge case.

### At-least-once (duplicate handling)

Every event includes a unique `event_id` (UUID). Central handlers write to `mesh-event-dedup` table with a conditional put (`attribute_not_exists(event_id)`). If the write fails (duplicate), the handler returns early. TTL is 24 hours.

For low-frequency events (`ProductDeprecated`, `DomainOnboarded`) that may be replayed after the 24h TTL, handlers must check current resource state before acting.

### No ordering (out-of-order handling)

Events may arrive out of order. Example: a `QualityAlert` can arrive before the `ProductCreated` event for the same product if they are emitted close together. All handlers must be resilient:
- If a handler receives an event for a product not yet in the catalog, queue for retry (SQS visibility timeout + re-drive).
- Never silently drop events.

### Event source validation

Every central Lambda handler follows this pattern:

1. Extract `account` from the EventBridge event envelope (set by AWS, not caller-controlled)
2. Look up the domain registered to that `account` in `mesh-domains` table
3. Verify the `domain` field in the event body matches the registered domain
4. Reject and write `SECURITY_ALERT` to audit log on mismatch

### Critical event isolation

`SubscriptionApproved` (source: `datameshy.central`) is only emitted by the central Step Functions subscription workflow. No domain account's `MeshEventRole` can PutEvents with that source -- there is an explicit Deny in the role policy (`infra/modules/domain-account/iam.tf:559-569`).

## Dead Letter Queues

Three SQS DLQs, all KMS-encrypted with 14-day retention:

| DLQ | Purpose | Defined In |
|---|---|---|
| `mesh-catalog-dlq` | Failed catalog update rule targets | `infra/modules/governance/eventbridge.tf:50` |
| `mesh-audit-dlq` | Failed audit/alert rule targets | `infra/modules/governance/eventbridge.tf:61` |
| `mesh-subscription-dlq` | Failed subscription workflow targets | `infra/modules/governance/eventbridge.tf:72` |

CloudWatch alarms trigger on `ApproximateNumberOfMessagesVisible > 0` for each DLQ. Any message in a DLQ is treated as an incident and routes to the `mesh-pipeline-failures` SNS topic.

Alarm definitions: `infra/modules/governance/eventbridge.tf:272-293`

## SNS Topics

| Topic | Triggered By | Subscribers |
|---|---|---|
| `mesh-quality-alerts` | QualityAlert, FreshnessViolation, SchemaChanged events | Product owner email |
| `mesh-pipeline-failures` | PipelineFailure events, DLQ alarms | Platform team, domain owner |
| `mesh-freshness-violations` | Freshness SLA breach | Product owner |
| `mesh-subscription-requests` | SubscriptionRequested events | Producer domain owner |

## Idempotency Pattern

All event handlers use the same idempotency pattern:

```
1. Event arrives with event_id (UUID)
2. Conditional PutItem to mesh-event-dedup (TTL 24h)
3. If attribute_not_exists(event_id) succeeds -> process event
4. If conditional write fails -> duplicate, return early
5. For Step Functions triggered by events: event_id is used as execution name
   (Step Functions rejects duplicate execution names natively)
```

Table definition: `infra/modules/governance/dynamodb.tf:224` (`mesh-event-dedup`, PK=`event_id`, TTL on `expires_at`)

## MeshEventRole

Domain accounts use `MeshEventRole` to PutEvents to the central bus. This role is strictly scoped:

- **Trust**: `lambda.amazonaws.com` and `states.amazonaws.com` only
- **Allow**: `events:PutEvents` on `mesh-central-bus` ARN and `mesh-domain-bus`
- **Deny**: `events:PutEvents` with `events:source = datameshy.central` (reserved for central workflows)
- **Allow**: Glue job start/stop (for Step Functions pipeline), CloudWatch Logs, X-Ray tracing

Defined in: `infra/modules/domain-account/iam.tf:515-629`

## Related Files

| File | Purpose |
|---|---|
| `infra/modules/governance/eventbridge.tf` | Central bus, Schema Registry, rules, DLQs, alarms |
| `infra/modules/domain-account/iam.tf:515` | MeshEventRole definition |
| `infra/modules/governance/dynamodb.tf:224` | `mesh-event-dedup` table |
| `schemas/events/*.json` | 10 JSON Schema event definitions |
| `plan/ARCHITECTURE.md:443` | Full event architecture specification |
