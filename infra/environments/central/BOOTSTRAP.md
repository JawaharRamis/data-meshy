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

## 6. Update backend.tf with your account ID

Edit `infra/environments/central/backend.tf` and replace `CENTRAL_ACCOUNT_ID`
with your real 12-digit account ID (the value of `$CENTRAL_ACCOUNT_ID`):

```
bucket = "data-meshy-tfstate-central-123456789012"
```

> Do NOT commit a real account ID in this file if the repo is public. The
> placeholder `CENTRAL_ACCOUNT_ID` is intentional — replace it locally only.

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

```bash
cd infra/environments/central
terraform init
```

---

## After Organizations is set up

Once AWS Organizations and IAM Identity Center are enabled:

1. Set `organizations_enabled = true` in your `terraform.tfvars`.
2. Set `org_id` to your Organization ID (e.g. `o-xxxxxxxxxx`).
3. Run `terraform plan` then `terraform apply` to create the SCPs and
   Identity Center permission sets.
