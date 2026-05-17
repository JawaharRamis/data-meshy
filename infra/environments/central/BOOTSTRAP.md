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

---

## After Organizations is set up

Once AWS Organizations and IAM Identity Center are enabled:

1. Set `organizations_enabled = true` in your `terraform.tfvars`.
2. Set `org_id` to your Organization ID (e.g. `o-xxxxxxxxxx`).
3. Run `terraform plan` then `terraform apply` to create the SCPs and
   Identity Center permission sets.
