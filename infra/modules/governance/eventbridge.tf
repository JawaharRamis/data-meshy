# infra/modules/governance/eventbridge.tf
# Central EventBridge bus, Schema Registry, rules, targets, and DLQs.

###############################################################################
# Central Event Bus: mesh-central-bus
###############################################################################
resource "aws_cloudwatch_event_bus" "mesh_central" {
  name = "mesh-central-bus"

  tags = merge(local.mandatory_tags, {
    Name = "mesh-central-bus"
  })
}

###############################################################################
# Bus resource policy — explicitly lists each domain account ID, no wildcards
###############################################################################
resource "aws_cloudwatch_event_bus_policy" "mesh_central_policy" {
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = length(var.domain_account_ids) > 0 ? [
      {
        Sid    = "AllowDomainAccountsPutEvents"
        Effect = "Allow"
        Principal = {
          AWS = [for account_id in var.domain_account_ids : "arn:aws:iam::${account_id}:root"]
        }
        Action   = "events:PutEvents"
        Resource = aws_cloudwatch_event_bus.mesh_central.arn
      }
    ] : []
  })
}

###############################################################################
# EventBridge Schema Registry: mesh-events
###############################################################################
resource "aws_schemas_registry" "mesh_events" {
  name        = "mesh-events"
  description = "JSON Schemas for all Data Meshy EventBridge event types."

  tags = local.mandatory_tags
}

###############################################################################
# SQS DLQs for EventBridge rule targets
###############################################################################
resource "aws_sqs_queue" "mesh_catalog_dlq" {
  name                      = "mesh-catalog-dlq"
  message_retention_seconds = 1209600 # 14 days
  kms_master_key_id         = aws_kms_key.mesh_central.arn

  tags = merge(local.mandatory_tags, {
    Name    = "mesh-catalog-dlq"
    Purpose = "DLQ for EventBridge catalog update rule targets"
  })
}

resource "aws_sqs_queue" "mesh_audit_dlq" {
  name                      = "mesh-audit-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = aws_kms_key.mesh_central.arn

  tags = merge(local.mandatory_tags, {
    Name    = "mesh-audit-dlq"
    Purpose = "DLQ for EventBridge audit rule targets"
  })
}

resource "aws_sqs_queue" "mesh_subscription_dlq" {
  name                      = "mesh-subscription-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = aws_kms_key.mesh_central.arn

  tags = merge(local.mandatory_tags, {
    Name    = "mesh-subscription-dlq"
    Purpose = "DLQ for EventBridge subscription workflow rule targets"
  })
}

