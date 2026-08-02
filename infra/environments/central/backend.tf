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
    region         = "eu-central-1"
    dynamodb_table = "data-meshy-tflock-central"
    # Dedicated state-encryption key, manually created and NEVER managed by
    # Terraform. Decoupling it from the platform's alias/mesh-central avoids the
    # destroy trap where teardown deletes the key the backend depends on.
    kms_key_id     = "alias/mesh-tfstate"
    encrypt        = true
  }
}
