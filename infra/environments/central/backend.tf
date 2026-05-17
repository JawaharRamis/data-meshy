terraform {
  backend "s3" {
    # bucket is intentionally omitted here — it contains the account ID and
    # must not be committed to a public repo. Pass it via partial backend config:
    #
    #   terraform init -backend-config=backend.tfbackend
    #
    # backend.tfbackend is gitignored. See BOOTSTRAP.md for how to create it,
    # and backend.tfbackend.example for the expected format.

    key            = "central/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "data-meshy-tflock-central"
    kms_key_id     = "alias/mesh-central"
    encrypt        = true
  }
}
