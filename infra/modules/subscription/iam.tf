# infra/modules/subscription/iam.tf
#
# IAM roles and policies for the subscription provisioner workflow.
#
# Roles:
#   SubscriptionSFNRole — Step Functions execution role; invokes provisioner
#                         and compensator Lambdas, writes to mesh-subscriptions.
#   SubscriptionEBRole  — EventBridge role; allows EventBridge to start the
#                         subscription-provisioner SFN on SubscriptionApproved events.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id     = data.aws_caller_identity.current.account_id
  region         = data.aws_region.current.name
  mandatory_tags = merge(var.tags, {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
    Module      = "subscription"
  })
}

###############################################################################
# Role: SubscriptionSFNRole
# Trust: states.amazonaws.com (Step Functions service)
# Permissions:
#   - Lambda InvokeFunction on provisioner + compensator
#   - DynamoDB read/write on mesh-subscriptions (status tracking)
#   - KMS decrypt for DynamoDB SSE
#   - CloudWatch Logs for SFN execution logging
###############################################################################
resource "aws_iam_role" "subscription_sfn" {
  name                 = "SubscriptionSFNRole"
  description          = "Step Functions execution role for the subscription-provisioner state machine. Invokes provisioner/compensator Lambdas and tracks status in mesh-subscriptions."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
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

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy" "subscription_sfn_policy" {
  name = "SubscriptionSFNPolicy"
  role = aws_iam_role.subscription_sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeProvisionerLambda"
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = compact([
          var.provisioner_lambda_arn,
          var.compensator_lambda_arn
        ])
      },
      {
        Sid    = "DynamoDBSubscriptionReadWrite"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query"
        ]
        Resource = [
          var.subscriptions_table_arn,
          "${var.subscriptions_table_arn}/index/*"
        ]
      },
      {
        Sid    = "KMSDecryptForDynamoDB"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = var.central_kms_key_arn
      },
      {
        Sid    = "CloudWatchLogsForSFN"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Sid    = "XRayTracingForSFN"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets"
        ]
        Resource = "*"
      }
    ]
  })
}

###############################################################################
# Role: SubscriptionEBRole
# Trust: events.amazonaws.com (EventBridge)
# Permissions: Start the subscription-provisioner SFN on SubscriptionApproved
###############################################################################
resource "aws_iam_role" "subscription_eb" {
  name                 = "SubscriptionEBRole"
  description          = "EventBridge role to start the subscription-provisioner Step Functions state machine on SubscriptionApproved events."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
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

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy" "subscription_eb_policy" {
  name = "SubscriptionEBPolicy"
  role = aws_iam_role.subscription_eb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "StartSubscriptionSFN"
        Effect = "Allow"
        Action = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.subscription_provisioner.arn
      }
    ]
  })
}