###############################################################################
# SQS queue policies — allow EventBridge to send messages to DLQs
###############################################################################
data "aws_iam_policy_document" "dlq_policy" {
  for_each = {
    catalog      = aws_sqs_queue.mesh_catalog_dlq.arn
    audit        = aws_sqs_queue.mesh_audit_dlq.arn
    subscription = aws_sqs_queue.mesh_subscription_dlq.arn
  }

  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    actions   = ["sqs:SendMessage"]
    resources = [each.value]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_bus.mesh_central.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "mesh_catalog_dlq" {
  queue_url = aws_sqs_queue.mesh_catalog_dlq.id
  policy    = data.aws_iam_policy_document.dlq_policy["catalog"].json
}

resource "aws_sqs_queue_policy" "mesh_audit_dlq" {
  queue_url = aws_sqs_queue.mesh_audit_dlq.id
  policy    = data.aws_iam_policy_document.dlq_policy["audit"].json
}

resource "aws_sqs_queue_policy" "mesh_subscription_dlq" {
  queue_url = aws_sqs_queue.mesh_subscription_dlq.id
  policy    = data.aws_iam_policy_document.dlq_policy["subscription"].json
}

###############################################################################
# CloudWatch Log Group for EventBridge audit trail
###############################################################################
resource "aws_cloudwatch_log_group" "mesh_event_audit" {
  name              = "/aws/events/mesh-central-audit"
  retention_in_days = 90

  tags = merge(local.mandatory_tags, {
    Name    = "mesh-central-audit-logs"
    Purpose = "Audit trail of all events on mesh-central-bus"
  })
}

###############################################################################
# EventBridge Rules on mesh-central-bus
###############################################################################

# Rule: Route ProductCreated / ProductRefreshed to catalog Lambda (+ DLQ)
resource "aws_cloudwatch_event_rule" "catalog_update" {
  name           = "mesh-catalog-update"
  description    = "Route ProductCreated and ProductRefreshed events to catalog update Lambda."
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name

  event_pattern = jsonencode({
    source      = ["datameshy"]
    detail-type = ["ProductCreated", "ProductRefreshed"]
  })

  tags = local.mandatory_tags
}

resource "aws_cloudwatch_event_target" "catalog_update_dlq" {
  rule           = aws_cloudwatch_event_rule.catalog_update.name
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name
  target_id      = "CatalogUpdateDLQ"
  arn            = aws_sqs_queue.mesh_catalog_dlq.arn

  dead_letter_config {
    arn = aws_sqs_queue.mesh_catalog_dlq.arn
  }
}

# Rule: Route SubscriptionRequested to Step Functions subscription workflow (+ DLQ)
resource "aws_cloudwatch_event_rule" "subscription_workflow" {
  name           = "mesh-subscription-workflow"
  description    = "Route SubscriptionRequested events to Step Functions approval workflow."
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name

  event_pattern = jsonencode({
    source      = ["datameshy"]
    detail-type = ["SubscriptionRequested"]
  })

  tags = local.mandatory_tags
}

resource "aws_cloudwatch_event_target" "subscription_workflow_dlq" {
  rule           = aws_cloudwatch_event_rule.subscription_workflow.name
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name
  target_id      = "SubscriptionWorkflowDLQ"
  arn            = aws_sqs_queue.mesh_subscription_dlq.arn

  dead_letter_config {
    arn = aws_sqs_queue.mesh_subscription_dlq.arn
  }
}

# Rule: Route QualityAlert / FreshnessViolation / SchemaChanged to SNS alerts
resource "aws_cloudwatch_event_rule" "quality_alerts" {
  name           = "mesh-quality-alerts"
  description    = "Route quality, freshness, and schema alerts to SNS."
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name

  event_pattern = jsonencode({
    source      = ["datameshy", "datameshy.central"]
    detail-type = ["QualityAlert", "FreshnessViolation", "SchemaChanged"]
  })

  tags = local.mandatory_tags
}

resource "aws_cloudwatch_event_target" "quality_alerts_sns" {
  rule           = aws_cloudwatch_event_rule.quality_alerts.name
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name
  target_id      = "QualityAlertsSNS"
  arn            = aws_sns_topic.mesh_quality_alerts.arn

  dead_letter_config {
    arn = aws_sqs_queue.mesh_audit_dlq.arn
  }
}

# Rule: Route PipelineFailure events to SNS pipeline-failures topic
resource "aws_cloudwatch_event_rule" "pipeline_failures" {
  name           = "mesh-pipeline-failures"
  description    = "Route PipelineFailure events to SNS."
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name

  event_pattern = jsonencode({
    source      = ["datameshy"]
    detail-type = ["PipelineFailure"]
  })

  tags = local.mandatory_tags
}

resource "aws_cloudwatch_event_target" "pipeline_failures_sns" {
  rule           = aws_cloudwatch_event_rule.pipeline_failures.name
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name
  target_id      = "PipelineFailuresSNS"
  arn            = aws_sns_topic.mesh_pipeline_failures.arn

  dead_letter_config {
    arn = aws_sqs_queue.mesh_audit_dlq.arn
  }
}

# Rule: Route ALL events to CloudWatch Logs for audit
resource "aws_cloudwatch_event_rule" "all_events_audit" {
  name           = "mesh-all-events-audit"
  description    = "Send all mesh events to CloudWatch Logs for audit."
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name

  event_pattern = jsonencode({
    source = [{ prefix = "datameshy" }]
  })

  tags = local.mandatory_tags
}

resource "aws_cloudwatch_event_target" "all_events_audit_logs" {
  rule           = aws_cloudwatch_event_rule.all_events_audit.name
  event_bus_name = aws_cloudwatch_event_bus.mesh_central.name
  target_id      = "AllEventsAuditLogs"
  arn            = aws_cloudwatch_log_group.mesh_event_audit.arn
}

###############################################################################
# CloudWatch Alarms on all DLQs (any message in DLQ = incident)
###############################################################################
locals {
  dlq_alarms = {
    catalog      = aws_sqs_queue.mesh_catalog_dlq.name
    audit        = aws_sqs_queue.mesh_audit_dlq.name
    subscription = aws_sqs_queue.mesh_subscription_dlq.name
  }
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  for_each = local.dlq_alarms

  alarm_name          = "mesh-${each.key}-dlq-not-empty"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "INCIDENT: Messages in ${each.value} — a processing failure occurred. Investigate immediately."
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = each.value
  }

  alarm_actions = [aws_sns_topic.mesh_pipeline_failures.arn]

  tags = local.mandatory_tags
}
