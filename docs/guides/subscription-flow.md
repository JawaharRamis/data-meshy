# Guide: Subscription Flow

> Extracted from `examples/marketing-domain/README.md` (Phase 2 subscriber example).
> This guide covers how a consumer domain subscribes to a data product and queries it via Athena.

---

## Overview

This walkthrough shows a consumer domain (e.g., marketing) consuming a producer domain's
data product (e.g., `sales/customer_orders`). It covers two personas and two flows:

| Persona | Tool | Flow |
|---------|------|------|
| Technical analyst | `datameshy` CLI + boto3 | CLI subscription + Athena query |
| Product owner / governance lead | AWS DataZone web UI | Portal-based subscription approval |

**What you will end up with**: a resource link `marketing_catalog.sales_customer_orders`
visible in the consumer account's Athena, containing only non-PII columns
(`order_id`, `order_date`, `order_total`). The PII column `customer_email` is
blocked by Lake Formation column-level filtering and cannot be queried.

---

## Prerequisites

- Phase 1 deployed (governance account + producer domain pipeline running).
- Phase 2 Stream 1 deployed (`subscription-provisioner` Step Function, DynamoDB table, API routes).
- Phase 2 Stream 2 deployed (subscription Lambda handlers).
- `datameshy` CLI installed: `pip install data-meshy`
- AWS credentials configured for both the **consumer account** and the **producer account**.

---

## Flow A — CLI (technical persona)

### Step 1: Request a subscription

From the **consumer account**:

```bash
datameshy subscribe request \
  --product sales/customer_orders \
  --columns order_id,order_date,order_total \
  --justification "Marketing attribution model requires order date and total; email excluded (PII)."
```

Expected output:

```json
{
  "subscription_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "PENDING",
  "product_id": "sales/customer_orders",
  "requested_columns": ["order_id", "order_date", "order_total"]
}
```

The `SubscriptionRequested` event is published to the central EventBridge bus.
The sales product owner is notified.

### Step 2: Approve the subscription (producer side)

The **producer account** approves the subscription. Either the governance team runs
this command from the producer account profile, or the product owner uses the
DataZone UI (see Flow B below):

```bash
datameshy subscribe approve --subscription-id a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

Behind the scenes the `subscription-provisioner` Step Function runs the saga:

1. Lake Formation cross-account grant (non-PII columns only).
2. KMS key grant so the consumer account can decrypt Iceberg data files.
3. Resource link creation in the consumer Glue catalog: `marketing_catalog.sales_customer_orders`.

On success a `SubscriptionApproved` event is published. On failure, the
compensator revokes any partial grants, sets `status = "FAILED"`, and publishes
a `SubscriptionRevoked` event with `revoked_by = "system"` and a
`compensation_reason` describing which step failed.

### Step 3: Verify the subscription is ACTIVE

```bash
datameshy subscribe list --product sales/customer_orders --status ACTIVE
```

Expected output:

```
subscription_id                        product_id              status  columns
a1b2c3d4-e5f6-7890-abcd-ef1234567890  sales/customer_orders   ACTIVE  order_id, order_date, order_total
```

### Step 4: Query via Athena

```bash
export AWS_PROFILE=<consumer-profile>
export ATHENA_OUTPUT_BUCKET=s3://<consumer-athena-results-bucket>/
```

Then run a query:

```sql
SELECT order_id, order_date, order_total
FROM marketing_catalog.sales_customer_orders
LIMIT 10;
```

### Column filtering — PII is not accessible

The `customer_email` column is marked `pii: true` in the product spec and is
excluded from the Lake Formation grant. Querying it returns an access denied
error at the Athena/LF layer:

```sql
-- This query will FAIL with Access Denied
SELECT order_id, customer_email
FROM marketing_catalog.sales_customer_orders
LIMIT 1;
```

---

## Flow B — DataZone web UI (product owner / governance lead persona)

This flow does not require CLI access. The product owner approves or rejects
subscription requests directly in the AWS DataZone portal.

### 1. Consumer: Browse the catalog and request access

1. Sign in to the AWS DataZone portal for the consumer domain project.
2. Navigate to **Catalog** > **Search** and search for `customer_orders`.
3. Select the `sales / customer_orders` asset.
4. Click **Request subscription** and fill in:
   - **Columns requested**: `order_id`, `order_date`, `order_total`
   - **Justification**: explain why your team needs access.
5. Submit the request.

DataZone publishes a `SubscriptionRequested` event to the central EventBridge
bus, which routes a notification to the producer domain team.

### 2. Producer: Approve the request in DataZone

1. The producer product owner receives an email notification (via SNS/SES) or sees
   the pending request in the DataZone portal under **My subscriptions to review**.
2. Select the pending request from the consumer.
3. Review the requested columns and justification.
4. Click **Approve**.

DataZone emits a `SubscriptionApproved` event. The `subscription-provisioner`
Step Function picks it up and runs the same saga as the CLI flow (LF grant →
KMS grant → resource link).

### 3. Consumer: Verify the subscription in DataZone

1. Return to the DataZone portal (consumer project).
2. Navigate to **My subscriptions**.
3. The status should change from **Pending** to **Active** within a few minutes
   (LF grant propagation typically takes 30–90 seconds).
4. The subscribed table appears under **My data** as `sales_customer_orders`.
5. Click **Query in Athena** to open the Athena console pre-loaded with the
   resource link database.

---

## What to expect when a subscription fails

If the saga fails mid-flight (e.g., the LF grant succeeds but the KMS grant
fails), the compensator rolls back:

1. The partial LF grant is revoked.
2. A `SubscriptionRevoked` event is published with `revoked_by = "system"` and
   a `compensation_reason` string such as `"KMS grant failed: AccessDenied"`.
3. The subscription record in DynamoDB is updated to `status = "FAILED"` with
   a `compensation_reason` field.

You can inspect the failed subscription via the CLI:

```bash
datameshy subscribe list --product sales/customer_orders --status FAILED
```

Or via the DynamoDB console in the governance account:

- Table: `mesh-subscriptions`
- Partition key: `sales/customer_orders`
- Sort key: `<consumer-account-id>`
- Inspect `status`, `compensation_reason`, and `provisioning_steps`.

To retry, revoke the failed subscription and request again:

```bash
datameshy subscribe revoke --subscription-id <id>
datameshy subscribe request \
  --product sales/customer_orders \
  --columns order_id,order_date,order_total \
  --justification "Retry after compensation."
```

---

## See Also

- [Add a Domain guide](ADD-DOMAIN.md) — onboarding a new consumer domain
- [Product Spec reference](../reference/PRODUCT-SPEC.md) — understanding PII flags and column classifications
- [Event Schemas reference](../reference/EVENT-SCHEMAS.md) — `SubscriptionRequested`, `SubscriptionApproved`, `SubscriptionRevoked`
- [Example domain repo](../../examples/example-domain-repo/) — reference implementation for a domain team's isolated repo
