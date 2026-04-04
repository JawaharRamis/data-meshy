# infra/modules/subscription/step_functions.tf
#
# Step Functions state machine: subscription-provisioner
#
# Implements the subscription saga pattern:
#   1. ValidateSubscription  — verify product exists and consumer is eligible
#   2. GrantLFPermissions    — call MeshLFGrantorRole to BatchGrant LF SELECT
#   3. CreateResourceLink    — create Glue resource link in consumer account
#   4. MarkActive            — update DynamoDB status to ACTIVE
#
# On any step failure → CompensateOnFailure (compensator Lambda revokes any
# partial grants and marks subscription FAILED with compensation_reason).
#
# ASL definition is in templates/step_functions/subscription_saga.asl.json.
# Stream 2 owns the Lambda implementation; this module wires the infrastructure.

###############################################################################
# CloudWatch Log Group for SFN execution history
###############################################################################
resource "aws_cloudwatch_log_group" "subscription_provisioner_sfn" {
  name              = "/aws/states/subscription-provisioner"
  retention_in_days = 90

  tags = merge(local.mandatory_tags, {
    Name = "subscription-provisioner-sfn-logs"
  })
}

###############################################################################
# Step Functions State Machine: subscription-provisioner
###############################################################################
resource "aws_sfn_state_machine" "subscription_provisioner" {
  name     = "subscription-provisioner"
  role_arn = aws_iam_role.subscription_sfn.arn
  type     = "STANDARD" # STANDARD for saga pattern — supports retries, heartbeats, and long-running waits

  definition = templatefile(
    "${path.module}/../../../templates/step_functions/subscription_saga.asl.json",
    {
      provisioner_lambda_arn = var.provisioner_lambda_arn != "" ? var.provisioner_lambda_arn : "arn:aws:lambda:${var.aws_region}:${var.central_account_id}:function:subscription-provisioner-placeholder"
      compensator_lambda_arn = var.compensator_lambda_arn != "" ? var.compensator_lambda_arn : "arn:aws:lambda:${var.aws_region}:${var.central_account_id}:function:subscription-compensator-placeholder"
      lf_grantor_role_arn    = var.lf_grantor_role_arn
      kms_grantor_role_arn   = var.kms_grantor_role_arn
    }
  )

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.subscription_provisioner_sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  tags = merge(local.mandatory_tags, {
    Name    = "subscription-provisioner"
    Purpose = "Subscription saga — LF grant, resource link creation, compensation"
  })
}

###############################################################################
# EventBridge rule: SubscriptionApproved → start SFN
# Source: datameshy.central (published by approval Lambda in Stream 2)
###############################################################################
resource "aws_cloudwatch_event_rule" "subscription_approved" {
  name           = "mesh-subscription-approved"
  description    = "Triggers subscription-provisioner SFN when a SubscriptionApproved event is published to mesh-central-bus."
  event_bus_name = "mesh-central-bus"

  event_pattern = jsonencode({
    source      = ["datameshy.central"]
    detail-type = ["SubscriptionApproved"]
  })

  tags = local.mandatory_tags
}

resource "aws_cloudwatch_event_target" "subscription_approved_sfn" {
  rule           = aws_cloudwatch_event_rule.subscription_approved.name
  event_bus_name = "mesh-central-bus"
  target_id      = "SubscriptionApprovedSFN"
  arn            = aws_sfn_state_machine.subscription_provisioner.arn
  role_arn       = aws_iam_role.subscription_eb.arn
}
