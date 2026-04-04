# infra/modules/governance/main.tf
# KMS key, SNS topics, and top-level module wiring.
# DynamoDB tables: dynamodb.tf
# IAM roles:       iam.tf
# EventBridge:     eventbridge.tf

###############################################################################
# KMS CMK: alias/mesh-central
# Used for: DynamoDB SSE, SQS encryption, Terraform state bucket
###############################################################################
resource "aws_kms_key" "mesh_central" {
  description             = "Central KMS key for Data Meshy governance account (DynamoDB, SQS, Terraform state)."
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Id      = "mesh-central-key-policy"
    Statement = [
      # Root account full control (required by KMS)
      {
        Sid    = "RootFullControl"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      # MeshAdminRole: full key management
      {
        Sid    = "MeshAdminFullAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/MeshAdminRole"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      # MeshCatalogWriterRole: encrypt/decrypt for DynamoDB writes
      {
        Sid    = "CatalogWriterEncryptDecrypt"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/MeshCatalogWriterRole"
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = "*"
      },
      # MeshAuditWriterRole: encrypt/decrypt for audit log writes
      {
        Sid    = "AuditWriterEncryptDecrypt"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/MeshAuditWriterRole"
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = "*"
      },
      # Lambda execution roles: decrypt for audit log reads
      {
        Sid    = "LambdaDecryptForAuditReads"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = data.aws_caller_identity.current.account_id
          }
        }
      },
      # DynamoDB service principal
      {
        Sid    = "DynamoDBServicePrincipal"
        Effect = "Allow"
        Principal = {
          Service = "dynamodb.amazonaws.com"
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = data.aws_caller_identity.current.account_id
          }
        }
      },
      # SQS service principal
      {
        Sid    = "SQSServicePrincipal"
        Effect = "Allow"
        Principal = {
          Service = "sqs.amazonaws.com"
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })

  tags = merge(local.mandatory_tags, {
    Name = "mesh-central-kms"
  })
}

resource "aws_kms_alias" "mesh_central" {
  name          = "alias/mesh-central"
  target_key_id = aws_kms_key.mesh_central.key_id
}

###############################################################################
# SNS Topics
###############################################################################
resource "aws_sns_topic" "mesh_quality_alerts" {
  name              = "mesh-quality-alerts"
  kms_master_key_id = aws_kms_key.mesh_central.arn

  tags = merge(local.mandatory_tags, {
    Name    = "mesh-quality-alerts"
    Purpose = "Alerts when a data product's quality score drops below threshold"
  })
}

resource "aws_sns_topic" "mesh_pipeline_failures" {
  name              = "mesh-pipeline-failures"
  kms_master_key_id = aws_kms_key.mesh_central.arn

  tags = merge(local.mandatory_tags, {
    Name    = "mesh-pipeline-failures"
    Purpose = "Alerts on medallion pipeline execution failures"
  })
}

resource "aws_sns_topic" "mesh_freshness_violations" {
  name              = "mesh-freshness-violations"
  kms_master_key_id = aws_kms_key.mesh_central.arn

  tags = merge(local.mandatory_tags, {
    Name    = "mesh-freshness-violations"
    Purpose = "Alerts when a data product exceeds its SLA refresh window"
  })
}

resource "aws_sns_topic" "mesh_subscription_requests" {
  name              = "mesh-subscription-requests"
  kms_master_key_id = aws_kms_key.mesh_central.arn

  tags = merge(local.mandatory_tags, {
    Name    = "mesh-subscription-requests"
    Purpose = "Notifications for new data product subscription requests"
  })
}

###############################################################################
# Optional SNS email subscriptions (only created if alert_email is set)
###############################################################################
resource "aws_sns_topic_subscription" "quality_alerts_email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.mesh_quality_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sns_topic_subscription" "pipeline_failures_email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.mesh_pipeline_failures.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

###############################################################################
# SNS topic policies — allow EventBridge to publish
###############################################################################
resource "aws_sns_topic_policy" "mesh_quality_alerts" {
  arn = aws_sns_topic.mesh_quality_alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEventBridgePublish"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.mesh_quality_alerts.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_cloudwatch_event_bus.mesh_central.arn
          }
        }
      }
    ]
  })
}

resource "aws_sns_topic_policy" "mesh_pipeline_failures" {
  arn = aws_sns_topic.mesh_pipeline_failures.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEventBridgeAndCWAlarms"
        Effect = "Allow"
        Principal = {
          Service = [
            "events.amazonaws.com",
            "cloudwatch.amazonaws.com"
          ]
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.mesh_pipeline_failures.arn
      }
    ]
  })
}
