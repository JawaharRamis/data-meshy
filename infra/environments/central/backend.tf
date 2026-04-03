terraform {
  backend "s3" {
    # Bucket name includes account_id to ensure global uniqueness.
    # Replace <account_id> with your central governance AWS account ID.
    bucket = "data-meshy-tfstate-central-<account_id>"

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
