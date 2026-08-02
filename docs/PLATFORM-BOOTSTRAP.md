# Platform Bootstrap Guide

> This guide is for platform engineers adopting data-meshy from scratch. It takes you from a freshly cloned repository and empty AWS accounts to a fully operational data mesh with one domain and one data product running end-to-end.

---

## Table of Contents

1. [Overview and prerequisites](#1-overview-and-prerequisites)
2. [One-time AWS bootstrap (before Terraform)](#2-one-time-aws-bootstrap-before-terraform)
3. [Terraform init and first apply](#3-terraform-init-and-first-apply)
4. [GitHub repository secrets and variables](#4-github-repository-secrets-and-variables)
5. [GitHub repository configuration](#5-github-repository-configuration)
6. [Verify the deployment](#6-verify-the-deployment)
7. [Onboard your first domain](#7-onboard-your-first-domain)
8. [Publish your first data product](#8-publish-your-first-data-product)
9. [Request and approve a subscription](#9-request-and-approve-a-subscription)
10. [Day-2 operations reference](#10-day-2-operations-reference)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Overview and prerequisites

### What is data-meshy

Data-meshy is an AWS platform for running a self-serve data mesh. A central governance account (owned by the platform team) maintains the catalog, enforces Lake Formation permissions, routes events, and orchestrates subscription workflows. Domain teams get their own AWS accounts with pre-wired IAM roles, and they publish data products by editing two YAML files and pushing to `main` — no Terraform, no CLI, no AWS console access required for day-to-day work.

The platform is opinionated: Apache Iceberg on S3 for storage, Glue for ETL, Step Functions for orchestration, Lake Formation cross-account grants for sharing, EventBridge for mesh events, and DynamoDB for the governance catalog. Only gold-layer tables are ever shared with consumers.

### Account model

```
AWS Organization (Management Account)
├── Platform OU
│   └── Central Governance Account   ← you are bootstrapping this
└── Domain OU
    ├── Domain Account 1 (e.g., Sales)
    ├── Domain Account 2 (e.g., Marketing)
    └── ...
```

You need at minimum one central governance account to run this bootstrap. Domain accounts can be added later — you can start with `domain_account_ids = []`.

### Prerequisites checklist

| Tool | Version | Why |
|---|---|---|
| AWS CLI v2 | Any recent v2 | Manual bootstrap steps and output capture |
| Terraform | >= 1.7.0 | Required by `infra/environments/central/main.tf` |
| GitHub CLI (`gh`) | >= 2.40 | Setting secrets, triggering workflows, opening PRs |
| Python | 3.12 | Lambda tests (`cd lambdas && pytest tests/`) |
| Git | Any | Branch management |

**AWS permissions required for the bootstrap operator** (the person running steps 2–3):

The bootstrap steps call the following AWS APIs directly against the central account. The simplest approach is to use a principal with `AdministratorAccess` for the one-time bootstrap:

- `kms:CreateKey`, `kms:CreateAlias`
- `s3:CreateBucket`, `s3:PutBucketVersioning`, `s3:PutBucketEncryption`, `s3:PutPublicAccessBlock`
- `dynamodb:CreateTable`
- `sts:GetCallerIdentity`
- `terraform apply` then needs broad rights to create ~150 AWS resources (IAM roles, Lambda, API GW, EventBridge, DynamoDB tables, Step Functions, KMS policies, SNS, SQS, CloudWatch, CloudTrail, DataZone)

> [!IMPORTANT]
> After the one-time bootstrap apply, all subsequent applies run via GitHub Actions using the `TerraformApplyRole` OIDC role that Terraform creates. You do not need `AdministratorAccess` again after the first apply.

---

## 2. One-time AWS bootstrap (before Terraform)

> [!WARNING]
> Run these steps exactly once, in order, before `terraform init`. Terraform's S3 backend needs the S3 bucket, DynamoDB table, and KMS key to exist before it can initialise. You cannot use Terraform to create its own state store — this is the bootstrap chicken-and-egg.

### Step 1: Confirm your central account identity

```bash
export CENTRAL_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Central account: $CENTRAL_ACCOUNT_ID"
```

Verify this prints a 12-digit account ID and that it is the governance account — not a domain account or management account.

### Step 2: Create the KMS key and alias

Terraform state is encrypted with a customer-managed KMS key. This key must exist before the S3 bucket can be configured with SSE-KMS.

```bash
KEY_ID=$(aws kms create-key \
  --description "Data Meshy central Terraform state encryption key" \
  --key-usage ENCRYPT_DECRYPT \
  --query KeyMetadata.KeyId \
  --output text)

aws kms create-alias \
  --alias-name alias/mesh-central \
  --target-key-id "$KEY_ID"

echo "KMS key: $KEY_ID   alias: alias/mesh-central"
```

> [!NOTE]
> Record `$KEY_ID`. If you need to re-run this step (e.g., the alias already exists), find the existing key ID with `aws kms describe-key --key-id alias/mesh-central --query KeyMetadata.KeyId --output text`.

### Step 3: Create the S3 state bucket

```bash
aws s3api create-bucket \
  --bucket "data-meshy-tfstate-central-${CENTRAL_ACCOUNT_ID}" \
  --region us-east-1

# Enable versioning — allows Terraform state recovery
aws s3api put-bucket-versioning \
  --bucket "data-meshy-tfstate-central-${CENTRAL_ACCOUNT_ID}" \
  --versioning-configuration Status=Enabled

# Enable SSE-KMS using the key created in step 2
aws s3api put-bucket-encryption \
  --bucket "data-meshy-tfstate-central-${CENTRAL_ACCOUNT_ID}" \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "aws:kms",
        "KMSMasterKeyID": "alias/mesh-central"
      },
      "BucketKeyEnabled": true
    }]
  }'
```

### Step 4: Block all public access on the state bucket

State files contain ARNs and account IDs. This must never be public.

```bash
aws s3api put-public-access-block \
  --bucket "data-meshy-tfstate-central-${CENTRAL_ACCOUNT_ID}" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

### Step 5: Create the DynamoDB state lock table

Prevents concurrent `terraform apply` runs from corrupting state.

```bash
aws dynamodb create-table \
  --table-name data-meshy-tflock-central \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

### Step 6: Create `backend.tfbackend`

`backend.tf` deliberately omits the bucket name because it contains your account ID and must not be committed to a public repository. You provide the bucket name via a gitignored partial backend config file.

```bash
cat > infra/environments/central/backend.tfbackend <<EOF
bucket = "data-meshy-tfstate-central-${CENTRAL_ACCOUNT_ID}"
EOF
```

> [!NOTE]
> `backend.tfbackend` is listed in `.gitignore`. It will never be committed. Every developer who clones the repo must create their own copy of this file before running `terraform init`. The expected format is shown in `infra/environments/central/backend.tfbackend.example`.

### Step 7: Create `terraform.tfvars`

```bash
cp infra/environments/central/terraform.tfvars.example \
   infra/environments/central/terraform.tfvars
```

Now edit `infra/environments/central/terraform.tfvars`. Every variable is explained below:

| Variable | Type | Required | Explanation |
|---|---|---|---|
| `aws_region` | string | Yes | AWS region for all central resources. Default: `us-east-1`. |
| `environment` | string | Yes | Label applied as a default tag. Use `portfolio` for dev/personal, `prod` for production. |
| `organizations_enabled` | bool | No | Set `false` until AWS Organizations and IAM Identity Center are fully configured. Gates all SCP and Identity Center resources. Flip to `true` only when ready — see the note below. |
| `org_id` | string | Conditional | Your AWS Organization ID (`o-xxxxxxxxxx`). Required only when `organizations_enabled = true`. Leave as `""` otherwise. |
| `domain_account_ids` | list(string) | No | 12-digit account IDs of domain accounts allowed to publish events to the central EventBridge bus. Can be `[]` initially. Add IDs as you onboard domains. |
| `github_org` | string | Yes | Your GitHub organisation or username (the one that hosts this repo). Example: `JawaharRamis`. |
| `github_repo` | string | Yes | Repository name. Default: `data-meshy`. Change if you forked under a different name. |
| `alert_email` | string | No | Email for SNS alert subscriptions (quality alerts, pipeline failures). Leave `""` to skip. |
| `domain_repo_paths` | list(string) | No | OIDC subject conditions for domain GitHub repos. Add one entry per domain as you onboard. Example: `["repo:JawaharRamis/sales-products:*"]`. |
| `datazone_domain_name` | string | No | Name of the AWS DataZone domain. Default: `data-meshy`. |
| `datazone_sso_type` | string | No | `DISABLED` for portfolio/dev. Set `IAM_IDC` to integrate with IAM Identity Center for web-UI subscription approval. |
| `subscription_provisioner_lambda_arn` | string | No | Leave `""`. Kept for backward compatibility with old tfvars files — Phase 6 creates Lambdas directly inside the governance module. |
| `subscription_compensator_lambda_arn` | string | No | Leave `""`. Same reason as above. |
| `subscription_approver_lambda_arn` | string | No | Leave `""`. Same reason as above. |
| `subscription_lister_lambda_arn` | string | No | Leave `""`. Same reason as above. |

> [!WARNING]
> Keep `organizations_enabled = false` for your first apply. Organizations-guarded resources (SCPs, Identity Center permission sets) will fail if AWS Organizations is not yet active in the account. You can flip this to `true` and re-apply later once Organizations is set up.

> [!NOTE]
> `terraform.tfvars` is listed in `.gitignore`. It will never be committed. It contains your GitHub org name which, if leaked, reveals your OIDC trust scope.

---

## 3. Terraform init and first apply

> [!IMPORTANT]
> This first `terraform apply` must be run manually from your local machine. The GitHub Actions OIDC provider (`aws_iam_openid_connect_provider.github_actions`) and `TerraformApplyRole` are created **by** this first apply — they do not exist yet, so GitHub Actions cannot use them to run the apply. After this single bootstrap apply, all subsequent infrastructure changes go through the `infra-apply.yml` workflow.

### Initialise the backend

```bash
cd infra/environments/central
terraform init -backend-config=backend.tfbackend
```

You should see: `Terraform has been successfully initialized!`

### Review the plan

```bash
terraform plan
```

Expect approximately 150 AWS resources to be created, including:
- 7 DynamoDB tables (products, domains, subscriptions, quality scores, audit log, event dedup, pipeline locks)
- 14 Lambda functions (all with `mesh-` prefix)
- 1 HTTP API Gateway (`mesh-governance-api`)
- 1 EventBridge custom bus (`mesh-central-bus`)
- 1 Step Functions state machine (`subscription-provisioner`)
- KMS key and alias (`alias/mesh-central`)
- Multiple IAM roles (see section 6 for the full list)
- SNS topics, SQS DLQs, CloudWatch log groups, CloudTrail log group
- 1 DataZone domain
- GitHub OIDC provider

Review the plan output carefully. Check that no existing resources are being destroyed.

### Apply

```bash
terraform apply
```

This takes approximately 5 minutes. Type `yes` when prompted to confirm.

### Capture the outputs

After apply completes, capture these outputs — you need them as GitHub secrets in the next step:

```bash
terraform output api_endpoint_url
terraform output terraform_apply_role_arn
terraform output terraform_plan_role_arn
terraform output central_event_bus_arn
terraform output subscription_sfn_arn
terraform output datazone_portal_url
```

Full list of all outputs:

| Output name | Used for |
|---|---|
| `api_endpoint_url` | `MESH_API_ENDPOINT` GitHub secret and variable |
| `terraform_apply_role_arn` | Set in `reusable-infra-apply.yml` via `AWS_ACCOUNT_ID` secret |
| `terraform_plan_role_arn` | Set in `reusable-infra-plan.yml` via `AWS_ACCOUNT_ID` secret |
| `central_event_bus_arn` | Reference for domain EventBridge rules |
| `subscription_sfn_arn` | Reference when monitoring subscriptions |
| `datazone_portal_url` | DataZone web UI for subscription approval |
| `mesh_products_table_name` | Verification: should be `mesh-products` |
| `mesh_domains_table_name` | Verification: should be `mesh-domains` |
| `mesh_subscriptions_table_name` | Verification: should be `mesh-subscriptions` |
| `mesh_quality_scores_table_name` | Verification: should be `mesh-quality-scores` |
| `mesh_audit_log_table_name` | Verification: should be `mesh-audit-log` |
| `mesh_event_dedup_table_name` | Verification: should be `mesh-event-dedup` |
| `mesh_pipeline_locks_table_name` | Verification: should be `mesh-pipeline-locks` |
| `mesh_lf_grantor_role_arn` | Reference for Lake Formation grant operations |
| `mesh_catalog_writer_role_arn` | Reference for catalog write operations |
| `mesh_audit_writer_role_arn` | Reference for audit log write operations |
| `central_kms_key_arn` | Reference for domain-level KMS configuration |
| `quality_alert_sns_topic_arn` | Monitoring: quality alert subscriptions |
| `pipeline_failure_sns_topic_arn` | Monitoring: pipeline failure subscriptions |
| `catalog_dlq_arn` | Monitoring: failed catalog events |
| `mesh_kms_grantor_role_arn` | Reference for subscription KMS grant operations |
| `datazone_domain_id` | DataZone integration |
| `datazone_domain_arn` | DataZone integration |

---

## 4. GitHub repository secrets and variables

This is the most error-prone configuration step. Every secret and variable the workflows need must be set before any workflow can succeed.

> [!WARNING]
> Missing `AWS_ACCOUNT_ID` is the single most common failure. The `reusable-infra-plan.yml` and `reusable-infra-apply.yml` workflows construct the OIDC role ARN as `arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/TerraformPlanRole` and `TerraformApplyRole`. If this secret is missing, OIDC role assumption fails silently with a confusing `Could not assume role` error.

> [!WARNING]
> Missing `TF_STATE_BUCKET` fails `terraform init` in CI with `Error: Missing Required Value` on `backend.tf`'s `bucket` attribute. `backend.tf` deliberately omits the bucket name (partial backend config, so the central account ID never enters git) — locally you supply it via the gitignored `backend.tfbackend` file, but CI has no such file, so `reusable-infra-plan.yml`/`reusable-infra-apply.yml` write one from this secret before running `terraform init -backend-config=backend.tfbackend`.

### Repository secrets

Set these at **Settings → Secrets and variables → Actions → Secrets**:

| Secret name | Where value comes from | How to set it |
|---|---|---|
| `AWS_ACCOUNT_ID` | Your central account ID — `echo $CENTRAL_ACCOUNT_ID` from step 2 | `gh secret set AWS_ACCOUNT_ID --repo JawaharRamis/data-meshy` |
| `TF_STATE_BUCKET` | The tfstate bucket name from `backend.tfbackend` (`data-meshy-tfstate-central-<region>-<CENTRAL_ACCOUNT_ID>`) | `gh secret set TF_STATE_BUCKET --repo JawaharRamis/data-meshy --body "data-meshy-tfstate-central-eu1-$CENTRAL_ACCOUNT_ID"` |
| `ORG_PAT` | A GitHub Personal Access Token scoped to your org | Generate at github.com/settings/tokens (see scope requirements in section 5), then `gh secret set ORG_PAT --repo JawaharRamis/data-meshy` |

### Repository variables

Set these at **Settings → Secrets and variables → Actions → Variables**:

| Variable name | Where value comes from | How to set it |
|---|---|---|
| `MESH_API_ENDPOINT` | `terraform output api_endpoint_url` (no trailing slash) | `gh variable set MESH_API_ENDPOINT --body "https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com" --repo JawaharRamis/data-meshy` |

### Domain repo secrets (set by `onboard-domain.yml` automatically)

These secrets are set automatically on newly created domain repos by the `onboard-domain.yml` workflow. You do not set them manually — they are listed here for reference and troubleshooting:

| Secret name | Set on repo | Value |
|---|---|---|
| `AWS_ROLE_ARN` | `{domain}-products` repo | ARN of `DomainGitHubActionsRole` created in the domain account |
| `MESH_API_ENDPOINT` | `{domain}-products` repo | Same as the platform repo variable (`api_endpoint_url`) |
| `MESH_DOMAIN_NAME` | `{domain}-products` repo | Domain name string (e.g., `sales`) |

### Workflow secret reference (complete)

For clarity, this table maps every `secrets.*` and `vars.*` reference found in all workflow files:

| Workflow file | Reference | Type | Description |
|---|---|---|---|
| `infra-plan.yml` | `secrets.AWS_ACCOUNT_ID` | Secret | Central account ID for constructing `TerraformPlanRole` ARN |
| `infra-apply.yml` | `secrets.AWS_ACCOUNT_ID` | Secret | Central account ID for constructing `TerraformApplyRole` ARN |
| `reusable-infra-plan.yml` | `secrets.AWS_ACCOUNT_ID` | Secret | Passed from caller |
| `reusable-infra-apply.yml` | `secrets.AWS_ACCOUNT_ID` | Secret | Passed from caller |
| `reusable-infra-plan.yml` | `secrets.TF_STATE_BUCKET` | Secret | Passed from caller; written to `backend.tfbackend` before `terraform init` |
| `reusable-infra-apply.yml` | `secrets.TF_STATE_BUCKET` | Secret | Passed from caller; written to `backend.tfbackend` before `terraform init` |
| `onboard-domain.yml` | `secrets.AWS_ACCOUNT_ID` | Secret | Used to construct `MeshOnboardingRole` ARN |
| `onboard-domain.yml` | `vars.MESH_API_ENDPOINT` | Variable | Set as secret on new domain repo |
| `onboard-domain.yml` | `secrets.ORG_PAT` | Secret | Creates DP repos and opens issues in external repos |
| `request-subscription.yml` | `secrets.AWS_ROLE_ARN` | Secret | Set on platform repo for subscription workflow OIDC |
| `request-subscription.yml` | `secrets.MESH_API_ENDPOINT` | Secret | Governance API endpoint |
| `request-subscription.yml` | `secrets.ORG_PAT` | Secret | Opens issues in external repos (producer and consumer) |
| `provision-product.yml` | `secrets.AWS_ROLE_ARN` | Secret | `DomainGitHubActionsRole` ARN — set by `onboard-domain.yml` |
| `provision-product.yml` | `secrets.MESH_API_ENDPOINT` | Secret | Governance API endpoint — set by `onboard-domain.yml` |
| `provision-product.yml` | `secrets.MESH_DOMAIN_NAME` | Secret | Domain name — set by `onboard-domain.yml` |

---

## 5. GitHub repository configuration

### Branch protection on `main`

Protecting `main` is required because:
- `TerraformApplyRole` has an OIDC trust condition that restricts to `ref:refs/heads/main`
- `MeshOnboardingRole` similarly restricts to the main branch
- `reusable-infra-apply.yml` runs at the `terraform-apply` environment which requires approvals

Set up branch protection at **Settings → Branches → Add rule**:

- Branch name pattern: `main`
- Enable: Require a pull request before merging
- Enable: Require status checks to pass before merging (add `Plan — central` once the first PR runs)
- Enable: Do not allow bypassing the above settings

### GitHub Environments

Create these two environments at **Settings → Environments → New environment**:

**`terraform-apply`** — Required by `reusable-infra-apply.yml`
- Add the platform team as required reviewers
- This gates every `terraform apply` run — a reviewer must approve before the apply proceeds

**`subscription-approval`** — Required by `request-subscription.yml`
- Add domain product owners or the platform team as required reviewers
- This gates the provisioning step in every subscription request

> [!IMPORTANT]
> If `subscription-approval` has no required reviewers configured, the subscription workflow will hang indefinitely at the `provision` job. Set up reviewers before accepting any subscription requests.

### Template repository: `data-meshy-product-template`

The `onboard-domain.yml` workflow calls the GitHub template API to create new domain repos. For this to work:

1. Ensure `JawaharRamis/data-meshy-product-template` (or your fork) exists and is marked as a Template Repository at **Settings → Template repository**.
2. The `ORG_PAT` must have permission to create repositories in the target org.

### `ORG_PAT` scope requirements

The Personal Access Token set as `ORG_PAT` must have:

| Scope | Why |
|---|---|
| `repo` | Read/write access to create repos from templates, set secrets, push to `main` on domain repos |
| `issues:write` | Open confirmation issues in domain repos and subscription request issues in producer repos |
| `admin:org` (if org-scoped) | Only needed if you want to add repos to an org team |

Generate at: github.com/settings/tokens → Generate new token (classic) → select `repo` and `write:issues`.

---

## 6. Verify the deployment

Run these AWS CLI commands to confirm all major resources were created successfully.

### DynamoDB tables

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

aws dynamodb list-tables --region us-east-1 --query "TableNames[?starts_with(@, 'mesh-')]" --output table
```

Expected output — all 7 tables:

```
mesh-audit-log
mesh-domains
mesh-event-dedup
mesh-pipeline-locks
mesh-products
mesh-quality-scores
mesh-subscriptions
```

### Lambda functions

```bash
aws lambda list-functions --region us-east-1 \
  --query "Functions[?starts_with(FunctionName, 'mesh-')].FunctionName" \
  --output table
```

Expected output — all 14 functions:

```
mesh-audit-writer
mesh-catalog-browse
mesh-catalog-describe
mesh-catalog-search
mesh-catalog-writer
mesh-datazone-connector
mesh-event-validator
mesh-freshness-monitor
mesh-product-deprecation
mesh-retirement
mesh-subscription-compensator
mesh-subscription-provisioner
mesh-subscription-request
```

### API Gateway

```bash
aws apigatewayv2 get-apis --region us-east-1 \
  --query "Items[?Name=='mesh-governance-api'].{Name:Name,Endpoint:ApiEndpoint}" \
  --output table
```

Expected: one entry with name `mesh-governance-api` and the endpoint URL matching `terraform output api_endpoint_url`.

### EventBridge bus

```bash
aws events list-event-buses --region us-east-1 \
  --query "EventBuses[?Name=='mesh-central-bus'].{Name:Name,Arn:Arn}" \
  --output table
```

Expected: one entry for `mesh-central-bus`.

### Step Functions state machine

```bash
aws stepfunctions list-state-machines --region us-east-1 \
  --query "stateMachines[?name=='subscription-provisioner'].{Name:name,Arn:stateMachineArn}" \
  --output table
```

Expected: one entry for `subscription-provisioner`.

### IAM roles

```bash
aws iam list-roles --query "Roles[?starts_with(RoleName, 'Mesh') || starts_with(RoleName, 'Terraform') || starts_with(RoleName, 'Governance') || RoleName=='DataZoneDomainExecutionRole'].RoleName" --output table
```

Expected roles:

| Role name | Purpose |
|---|---|
| `MeshAdminRole` | Break-glass / MFA-required admin access |
| `MeshLFGrantorRole` | Lake Formation SELECT grants on gold tables (subscription provisioner) |
| `MeshCatalogWriterRole` | DynamoDB writes to catalog tables |
| `MeshAuditWriterRole` | Append-only writes to `mesh-audit-log` |
| `MeshKmsGrantorRole` | KMS grants for subscription consumer access |
| `MeshOnboardingRole` | Domain onboarding workflow (OIDC, main branch only) |
| `GovernanceReadRole` | Read-only access for governance leads (SSO) |
| `TerraformPlanRole` | GitHub Actions OIDC — `terraform plan` (any branch) |
| `TerraformApplyRole` | GitHub Actions OIDC — `terraform apply` (main branch only) |
| `DataZoneDomainExecutionRole` | DataZone service role |
| `MeshCatalogWriterLambdaRole` | Lambda execution: `mesh-catalog-writer` |
| `MeshCatalogSearchLambdaRole` | Lambda execution: `mesh-catalog-search` |
| `MeshCatalogBrowseLambdaRole` | Lambda execution: `mesh-catalog-browse` |
| `MeshCatalogDescribeLambdaRole` | Lambda execution: `mesh-catalog-describe` |
| `MeshSubscriptionRequestLambdaRole` | Lambda execution: `mesh-subscription-request` |
| `MeshSubscriptionProvisionerLambdaRole` | Lambda execution: `mesh-subscription-provisioner` |
| `MeshSubscriptionCompensatorLambdaRole` | Lambda execution: `mesh-subscription-compensator` |
| `MeshAuditWriterLambdaRole` | Lambda execution: `mesh-audit-writer` |
| `MeshEventValidatorLambdaRole` | Lambda execution: `mesh-event-validator` |
| `MeshFreshnessMonitorLambdaRole` | Lambda execution: `mesh-freshness-monitor` |
| `MeshProductDeprecationLambdaRole` | Lambda execution: `mesh-product-deprecation` |
| `MeshRetirementLambdaRole` | Lambda execution: `mesh-retirement` |
| `MeshDataZoneConnectorLambdaRole` | Lambda execution: `mesh-datazone-connector` |

### Verify Lambda env vars (spot check)

```bash
aws lambda get-function-configuration \
  --function-name mesh-audit-writer \
  --region us-east-1 \
  --query "Environment.Variables"
```

Expected: `{"MESH_AUDIT_LOG_TABLE": "mesh-audit-log"}`.

> [!NOTE]
> The Lambda environment variable is `MESH_AUDIT_LOG_TABLE`. The Python handler in `lambdas/audit_writer.py` reads from `MESH_AUDIT_TABLE` (without `_LOG_`). This env-var name mismatch causes the handler to fall back to the default `mesh-audit-log` — so it works, but it is a known discrepancy documented in the troubleshooting section.

---

## 7. Onboard your first domain

Domain onboarding is fully automated by `onboard-domain.yml`. A platform engineer triggers it once per domain. There are no manual AWS console steps.

### Trigger the workflow

```bash
export DOMAIN_NAME="sales"
export DOMAIN_ACCOUNT_ID="123456789012"   # 12-digit AWS account ID
export DOMAIN_REGION="us-east-1"
export OWNER_EMAIL="sales-team@acme.com"
export GITHUB_REPO_SLUG="JawaharRamis/sales-products"
export REPO_PATTERN="JawaharRamis/sales-*"

gh workflow run onboard-domain.yml \
  --repo JawaharRamis/data-meshy \
  --ref main \
  --field domain_name="$DOMAIN_NAME" \
  --field account_id="$DOMAIN_ACCOUNT_ID" \
  --field aws_region="$DOMAIN_REGION" \
  --field owner_email="$OWNER_EMAIL" \
  --field github_repo="$GITHUB_REPO_SLUG" \
  --field repo_pattern="$REPO_PATTERN"
```

### Workflow inputs explained

| Input | Pattern/format | Example | Notes |
|---|---|---|---|
| `domain_name` | `^[a-z][a-z0-9_]*$` | `sales` | Becomes a key throughout the mesh — S3 prefix, Glue DB prefix, registry key. Cannot be changed after onboarding. |
| `account_id` | 12 digits | `123456789012` | AWS account ID for the domain. Must be accessible from `OrganizationAccountAccessRole`. |
| `aws_region` | AWS region string | `us-east-1` | Region where domain infrastructure is deployed. |
| `owner_email` | Email | `sales@acme.com` | Recorded in `domains/{domain}.yaml`. Used for notification routing. |
| `github_repo` | `org/repo` | `JawaharRamis/sales-products` | The DP repo that will be created from the template. Must not already exist (or if it does, the workflow treats it as idempotent). |
| `repo_pattern` | `org/glob` | `JawaharRamis/sales-*` | OIDC sub condition for `DomainGitHubActionsRole` and `TerraformPlanRole`. Use a wildcard pattern that covers all product repos for this domain. |

### What the workflow does

The workflow runs four jobs:

**Job 1: `write-registry`** — Generates `domains/sales.yaml`, validates it against `schemas/domain.json`, commits it to `main`. This file is the source of truth for all subscription and upgrade workflows.

**Job 2: `provision-iam`** — Assumes `MeshOnboardingRole` (central account, OIDC) then cross-account assumes `OrganizationAccountAccessRole` in the domain account. Creates idempotently:

- GitHub OIDC provider in the domain account
- `DomainGitHubActionsRole` — trusted by the domain DP repo's `main` branch. Has S3/Glue/Step Functions permissions scoped to `mesh-{domain}-*` prefix, plus `events:PutEvents` to the central bus. Contains an **explicit deny on all Lake Formation permission management** — domain teams can never grant or revoke LF permissions directly.
- `MeshEventRole` — trusted by EventBridge, allows only `events:PutEvents` to the central bus.
- EventBridge cross-account forwarding rule (`mesh-{domain}-to-central`) from the domain bus to the central bus.
- Lake Formation S3 location registration for `mesh-{domain}-gold` (best-effort, continues on error).

**Job 3: `rollback-on-failure`** — If job 2 fails, reverts the `domains/` commit from job 1. Registry and IAM are always kept in sync.

**Job 4: `setup-repo`** — Creates `JawaharRamis/sales-products` from `JawaharRamis/data-meshy-product-template` via the GitHub template API. Sets three secrets on the new repo (`AWS_ROLE_ARN`, `MESH_API_ENDPOINT`, `MESH_DOMAIN_NAME`). Opens a quickstart checklist issue in the new repo.

### What to verify after the workflow succeeds

```bash
# 1. DomainGitHubActionsRole exists in domain account
aws iam get-role --role-name DomainGitHubActionsRole \
  --query "Role.Arn" --output text

# 2. MeshEventRole exists in domain account
aws iam get-role --role-name MeshEventRole \
  --query "Role.Arn" --output text

# 3. OIDC provider exists in domain account
aws iam list-open-id-connect-providers \
  --query "OpenIDConnectProviderList[?ends_with(Arn, '/token.actions.githubusercontent.com')].Arn" \
  --output text

# 4. EventBridge rule exists in domain account
aws events list-rules --region "$DOMAIN_REGION" \
  --query "Rules[?Name=='mesh-${DOMAIN_NAME}-to-central'].State" \
  --output text
# Expected: ENABLED

# 5. Domain repo was created on GitHub
gh repo view "JawaharRamis/${DOMAIN_NAME}-products"

# 6. Quickstart issue was opened in the new repo
gh issue list --repo "JawaharRamis/${DOMAIN_NAME}-products" --limit 5

# 7. Registry entry committed to main in platform repo
cat domains/${DOMAIN_NAME}.yaml
```

> [!NOTE]
> The `provision-iam` job requires that `OrganizationAccountAccessRole` exists in the domain account. This role is created automatically by AWS Organizations when you add a member account. If your domain account was created outside of Organizations (e.g., a standalone account), you must manually create this role before triggering `onboard-domain.yml`. See the troubleshooting section.

---

## 8. Publish your first data product

This section describes the domain team workflow. As a platform engineer, you may need to walk a domain team through this, or run it yourself to verify the platform is working.

For full field-level documentation, see [PRODUCT-SPEC.md](../reference/PRODUCT-SPEC.md) and [ADD-PRODUCT.md](ADD-PRODUCT.md).

### 1. Clone the domain repo

```bash
git clone https://github.com/JawaharRamis/sales-products.git
cd sales-products
```

The repo was generated from `data-meshy-product-template`. It contains example files at `products/example/` and workflow stubs in `.github/workflows/`.

### 2. Create product files

```bash
mkdir -p products/revenue_daily
cp products/example/product.yaml products/revenue_daily/product.yaml
cp products/example/infra.yaml products/revenue_daily/infra.yaml
```

**Edit `products/revenue_daily/product.yaml`** (the public contract — visible in the catalog and to consumers):

```yaml
name: revenue_daily
domain: sales
owner: sales-team@acme.com
description: Daily revenue aggregated by region
version: "1"
sla:
  freshness_hours: 24
  tier: gold
schema:
  - name: date
    type: date
  - name: region
    type: string
  - name: gross_revenue
    type: decimal
classification: internal
tags: [finance, revenue]
quality_rules:
  - rule: IsComplete("gross_revenue")
  - rule: IsComplete("date")
```

**Edit `products/revenue_daily/infra.yaml`** (private infra config — not exposed to consumers):

```yaml
platform_version: v1.2
glue:
  dpu: 2
  schedule: "cron(0 3 * * ? *)"
  worker_type: G.1X
iceberg:
  partition_keys: [date]
  compaction: true
  retention_days: 730
s3:
  source_prefix: s3://mesh-sales-raw/revenue/
```

> [!IMPORTANT]
> `platform_version` must match a release tag that exists in `JawaharRamis/data-meshy-product-template`. Ask the platform team for the current stable version. The `provision-product.yml` workflow pulls the versioned module directly from that tag — a wrong version causes a Terraform init failure.

### 3. Push to main

```bash
git add products/revenue_daily/
git commit -m "feat: add revenue_daily data product"
git push origin main
```

The push triggers `on-push.yml` in the domain repo, which calls `provision-product.yml` in the platform repo via `workflow_call`.

### What `provision-product.yml` does

1. **Validate (gate)** — Validates `product.yaml` against `schemas/product_spec.json`. The workflow stops here on failure, nothing is created.
2. **Register** — Assumes `DomainGitHubActionsRole` via OIDC, then POSTs `product.yaml` to `MESH_API_ENDPOINT/products` (SigV4-signed). Creates a DynamoDB record in `mesh-products` with status `PENDING`.
3. **Terraform apply** — Parses `infra.yaml` into Terraform `-var` flags, writes an ephemeral `main.tf` sourcing the versioned `modules/data-product` from `data-meshy-product-template`, and runs `terraform apply`. Provisions: Iceberg table in the domain Glue catalog, Glue Data Quality ruleset, Step Functions medallion pipeline state machine, and Glue job scripts.
4. **Rollback on failure** — If Terraform fails, a best-effort DELETE request removes the catalog entry.

### Verify the product is live

```bash
# Platform engineer — using central account credentials
python tools/catalog.py --profile central-admin describe sales revenue_daily
# Expected: status: ACTIVE, domain: sales, name: revenue_daily

# Or using AWS CLI directly
aws dynamodb get-item \
  --table-name mesh-products \
  --key '{"domain": {"S": "sales"}, "name": {"S": "revenue_daily"}}' \
  --region us-east-1
```

---

## 9. Request and approve a subscription

Once two domains exist and one has an active product, you can test the full end-to-end subscription flow.

For detailed step-by-step coverage including PII filtering and Athena query patterns, see [subscription-flow.md](subscription-flow.md).

### Step 1: Trigger the subscription request

```bash
export PRODUCER_DOMAIN="sales"
export PRODUCT_NAME="revenue_daily"
export CONSUMER_DOMAIN="marketing"
export CONSUMER_REPO="JawaharRamis/marketing-products"
export JUSTIFICATION="Marketing attribution model requires daily revenue by region to calculate ROAS."

gh workflow run request-subscription.yml \
  --repo JawaharRamis/data-meshy \
  --ref main \
  --field producer_domain="$PRODUCER_DOMAIN" \
  --field product_name="$PRODUCT_NAME" \
  --field consumer_domain="$CONSUMER_DOMAIN" \
  --field consumer_repo="$CONSUMER_REPO" \
  --field justification="$JUSTIFICATION"
```

Both producer and consumer domains must be registered in `domains/*.yaml` before this will succeed.

### Step 2: What happens before the approval gate

The workflow immediately runs two jobs:

1. **`validate`** — Checks that `domains/sales.yaml` and `domains/marketing.yaml` both exist, and that `sales/revenue_daily` is `ACTIVE` in `mesh-products`. Fails fast with a clear error if either domain is unregistered or the product does not exist.

2. **`request`** — Writes a `PENDING` record to `mesh-subscriptions`. Opens a GitHub issue in `JawaharRamis/sales-products` with the consumer's justification and a link to the approval gate.

### Step 3: Approve via the GitHub Environment gate

The `provision` job targets the `subscription-approval` environment and pauses. A required reviewer must:

1. Navigate to **Actions → request-subscription.yml → the running workflow**.
2. Click **Review deployments** on the `provision` job.
3. Select `subscription-approval` and click **Approve and deploy**.

### Step 4: Monitor Step Functions

After approval, the workflow calls `POST MESH_API_ENDPOINT/subscriptions` which triggers the `subscription-provisioner` Step Functions state machine via EventBridge (`SubscriptionApproved` event on `mesh-central-bus`).

```bash
# Monitor the state machine execution
export SFN_ARN=$(terraform -chdir=infra/environments/central output -raw subscription_sfn_arn)

aws stepfunctions list-executions \
  --state-machine-arn "$SFN_ARN" \
  --region us-east-1 \
  --query "executions[:3].{Status:status,Name:name,Start:startDate}" \
  --output table
```

### Step 5: Verify the Lake Formation grant

```bash
# Check the subscription record is ACTIVE
aws dynamodb query \
  --table-name mesh-subscriptions \
  --key-condition-expression "product_id = :pid" \
  --expression-attribute-values '{":pid": {"S": "sales#revenue_daily"}}' \
  --region us-east-1 \
  --query "Items[*].{ProductId:product_id.S,Status:status.S,Consumer:consumer_domain.S}"

# Check the LF grant exists in the governance account
aws lakeformation list-permissions \
  --region us-east-1 \
  --resource-type TABLE \
  --query "PrincipalResourcePermissions[?contains(Principal.DataLakePrincipalIdentifier, '123456789012')].Permissions"
```

After the subscription is `ACTIVE`, the consumer domain can query via Athena using the Glue resource link in their account. LF grant propagation takes up to 90 seconds — if queries fail immediately after provisioning, wait and retry.

---

## 10. Day-2 operations reference

This section points to the existing guides for ongoing platform operations. Do not repeat them here.

### Adding another domain account

Run `onboard-domain.yml` again with the new domain's details. The workflow is idempotent — re-running for an existing domain is safe. See [ADD-DOMAIN.md](ADD-DOMAIN.md) for the full registry schema and troubleshooting.

### Platform version upgrades

When a new `data-meshy-product-template` release is published, domain teams receive upgrade notification issues in their DP repos. They update `platform_version` in their `infra.yaml` and push to `main` to re-run `provision-product.yml` at the new version. See `docs/guides/UPGRADE-PLATFORM.md` for the upgrade runbook.

### Retiring a data product

Domain teams trigger `deprecate.yml` in their DP repo. This calls `deprecate-product.yml` in the platform repo, marks the product `DEPRECATED` with a `sunset_date`, notifies all subscribers, and schedules an EventBridge rule to fire the retirement Lambda at sunset. See `docs/guides/DEPRECATE-PRODUCT.md` for details.

### Subscription lifecycle detail

For the complete subscription flow including PII filtering, Athena query patterns, failure/retry procedures, and self-service revocation limitations, see [subscription-flow.md](subscription-flow.md).

### After Organizations is set up

Once AWS Organizations and IAM Identity Center are fully enabled:

1. Set `organizations_enabled = true` in `terraform.tfvars`.
2. Set `org_id` to your Organization ID (e.g., `o-xxxxxxxxxx`).
3. Run on a feature branch: `terraform plan` to review SCP and Identity Center resources.
4. Merge to `main` — `infra-apply.yml` applies automatically (after environment approval).

---

## 11. Troubleshooting

| Failure | Root cause | Fix |
|---|---|---|
| **`Could not assume role` on `infra-plan` or `infra-apply`** | `AWS_ACCOUNT_ID` secret is missing from the repo. The workflow constructs the OIDC role ARN as `arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/TerraformPlanRole`. A blank secret renders this ARN invalid. | Set `AWS_ACCOUNT_ID` at Settings → Secrets. Value is the 12-digit central account ID. |
| **`terraform init` fails in CI with `Error: Missing Required Value` on `backend.tf`'s `bucket` attribute** | `TF_STATE_BUCKET` secret is missing from the repo. `backend.tf` uses partial backend config (no bucket committed to git); locally you supply it via the gitignored `backend.tfbackend` file, but CI has no such file until `reusable-infra-plan.yml`/`reusable-infra-apply.yml` write one from this secret. | Set `TF_STATE_BUCKET` at Settings → Secrets to the tfstate bucket name (e.g. `data-meshy-tfstate-central-eu1-<CENTRAL_ACCOUNT_ID>`). |
| **Lambda `ImportModuleError: No module named 'catalog_writer'`** | The Lambda zip was built from a single source file but the handler imports a sibling module. Both `catalog_writer.py` and any shared utility it imports must be included in the same zip. | In `lambdas.tf`, change the `archive_file` data source from `source_file` (single file) to `source_dir` pointing at the directory containing all Lambda files. Re-apply. |
| **`audit_writer` reads wrong table name** | `audit_writer.py` reads the env var `MESH_AUDIT_TABLE`, but Terraform sets `MESH_AUDIT_LOG_TABLE`. The handler falls back to the hardcoded default `mesh-audit-log` which matches the actual table name, so it works — but any local test that sets `MESH_AUDIT_TABLE` will hit the wrong key. | Either align the env var name in `lambdas.tf` (change to `MESH_AUDIT_TABLE`) or update `audit_writer.py` to read `MESH_AUDIT_LOG_TABLE`. Both approaches are safe — pick one and be consistent. |
| **First `terraform apply` fails on existing orphaned resources** | A previous partial apply or manual creation left resources that Terraform now wants to create but that already exist. | Use `terraform import` to bring the existing resource under Terraform management. Example: `terraform import aws_kms_alias.mesh_central alias/mesh-central`. Then re-run `terraform apply`. |
| **`terraform apply` fails on Organizations-guarded resources (SCPs, Identity Center)** | `organizations_enabled = true` was set before AWS Organizations was activated in the account. The `aws_organizations_policy` and `aws_ssoadmin_*` resources require the Organizations and SSO service to be enabled. | Set `organizations_enabled = false` in `terraform.tfvars`, apply cleanly, then enable Organizations in the AWS console, then flip the variable to `true` and apply again. |
| **SNS topic or Step Functions state machine fails to apply with `InvalidParameter` or `ValidationException` on tag values** | Tag values containing apostrophes (`'`), em-dashes (`—`), or other non-ASCII characters fail AWS tag validation. This was a known blocker in Phase 6. | Inspect `tags` blocks in `infra/modules/governance/main.tf` and `infra/modules/subscription/step_functions.tf`. Replace any special characters in tag values with ASCII equivalents. |
| **`onboard-domain.yml` fails at `provision-iam` with `OrganizationAccountAccessRole` not found** | The domain account was created outside AWS Organizations or the role was renamed. | Manually create `OrganizationAccountAccessRole` in the domain account with a trust policy allowing the central account to assume it. Alternatively, create the domain account through the Organizations console so the role is created automatically. |
| **`onboard-domain.yml` fails at `provision-iam` with `AccessDenied` on `sts:AssumeRole`** | `MeshOnboardingRole` does not trust the GitHub OIDC token from the platform repo's `main` branch, or the `AWS_ACCOUNT_ID` secret is wrong. | Verify `MeshOnboardingRole` exists in the central account. Check its trust policy includes `repo:${github_org}/data-meshy:ref:refs/heads/main` as the OIDC sub condition. Confirm `AWS_ACCOUNT_ID` is set correctly. |
| **`request-subscription.yml` hangs indefinitely at `provision` job** | The `subscription-approval` GitHub environment has no required reviewers, so the gate never fires. | Settings → Environments → `subscription-approval` → Required reviewers → add your team. |
| **`terraform apply` fails on S3 lifecycle rule with `InvalidArgument`** | S3 lifecycle rules require at least one filter condition if the `filter` block is present. An empty `filter {}` block is invalid. | Add `prefix = ""` inside the `filter` block, or remove the `filter` block entirely (applies lifecycle to all objects). |
| **CloudTrail log group resource already exists during apply** | The `/aws/cloudtrail/mesh-central` log group was created manually before Terraform managed it. | `terraform import aws_cloudwatch_log_group.mesh_cloudtrail /aws/cloudtrail/mesh-central` then re-apply. |

---

## What's next

You now have a fully operational data mesh platform. Next steps:

- **[ADD-DOMAIN.md](ADD-DOMAIN.md)** — Full domain onboarding reference including registry schema validation and multi-region considerations
- **[ADD-PRODUCT.md](ADD-PRODUCT.md)** — Complete `product.yaml` and `infra.yaml` field reference with examples for all data types
- **[subscription-flow.md](subscription-flow.md)** — Subscription lifecycle in depth: PII filtering, Athena usage, failure recovery, revocation
- **[PRODUCT-SPEC.md](../reference/PRODUCT-SPEC.md)** — Authoritative product specification schema
- **[RESOURCE-NAMING.md](../reference/RESOURCE-NAMING.md)** — Naming conventions for all AWS resources
- **[OVERVIEW.md](../architecture/OVERVIEW.md)** — System architecture, design principles, and technology stack

For issues and feedback: [JawaharRamis/data-meshy Issues](https://github.com/JawaharRamis/data-meshy/issues)
