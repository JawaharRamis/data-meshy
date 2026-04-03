##############################################################################
# data-product/iceberg.tf
#
# Iceberg table registration in Glue Data Catalog (gold database).
# LF-Tags applied: classification, pii, domain.
##############################################################################

##############################################################################
# Glue Catalog Table — Iceberg format in the gold database
##############################################################################

resource "aws_glue_catalog_table" "product" {
  name          = var.product_name
  database_name = var.glue_catalog_db_gold
  description   = var.description

  table_type = "EXTERNAL_TABLE"

  open_table_format_input {
    iceberg_input {
      metadata_operation = "CREATE"
      version            = "2"
    }
  }

  storage_descriptor {
    location = "s3://${var.gold_bucket_name}/${var.domain}/${var.product_name}/"

    dynamic "columns" {
      for_each = var.schema_columns
      content {
        name    = columns.value.name
        type    = columns.value.type
        comment = columns.value.comment
      }
    }

    input_format  = "org.apache.iceberg.mr.hive.HiveIcebergInputFormat"
    output_format = "org.apache.iceberg.mr.hive.HiveIcebergOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.iceberg.mr.hive.HiveIcebergSerDe"
    }
  }

  dynamic "partition_keys" {
    for_each = var.partition_keys
    content {
      name = partition_keys.value
      type = "string"
    }
  }

  parameters = {
    "table_type"       = "ICEBERG"
    "metadata_location" = "s3://${var.gold_bucket_name}/${var.domain}/${var.product_name}/metadata/"
    "classification"   = var.classification
    "schema_version"   = tostring(var.schema_version)
    "product_id"       = local.product_id
    "owner"            = var.owner
  }
}

##############################################################################
# LF-Tags applied to the Iceberg table
##############################################################################

resource "aws_lakeformation_resource_lf_tags" "product_table" {
  database {
    name = var.glue_catalog_db_gold
  }

  table {
    database_name = var.glue_catalog_db_gold
    name          = aws_glue_catalog_table.product.name
  }

  lf_tag {
    key   = "domain"
    value = var.domain
  }

  lf_tag {
    key   = "classification"
    value = var.classification
  }

  lf_tag {
    key   = "pii"
    value = tostring(var.pii)
  }
}
