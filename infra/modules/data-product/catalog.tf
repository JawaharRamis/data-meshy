##############################################################################
# data-product/catalog.tf
#
# Registers the data product in the central DynamoDB mesh-products table.
# PK: {domain}#{product_name}, initial status = PROVISIONED.
#
# Note: This resource uses aws_dynamodb_table_item which writes directly to
# the table. In production, the central Lambda handler (Stream 3) writes the
# catalog entry via ProductCreated event. This Terraform resource creates the
# initial entry so the product is visible in the catalog immediately after
# infrastructure provisioning.
#
# The DynamoDB table itself is created in the central governance account
# (Stream 1). This resource assumes cross-account DynamoDB access or that
# Terraform is run with credentials that have write access to the table.
# For Phase 1 portfolio, the same Terraform state manages both accounts.
##############################################################################

resource "aws_dynamodb_table_item" "product_catalog_entry" {
  table_name = var.mesh_products_table_name
  hash_key   = "domain#product_name"

  item = jsonencode({
    "domain#product_name" = {
      S = local.product_id
    }
    "domain" = {
      S = var.domain
    }
    "product_name" = {
      S = var.product_name
    }
    "status" = {
      S = "PROVISIONED"
    }
    "schema_version" = {
      N = tostring(var.schema_version)
    }
    "owner" = {
      S = var.owner
    }
    "description" = {
      S = var.description
    }
    "classification" = {
      S = var.classification
    }
    "pii" = {
      BOOL = var.pii
    }
    "sla_refresh_frequency" = {
      S = var.sla_refresh_frequency
    }
    "sla_availability" = {
      S = var.sla_availability
    }
    "gold_bucket" = {
      S = var.gold_bucket_name
    }
    "gold_db" = {
      S = var.glue_catalog_db_gold
    }
    "table_name" = {
      S = var.product_name
    }
    "quality_ruleset_name" = {
      S = "${var.domain}_${var.product_name}_dq"
    }
    "created_at" = {
      S = "PROVISIONED_BY_TERRAFORM"
    }
  })
}
