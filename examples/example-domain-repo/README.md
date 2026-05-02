# Example Domain Repo

This directory is a reference implementation of what `datameshy domain init` produces.
It represents how a domain team's isolated repo (e.g., `data-meshy-sales`) should look.

Use this as a starting point for a new domain repo or to understand the expected structure.

---

## Structure

```
data-meshy-{domain}/
├── .github/workflows/        # CI/CD — calls reusable workflows from the platform repo
│   ├── infra-plan.yml        # Runs terraform plan on PRs
│   ├── infra-apply.yml       # Runs terraform apply on merge to main
│   └── product-validate.yml  # Validates product.yaml specs on every push
├── infra/
│   ├── backend.tf            # S3 remote state (fill in bucket/table names)
│   ├── main.tf               # Module wiring — uses git:: sources pinned to v1.0.0
│   ├── variables.tf          # Input variable declarations
│   ├── outputs.tf            # S3 bucket names, state machine ARN, KMS key ARN
│   └── terraform.tfvars      # Your domain-specific values (edit before applying)
├── products/
│   └── customer_orders/
│       ├── product.yaml      # Full working product spec for the customer_orders product
│       └── glue_jobs/        # Optional Glue job overrides (empty by default)
├── .datameshy.toml           # Pins platform_version = 1.0.0
└── README.md                 # This file
```

---

## Quick Start

### 1. Clone (or scaffold with the CLI)

Clone this example and rename it, or use the CLI to scaffold a fresh repo:

```bash
datameshy domain init \
  --name <your-domain> \
  --account-id <your-aws-account-id> \
  --owner <your-email>
```

The CLI generates the same structure as this example.

### 2. Edit `infra/terraform.tfvars`

Replace all `REPLACE_ME` placeholders:

```hcl
domain                = "sales"
account_id            = "123456789012"
owner                 = "sales-team@company.com"
aws_region            = "us-east-1"
central_event_bus_arn = "arn:aws:events:us-east-1:GOVERNANCE_ACCOUNT_ID:event-bus/mesh-central-bus"
```

Get the `central_event_bus_arn` from the governance account:

```bash
cd /path/to/data-meshy-platform/infra/environments/central
terraform output central_event_bus_arn
```

### 3. Edit `infra/backend.tf`

Fill in your state bucket and DynamoDB lock table names:

```hcl
backend "s3" {
  bucket         = "your-org-terraform-state"
  key            = "sales/terraform.tfstate"
  region         = "us-east-1"
  dynamodb_table = "your-org-terraform-locks"
  encrypt        = true
}
```

### 4. Update `.datameshy.toml`

Replace the placeholder values with your actual domain name, account ID, and owner email.

### 5. Initialize and apply Terraform

```bash
cd infra/
terraform init
terraform plan
terraform apply
```

### 6. Validate your product spec

```bash
datameshy product validate --spec products/customer_orders/product.yaml
```

### 7. Create the data product

```bash
datameshy product create \
  --spec products/customer_orders/product.yaml \
  --event-bus-arn "arn:aws:events:us-east-1:GOVERNANCE_ACCOUNT_ID:event-bus/mesh-central-bus"
```

---

## Module Sources

All Terraform modules are sourced from the platform repo at a pinned version:

```hcl
source = "git::https://github.com/JawaharRamis/data-meshy-platform.git//infra/modules/domain-account?ref=v1.0.0"
```

To upgrade to a new platform version, update the `ref=` tag in `infra/main.tf` and the
`version` field in `.datameshy.toml`, then run `terraform init -upgrade`.

---

## Adding More Data Products

To add a new product, create a new directory under `products/`:

```bash
mkdir -p products/my_new_product/glue_jobs
cp products/customer_orders/product.yaml products/my_new_product/product.yaml
# Edit product.yaml with your product details
datameshy product validate --spec products/my_new_product/product.yaml
```

Then add a new `module "data_product"` block in `infra/main.tf` for the new product.

---

## See Also

- [Platform repo](https://github.com/JawaharRamis/data-meshy-platform) — shared modules and reusable workflows
- [Add a Domain guide](https://github.com/JawaharRamis/data-meshy-platform/blob/main/docs/guides/ADD-DOMAIN.md)
- [Product Spec reference](https://github.com/JawaharRamis/data-meshy-platform/blob/main/docs/reference/PRODUCT-SPEC.md)
- [Subscription flow guide](https://github.com/JawaharRamis/data-meshy-platform/blob/main/docs/guides/subscription-flow.md)
