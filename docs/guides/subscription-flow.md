# Guide: Subscription Flow

> **Phase coverage**: Phase 5 | **Last updated**: 2026-05-15

## Navigation

<- [Docs home](../README.md) | [Add a Domain](ADD-DOMAIN.md) | [Add a Product](ADD-PRODUCT.md)

---

## Overview

A subscription gives a consumer domain read access to a producer domain's gold-layer data product. The platform provisions three things in the consumer account:

1. **Lake Formation cross-account grant** — column-level permissions on the Iceberg table (PII columns are automatically excluded)
2. **KMS key grant** — allows the consumer account to decrypt Iceberg data files encrypted with the producer domain's KMS key
3. **Glue resource link** — creates `{consumer_domain}_catalog.{producer_domain}_{product_name}` in the consumer Glue catalog so Athena can query it

After provisioning, consumer analysts query the product via Athena using the resource link as if it were a local table. They never access the producer account directly.

All subscription requests go through the `request-subscription.yml` workflow in the data-meshy platform repo and require explicit approval from a required reviewer before any access is provisioned.

---

## Prerequisites

| Requirement | How to verify |
|---|---|
| Producer domain is registered | `domains/{producer_domain}.yaml` exists in data-meshy main |
| Consumer domain is registered | `domains/{consumer_domain}.yaml` exists in data-meshy main |
| Product is ACTIVE in catalog | Platform engineers: `python tools/catalog.py describe {domain} {product}` shows `status: ACTIVE` |
| Product is not DEPRECATED | Same command — status must not be `DEPRECATED` or `RETIRED` |
| You have `workflow_dispatch` permission on data-meshy | You can trigger workflows in the data-meshy GitHub Actions UI |
| Justification is ready | A clear business justification for why your team needs this data |

If the consumer domain is not yet onboarded, contact the platform team (see [Add a Domain](ADD-DOMAIN.md)).

---

## Step 1: Trigger `request-subscription.yml`

Navigate to **Actions → request-subscription.yml → Run workflow** in the **data-meshy** platform repository (not your DP repo).

Fill in all five inputs:

### `producer_domain` — required

The domain name of the team that owns the product you want to subscribe to.

- Example: `sales`
- Must match a file in `domains/` — the workflow validates this

### `product_name` — required

The name of the specific product within the producer domain.

- Example: `revenue_daily`
- Must match an `ACTIVE` product in the central catalog

### `consumer_domain` — required

Your domain name — the domain that will receive access.

- Example: `marketing`
- Must match a file in `domains/`

### `consumer_repo` — required

The `org/repo` slug of your DP repo. This is where the platform posts tracking issues and notifications about the subscription.

- Example: `JawaharRamis/marketing-products`

### `justification` — required

Plain-English explanation of why your team needs access to this product. This is shown to the producer domain owner in their GitHub issue and is recorded in the subscription audit trail. Be specific.

- Example: `Marketing attribution model requires daily revenue by region to calculate ROAS per product line. No individual-level data needed — aggregate only.`

### Sample filled-in form

```
producer_domain:  sales
product_name:     revenue_daily
consumer_domain:  marketing
consumer_repo:    JawaharRamis/marketing-products
justification:    Marketing attribution model requires daily revenue by region
                  to calculate ROAS per product line. Aggregate data only —
                  no individual-level records are needed.
```

Click **Run workflow** (targeting `main`).

---

## Step 2: Validate and request jobs

The workflow immediately runs two jobs before any human review is needed.

### Job 1: `validate`

Duration: ~15 seconds.

Checks:
- `domains/{producer_domain}.yaml` exists in data-meshy main
- `domains/{consumer_domain}.yaml` exists in data-meshy main
- The product `{producer_domain}/{product_name}` exists in `mesh-products` DynamoDB with `status: ACTIVE`

If either domain is not registered, or the product does not exist or is not ACTIVE, the workflow fails here with a clear error message. No state is written. Fix the input and re-run.

### Job 2: `request`

Duration: ~20 seconds.

