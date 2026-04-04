##############################################################################
# domain-account/eventbridge.tf
#
# Domain EventBridge bus configuration:
#   - Custom event bus: mesh-domain-bus
#   - Resource policy: only allows PutEvents from SAME account
#     (prevents cross-domain event injection)
#   - Forwarding rule: source = "datameshy" (NOT datameshy.central) → central bus
#   - IAM role for the forwarding rule target
##############################################################################

##############################################################################
# Domain Event Bus
##############################################################################

resource "aws_cloudwatch_event_bus" "domain" {
  name = "mesh-domain-bus"
  tags = local.tags
}

##############################################################################
# Resource Policy — only same-account PutEvents allowed
# This prevents any other domain account (or external party) from injecting
# events into this domain's bus.
##############################################################################

resource "aws_cloudwatch_event_bus_policy" "domain" {
  event_bus_name = aws_cloudwatch_event_bus.domain.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSameAccountPutEvents"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = "events:PutEvents"
        Resource = aws_cloudwatch_event_bus.domain.arn
      },
      # Explicitly deny any other principal
      {
        Sid       = "DenyAllOtherAccounts"
        Effect    = "Deny"
        Principal = "*"
        Action    = "events:PutEvents"
        Resource  = aws_cloudwatch_event_bus.domain.arn
        Condition = {
          StringNotEquals = {
            "aws:PrincipalAccount" = local.account_id
          }
        }
      }
    ]
  })
}

##############################################################################
# IAM Role — EventBridge rule → central bus forwarding
##############################################################################

resource "aws_iam_role" "eventbridge_forwarder" {
  name        = "${var.domain}-EventBridgeForwarderRole"
  description = "Allows EventBridge rule to forward events to the central mesh bus."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EventBridgeAssume"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action = "sts:AssumeRole"
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

resource "aws_iam_role_policy" "eventbridge_forwarder_policy" {
  name = "${var.domain}-EventBridgeForwarderPolicy"
  role = aws_iam_role.eventbridge_forwarder.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "PutEventsOnCentralBus"
        Effect = "Allow"
        Action = "events:PutEvents"
        Resource = var.central_event_bus_arn
      }
    ]
  })
}

##############################################################################
# Forwarding Rule
# Pattern: source = "datameshy" (NOT "datameshy.central" — that's reserved
# for central SFN events and must not originate from domain accounts).
##############################################################################

resource "aws_cloudwatch_event_rule" "forward_to_central" {
  name           = "${var.domain}-forward-datameshy-events"
  description    = "Forward all datameshy events (source=datameshy) from domain bus to central mesh bus."
  event_bus_name = aws_cloudwatch_event_bus.domain.name

  event_pattern = jsonencode({
    source = ["datameshy"]
  })

  tags = local.tags
}

resource "aws_cloudwatch_event_target" "forward_to_central" {
  rule           = aws_cloudwatch_event_rule.forward_to_central.name
  event_bus_name = aws_cloudwatch_event_bus.domain.name
  target_id      = "ForwardToCentralBus"
  arn            = var.central_event_bus_arn
  role_arn       = aws_iam_role.eventbridge_forwarder.arn
}

##############################################################################
# Dead-letter queue for the forwarding rule target
##############################################################################

resource "aws_sqs_queue" "eventbridge_forwarder_dlq" {
  name                       = "${var.domain}-eventbridge-forward-dlq"
  message_retention_seconds  = 1209600 # 14 days
  visibility_timeout_seconds = 60

  kms_master_key_id                 = aws_kms_key.domain.id
  kms_data_key_reuse_period_seconds = 300

  tags = local.tags
}

resource "aws_sqs_queue_policy" "eventbridge_forwarder_dlq" {
  queue_url = aws_sqs_queue.eventbridge_forwarder_dlq.url

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEventBridgeSendMessage"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.eventbridge_forwarder_dlq.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_cloudwatch_event_rule.forward_to_central.arn
          }
        }
      }
    ]
  })
}
