# Guide: Deprecate a Data Product

> **Phase coverage**: Phase 5 | **Last updated**: 2026-05-15

## Navigation

[<- Docs home](../README.md) | [Product Spec Reference](../reference/PRODUCT-SPEC.md) | [Upgrade Platform](UPGRADE-PLATFORM.md)

---

## Overview

Deprecating a product marks it as DEPRECATED in the governance catalog and schedules its automatic retirement. On the `sunset_date`, a retirement Lambda automatically revokes all Lake Formation grants for the product, cutting off consumer access.

Deprecation is **not** the same as rolling back a bad data load. Deprecation is a lifecycle operation; rollback is a data recovery operation. See [Rolling back a bad data load](#rolling-back-a-bad-data-load) at the end of this guide.

**What deprecation does:**

1. Marks the catalog entry status as `DEPRECATED` in DynamoDB (visible immediately via catalog search and describe)
2. Sets `sunset_date` on the catalog entry
3. Creates an EventBridge Scheduler one-time rule that fires a `retirement` Lambda at `sunset_date`
4. The `retirement` Lambda revokes all active Lake Formation grants for the product at the scheduled time

**What deprecation does not do:**

- It does not delete data. The Iceberg table and S3 data remain until manually cleaned up.
- It does not stop the pipeline from running until `sunset_date`. Domain teams can choose to stop pipeline runs manually before then.
- It does not immediately cut off consumer access. Consumers retain read access until `sunset_date`.

---

## Prerequisites

| Requirement | Details |
|---|---|
| Product is ACTIVE | The product must currently have `ACTIVE` status in the catalog. You cannot deprecate a product that is already DEPRECATED or RETIRED. |
| DP repo access | You must have write access to the domain's DP repo (to trigger workflow_dispatch). |
| `deprecate.yml` stub exists | The DP repo must have a `deprecate.yml` workflow file (created at onboarding from the platform template). |
| Replacement product ready (if replacing) | If this deprecation is due to a breaking schema change, the new version product should be provisioned and ACTIVE before the old one is deprecated. |

---

## When to deprecate

| Scenario | Action |
|---|---|
| Breaking schema change (column removed, type changed, column renamed) | Create a new versioned product (e.g., `revenue_daily_v2`) and deprecate the old one. Give consumers a `sunset_date` at least 90 days out. |
| Product is being sunset (no replacement) | Deprecate with a `sunset_date` that gives consumers adequate notice. Notify stakeholders out-of-band. |
| Migration to a different system or domain | Create the replacement product in the new domain, deprecate the old product with `superseded_by` pointing to the replacement. |
| Bad data load or corruption | **Do not deprecate.** Use rollback instead. See [Rolling back a bad data load](#rolling-back-a-bad-data-load). |

---

## Step 1: Create the replacement product (if applicable)

If this deprecation is due to a breaking schema change, create and provision the new version product first. Deprecating the old product before the new one is ACTIVE leaves consumers with no working product to migrate to.

Example: if `revenue_daily` has a breaking schema change, create `revenue_daily_v2` in the same domain:

1. Create `products/revenue_daily_v2/product.yaml` and `products/revenue_daily_v2/infra.yaml` in the DP repo.
2. Push to main. `provision-product.yml` provisions the new product.
3. Verify `revenue_daily_v2` is ACTIVE in the catalog:

```bash
# Platform engineers only
python tools/catalog.py describe sales revenue_daily_v2
```

4. Proceed to Step 2.

---

## Step 2: Trigger `deprecate.yml` in the DP repo

Navigate to the domain's DP repo on GitHub. Go to **Actions** → **Deprecate Data Product** → **Run workflow**.

Provide the following inputs:

| Input | Type | Required | Description |
|---|---|---|---|
| `product_name` | string | Yes | Exact product name as it appears in `product.yaml` (snake_case). Example: `revenue_daily`. |
| `sunset_days` | integer | Yes | Number of days from today until the product is retired. Must be a positive integer. Minimum recommended: 90 days for products with active subscribers. |
| `reason` | string | No | Human-readable reason for deprecation. Included in subscriber notifications. |

Example: to deprecate `revenue_daily` with a 90-day sunset window:

- `product_name`: `revenue_daily`
- `sunset_days`: `90`
- `reason`: `Breaking schema change — replaced by revenue_daily_v2. Migrate queries before sunset_date.`

---

## Step 3: What the workflow does

`deprecate.yml` in the DP repo calls `deprecate-product.yml` in `JawaharRamis/data-meshy`. The platform workflow:

1. Assumes `DomainGitHubActionsRole` via OIDC.
2. POSTs a deprecation request to the governance API at `{MESH_API_ENDPOINT}/products/{domain}/{product_name}/deprecate` with `{"sunset_days": N, "reason": "..."}`.
3. The governance API (backed by the `product_deprecation` Lambda):
   - Validates the product is currently ACTIVE
   - Updates the DynamoDB `mesh-products` entry: sets `status=DEPRECATED`, computes and sets `sunset_date` as `today + sunset_days`
   - Creates an EventBridge Scheduler one-time rule named `retire-{domain}-{product_name}` that fires at `sunset_date` and invokes the `retirement` Lambda
4. The workflow logs the computed `sunset_date` to the Actions run output.

The catalog now returns `status: DEPRECATED` for this product immediately.

---

## Step 4: Notify subscribers

After the deprecation workflow completes, the governance API opens a GitHub issue in the producer (DP) repo listing all domains that currently have active subscriptions to the deprecated product. The issue body includes:

- The `sunset_date`
- The `superseded_by` product name (if provided)
- A list of subscriber domain names

**As the product owner, you must notify subscriber teams** so they can update their queries or subscribe to the replacement product before `sunset_date`. The GitHub issue is the minimum notification — out-of-band communication (email, Slack) is strongly recommended for products with more than one subscriber.

Subscribers can check the catalog for their subscriptions:

```bash
# Platform engineers only
python tools/catalog.py describe sales revenue_daily
# Returns: status, sunset_date, superseded_by, active subscriber list
```

---

## Step 5: Update product.yaml (optional but recommended)

After triggering deprecation, update `product.yaml` in the DP repo to document the lifecycle state. This keeps the YAML in sync with the catalog state and makes the deprecation visible in git history.

Add the lifecycle fields to `product.yaml`:

```yaml
name: revenue_daily
domain: sales
owner: jawahar@acme.com
description: Daily revenue aggregated by region.
version: "2"

# ... sla, schema, classification, tags, quality_rules unchanged ...

deprecated: true
sunset_date: "2026-09-01"      # the date computed by the deprecation workflow
superseded_by: revenue_daily_v2
```

Commit and push. The next `provision-product.yml` run will re-register the product with these fields present. These fields are stored in the catalog as documentation but the authoritative state is the `status=DEPRECATED` DynamoDB entry that was set in Step 3.

---

## Step 6: Automatic retirement at sunset_date

On `sunset_date` at midnight UTC, the EventBridge Scheduler rule fires the `retirement` Lambda. The Lambda:

1. Looks up all active Lake Formation grants for the product (tag-based: `domain={domain}`, `classification=*`, table `{domain_gold}.{product_name}`)
2. Revokes each grant
3. Updates the DynamoDB entry status to `RETIRED`
4. Emits a `ProductRetired` event to the central EventBridge bus

After retirement, consumers attempting to query the product via Athena will receive an access denied error. The Iceberg table and S3 data are not deleted.

No action is required from the domain team for retirement — it happens automatically.

---

## Rolling back a bad data load

If a pipeline run wrote incorrect data to the gold Iceberg table, **do not deprecate**. Use the Iceberg rollback workflow to restore the table to a previous snapshot.

### Find the target snapshot ID

Query the Iceberg snapshots metadata table via Athena:

```sql
SELECT snapshot_id, committed_at, operation, summary
FROM "{domain}_gold"."{product_name}$snapshots"
ORDER BY committed_at DESC
LIMIT 20;
```

Identify the `snapshot_id` of the last known-good state before the bad write. Copy the `snapshot_id` value (a 19-digit integer).

### Trigger the rollback

Navigate to the domain's DP repo on GitHub. Go to **Actions** → **Rollback Data Product** → **Run workflow**.

| Input | Type | Required | Description |
|---|---|---|---|
| `product_name` | string | Yes | Product name (snake_case). |
| `snapshot_id` | string | Yes | The Iceberg snapshot ID to restore (from the query above). |

`rollback.yml` in the DP repo calls `rollback-product.yml` in `JawaharRamis/data-meshy`. The platform workflow:

1. Assumes `DomainGitHubActionsRole` via OIDC.
2. POSTs to `{MESH_API_ENDPOINT}/products/{domain}/{product_name}/rollback` with `{"snapshot_id": "..."}`.
3. The governance API triggers the Iceberg rollback Glue job, which executes `CALL system.rollback_to_snapshot('{domain}_gold', '{product_name}', {snapshot_id})` via a Spark SQL step.
4. Returns the Glue job run ID so you can monitor progress in the Glue console.

Rollback affects only the gold table. It does not re-run the pipeline or affect the raw/silver layers.

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| `deprecate.yml` workflow not found | DP repo was onboarded before Phase 5 or template not applied | Platform team must re-apply the updated template to the DP repo. Contact platform team. |
| Governance API returns 404 on deprecation | Product name does not exist in the catalog | Verify the exact product name with `python tools/catalog.py browse --domain {domain}` (platform engineers only). |
| Governance API returns 409 on deprecation | Product is already DEPRECATED or RETIRED | No action needed — product is already in the desired state. |
| Governance API returns 400 on deprecation | `sunset_days` is missing or not a positive integer | Check the workflow_dispatch inputs. `sunset_days` must be a positive integer. |
| EventBridge Scheduler rule not created | Governance Lambda IAM permissions missing `scheduler:CreateSchedule` | Platform team must update `infra/modules/governance/iam.tf`. File a bug in `JawaharRamis/data-meshy`. |
| Retirement did not fire at sunset_date | EventBridge Scheduler rule misconfigured or Lambda throttled | Check EventBridge Scheduler console for rule status. Check `retirement` Lambda CloudWatch logs. Platform team to investigate. |
| Rollback: Glue job fails | Snapshot ID is invalid or older than the retention window | Verify the snapshot ID from the Athena query. Snapshots older than `retention_days` (set in `infra.yaml`) are expired and cannot be restored. |
| Rollback: `{product_name}$snapshots` table not found | Product table not registered in Glue or using wrong catalog DB | Ensure Athena is using the correct workgroup and the domain gold DB name matches `{domain}_gold`. |

---

## See Also

- [Product Spec Reference](../reference/PRODUCT-SPEC.md) — `deprecated`, `sunset_date`, `superseded_by` field definitions
- [Governance component](../components/GOVERNANCE.md) — `product_deprecation` and `retirement` Lambda details
- [Event Schemas Reference](../reference/EVENT-SCHEMAS.md) — `ProductDeprecated` and `ProductRetired` event formats
- `deprecate-product.yml` — platform reusable workflow implementation
- `rollback-product.yml` — platform reusable workflow implementation
