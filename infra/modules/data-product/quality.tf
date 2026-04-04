##############################################################################
# data-product/quality.tf
#
# Glue Data Quality ruleset attached to the gold Iceberg table.
# Rules are sourced from product.yaml quality.rules — passed as a variable.
# Ruleset name follows the naming convention: {domain}_{product_name}_dq
##############################################################################

resource "aws_glue_data_quality_ruleset" "product" {
  name = "${var.domain}_${var.product_name}_dq"

  target_table {
    database_name = var.glue_catalog_db_gold
    table_name    = aws_glue_catalog_table.product.name
  }

  ruleset = join("\n", var.dq_rules)

  tags = merge(local.tags, { ProductName = var.product_name })
}
