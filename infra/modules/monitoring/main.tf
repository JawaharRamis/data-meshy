##############################################################################
# monitoring/main.tf
#
# CloudWatch alarms, log groups, and AWS Budgets for a domain account.
# Phase 1 MVP — basic but pager-worthy alarms:
#   - Lambda error rate > 1 in 5 min -> SNS alert
#   - SQS DLQ message count > 0 -> SNS alert
#   - Step Functions execution failures > 0 in 5 min -> SNS alert
#   - Glue job failure -> CloudWatch metric alarm -> SNS alert
#   - AWS Budget alerts at $20/$50/$100 monthly thresholds
##############################################################################

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.30.0"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  mandatory_tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
    Domain      = var.domain
  }

  tags = merge(local.mandatory_tags, var.tags)
}

##############################################################################
# CloudWatch Log Groups — 30-day retention for all domain Lambda functions
##############################################################################

resource "aws_cloudwatch_log_group" "lambda" {
  count = length(var.lambda_function_names)

  name              = "/aws/lambda/${var.lambda_function_names[count.index]}"
  retention_in_days = 30

  tags = merge(local.tags, {
    Name = "${var.domain}-lambda-${var.lambda_function_names[count.index]}-logs"
  })
}

##############################################################################
# CloudWatch Log Group — Step Functions state machine execution logs
##############################################################################

resource "aws_cloudwatch_log_group" "step_functions" {
  count = var.state_machine_arn != "" ? 1 : 0

  name              = "/data-meshy/${var.domain}/pipeline"
  retention_in_days = 30

  tags = merge(local.tags, {
    Name = "${var.domain}-step-functions-logs"
  })
}

##############################################################################
# Alarms: Lambda Errors
# Triggers when any Lambda function has > 1 error in a 5-minute window.
##############################################################################

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  count = length(var.lambda_function_names)

  alarm_name          = "${var.domain}-lambda-error-${var.lambda_function_names[count.index]}"
  alarm_description   = "Lambda function ${var.lambda_function_names[count.index]} error count > 1 in 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = var.lambda_function_names[count.index]
  }

  alarm_actions = [var.alarm_notification_arn]
  ok_actions    = [var.alarm_notification_arn]

  tags = merge(local.tags, {
    Name = "${var.domain}-lambda-error-${var.lambda_function_names[count.index]}"
  })
}

##############################################################################
# Alarms: SQS DLQ Message Count
# Triggers when any DLQ has > 0 messages (any message in a DLQ is an incident).
##############################################################################

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  for_each = var.dlq_queue_arns

  alarm_name          = "${var.domain}-dlq-depth-${replace(each.key, "_", "-")}"
  alarm_description   = "DLQ ${each.key} has messages. Any message in a DLQ is an incident."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = each.key
  }

  alarm_actions = [var.alarm_notification_arn]
  ok_actions    = [var.alarm_notification_arn]

  tags = merge(local.tags, {
    Name = "${var.domain}-dlq-${each.key}"
  })
}

##############################################################################
# Alarm: Step Functions Execution Failures
# Triggers when the state machine has > 0 failed executions in 5 minutes.
##############################################################################

resource "aws_cloudwatch_metric_alarm" "sfn_failures" {
  count = var.state_machine_arn != "" ? 1 : 0

  alarm_name          = "${var.domain}-sfn-pipeline-failures"
  alarm_description   = "Step Functions pipeline execution failures > 0 in 5 minutes for ${var.domain}."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = var.state_machine_arn
  }

  alarm_actions = [var.alarm_notification_arn]
  ok_actions    = [var.alarm_notification_arn]

  tags = merge(local.tags, {
    Name = "${var.domain}-sfn-pipeline-failures"
  })
}

##############################################################################
# Alarms: Glue Job Failures
# Triggers when any Glue job has a failure (metric: glue.driver.aggregate.numFailedTasks).
##############################################################################

resource "aws_cloudwatch_metric_alarm" "glue_failures" {
  count = length(var.glue_job_names)

  alarm_name          = "${var.domain}-glue-failure-${var.glue_job_names[count.index]}"
  alarm_description   = "Glue job ${var.glue_job_names[count.index]} failure detected for ${var.domain}."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "glue.driver.aggregate.numFailedTasks"
  namespace           = "Glue"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    JobName = var.glue_job_names[count.index]
    JobRunId = "ALL"
    Type     = "gauge"
  }

  alarm_actions = [var.alarm_notification_arn]
  ok_actions    = [var.alarm_notification_arn]

  tags = merge(local.tags, {
    Name = "${var.domain}-glue-${var.glue_job_names[count.index]}"
  })
}

##############################################################################
# AWS Budgets — alert at configured thresholds ($20, $50, $100 default)
##############################################################################

resource "aws_budgets_budget" "domain" {
  count = length(var.budget_thresholds) > 0 ? 1 : 0

  name         = "${var.domain}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(max(var.budget_thresholds...))
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "cost_filter" {
    for_each = [
      {
        name = "TagKeyValue"
        values = ["user:Domain$${var.domain}"]
      }
    ]
    content {
      name   = cost_filter.value.name
      values = cost_filter.value.values
    }
  }

  dynamic "notification" {
    for_each = var.budget_thresholds
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "ABSOLUTE_VALUE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = length(var.budget_email_recipients) > 0 ? var.budget_email_recipients : null
      subscriber_sns_topic_arns  = length(var.budget_email_recipients) == 0 ? [var.alarm_notification_arn] : []
    }
  }

  tags = local.tags
}
