terraform {
  backend "s3" {
    # Bucket name includes account_id to ensure global uniqueness.
    # Replace CENTRAL_ACCOUNT_ID below with your 12-digit AWS account ID.
    # See BOOTSTRAP.md for step-by-step instructions to create this bucket
    # and all other backend prerequisites before running terraform init.
    bucket = "data-meshy-tfstate-central-CENTRAL_ACCOUNT_ID"

    key    = "central/terraform.tfstate"
    region = "us-east-1"

    # DynamoDB table for state locking
    dynamodb_table = "data-meshy-tflock-central"

    # KMS key for state encryption (alias resolves to the mesh-central CMK
    # but the alias must exist before first apply; bootstrap with a pre-existing key)
    kms_key_id = "alias/mesh-central"
    encrypt    = true
  }
}
