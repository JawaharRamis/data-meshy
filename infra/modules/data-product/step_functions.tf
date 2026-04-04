##############################################################################
# data-product/step_functions.tf
#
# Step Functions state machine for the medallion pipeline:
#   Raw Ingestion -> Silver Transform -> Gold Aggregate -> Schema Validate
#   -> Quality Check -> Publish/Alert -> Release Lock -> Iceberg Maintenance
#
# ASL definition sourced from templates/step_functions/medallion_pipeline.asl.json.
# Execution role: MeshEventRole (PutEvents on central bus) + Glue start job permissions.
# State machine name: {domain}-{product_name}-pipeline
##############################################################################

##############################################################################
# CloudWatch Log Group for Step Functions execution logs
##############################################################################

resource "aws_cloudwatch_log_group" "sfn_pipeline" {
  name              = "/data-meshy/${var.domain}/${var.product_name}/pipeline"
  retention_in_days = 30

  tags = merge(local.tags, {
    Name        = "${var.domain}-${var.product_name}-pipeline-logs"
    ProductName = var.product_name
  })
}

##############################################################################
# Step Functions State Machine
##############################################################################

resource "aws_sfn_state_machine" "medallion_pipeline" {
  name     = "${var.domain}-${var.product_name}-pipeline"
  role_arn = var.mesh_event_role_arn

  # ASL definition from the shared template (written by Stream 3).
  # Falls back to an inline definition if the file does not yet exist.
  definition = fileexists(var.medallion_pipeline_asl_path) ? templatefile(var.medallion_pipeline_asl_path, {}) : jsonencode({
    Comment       = "Data Meshy — Medallion Pipeline for ${var.domain}/${var.product_name}. Placeholder: replace with full ASL from templates/step_functions/medallion_pipeline.asl.json."
    StartAt       = "PlaceholderSucceed"
    TimeoutSeconds = 7200
    States = {
      PlaceholderSucceed = {
        Type = "Succeed"
      }
    }
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn_pipeline.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  tags = merge(local.tags, {
    Name        = "${var.domain}-${var.product_name}-pipeline"
    ProductName = var.product_name
  })
}
