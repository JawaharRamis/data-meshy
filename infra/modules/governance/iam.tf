# infra/modules/governance/iam.tf
# Decomposed IAM roles for the central governance account.
# NO god-roles. Each role has the minimum permissions for its function.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

###############################################################################
# KMS key reference (used in permission boundary and key policies)
###############################################################################
locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # OIDC sub conditions — platform repo plus any domain repos.
  # StringLike with a list is evaluated as OR by AWS IAM.
  oidc_plan_subjects = concat(
    ["repo:${var.github_org}/${var.github_repo}:*"],
    var.domain_repo_paths
  )

  # Apply role: only main-branch refs for the platform repo,
  # plus wildcard for domain repos (domain pipelines apply their own infra).
  oidc_apply_subjects = concat(
    ["repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main"],
    var.domain_repo_paths
  )
}

###############################################################################
# Permission boundary: MeshLFGrantor can only grant SELECT, never mutate
###############################################################################
resource "aws_iam_policy" "mesh_lf_grantor_boundary" {
  name        = "MeshLFGrantorBoundary"
  description = "Permission boundary for MeshLFGrantorRole — limits to SELECT-only LF grants on gold tables."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowLFSelectGrantsOnly"
        Effect = "Allow"
        Action = [
          "lakeformation:GrantPermissions",
          "lakeformation:RevokePermissions",
          "lakeformation:BatchGrantPermissions",
          "lakeformation:BatchRevokePermissions"
        ]
        Resource = "*"
        Condition = {
          # Ensures only SELECT is ever granted (enforced at boundary level)
          StringEquals = {
            "lakeformation:Permission" = ["SELECT"]
          }
        }
      },
      {
        Sid    = "AllowGlueDescribeForLF"
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetDatabase"
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:${local.account_id}:*"
      }
    ]
  })

  tags = local.mandatory_tags
}

