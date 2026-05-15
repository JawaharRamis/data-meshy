# Guide: Quick Start (15 Minutes)

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03

## Navigation
<- [Docs home](../README.md)

---

## Goal

Get a working data mesh with one domain and one data product running end-to-end: from raw ingestion through silver transformation to gold aggregation, with quality checks and catalog registration. After completing this guide you will have data flowing through all three medallion layers and visible in the mesh catalog.

---

## Prerequisites

| Requirement | Details |
|---|---|
| AWS Account | Three AWS accounts recommended (central governance + 2 domains). Single-account works for evaluation. |
| AWS SSO | IAM Identity Center configured with permission sets. See [Architecture -- Authentication Model](../../plan/ARCHITECTURE.md). |
| Terraform | Version >= 1.6.0 installed and in `$PATH`. |
| Python | Version >= 3.12. |
| AWS CLI | Version 2.x with SSO support. |
| Git | For cloning the repository. |

Domain teams interact with the mesh via `product.yaml` / `infra.yaml` files and GitHub Actions — no pip install required.

Platform engineers who need to query the catalog directly can use:

```bash
python tools/catalog.py --help
```

---

## Steps

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/data-meshy.git
cd data-meshy
```

### 2. Authenticate with AWS SSO

Log in using the profile for the central governance account:

```bash
aws sso login --profile central-admin
```

Verify authentication:

```bash
aws sts get-caller-identity --profile central-admin
```

### 3. Deploy Central Governance Infrastructure

```bash
cd infra/environments/central/

# Initialize Terraform (first time only, backend may need manual provisioning)
terraform init

# Review the plan
terraform plan -var="environment=dev"

# Apply
terraform apply -var="environment=dev"
```

Note the outputs -- you will need them for domain configuration:

```bash
terraform output central_event_bus_arn
terraform output mesh_catalog_writer_role_arn
terraform output quality_alert_sns_topic_arn
```

### 4. Onboard the Sales Domain

Switch to the sales account SSO profile:

```bash
aws sso login --profile sales-engineer
```

Trigger the `onboard-domain.yml` workflow in the platform repo, or configure Terraform manually using the example domain repo as a reference.

Copy `examples/example-domain-repo/infra/terraform.tfvars` and fill in the actual values
from the governance module outputs:

```hcl
domain                = "sales"
account_id            = "123456789012"            # Replace
owner                 = "sales-team@company.com"  # Replace
aws_region            = "us-east-1"
central_event_bus_arn = "arn:aws:events:..."      # Replace
```

Then apply from your domain repo's infra directory:

```bash
cd infra/
terraform init
terraform plan
terraform apply
```

### 5. Create the customer_orders Data Product

The `product.yaml` is already provided at `examples/example-domain-repo/products/customer_orders/product.yaml`. You can use it directly or copy it as a starting point.

Push `product.yaml` to your domain repo; the `provision-product.yml` reusable workflow triggers automatically and:
1. Validates `product.yaml` against `schemas/product_spec.json`
2. Runs Terraform to provision the data-product module
3. Emits a `ProductCreated` event

This provisions: Iceberg table, Glue DQ ruleset (`sales_customer_orders_dq`), Step Functions state machine (`sales-customer_orders-pipeline`), and a catalog entry in DynamoDB.

### 6. Run a Pipeline Refresh

Trigger the medallion pipeline (raw -> silver -> gold) via the GitHub Actions workflow in your domain repo, or directly via the AWS Step Functions console.

The pipeline will:
1. Acquire a lock in `mesh-pipeline-locks` (prevents concurrent runs)
2. Run raw ingestion (reads source data into raw S3)
3. Run silver transform (validate, dedup, enforce schema into Iceberg)
4. Run gold aggregate (business logic, enrichment into Iceberg)
5. Validate schema against `product.yaml`
6. Evaluate quality rules
7. On pass: publish to catalog, emit `ProductRefreshed`, release lock
8. Run Iceberg maintenance (OPTIMIZE + VACUUM)

### 7. Verify Results

Check the product in the DynamoDB catalog table (`mesh-products`) via the AWS Console or:

```bash
# Platform engineers only:
python tools/catalog.py --profile central-admin describe sales customer_orders
```

Query the data in Athena (from the sales account AWS console):

```sql
SELECT * FROM sales_gold.customer_orders LIMIT 10;
```

Verify the domain is registered via the DynamoDB `mesh-domains` table, or:

```bash
# Platform engineers only:
python tools/catalog.py --profile central-admin browse --domain sales
```

---

## Verify

| Check | Expected Result |
|---|---|
| `mesh-domains` DynamoDB table | Shows `sales` domain with status `ACTIVE` |
| `mesh-products` DynamoDB table | Shows `sales/customer_orders` with status `ACTIVE`, quality score >= 95 |
| Athena query on `sales_gold.customer_orders` | Returns rows |
| `ProductRefreshed` event in EventBridge | Visible in central bus metrics |
| Quality score in `mesh-quality-scores` | Record exists with timestamp |
| No messages in DLQs | `mesh-catalog-dlq`, `mesh-audit-dlq` are empty |

---

## Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| `terraform plan` fails with backend error | S3 state bucket not provisioned | Run `terraform init -backend=false` for initial setup, or provision the backend bucket manually. |
| `Spec validation failed` | `product.yaml` does not match JSON Schema | Check required fields (`schema_version`, `product`, `sla`, `schema`, `quality`, `classification`). Validate locally: `python -c "import jsonschema; jsonschema.validate(...)"`. |
| `Product already exists` | Product was previously created | Delete the item from `mesh-products` DynamoDB table and re-run the workflow, or update the existing product. |
| `Pipeline is already running` | Concurrent run lock exists | Wait for the current execution to complete, or check `mesh-pipeline-locks` for stale locks (TTL 3h). |
| `Quality check failed` | DQDL rules did not pass | Review the failed rules in the quality alert. Adjust data or rules in `product.yaml`. |
| `aws sso login` fails | SSO not configured | Work with your AWS admin to set up IAM Identity Center permission sets. |
| `Module source not found` | Running terraform from wrong directory | Always `cd` into the environment directory before running terraform commands. |
| Glue job OOM | Dataset too large for 2 DPU | Increase DPU in the terraform configuration (SCP allows up to 4 DPU). |

---

## See Also

- [Add a Domain Guide](ADD-DOMAIN.md) -- detailed domain onboarding
- [Add a Product Guide](ADD-PRODUCT.md) -- detailed product creation
- [Customize Pipeline Guide](CUSTOMIZE-PIPELINE.md) -- customizing Glue job transforms
- [Resource Naming Reference](../reference/RESOURCE-NAMING.md) -- naming conventions
- [Product Spec Reference](../reference/PRODUCT-SPEC.md) -- full product.yaml field documentation
- [Architecture Document](../../plan/ARCHITECTURE.md) -- full architecture and design decisions
