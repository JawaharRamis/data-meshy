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

Install the CLI:

```bash
cd cli/
pip install -e .
# Verify:
datameshy --version
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

Use the CLI to onboard the domain:

```bash
datameshy --profile sales-engineer domain onboard \
  --name sales \
  --account-id 123456789012 \
  --owner sales-data-team@company.com \
  --event-bus-arn "arn:aws:events:us-east-1:CENTRAL_ACCOUNT_ID:event-bus/mesh-central-bus"
```

The CLI will:
1. Validate inputs
2. Scaffold `infra/environments/domain-sales/` with `terraform.tfvars` and `main.tf`
3. Run `terraform plan`
4. Prompt for confirmation
5. Run `terraform apply`
6. Emit a `DomainOnboarded` event to the central bus

Alternatively, configure Terraform manually by updating `infra/environments/domain-sales/terraform.tfvars` with the actual values from the governance module outputs:

```hcl
domain                = "sales"
environment           = "dev"
aws_region            = "us-east-1"
aws_org_id            = "o-xxxxxxxxxx"           # Replace
central_account_id    = "000000000000"            # Replace
central_event_bus_arn = "arn:aws:events:..."      # Replace
mesh_catalog_writer_role_arn = "arn:aws:iam::..." # Replace
quality_alert_sns_topic_arn  = "arn:aws:sns:..."  # Replace
```

Then apply:

```bash
cd infra/environments/domain-sales/
terraform init
terraform plan
terraform apply
```

### 5. Create the customer_orders Data Product

The `product.yaml` is already provided at `examples/sales-domain/products/customer_orders/product.yaml`. You can use it directly or copy it as a starting point.

Create the product using the CLI:

```bash
datameshy --profile sales-engineer product create \
  --spec examples/sales-domain/products/customer_orders/product.yaml \
  --event-bus-arn "arn:aws:events:us-east-1:CENTRAL_ACCOUNT_ID:event-bus/mesh-central-bus"
```

The CLI will:
1. Validate `product.yaml` against `schemas/product_spec.json`
2. Check the product does not already exist in `mesh-products`
3. Upload Glue job templates to the raw S3 bucket
4. Run `terraform plan` for the `data-product` module
5. Prompt for confirmation, then apply
6. Emit a `ProductCreated` event

This provisions: Iceberg table, Glue DQ ruleset (`sales_customer_orders_dq`), Step Functions state machine (`sales-customer_orders-pipeline`), and a catalog entry in DynamoDB.

### 6. Run a Pipeline Refresh

Trigger the medallion pipeline (raw -> silver -> gold):

```bash
datameshy --profile sales-engineer product refresh \
  --domain sales \
  --name customer_orders
```

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

Check the product status:

```bash
datameshy --profile sales-engineer product status \
  --domain sales \
  --name customer_orders
```

This displays: product ID, owner, status, schema version, last refresh time, quality score, rows written, and subscriber count.

Query the data in Athena (from the sales account AWS console):

```sql
SELECT * FROM sales_gold.customer_orders LIMIT 10;
```

Verify the domain is registered:

```bash
datameshy --profile central-admin domain list
```

---

## Verify

| Check | Expected Result |
|---|---|
| `datameshy domain list` | Shows `sales` domain with status `ACTIVE` |
| `datameshy product status --domain sales --name customer_orders` | Shows status `ACTIVE`, quality score >= 95 |
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
| `Product already exists` | Product was previously created | Use `datameshy product refresh` instead, or delete the existing product first. |
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
