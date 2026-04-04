# Guide: Add a New Domain

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03

## Navigation
<- [Docs home](../README.md)

---

## Goal

Add a new domain (e.g., `marketing`) to the data mesh by provisioning its AWS account infrastructure, registering it in the central governance catalog, and preparing it for data product creation.

---

## Prerequisites

| Requirement | Details |
|---|---|
| Central governance deployed | The `governance` Terraform module must be applied and its outputs available. |
| AWS account for the new domain | A separate AWS account (recommended) or a dedicated region/profile in an existing account. |
| AWS SSO access | A profile with `MeshPlatformAdmin` permission set (central account) and a profile for the new domain account. |
| Terraform >= 1.6.0 | Installed and in `$PATH`. |
| `datameshy` CLI installed | Version 0.1.0+. See `cli/pyproject.toml`. |
| Governance module outputs | `central_event_bus_arn`, `mesh_catalog_writer_role_arn`, `central_account_id`, `aws_org_id`. |

---

## Steps

### 1. Collect Governance Module Outputs

From the central account, gather the required ARNs and IDs:

```bash
cd infra/environments/central/
terraform output central_event_bus_arn
terraform output mesh_catalog_writer_role_arn
terraform output quality_alert_sns_topic_arn
terraform output central_kms_key_arn
```

Record these values. You will need them for the domain's `terraform.tfvars`.

### 2. Prepare the AWS Account

Ensure the new domain's AWS account has:

- **AWS Organization membership**: The account must be in the same AWS Organization. Note the Organization ID (`o-xxxxxxxxxx`).
- **SSO configuration**: IAM Identity Center permission sets (`DomainAdmin`, `DomainDataEngineer`, `DomainConsumer`) must be assigned to the appropriate users/groups for this account.
- **No conflicting resources**: The account should not have existing S3 buckets or Glue databases that match the naming conventions (`{domain}-raw-*`, `{domain}_raw`, etc.).

### 3. Run `datameshy domain onboard`

```bash
datameshy --profile new-domain-admin domain onboard \
  --name marketing \
  --account-id 987654321098 \
  --owner marketing-data-team@company.com \
  --event-bus-arn "arn:aws:events:us-east-1:CENTRAL_ACCOUNT_ID:event-bus/mesh-central-bus"
```

The CLI performs these actions automatically:

1. **Validates inputs** -- domain name must be alphanumeric + hyphens, max 32 characters. Account ID must be 12 digits. Owner must be a valid email.
2. **Scaffolds the Terraform environment** -- creates `infra/environments/domain-marketing/` with:
   - `main.tf` -- instantiates `domain-account`, `data-product` (placeholder), and `monitoring` modules
   - `terraform.tfvars` -- populated with domain name, account ID, and owner
   - `backend.tf` -- S3 backend template (commented out, requires manual setup)
3. **Runs terraform plan** -- shows the resources that will be created
4. **Prompts for confirmation** -- review the plan before applying
5. **Runs terraform apply** -- provisions all resources
6. **Emits `DomainOnboarded` event** -- registers the domain in the central `mesh-domains` DynamoDB table

Use `--dry-run` to scaffold without applying:

```bash
datameshy --profile new-domain-admin domain onboard \
  --name marketing \
  --account-id 987654321098 \
  --owner marketing-data-team@company.com \
  --dry-run
```

### 4. Configure Terraform Variables

Open `infra/environments/domain-marketing/terraform.tfvars` and update the cross-account references with actual values from the governance module:

```hcl
domain                        = "marketing"
environment                   = "dev"
aws_region                    = "us-east-1"

# Cross-account references (REPLACE with actual values)
aws_org_id                    = "o-xxxxxxxxxx"
central_account_id            = "000000000000"
central_event_bus_arn         = "arn:aws:events:us-east-1:000000000000:event-bus/mesh-central-bus"
mesh_catalog_writer_role_arn  = "arn:aws:iam::000000000000:role/MeshCatalogWriterRole"
quality_alert_sns_topic_arn   = "arn:aws:sns:us-east-1:000000000000:mesh-quality-alerts"
```

If `main.tf` was auto-generated, verify it correctly references the module sources:

```hcl
module "domain_account" {
  source = "../../modules/domain-account"

  domain                = var.domain
  environment           = var.environment
  aws_org_id            = var.aws_org_id
  central_account_id    = var.central_account_id
  central_event_bus_arn = var.central_event_bus_arn
  mesh_catalog_writer_role_arn = var.mesh_catalog_writer_role_arn

  tags = var.tags
}
```