On validation success:

1. Writes a `PENDING` subscription record to the `mesh-subscriptions` DynamoDB table:

```json
{
  "pk": "sales/revenue_daily",
  "sk": "marketing",
  "status": "PENDING",
  "consumer_repo": "JawaharRamis/marketing-products",
  "justification": "...",
  "requested_at": "2026-05-15T...",
  "workflow_run_id": "..."
}
```

2. Opens a GitHub Issue in the producer DP repo (e.g., `JawaharRamis/sales-products`) with:
   - Consumer domain and repo
   - Product name
   - Justification text
   - Link to the Actions run where the approval gate lives

The producer domain team is automatically notified via GitHub issue assignee or label (depending on their repo notification settings).

---

## Step 3: Approval gate

After the `request` job, the workflow pauses at the **`provision`** job, which targets the `subscription-approval` GitHub environment. This environment has one or more required reviewers configured by the platform team.

**The workflow does not proceed until a required reviewer explicitly approves it in the GitHub Actions UI.**

### What the producer team needs to do

1. Review the GitHub issue opened in their DP repo. It contains the consumer's justification and a direct link to the waiting workflow run.
2. Navigate to the linked workflow run in **data-meshy → Actions**.
3. In the `provision` job, click **Review deployments**.
4. Select `subscription-approval` and choose either:
   - **Approve and deploy** — provisioning proceeds
   - **Reject** — provisioning is skipped; the subscription record is set to `REJECTED`

The reviewer must be a member of the `subscription-reviewers` GitHub team (configured by the platform team). Domain product owners should be added to this team during domain onboarding.

### What happens on rejection

The workflow logs the rejection, updates the DynamoDB record to `status: REJECTED`, and comments on both the producer issue and opens a notification issue in the consumer repo explaining the rejection. No AWS access is granted.

The consumer team may re-submit a new request by running `request-subscription.yml` again with an updated justification.

---

## Step 4: Provisioning on approval

When a required reviewer approves, the `provision` job runs:

### Sub-step 1: Lake Formation grant

A SigV4-signed POST to the governance API (`MESH_API_ENDPOINT/subscriptions`) triggers the subscription Lambda, which calls Lake Formation in the central governance account to grant column-level SELECT permissions on the producer's Iceberg table to the consumer account.

PII columns are automatically excluded — any column absent from the approved column list in the product's Lake Formation tag policy is not included in the grant. The consumer cannot request or receive PII column access through this workflow.

### Sub-step 2: KMS grant

The governance API adds a key grant on the producer domain's KMS key (`alias/mesh-{producer_domain}`) so that the consumer account can call `kms:Decrypt` and `kms:GenerateDataKey` when reading Iceberg data files.

### Sub-step 3: Glue resource link

The governance API creates a Glue resource link in the consumer account's Glue catalog:

- Database: `{consumer_domain}_catalog`
- Table: `{producer_domain}_{product_name}`
- Points to: `{producer_domain}_gold.{product_name}` in the central catalog

This makes the table discoverable and queryable in Athena from the consumer account without any direct cross-account S3 calls.

### Sub-step 4: Finalize

On success:
- DynamoDB record updated to `status: ACTIVE`
- Comment posted on the producer issue: "Subscription approved and provisioned for marketing."
- Tracking issue opened in the consumer repo (`JawaharRamis/marketing-products`) with connection details

LF grant propagation typically takes 30–90 seconds after the API call returns. If Athena queries fail immediately after provisioning, wait 90 seconds and retry.

---

## Step 5: Query via Athena

Once the subscription is `ACTIVE`, consumer analysts can query the product via Athena in the consumer account.

The resource link is in the `{consumer_domain}_catalog` database:

```sql
-- In Athena, select the marketing_catalog database
SELECT date, region, gross_revenue, net_revenue
FROM marketing_catalog.sales_revenue_daily
WHERE date >= DATE '2026-01-01'
ORDER BY date DESC
LIMIT 100;
```

The table behaves like any other Athena table. Query results are billed to the consumer account. Iceberg time-travel is supported:

