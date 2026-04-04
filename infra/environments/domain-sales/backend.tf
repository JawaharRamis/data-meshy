##############################################################################
# domain-sales/backend.tf
#
# S3 backend for domain-sales Terraform state.
# Separate from central backend — isolated state per environment.
# SSE-KMS encrypted, DynamoDB locking enabled.
#
# IMPORTANT: The S3 bucket and DynamoDB table must be provisioned before
# running `terraform init`. For initial setup, use `terraform init -backend=false`
# or provision the backend resources manually.
##############################################################################

data "aws_caller_identity" "current" {}

terraform {
  backend "s3" {
    # Bucket and table must be provisioned before terraform init.
    # Uncomment and fill in after provisioning the backend:
    # bucket         = "data-meshy-tfstate-domain-sales-{ACCOUNT_ID}"
    # key            = "domain-sales/terraform.tfstate"
    # region         = "us-east-1"
    # encrypt        = true
    # kms_key_id     = "alias/mesh-sales"
    # dynamodb_table = "data-meshy-tflock-domain-sales"
  }
}
