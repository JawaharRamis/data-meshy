# infra/shared/backend.tf
#
# S3 backend template with placeholders.
# Each environment (central/, domain-sales/) has its OWN backend.tf.
# This file documents the pattern; actual config lives per environment.
#
# IMPORTANT: .terraform.lock.hcl must be committed (never .gitignored).

# terraform {
#   backend "s3" {
#     bucket         = "<tfstate-bucket-name>"        # e.g. data-meshy-tfstate-central-<account_id>
#     key            = "<path/to/terraform.tfstate>"  # e.g. central/terraform.tfstate
#     region         = "us-east-1"
#     dynamodb_table = "<tflock-table-name>"           # e.g. data-meshy-tflock-central
#     kms_key_id     = "<kms-key-arn-or-alias>"        # e.g. alias/mesh-central
#     encrypt        = true
#   }
# }
