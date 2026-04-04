##############################################################################
# domain-account/lakeformation.tf
#
# Lake Formation configuration:
#   - LF admin settings for the domain account
#   - LF-Tag: domain={domain_name}
#   - LF-Tag binding to all gold catalog databases/tables
#   - Database-level LF grants to GlueJobExecutionRole
##############################################################################

##############################################################################
# Lake Formation Settings — mark GlueJobExecutionRole as a data lake admin
# so it can register locations and create tables without IAMAllowedPrincipals.
##############################################################################

resource "aws_lakeformation_data_lake_settings" "domain" {
  admins = [
    aws_iam_role.glue_job_execution.arn,
    aws_iam_role.domain_admin.arn
  ]
}

##############################################################################
# LF-Tags
##############################################################################

resource "aws_lakeformation_lf_tag" "domain_tag" {
  key    = "domain"
  values = [var.domain]

  depends_on = [aws_lakeformation_data_lake_settings.domain]
}

resource "aws_lakeformation_lf_tag" "classification_tag" {
  key    = "classification"
  values = ["public", "internal", "confidential", "restricted"]

  depends_on = [aws_lakeformation_data_lake_settings.domain]
}

resource "aws_lakeformation_lf_tag" "pii_tag" {
  key    = "pii"
  values = ["true", "false"]

  depends_on = [aws_lakeformation_data_lake_settings.domain]
}

##############################################################################
# Apply domain LF-Tag to the gold Glue catalog database.
# (Individual tables get classification + pii tags in the data-product module)
##############################################################################

resource "aws_lakeformation_resource_lf_tags" "gold_db" {
  database {
    name = aws_glue_catalog_database.gold.name
  }

  lf_tag {
    key   = "domain"
    value = var.domain
  }

  depends_on = [
    aws_lakeformation_lf_tag.domain_tag,
    aws_glue_catalog_database.gold
  ]
}

##############################################################################
# LF Grants — GlueJobExecutionRole gets CREATE_TABLE + data access on all
# three databases so it can write Iceberg tables during pipeline execution.
##############################################################################

resource "aws_lakeformation_permissions" "glue_raw_db" {
  principal   = aws_iam_role.glue_job_execution.arn
  permissions = ["CREATE_TABLE", "DESCRIBE"]

  database {
    name = aws_glue_catalog_database.raw.name
  }

  depends_on = [
    aws_lakeformation_data_lake_settings.domain,
    aws_lakeformation_resource.raw
  ]
}

resource "aws_lakeformation_permissions" "glue_silver_db" {
  principal   = aws_iam_role.glue_job_execution.arn
  permissions = ["CREATE_TABLE", "DESCRIBE"]

  database {
    name = aws_glue_catalog_database.silver.name
  }

  depends_on = [
    aws_lakeformation_data_lake_settings.domain,
    aws_lakeformation_resource.silver
  ]
}

resource "aws_lakeformation_permissions" "glue_gold_db" {
  principal   = aws_iam_role.glue_job_execution.arn
  permissions = ["CREATE_TABLE", "DESCRIBE", "ALTER", "DROP"]

  database {
    name = aws_glue_catalog_database.gold.name
  }

  depends_on = [
    aws_lakeformation_data_lake_settings.domain,
    aws_lakeformation_resource.gold
  ]
}

##############################################################################
# DomainDataEngineerRole — read access to all 3 databases for catalog browsing
##############################################################################

resource "aws_lakeformation_permissions" "engineer_raw_db" {
  principal   = aws_iam_role.domain_data_engineer.arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.raw.name
  }

  depends_on = [aws_lakeformation_data_lake_settings.domain]
}

resource "aws_lakeformation_permissions" "engineer_silver_db" {
  principal   = aws_iam_role.domain_data_engineer.arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.silver.name
  }

  depends_on = [aws_lakeformation_data_lake_settings.domain]
}

resource "aws_lakeformation_permissions" "engineer_gold_db" {
  principal   = aws_iam_role.domain_data_engineer.arn
  permissions = ["DESCRIBE"]

  database {
    name = aws_glue_catalog_database.gold.name
  }

  depends_on = [aws_lakeformation_data_lake_settings.domain]
}