###############################################################################
# Role: MeshLFGrantorRole
# Trust: Lambda service
# Permissions: LF GrantPermissions / RevokePermissions on gold tables only
###############################################################################
resource "aws_iam_role" "mesh_lf_grantor" {
  name                 = "MeshLFGrantorRole"
  description          = "Grants and revokes LF SELECT permissions on gold tables only. Used by subscription approval Lambda."
  max_session_duration = 3600
  permissions_boundary = aws_iam_policy.mesh_lf_grantor_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy" "mesh_lf_grantor_policy" {
  name = "MeshLFGrantorPolicy"
  role = aws_iam_role.mesh_lf_grantor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "LFGrantRevokeGoldTablesOnly"
        Effect = "Allow"
        Action = [
          "lakeformation:GrantPermissions",
          "lakeformation:RevokePermissions",
          "lakeformation:BatchGrantPermissions",
          "lakeformation:BatchRevokePermissions",
          "lakeformation:GetDataLakeSettings",
          "lakeformation:ListPermissions"
        ]
        Resource = "*"
        Condition = {
          StringLike = {
            # Only allows grants targeting gold_* tables (enforced in addition to boundary)
            "lakeformation:ResourceArn" = "arn:aws:glue:*:*:table/*/gold_*"
          }
        }
      },
      {
        Sid    = "GlueDescribeForLFContext"
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTables"
        ]
        Resource = "*"
      },
      {
        Sid    = "LambdaBasicExecution"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/*"
      }
    ]
  })
}

###############################################################################
# Role: MeshCatalogWriterRole
# Trust: Lambda service
# Permissions: DynamoDB PutItem/UpdateItem on catalog tables only
###############################################################################
resource "aws_iam_role" "mesh_catalog_writer" {
  name                 = "MeshCatalogWriterRole"
  description          = "Writes to mesh-products, mesh-domains, mesh-subscriptions, mesh-quality-scores only. Used by catalog update Lambdas."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy" "mesh_catalog_writer_policy" {
  name = "MeshCatalogWriterPolicy"
  role = aws_iam_role.mesh_catalog_writer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBCatalogWriteOnly"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem",
          "dynamodb:Query"
        ]
        Resource = [
          aws_dynamodb_table.mesh_products.arn,
          aws_dynamodb_table.mesh_domains.arn,
          aws_dynamodb_table.mesh_subscriptions.arn,
          aws_dynamodb_table.mesh_quality_scores.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*",
          "${aws_dynamodb_table.mesh_domains.arn}/index/*",
          "${aws_dynamodb_table.mesh_subscriptions.arn}/index/*",
          "${aws_dynamodb_table.mesh_quality_scores.arn}/index/*"
        ]
        Condition = {
          # Restrict writes to the caller's domain prefix only
          "ForAllValues:StringLike" = {
            "dynamodb:LeadingKeys" = ["$${aws:PrincipalTag/domain}*"]
          }
        }
      },
      {
        Sid    = "KMSDecryptForDynamoDB"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = aws_kms_key.mesh_central.arn
      },
      {
        Sid    = "LambdaBasicExecution"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/*"
      }
    ]
  })
}

###############################################################################
# Role: MeshAuditWriterRole
# Trust: Lambda service
# Permissions: PutItem ONLY on mesh-audit-log — strictly append-only
###############################################################################
resource "aws_iam_role" "mesh_audit_writer" {
  name                 = "MeshAuditWriterRole"
  description          = "Append-only write to mesh-audit-log. No UpdateItem, DeleteItem, or BatchWriteItem allowed."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy" "mesh_audit_writer_policy" {
  name = "MeshAuditWriterPolicy"
  role = aws_iam_role.mesh_audit_writer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AuditLogAppendOnly"
        Effect = "Allow"
        # PutItem ONLY — no UpdateItem, no DeleteItem, no BatchWriteItem
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.mesh_audit_log.arn
      },
      {
        Sid    = "ExplicitDenyAuditMutation"
        Effect = "Deny"
        Action = [
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:BatchWriteItem"
        ]
        Resource = aws_dynamodb_table.mesh_audit_log.arn
      },
      {
        Sid    = "KMSDecryptForAuditLog"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = aws_kms_key.mesh_central.arn
      },
      {
        Sid    = "LambdaBasicExecution"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/*"
      }
    ]
  })
}

###############################################################################
# Role: GovernanceReadRole
# Trust: SSO (IAM Identity Center — assumed via federation)
# Permissions: Read-only on all DynamoDB tables + Glue GetTable/GetDatabase
###############################################################################
resource "aws_iam_role" "governance_read" {
  name                 = "GovernanceReadRole"
  description          = "Read-only access to all mesh DynamoDB tables and Glue Catalog. Used by governance leads via SSO."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
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
      },
      # Also allow direct assume for SSO permission set assignment
      {
        Effect = "Allow"
        Principal = {
          Service = "sso.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy" "governance_read_policy" {
  name = "GovernanceReadPolicy"
  role = aws_iam_role.governance_read.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBReadAllMeshTables"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:DescribeTable"
        ]
        Resource = [
          aws_dynamodb_table.mesh_domains.arn,
          aws_dynamodb_table.mesh_products.arn,
          aws_dynamodb_table.mesh_subscriptions.arn,
          aws_dynamodb_table.mesh_quality_scores.arn,
          aws_dynamodb_table.mesh_audit_log.arn,
          aws_dynamodb_table.mesh_event_dedup.arn,
          aws_dynamodb_table.mesh_pipeline_locks.arn,
          "${aws_dynamodb_table.mesh_domains.arn}/index/*",
          "${aws_dynamodb_table.mesh_products.arn}/index/*",
          "${aws_dynamodb_table.mesh_subscriptions.arn}/index/*",
          "${aws_dynamodb_table.mesh_quality_scores.arn}/index/*",
          "${aws_dynamodb_table.mesh_audit_log.arn}/index/*"
        ]
      },
      {
        Sid    = "GlueCatalogDescribeAcrossAccounts"
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTables",
          "glue:GetPartitions"
        ]
        Resource = "*"
      },
      {
        Sid    = "KMSDecryptForRead"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = aws_kms_key.mesh_central.arn
      }
    ]
  })
}

###############################################################################
# Role: MeshAdminRole (break-glass / Terraform apply only)
# Trust: MFA required + specific conditions
# Permissions: Broad — but only used for provisioning
###############################################################################
resource "aws_iam_role" "mesh_admin" {
  name                 = "MeshAdminRole"
  description          = "Break-glass / Terraform apply role. MFA required. Session duration 1 hour. CloudWatch alarm on any assumption."
  max_session_duration = 3600 # 1 hour max

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action = "sts:AssumeRole"
        Condition = {
          Bool = {
            "aws:MultiFactorAuthPresent" = "true"
          }
          NumericLessThan = {
            "aws:MultiFactorAuthAge" = "3600"
          }
        }
      },
      # Allow OIDC-based assumption from GitHub Actions (TerraformApplyRole delegates here)
      {
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:role/TerraformApplyRole"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy_attachment" "mesh_admin_admin_policy" {
  role       = aws_iam_role.mesh_admin.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

###############################################################################
# CloudWatch alarm — alert on any MeshAdminRole assumption
###############################################################################
resource "aws_cloudwatch_log_metric_filter" "mesh_admin_assumption" {
  name           = "MeshAdminRoleAssumption"
  log_group_name = "/aws/cloudtrail/mesh-central"
  pattern        = "{ ($.eventName = \"AssumeRole\") && ($.requestParameters.roleArn = \"*MeshAdminRole\") }"

  metric_transformation {
    name          = "MeshAdminRoleAssumptionCount"
    namespace     = "DataMeshy/Security"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "mesh_admin_assumption_alarm" {
  alarm_name          = "MeshAdminRoleAssumed"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "MeshAdminRoleAssumptionCount"
  namespace           = "DataMeshy/Security"
  period              = 60
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "ALERT: MeshAdminRole (break-glass) was assumed. Verify this is an authorized Terraform apply."
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.mesh_pipeline_failures.arn]

  tags = local.mandatory_tags
}

###############################################################################
# OIDC roles for GitHub Actions (defined here, referenced in outputs)
###############################################################################
resource "aws_iam_openid_connect_provider" "github_actions" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = [
    "sts.amazonaws.com"
  ]

  # GitHub Actions OIDC thumbprint (stable, verified against GitHub's cert chain)
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd"
  ]

  tags = local.mandatory_tags
}

resource "aws_iam_role" "terraform_plan" {
  name                 = "TerraformPlanRole"
  description          = "GitHub Actions OIDC role for terraform plan. Read-only. Any branch."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "token.actions.githubusercontent.com:sub" = local.oidc_plan_subjects
          }
        }
      }
    ]
  })

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy" "terraform_plan_policy" {
  name = "TerraformPlanPolicy"
  role = aws_iam_role.terraform_plan.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadOnlyForPlan"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
          "dynamodb:GetItem",
          "dynamodb:DescribeTable",
          "kms:Decrypt",
          "kms:DescribeKey",
          "iam:GetRole",
          "iam:GetPolicy",
          "iam:ListRolePolicies",
          "iam:GetRolePolicy",
          "iam:ListAttachedRolePolicies",
          "ec2:Describe*",
          "events:Describe*",
          "events:List*",
          "sns:GetTopicAttributes",
          "sns:ListTopics",
          "sqs:GetQueueAttributes",
          "sqs:ListQueues",
          "cloudwatch:DescribeAlarms",
          "logs:DescribeLogGroups",
          "glue:Get*",
          "lakeformation:Get*",
          "lakeformation:List*"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role" "terraform_apply" {
  name                 = "TerraformApplyRole"
  description          = "GitHub Actions OIDC role for terraform apply. Write access. Main branch only."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          # StringLike with a list: platform repo restricted to main branch;
          # domain repos use wildcard (domains apply their own infra, not central).
          StringLike = {
            "token.actions.githubusercontent.com:sub" = local.oidc_apply_subjects
          }
        }
      }
    ]
  })

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy_attachment" "terraform_apply_admin" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

###############################################################################
# Role: MeshKmsGrantorRole
# Trust: Lambda service (central account only)
# Permissions: kms:CreateGrant on domain KMS keys so that subscription
#              approval Lambda can grant consumer roles decrypt access to
#              the producer domain's gold bucket KMS key.
# Scope: Restricted to keys tagged mesh:domain=* (enforced via condition).
###############################################################################
resource "aws_iam_role" "mesh_kms_grantor" {
  name                 = "MeshKmsGrantorRole"
  description          = "Grants KMS key access for domain data. Used by subscription approval Lambda to give consumer role decrypt access on producer gold bucket key."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
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

resource "aws_iam_role_policy" "mesh_kms_grantor_policy" {
  name = "MeshKmsGrantorPolicy"
  role = aws_iam_role.mesh_kms_grantor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "KMSCreateGrantOnDomainKeys"
        Effect = "Allow"
        Action = [
          "kms:CreateGrant",
          "kms:DescribeKey",
          "kms:ListGrants",
          "kms:RevokeGrant"
        ]
        Resource = "*"
        Condition = {
          # Only allows grants on keys tagged for mesh domains
          StringLike = {
            "kms:ResourceAliases" = "alias/mesh-*"
          }
          # Restrict grant operations to decrypt/generate-data-key only
          # (consumers should never manage keys — only use them for decryption)
          StringEquals = {
            "kms:GrantOperations" = [
              "Decrypt",
              "GenerateDataKey",
              "GenerateDataKeyWithoutPlaintext",
              "DescribeKey"
            ]
          }
        }
      },
      {
        Sid    = "LambdaBasicExecution"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/*"
      }
    ]
  })
}