### 5. Configure the Terraform Backend

Edit `infra/environments/domain-marketing/backend.tf` and uncomment the S3 backend block. Fill in the bucket name and DynamoDB table:

```hcl
terraform {
  backend "s3" {
    bucket         = "data-meshy-tfstate-domain-marketing-ACCOUNT_ID"
    key            = "domain-marketing/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    kms_key_id     = "alias/mesh-marketing"
    dynamodb_table = "data-meshy-tflock-domain-marketing"
  }
}
```

The S3 bucket and DynamoDB lock table must be provisioned before running `terraform init`. For initial setup:

```bash
terraform init -backend=false
```

### 6. Deploy the Domain Infrastructure

```bash
cd infra/environments/domain-marketing/

# Initialize (use -backend=false on first run if backend not yet provisioned)
terraform init

# Review the plan
terraform plan

# Apply
terraform apply
```

This provisions the following resources in the domain account:

| Resource | Name | Purpose |
|---|---|---|
| S3 buckets | `marketing-raw-*`, `marketing-silver-*`, `marketing-gold-*` | Medallion storage layers |
| KMS key | `alias/mesh-marketing` | Per-domain encryption key |
| Glue databases | `marketing_raw`, `marketing_silver`, `marketing_gold` | Data catalog databases |
| IAM roles | `DomainAdminRole`, `DomainDataEngineerRole`, etc. | Scoped access roles |
| EventBridge bus | `mesh-domain-bus` | Forwards to central bus |
| Lake Formation registration | S3 paths + LF-Tags | Governance-ready |

### 7. Verify the Domain

Check domain registration:

```bash
datameshy --profile central-admin domain list
```

Check domain details:

```bash
datameshy --profile central-admin domain status --name marketing
```

Verify infrastructure in the domain account:

```bash
# S3 buckets exist
aws s3 ls --profile marketing-admin | grep marketing

# Glue databases exist
aws glue get-databases --profile marketing-admin

# EventBridge bus forwards to central
aws events describe-event-bus --name mesh-domain-bus --profile marketing-admin
```

---

## Verify

| Check | Expected Result |
|---|---|
| `datameshy domain list` shows the domain | `marketing` listed with status `ACTIVE` |
| `datameshy domain status --name marketing` | Shows account ID, owner, 0 active products |
| 3 S3 buckets exist in domain account | `marketing-raw-*`, `marketing-silver-*`, `marketing-gold-*` |
| 3 Glue databases exist | `marketing_raw`, `marketing_silver`, `marketing_gold` |
| KMS key exists | `alias/mesh-marketing` with correct key policy |
| EventBridge forwarding works | Domain bus has rule forwarding `source: datameshy` to central bus |
| `DomainOnboarded` event received | Check central bus metrics or `mesh-audit-log` table |

---

## Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| `Domain name must be <= 32 characters` | Name too long | Shorten the domain name. Max 32 chars, alphanumeric + hyphens only. |
| `terraform init` fails with S3 backend error | State bucket not provisioned | Use `terraform init -backend=false` for initial setup, then provision the backend bucket and re-run `terraform init`. |
| `aws_org_id` validation error | Missing or invalid Organization ID | Run `aws organizations describe-organization` to get the correct ID. |
| Event forwarding not working | Domain account ID not in `domain_account_ids` | Add the new domain's account ID to the `governance` module's `domain_account_ids` variable and re-apply the central account. |
| KMS key policy access denied | Central account not in key policy | Verify `central_account_id` is correct in the domain's `terraform.tfvars`. |
| `DomainOnboarded` event not received | Event bus ARN incorrect or bus resource policy blocks the domain | Verify `central_event_bus_arn` in the CLI command. Check the central bus resource policy includes the domain account ID. |
| SSO permission set not available in new account | Permission sets not assigned | Work with the platform team to assign `DomainAdmin` and `DomainDataEngineer` permission sets to the appropriate SSO groups for this account. |

---

## See Also

- [Quick Start Guide](QUICK-START.md) -- end-to-end from scratch
- [Add a Product Guide](ADD-PRODUCT.md) -- next step after domain onboarding
- [Terraform Modules Reference](../reference/TERRAFORM-MODULES.md) -- module variables and outputs
- [Resource Naming Reference](../reference/RESOURCE-NAMING.md) -- naming conventions for domain resources
- [Architecture Document](../../plan/ARCHITECTURE.md) -- multi-account architecture and security model