```sql
-- Query the table as of a specific snapshot
SELECT * FROM marketing_catalog.sales_revenue_daily
FOR SYSTEM_TIME AS OF TIMESTAMP '2026-05-01 00:00:00'
LIMIT 10;
```

Set your Athena output location to a bucket in your consumer account before running queries. The consumer account's S3 result bucket must be configured in the Athena console or workgroup settings.

---

## Step 6: PII filtering

Lake Formation column-level security is applied automatically based on the producer's `product.yaml` schema. Any column not included in the LF grant is completely inaccessible — it does not appear in `DESCRIBE` results and querying it returns an `AccessDenied` error at the Lake Formation layer before Athena even reads any data.

As a consumer, you can discover which columns are available by running:

```sql
DESCRIBE marketing_catalog.sales_revenue_daily;
```

Only the columns included in the grant appear. You will never see PII columns in the output. This enforcement happens in the governance account's Lake Formation, not in Athena — it cannot be bypassed by direct S3 access because the KMS grant is scoped to the LF service principal.

If you believe a non-PII column is missing from your grant, contact the producer domain team. They can update the product's LF tag policy and re-provision the grant.

---

## Failure and retry

### If `validate` or `request` fails

No state has been written to DynamoDB. Fix the input and re-run `request-subscription.yml`.

### If provisioning fails after approval

The platform attempts a partial rollback:
- If LF grant succeeded but KMS grant failed: the LF grant is revoked
- If KMS grant succeeded but resource link creation failed: both LF and KMS grants are revoked
- DynamoDB record is set to `status: FAILED` with a `failure_reason` field
- Both the producer issue and a consumer tracking issue are commented with the error

**To retry after a FAILED subscription:**

1. In the governance account, open the `mesh-subscriptions` DynamoDB table
2. Find the record with `pk: {producer_domain}/{product_name}` and `sk: {consumer_domain}`
3. Delete the record
4. Re-run `request-subscription.yml` with the same inputs

The approval gate will trigger again — you will need a reviewer to re-approve.

**Common failure causes:**

| Error | Cause | Fix |
|---|---|---|
| `LF grant failed: AccessDenied` | `MeshOnboardingRole` does not have LF admin in the producer account | Platform team: verify LF data lake admin for the governance role |
| `KMS grant failed: LimitExceededException` | KMS key grant limit reached | Platform team: remove stale grants from the producer KMS key |
| `Resource link already exists` | A previous failed run left a partial resource link | Platform team: delete the orphaned resource link in the consumer Glue catalog and retry |
| `LakeFormation: TableNotFoundException` | Product Terraform was not fully applied | Producer team: re-push `product.yaml` to re-run provisioning, then retry subscription |

---

## Revoking a subscription

Subscriptions are revoked automatically when a product is deprecated or retired. The domain team triggers `deprecate.yml` in their DP repo, which calls `deprecate-product.yml` in data-meshy. This:

1. Marks the catalog entry `DEPRECATED` and sets a `sunset_date`
2. Creates an EventBridge Scheduler rule to fire the retirement Lambda at `sunset_date`
3. At sunset, the retirement Lambda revokes all active LF grants, KMS grants, and drops Glue resource links for all subscribers

Consumer teams receive a notification issue in their DP repo when a product they subscribe to is deprecated. Plan your migration before the `sunset_date`.

There is no self-service subscription cancellation workflow in Phase 5. If you need to cancel an active subscription before the product is deprecated, contact the platform team to delete the DynamoDB record and revoke the LF/KMS grants manually.

---

## See Also

- [Add a Domain Guide](ADD-DOMAIN.md) — consumer domain must be onboarded first
- [Add a Product Guide](ADD-PRODUCT.md) — producer side: publishing products to the mesh
- [Product Spec Reference](../reference/PRODUCT-SPEC.md) — understanding classification and schema fields
- [Architecture Document](../../plan/ARCHITECTURE.md) — cross-account access model and Lake Formation governance
