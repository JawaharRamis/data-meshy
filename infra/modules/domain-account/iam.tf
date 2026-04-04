##############################################################################
# domain-account/iam.tf
#
# IAM roles per domain:
#   - DomainAdminRole         — full domain access, trust=SSO
#   - DomainDataEngineerRole  — S3+Glue access, trust=SSO, permission boundary
#   - DomainConsumerRole      — LF read-only, trust=SSO
#   - GlueJobExecutionRole    — ETL service role, trust=glue, permission boundary
#   - MeshEventRole           — PutEvents on central bus, trust=lambda+states
##############################################################################

##############################################################################
# Permission Boundary: restrict S3 access to own domain buckets only
# Applied to GlueJobExecutionRole and DomainDataEngineerRole.
##############################################################################

resource "aws_iam_policy" "domain_s3_boundary" {
  name        = "${var.domain}-DomainS3PermissionBoundary"
  description = "Permission boundary restricting S3 access to ${var.domain} domain buckets only."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowDomainBucketsOnly"
        Effect = "Allow"
        Action = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}",
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}/*",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}/*",
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}",
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}/*"
        ]
      },
      # Allow all non-S3 actions (so boundary only limits S3 scope)
      {
        Sid      = "AllowNonS3Services"
        Effect   = "Allow"
        NotAction = "s3:*"
        Resource = "*"
      }
    ]
  })

  tags = local.tags
}

##############################################################################
# DomainAdminRole
# Full domain resource access. Cannot modify LF permissions.
# Trust: SAML/SSO (generic SSO trust — actual federation configured in IAM IC)
##############################################################################

resource "aws_iam_role" "domain_admin" {
  name        = "DomainAdminRole"
  description = "Full domain resource access for ${var.domain} domain admins. Trust: SSO."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSSOAssumeRole"
        Effect = "Allow"
        Principal = {
          Federated = "arn:aws:iam::${local.account_id}:saml-provider/AWSSSO"
        }
        Action = "sts:AssumeRoleWithSAML"
        Condition = {
          StringEquals = {
            "SAML:aud" = "https://signin.aws.amazon.com/saml"
          }
        }
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "domain_admin_policy" {
  name = "${var.domain}-DomainAdminPolicy"
  role = aws_iam_role.domain_admin.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "FullDomainS3Access"
        Effect = "Allow"
        Action = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}",
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}/*",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}/*",
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}",
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}/*"
        ]
      },
      {
        Sid    = "GlueCatalogReadWrite"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase*",
          "glue:GetTable*",
          "glue:CreateTable",
          "glue:UpdateTable",
          "glue:DeleteTable",
          "glue:GetJob*",
          "glue:CreateJob",
          "glue:UpdateJob",
          "glue:StartJobRun",
          "glue:GetCrawler*",
          "glue:CreateCrawler",
          "glue:StartCrawler",
          "glue:GetDataQualityRuleset*",
          "glue:CreateDataQualityRuleset"
        ]
        Resource = "*"
        Condition = {
          StringEquals = { "glue:database" = ["${var.domain}_raw", "${var.domain}_silver", "${var.domain}_gold"] }
        }
      },
      {
        Sid    = "GlueJobAndCrawlerNoCondition"
        Effect = "Allow"
        Action = [
          "glue:GetJob*",
          "glue:CreateJob",
          "glue:UpdateJob",
          "glue:StartJobRun",
          "glue:ListJobs"
        ]
        Resource = "*"
      },
      {
        Sid    = "StepFunctionsReadWrite"
        Effect = "Allow"
        Action = [
          "states:Describe*",
          "states:List*",
          "states:Start*",
          "states:Stop*",
          "states:Get*"
        ]
        Resource = "arn:aws:states:${local.region}:${local.account_id}:stateMachine:${var.domain}-*"
      },
      {
        Sid    = "SecretsManagerReadDomain"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
          "secretsmanager:ListSecretVersionIds"
        ]
        Resource = "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:${var.domain}/*"
      },
      {
        Sid    = "KMSUseDomainKey"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = aws_kms_key.domain.arn
      },
      # Explicitly DENY LF permission management (admin cannot change grants)
      {
        Sid    = "DenyLakeFormationPermissionManagement"
        Effect = "Deny"
        Action = [
          "lakeformation:GrantPermissions",
          "lakeformation:RevokePermissions",
          "lakeformation:BatchGrantPermissions",
          "lakeformation:BatchRevokePermissions"
        ]
        Resource = "*"
      }
    ]
  })
}

##############################################################################
# DomainDataEngineerRole
# Read/write S3 (own buckets), create/run Glue jobs, read catalog.
# Cannot modify LF permissions.
# Trust: SSO | Permission boundary: s3:* restricted to own domain buckets.
##############################################################################

resource "aws_iam_role" "domain_data_engineer" {
  name                 = "DomainDataEngineerRole"
  description          = "Data engineer role for ${var.domain} domain. Trust: SSO."
  permissions_boundary = aws_iam_policy.domain_s3_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSSOAssumeRole"
        Effect = "Allow"
        Principal = {
          Federated = "arn:aws:iam::${local.account_id}:saml-provider/AWSSSO"
        }
        Action = "sts:AssumeRoleWithSAML"
        Condition = {
          StringEquals = {
            "SAML:aud" = "https://signin.aws.amazon.com/saml"
          }
        }
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "domain_data_engineer_policy" {
  name = "${var.domain}-DomainDataEngineerPolicy"
  role = aws_iam_role.domain_data_engineer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3DomainBuckets"
        Effect = "Allow"
        Action = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}",
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}/*",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}/*",
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}",
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}/*"
        ]
      },
      {
        Sid    = "GlueJobsAndCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase*",
          "glue:GetTable*",
          "glue:CreateTable",
          "glue:UpdateTable",
          "glue:GetJob*",
          "glue:CreateJob",
          "glue:UpdateJob",
          "glue:StartJobRun",
          "glue:BatchStopJobRun",
          "glue:GetJobRun*",
          "glue:ListJobs",
          "glue:GetDataQualityRuleset*",
          "glue:CreateDataQualityRuleset",
          "glue:StartDataQualityRulesetEvaluationRun"
        ]
        Resource = "*"
      },
      {
        Sid    = "StepFunctionsRead"
        Effect = "Allow"
        Action = [
          "states:DescribeStateMachine",
          "states:DescribeExecution",
          "states:ListExecutions",
          "states:GetExecutionHistory",
          "states:StartExecution"
        ]
        Resource = "arn:aws:states:${local.region}:${local.account_id}:stateMachine:${var.domain}-*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:GetLogEvents",
          "logs:DescribeLogGroups"
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/data-meshy/${var.domain}/*"
      },
      {
        Sid    = "KMSUseDomainKey"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = aws_kms_key.domain.arn
      }
    ]
  })
}

##############################################################################
# DomainConsumerRole
# Read-only on subscribed tables via LF grants. Athena query access.
# Trust: SSO.
##############################################################################

resource "aws_iam_role" "domain_consumer" {
  name        = "DomainConsumerRole"
  description = "Read-only consumer role for ${var.domain} domain. Trust: SSO."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSSOAssumeRole"
        Effect = "Allow"
        Principal = {
          Federated = "arn:aws:iam::${local.account_id}:saml-provider/AWSSSO"
        }
        Action = "sts:AssumeRoleWithSAML"
        Condition = {
          StringEquals = {
            "SAML:aud" = "https://signin.aws.amazon.com/saml"
          }
        }
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "domain_consumer_policy" {
  name = "${var.domain}-DomainConsumerPolicy"
  role = aws_iam_role.domain_consumer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AthenaQueryAccess"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
          "athena:ListWorkGroups",
          "athena:GetWorkGroup",
          "athena:ListQueryExecutions",
          "athena:BatchGetQueryExecution"
        ]
        Resource = "arn:aws:athena:${local.region}:${local.account_id}:workgroup/*"
      },
      {
        Sid    = "AthenaResultsBucket"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:GetBucketLocation",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::aws-athena-query-results-${local.account_id}-${local.region}",
          "arn:aws:s3:::aws-athena-query-results-${local.account_id}-${local.region}/*"
        ]
      },
      {
        Sid    = "GlueCatalogRead"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase*",
          "glue:GetTable*",
          "glue:GetPartition*",
          "glue:SearchTables"
        ]
        Resource = "*"
      },
      {
        Sid    = "KMSDecryptForConsumer"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey",
          "kms:GenerateDataKey"
        ]
        Resource = aws_kms_key.domain.arn
      }
    ]
  })
}

##############################################################################
# GlueJobExecutionRole
# Trust: glue.amazonaws.com service only.
# Permission boundary: S3 restricted to own domain buckets.
# Grants: S3 R/W for all 3 layers, Glue catalog, Secrets Manager (domain-scoped),
#         KMS decrypt/generate on domain key.
##############################################################################

resource "aws_iam_role" "glue_job_execution" {
  name                 = "GlueJobExecutionRole"
  description          = "Service role for Glue ETL jobs in ${var.domain} domain."
  permissions_boundary = aws_iam_policy.domain_s3_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "GlueServiceAssume"
        Effect = "Allow"
        Principal = {
          Service = "glue.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "glue_job_execution_policy" {
  name = "${var.domain}-GlueJobExecutionPolicy"
  role = aws_iam_role.glue_job_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3DomainBuckets"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts"
        ]
        Resource = [
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}",
          "arn:aws:s3:::${var.domain}-raw-${local.account_id}/*",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}",
          "arn:aws:s3:::${var.domain}-silver-${local.account_id}/*",
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}",
          "arn:aws:s3:::${var.domain}-gold-${local.account_id}/*"
        ]
      },
      {
        Sid    = "GlueCatalogAccess"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase*",
          "glue:GetTable*",
          "glue:CreateTable",
          "glue:UpdateTable",
          "glue:GetPartition*",
          "glue:BatchCreatePartition",
          "glue:BatchDeletePartition",
          "glue:UpdatePartition",
          "glue:GetUserDefinedFunction*",
          "glue:GetDataQualityRuleset*",
          "glue:StartDataQualityRulesetEvaluationRun",
          "glue:GetDataQualityRulesetEvaluationRun",
          "glue:ListDataQualityRulesets"
        ]
        Resource = "*"
      },
      {
        Sid    = "SecretsManagerDomainScoped"
        Effect = "Allow"
        Action = "secretsmanager:GetSecretValue"
        Resource = "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:${var.domain}/*"
      },
      {
        Sid    = "KMSDomainKey"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey"
        ]
        Resource = aws_kms_key.domain.arn
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/glue/*"
      },
      {
        Sid    = "LakeFormationGetDataAccess"
        Effect = "Allow"
        Action = "lakeformation:GetDataAccess"
        Resource = "*"
      }
    ]
  })
}

##############################################################################
# MeshEventRole
# Trust: lambda.amazonaws.com and states.amazonaws.com.
# Grants: events:PutEvents on central event bus ONLY.
# Condition: source CANNOT be datameshy.central (reserved for central SFN).
##############################################################################

resource "aws_iam_role" "mesh_event" {
  name        = "MeshEventRole"
  description = "PutEvents on central EventBridge bus for ${var.domain} domain. Trust: Lambda + Step Functions."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "LambdaAssume"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      },
      {
        Sid    = "StepFunctionsAssume"
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "mesh_event_policy" {
  name = "${var.domain}-MeshEventPolicy"
  role = aws_iam_role.mesh_event.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Allow PutEvents only on the central event bus
      {
        Sid    = "PutEventsToCentralBus"
        Effect = "Allow"
        Action = "events:PutEvents"
        Resource = var.central_event_bus_arn
      },
      # Deny PutEvents with source = datameshy.central (reserved for central SFN)
      {
        Sid    = "DenyReservedCentralSource"
        Effect = "Deny"
        Action = "events:PutEvents"
        Resource = var.central_event_bus_arn
        Condition = {
          StringEquals = {
            "events:source" = "datameshy.central"
          }
        }
      },
      # Allow PutEvents on domain bus as well (for local events)
      {
        Sid    = "PutEventsToDomainBus"
        Effect = "Allow"
        Action = "events:PutEvents"
        Resource = "arn:aws:events:${local.region}:${local.account_id}:event-bus/mesh-domain-bus"
      },
      # CloudWatch Logs for Lambda execution
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/*"
      },
      # Step Functions — allow Glue job start (needed for medallion pipeline SM)
      {
        Sid    = "GlueStartJobRun"
        Effect = "Allow"
        Action = [
          "glue:StartJobRun",
          "glue:GetJobRun",
          "glue:BatchStopJobRun"
        ]
        Resource = "arn:aws:glue:${local.region}:${local.account_id}:job/*"
      },
      # CloudWatch Logs for Step Functions execution logs
      {
        Sid    = "SFNCloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      },
      # X-Ray tracing
      {
        Sid    = "XRayAccess"
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
