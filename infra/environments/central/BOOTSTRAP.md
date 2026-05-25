# Bootstrap: Central Governance Environment

Run these steps **once** before `terraform init`. The S3 state bucket, DynamoDB
lock table, and KMS key must exist before Terraform can initialise its backend
(chicken-and-egg — you cannot use Terraform to create its own state store).

Assumes you have the AWS CLI configured with credentials that have sufficient
permissions in the central governance account (e.g. `AdministratorAccess`).

---

## 1. Export your central account ID

```bash
export CENTRAL_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Central account: $CENTRAL_ACCOUNT_ID"
```

---

## 2. Create the KMS key and alias

```bash
KEY_ID=$(aws kms create-key \
  --description "Data Meshy central Terraform state encryption key" \
  --key-usage ENCRYPT_DECRYPT \
  --query KeyMetadata.KeyId \
  --output text)

aws kms create-alias \
  --alias-name alias/mesh-central \
  --target-key-id "$KEY_ID"

echo "KMS key: $KEY_ID  alias: alias/mesh-central"
```

---

## 3. Create the S3 state bucket

```bash
aws s3api create-bucket \
  --bucket "data-meshy-tfstate-central-${CENTRAL_ACCOUNT_ID}" \
  --region us-east-1

# Enable versioning
aws s3api put-bucket-versioning \
  --bucket "data-meshy-tfstate-central-${CENTRAL_ACCOUNT_ID}" \
  --versioning-configuration Status=Enabled

# Enable SSE-KMS default encryption
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

---

## 4. Block all public access on the state bucket

```bash
aws s3api put-public-access-block \
  --bucket "data-meshy-tfstate-central-${CENTRAL_ACCOUNT_ID}" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

---

## 5. Create the DynamoDB lock table

```bash
aws dynamodb create-table \
  --table-name data-meshy-tflock-central \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

---

## 6. Create backend.tfbackend (partial backend config)

`backend.tf` omits the bucket name to avoid committing an account ID to a public
repo. Create a gitignored file with just the bucket name:

```bash
cat > infra/environments/central/backend.tfbackend <<EOF
bucket = "data-meshy-tfstate-central-${CENTRAL_ACCOUNT_ID}"
EOF
```

Pass this file to `terraform init` via `-backend-config` (see step 8).
`backend.tfbackend` is listed in `.gitignore` — it will not be committed.

---

## 7. Create terraform.tfvars

```bash
cp infra/environments/central/terraform.tfvars.example \
   infra/environments/central/terraform.tfvars
```

Then edit `terraform.tfvars` and fill in:

- `org_id` — your AWS Organization ID (e.g. `o-xxxxxxxxxx`). Leave as `""` if
  Organizations is not yet set up (keep `organizations_enabled = false`).
- `github_org` — your GitHub organisation or username.
- `domain_account_ids` — list of domain account IDs (can be `[]` initially).

> `terraform.tfvars` is listed in `.gitignore` — it will not be committed.

---

## 8. Run terraform init

Pass the partial backend config file so Terraform knows which bucket to use:

```bash
cd infra/environments/central
terraform init -backend-config=backend.tfbackend
```

---

## 9. Run terraform apply (first-time bootstrap only)

> **Why can't this be done via GitHub Actions?**
> The GitHub Actions OIDC provider and `TerraformApplyRole` are created *by* this
> first apply — they don't exist yet. It's a bootstrap chicken-and-egg. After this
> one-time apply, all subsequent applies can run via the `terraform-apply` GitHub
> Actions workflow.

```bash
cd infra/environments/central
terraform plan   # review what will be created
terraform apply  # ~5 minutes; creates ~150 AWS resources
```

When the apply completes, capture the outputs for use as GitHub secrets:

```bash
terraform output api_endpoint_url
terraform output terraform_apply_role_arn
terraform output terraform_plan_role_arn
```

Add these to your GitHub repository secrets:
- `API_ENDPOINT_URL` ← value of `api_endpoint_url`
- `CENTRAL_TERRAFORM_APPLY_ROLE_ARN` ← value of `terraform_apply_role_arn`
- `CENTRAL_TERRAFORM_PLAN_ROLE_ARN` ← value of `terraform_plan_role_arn`

### Reference: deployed values (2026-05-17)

| Output | Value |
|--------|-------|
| `api_endpoint_url` | `https://1iacn0ajp5.execute-api.us-east-1.amazonaws.com` |
| `terraform_apply_role_arn` | `arn:aws:iam::521965996346:role/TerraformApplyRole` |
| `terraform_plan_role_arn` | `arn:aws:iam::521965996346:role/TerraformPlanRole` |
| `central_event_bus_arn` | `arn:aws:events:us-east-1:521965996346:event-bus/mesh-central-bus` |
| `subscription_sfn_arn` | `arn:aws:states:us-east-1:521965996346:stateMachine:subscription-provisioner` |
| `datazone_portal_url` | `https://dzd-4uxi3r22b3t413.datazone.us-east-1.on.aws/` |

---

## Phase 6 Smoke Test Results (2026-05-25)

### Resource Verification

| Resource Group | Count | Status |
|---|---|---|
| Lambda functions (`mesh-*`) | 13 | All `State: Active` |
| DynamoDB tables | 7 | All exist, PITR enabled |
| API Gateway routes (`mesh-governance-api`) | 7 | All wired to Lambda integrations |
| EventBridge bus | 1 (`mesh-central-bus`) | Exists |
| Step Functions state machine | 1 (`subscription-provisioner`) | `ACTIVE` |
| IAM roles | 6 | All exist |

### Lambda Smoke Test Results

| Lambda | Payload | Result | Notes |
|---|---|---|---|
| `mesh-catalog-search` | `{"queryStringParameters": {"keyword": "test"}}` | 200 `{"items": [], "count": 0}` | Pass |
| `mesh-catalog-writer` | ProductCreated EventBridge event | 200 `{"status": "success", "action": "ProductCreated"}` | Fixed — see below |
| `mesh-audit-writer` | EventBridge event | 200 `{"status": "success"}` | Fixed — see below |

### Fixes Applied

**`mesh-catalog-writer` — `Runtime.ImportModuleError: No module named 'event_validator'`**

The Terraform `archive_file` data source used `source_file` (single file) but `catalog_writer.py` imports `event_validator.py`. Fixed by:
1. Updated `lambdas/catalog_writer.py` — no code change needed (import was correct)
2. Updated `infra/modules/governance/lambdas.tf` to bundle `event_validator.py` into the `catalog_writer` archive using dynamic `source` blocks
3. Hot-patched deployed Lambda with correct zip containing both files

**`mesh-audit-writer` — env var name mismatch**

Lambda config exports `MESH_AUDIT_LOG_TABLE` but `audit_writer.py` read `MESH_AUDIT_TABLE`. Fixed by updating `audit_writer.py` to use the correct env var key `MESH_AUDIT_LOG_TABLE`. Function still worked via the fallback default value, but is now correctly wired.

---

## After Organizations is set up

Once AWS Organizations and IAM Identity Center are enabled:

1. Set `organizations_enabled = true` in your `terraform.tfvars`.
2. Set `org_id` to your Organization ID (e.g. `o-xxxxxxxxxx`).
3. Run `terraform plan` then `terraform apply` to create the SCPs and
   Identity Center permission sets.
