# Marketing Domain — Subscribing to sales/customer_orders

This walkthrough shows the marketing domain consuming the `sales/customer_orders` data
product.  It covers two personas and two flows:

| Persona | Tool | Flow |
|---------|------|------|
| Marketing analyst (technical) | `datameshy` CLI + boto3 | CLI subscription + Athena query |
| Product owner / governance lead | AWS DataZone web UI | Portal-based subscription approval |

**What you will end up with**: a resource link `marketing_catalog.sales_customer_orders`
visible in the marketing account's Athena, containing only non-PII columns
(`order_id`, `order_date`, `order_total`).  The PII column `customer_email` is
blocked by Lake Formation column-level filtering and cannot be queried.

---

## Prerequisites

- Phase 1 deployed (governance account + sales domain pipeline running).
- Phase 2 Stream 1 deployed (`subscription-provisioner` Step Function, DynamoDB table, API routes).
- Phase 2 Stream 2 deployed (subscription Lambda handlers).
- `datameshy` CLI installed: `pip install data-meshy`
- AWS credentials configured for both the **marketing account** and the **sales/producer account**.

---

## Flow A — CLI (technical persona)

### Step 1: Request a subscription

From the **marketing account**:

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

The **sales account** approves the subscription.  Either the governance team runs
this command from the producer account profile, or the product owner uses the
DataZone UI (see Flow B below):

```bash
datameshy subscribe approve --subscription-id a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

Behind the scenes the `subscription-provisioner` Step Function runs the saga:

1. Lake Formation cross-account grant (non-PII columns only).
2. KMS key grant so the marketing account can decrypt Iceberg data files.
3. Resource link creation in the marketing Glue catalog: `marketing_catalog.sales_customer_orders`.

On success a `SubscriptionApproved` event is published.  On failure, the
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

Set required environment variables, then run the query script:

```bash
export AWS_PROFILE=marketing
export ATHENA_OUTPUT_BUCKET=s3://marketing-athena-results-123456789012/
python query_customer_orders.py
```

The script runs:

```sql
SELECT order_id, order_date, order_total
FROM marketing_catalog.sales_customer_orders
LIMIT 10
```

And prints a formatted table, for example:

```
+-------------------+------------+-------------+
| order_id          | order_date | order_total |
+-------------------+------------+-------------+
| ORD-42-000001     | 2026-01-15 | 142.50      |
| ORD-42-000002     | 2026-01-16 | 87.99       |
| ...               | ...        | ...         |
+-------------------+------------+-------------+
```

### Column filtering — PII is not accessible

The `customer_email` column is marked `pii: true` in the product spec and is
excluded from the Lake Formation grant.  Querying it returns an access denied
error at the Athena/LF layer:

```sql
-- This query will FAIL with Access Denied
SELECT order_id, customer_email
FROM marketing_catalog.sales_customer_orders
LIMIT 1
```

The commented-out `BLOCKED_PII_QUERY` block in `query_customer_orders.py`
demonstrates this behaviour.

### Automated script

`subscribe_to_customer_orders.sh` wraps all three CLI steps in a single script
parameterised via environment variables.  See the file header for usage.

---

## Flow B — DataZone web UI (product owner / governance lead persona)

This flow does not require CLI access.  The product owner approves or rejects
subscription requests directly in the AWS DataZone portal.

### 1. Consumer: Browse the catalog and request access

1. Sign in to the AWS DataZone portal for the marketing domain project.
2. Navigate to **Catalog** > **Search** and search for `customer_orders`.
3. Select the `sales / customer_orders` asset.
4. Click **Request subscription** and fill in:
   - **Columns requested**: `order_id`, `order_date`, `order_total`
   - **Justification**: explain why your team needs access.
5. Submit the request.

DataZone publishes a `SubscriptionRequested` event to the central EventBridge
bus, which routes a notification to the sales domain team.

### 2. Producer: Approve the request in DataZone

1. The sales product owner receives an email notification (via SNS/SES) or sees
   the pending request in the DataZone portal under **My subscriptions to review**.
2. Select the pending request from marketing.
3. Review the requested columns and justification.
4. Click **Approve**.

DataZone emits a `SubscriptionApproved` event.  The `subscription-provisioner`
Step Function picks it up and runs the same saga as the CLI flow (LF grant →
KMS grant → resource link).

### 3. Consumer: Verify the subscription in DataZone

1. Return to the DataZone portal (marketing project).
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
- Sort key: `<marketing-account-id>`
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

## File index

| File | Purpose |
|------|---------|
| `subscribe_to_customer_orders.sh` | CLI subscription flow (all three steps in one script) |
| `query_customer_orders.py` | Athena query via boto3, demonstrates PII column blocking |
| `README.md` | This walkthrough |
