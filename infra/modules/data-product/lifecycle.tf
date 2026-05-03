##############################################################################
# data-product/lifecycle.tf
#
# Product lifecycle infrastructure — Phase 3, Stream 2
#
# Creates:
#   - EventBridge rule matching ProductDeprecated events → product_deprecation Lambda
#   - IAM role for EventBridge Scheduler to invoke the retirement Lambda
#   - IAM role for the retirement Lambda
#   - (Optional) Glue job for Iceberg rollback
#
# Note: The one-shot EventBridge Scheduler rule targeting the retirement Lambda
# at sunset_date is created DYNAMICALLY by the CLI (`datameshy product deprecate`)
# via Boto3, not statically here — because each product has a different sunset_date.
# This file provisions only the IAM roles/policies needed for that dynamic pattern.
##############################################################################

# ── EventBridge rule: ProductDeprecated → deprecation Lambda ──────────────────

resource "aws_cloudwatch_event_rule" "product_deprecated" {
  name           = "${var.domain}-${var.product_name}-product-deprecated"
  description    = "Trigger deprecation Lambda when ProductDeprecated event fires for ${var.domain}/${var.product_name}."
  event_bus_name = var.domain_event_bus_arn

  event_pattern = jsonencode({
    detail-type = ["ProductDeprecated"]
    detail = {
      product_id = ["${local.product_id}"]
    }
  })

  tags = local.tags
}

resource "aws_cloudwatch_event_target" "product_deprecated_lambda" {
  count     = var.product_deprecation_lambda_arn != "" ? 1 : 0
  rule      = aws_cloudwatch_event_rule.product_deprecated.name
  target_id = "ProductDeprecationHandler"
  arn       = var.product_deprecation_lambda_arn

  event_bus_name = var.domain_event_bus_arn
}

resource "aws_lambda_permission" "allow_events_deprecation" {
  count         = var.product_deprecation_lambda_arn != "" ? 1 : 0
  statement_id  = "AllowEventBridgeDeprecation-${var.domain}-${var.product_name}"
  action        = "lambda:InvokeFunction"
  function_name = var.product_deprecation_lambda_arn
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.product_deprecated.arn
}

# ── IAM role for EventBridge Scheduler → retirement Lambda ───────────────────
# The one-shot schedule is created dynamically by the CLI; this role is the
# static execution role referenced in the dynamic create_schedule call.

resource "aws_iam_role" "scheduler_retirement" {
  name        = "${var.domain}-${var.product_name}-scheduler-retirement"
  description = "Allows EventBridge Scheduler to invoke the retirement Lambda for ${var.domain}/${var.product_name}."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "scheduler.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = local.account_id
          }
        }
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "scheduler_retirement_invoke" {
  name = "InvokeRetirementLambda"
  role = aws_iam_role.scheduler_retirement.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [var.retirement_lambda_arn]
      }
    ]
  })
}

# ── Lambda permission: restrict retirement Lambda invocation to Scheduler ──────
# CRITICAL 1: Prevents unauthorized invocation of the retirement Lambda which
# performs mass LF grant revocations. Only the specific scheduler group may invoke.

resource "aws_lambda_permission" "retirement_scheduler" {
  count         = var.retirement_lambda_arn != "" ? 1 : 0
  statement_id  = "AllowSchedulerInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.retirement_lambda_arn
  principal     = "scheduler.amazonaws.com"
  source_arn    = "arn:aws:scheduler:${local.region}:${local.account_id}:schedule/mesh-retirement/*"
}

# ── IAM role for retirement Lambda execution ──────────────────────────────────

resource "aws_iam_role" "retirement_lambda" {
  name        = "${var.domain}-retirement-lambda"
  description = "Execution role for the retirement Lambda — revokes LF grants, marks product RETIRED."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "retirement_lambda_permissions" {
  name = "RetirementLambdaPermissions"
  role = aws_iam_role.retirement_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${var.mesh_products_table_name}",
          "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${var.mesh_subscriptions_table_name}",
          "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${var.mesh_audit_log_table_name}",
          "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${var.mesh_subscriptions_table_name}/index/*"
        ]
      },
      {
        Sid    = "LakeFormationRevoke"
        Effect = "Allow"
        Action = [
          "lakeformation:BatchRevokePermissions",
          "lakeformation:RevokePermissions",
          "lakeformation:ListPermissions"
        ]
        Resource = ["*"]
      },
      {
        Sid    = "EventBridgePutEvents"
        Effect = "Allow"
        Action = ["events:PutEvents"]
        Resource = [
          "arn:aws:events:${local.region}:${local.account_id}:event-bus/*"
        ]
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/*"]
      }
    ]
  })
}

# ── Glue job for Iceberg rollback ─────────────────────────────────────────────
# Optional: only created when var.rollback_glue_script_s3_path is set.

resource "aws_glue_job" "iceberg_rollback" {
  count    = var.rollback_glue_script_s3_path != "" ? 1 : 0
  name     = "${var.domain}-${var.product_name}-iceberg-rollback"
  role_arn = var.glue_job_execution_role_arn

  description = "Restores the ${var.domain}/${var.product_name} gold Iceberg table to a prior snapshot."

  glue_version      = "4.0"
  number_of_workers = 2
  worker_type       = "G.1X"

  command {
    name            = "glueetl"
    script_location = var.rollback_glue_script_s3_path
    python_version  = "3"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--enable-glue-datacatalog"          = "true"
    "--domain"                           = var.domain
    "--product_name"                     = var.product_name
    "--gold_db"                          = var.glue_catalog_db_gold
    "--table_name"                       = var.product_name
    "--products_table_name"              = var.mesh_products_table_name
    "--pipeline_locks_table_name"        = var.mesh_pipeline_locks_table_name
    "--central_event_bus_arn"            = var.central_event_bus_arn
    # --snapshot_id is passed at runtime by the CLI
    "--datalake-formats" = "iceberg"
    "--conf"             = "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"
  }

  tags = local.tags
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "scheduler_retirement_role_arn" {
  description = "IAM role ARN for EventBridge Scheduler to invoke the retirement Lambda."
  value       = aws_iam_role.scheduler_retirement.arn
}

output "retirement_lambda_role_arn" {
  description = "IAM role ARN for the retirement Lambda execution."
  value       = aws_iam_role.retirement_lambda.arn
}

output "rollback_glue_job_name" {
  description = "Name of the Glue Iceberg rollback job (empty string if not created)."
  value       = var.rollback_glue_script_s3_path != "" ? aws_glue_job.iceberg_rollback[0].name : ""
}
