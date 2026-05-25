# infra/modules/governance/lambdas.tf
#
# All 14 Lambda function resources for the Data Meshy governance plane.
# Each Lambda has:
#   - archive_file data source (zips from lambdas/)
#   - aws_lambda_function (Python 3.12, 256 MB, 30s timeout)
#   - aws_iam_role execution role with least-privilege inline policy
#   - aws_cloudwatch_log_group (14-day retention)
#   - aws_lambda_permission for EventBridge / API GW triggers where applicable
#
# Groups:
#   1. Catalog Lambdas      — catalog_writer, catalog_search, catalog_browse, catalog_describe
#   2. Subscription Lambdas — subscription_request, subscription_provisioner, subscription_compensator
#   3. Event handler        — audit_writer, event_validator, freshness_monitor
#   4. Lifecycle            — product_deprecation, retirement
#   5. Integration stub     — datazone_connector

###############################################################################
# Shared trust policy for Lambda execution roles
###############################################################################
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

###############################################################################
# ── Section 1: Catalog Lambdas ───────────────────────────────────────────────
###############################################################################

# ── catalog_writer ────────────────────────────────────────────────────────────

locals {
  # catalog_writer depends on event_validator — bundle both into one zip.
  # The build script copies both source files into a staging directory so that
  # archive_file.source_dir captures them together.
  catalog_writer_sources = {
    "catalog_writer.py"  = "${path.root}/../../../lambdas/catalog_writer.py"
    "event_validator.py" = "${path.root}/../../../lambdas/event_validator.py"
  }
}

data "archive_file" "catalog_writer" {
  type        = "zip"
  output_path = "${path.module}/.build/catalog_writer.zip"

  dynamic "source" {
    for_each = local.catalog_writer_sources
    content {
      filename = source.key
      content  = file(source.value)
    }
  }
}

resource "aws_cloudwatch_log_group" "catalog_writer" {
  name              = "/aws/lambda/mesh-catalog-writer"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "catalog_writer_lambda" {
  name               = "MeshCatalogWriterLambdaRole"
  description        = "Execution role for catalog_writer Lambda."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "catalog_writer_lambda" {
  name = "MeshCatalogWriterLambdaPolicy"
  role = aws_iam_role.catalog_writer_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBWrite"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem",
          "dynamodb:Query"
        ]
        Resource = [
          aws_dynamodb_table.mesh_products.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*",
          aws_dynamodb_table.mesh_domains.arn,
          "${aws_dynamodb_table.mesh_domains.arn}/index/*",
          aws_dynamodb_table.mesh_subscriptions.arn,
          "${aws_dynamodb_table.mesh_subscriptions.arn}/index/*",
          aws_dynamodb_table.mesh_quality_scores.arn,
          "${aws_dynamodb_table.mesh_quality_scores.arn}/index/*",
          aws_dynamodb_table.mesh_event_dedup.arn
        ]
      },
      {
        Sid    = "EventBridgePutEvents"
        Effect = "Allow"
        Action = ["events:PutEvents"]
        Resource = [aws_cloudwatch_event_bus.mesh_central.arn]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-catalog-writer:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "catalog_writer" {
  function_name    = "mesh-catalog-writer"
  description      = "Handles ProductCreated and ProductRefreshed events — writes to mesh-products and mesh-quality-scores."
  filename         = data.archive_file.catalog_writer.output_path
  source_code_hash = data.archive_file.catalog_writer.output_base64sha256
  handler          = "catalog_writer.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.catalog_writer_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_PRODUCTS_TABLE      = aws_dynamodb_table.mesh_products.name
      MESH_DOMAINS_TABLE       = aws_dynamodb_table.mesh_domains.name
      MESH_SUBSCRIPTIONS_TABLE = aws_dynamodb_table.mesh_subscriptions.name
      MESH_EVENT_DEDUP_TABLE   = aws_dynamodb_table.mesh_event_dedup.name
      CENTRAL_EVENT_BUS_NAME   = aws_cloudwatch_event_bus.mesh_central.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.catalog_writer]

  tags = local.mandatory_tags
}

# Allow EventBridge to invoke catalog_writer
resource "aws_lambda_permission" "eventbridge_catalog_writer" {
  statement_id  = "AllowEventBridgeInvokeCatalogWriter"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.catalog_writer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.catalog_update.arn
}

# ── catalog_search ────────────────────────────────────────────────────────────

data "archive_file" "catalog_search" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/catalog_search.py"
  output_path = "${path.module}/.build/catalog_search.zip"
}

resource "aws_cloudwatch_log_group" "catalog_search" {
  name              = "/aws/lambda/mesh-catalog-search"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "catalog_search_lambda" {
  name               = "MeshCatalogSearchLambdaRole"
  description        = "Execution role for catalog_search Lambda."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "catalog_search_lambda" {
  name = "MeshCatalogSearchLambdaPolicy"
  role = aws_iam_role.catalog_search_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBRead"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
        Resource = [
          aws_dynamodb_table.mesh_products.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*",
          aws_dynamodb_table.mesh_domains.arn,
          "${aws_dynamodb_table.mesh_domains.arn}/index/*"
        ]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-catalog-search:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "catalog_search" {
  function_name    = "mesh-catalog-search"
  description      = "Search products by keyword, domain, tag, and classification."
  filename         = data.archive_file.catalog_search.output_path
  source_code_hash = data.archive_file.catalog_search.output_base64sha256
  handler          = "catalog_search.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.catalog_search_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_PRODUCTS_TABLE = aws_dynamodb_table.mesh_products.name
      MESH_DOMAINS_TABLE  = aws_dynamodb_table.mesh_domains.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.catalog_search]

  tags = local.mandatory_tags
}

# ── catalog_browse ────────────────────────────────────────────────────────────

data "archive_file" "catalog_browse" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/catalog_browse.py"
  output_path = "${path.module}/.build/catalog_browse.zip"
}

resource "aws_cloudwatch_log_group" "catalog_browse" {
  name              = "/aws/lambda/mesh-catalog-browse"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "catalog_browse_lambda" {
  name               = "MeshCatalogBrowseLambdaRole"
  description        = "Execution role for catalog_browse Lambda."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "catalog_browse_lambda" {
  name = "MeshCatalogBrowseLambdaPolicy"
  role = aws_iam_role.catalog_browse_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBRead"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
        Resource = [
          aws_dynamodb_table.mesh_products.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*",
          aws_dynamodb_table.mesh_domains.arn,
          "${aws_dynamodb_table.mesh_domains.arn}/index/*"
        ]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-catalog-browse:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "catalog_browse" {
  function_name    = "mesh-catalog-browse"
  description      = "Browse all products grouped by domain."
  filename         = data.archive_file.catalog_browse.output_path
  source_code_hash = data.archive_file.catalog_browse.output_base64sha256
  handler          = "catalog_browse.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.catalog_browse_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_PRODUCTS_TABLE = aws_dynamodb_table.mesh_products.name
      MESH_DOMAINS_TABLE  = aws_dynamodb_table.mesh_domains.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.catalog_browse]

  tags = local.mandatory_tags
}

# ── catalog_describe ──────────────────────────────────────────────────────────

data "archive_file" "catalog_describe" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/catalog_describe.py"
  output_path = "${path.module}/.build/catalog_describe.zip"
}

resource "aws_cloudwatch_log_group" "catalog_describe" {
  name              = "/aws/lambda/mesh-catalog-describe"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "catalog_describe_lambda" {
  name               = "MeshCatalogDescribeLambdaRole"
  description        = "Execution role for catalog_describe Lambda."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "catalog_describe_lambda" {
  name = "MeshCatalogDescribeLambdaPolicy"
  role = aws_iam_role.catalog_describe_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBRead"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.mesh_products.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*",
          aws_dynamodb_table.mesh_domains.arn
        ]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-catalog-describe:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "catalog_describe" {
  function_name    = "mesh-catalog-describe"
  description      = "Describe a specific data product by domain and product name."
  filename         = data.archive_file.catalog_describe.output_path
  source_code_hash = data.archive_file.catalog_describe.output_base64sha256
  handler          = "catalog_describe.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.catalog_describe_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_PRODUCTS_TABLE = aws_dynamodb_table.mesh_products.name
      MESH_DOMAINS_TABLE  = aws_dynamodb_table.mesh_domains.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.catalog_describe]

  tags = local.mandatory_tags
}

###############################################################################
# ── Section 2: Subscription Lambdas ─────────────────────────────────────────
###############################################################################

# ── subscription_request ──────────────────────────────────────────────────────

data "archive_file" "subscription_request" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/subscription_request.py"
  output_path = "${path.module}/.build/subscription_request.zip"
}

resource "aws_cloudwatch_log_group" "subscription_request" {
  name              = "/aws/lambda/mesh-subscription-request"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "subscription_request_lambda" {
  name               = "MeshSubscriptionRequestLambdaRole"
  description        = "Execution role for subscription_request Lambda."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "subscription_request_lambda" {
  name = "MeshSubscriptionRequestLambdaPolicy"
  role = aws_iam_role.subscription_request_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBWrite"
        Effect = "Allow"
        Action = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.mesh_subscriptions.arn,
          "${aws_dynamodb_table.mesh_subscriptions.arn}/index/*",
          aws_dynamodb_table.mesh_products.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*"
        ]
      },
      {
        Sid    = "EventBridgePutEvents"
        Effect = "Allow"
        Action = ["events:PutEvents"]
        Resource = [aws_cloudwatch_event_bus.mesh_central.arn]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-subscription-request:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "subscription_request" {
  function_name    = "mesh-subscription-request"
  description      = "Creates a new subscription request and emits SubscriptionRequested event."
  filename         = data.archive_file.subscription_request.output_path
  source_code_hash = data.archive_file.subscription_request.output_base64sha256
  handler          = "subscription_request.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.subscription_request_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_PRODUCTS_TABLE      = aws_dynamodb_table.mesh_products.name
      MESH_DOMAINS_TABLE       = aws_dynamodb_table.mesh_domains.name
      MESH_SUBSCRIPTIONS_TABLE = aws_dynamodb_table.mesh_subscriptions.name
      CENTRAL_EVENT_BUS_NAME   = aws_cloudwatch_event_bus.mesh_central.name
      CENTRAL_ACCOUNT_ID       = data.aws_caller_identity.current.account_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.subscription_request]

  tags = local.mandatory_tags
}

# ── subscription_provisioner ──────────────────────────────────────────────────

data "archive_file" "subscription_provisioner" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/subscription_provisioner.py"
  output_path = "${path.module}/.build/subscription_provisioner.zip"
}

resource "aws_cloudwatch_log_group" "subscription_provisioner" {
  name              = "/aws/lambda/mesh-subscription-provisioner"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "subscription_provisioner_lambda" {
  name               = "MeshSubscriptionProvisionerLambdaRole"
  description        = "Execution role for subscription_provisioner Lambda (saga steps A/B/C)."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "subscription_provisioner_lambda" {
  name = "MeshSubscriptionProvisionerLambdaPolicy"
  role = aws_iam_role.subscription_provisioner_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBReadWrite"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.mesh_subscriptions.arn,
          "${aws_dynamodb_table.mesh_subscriptions.arn}/index/*",
          aws_dynamodb_table.mesh_products.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*"
        ]
      },
      {
        Sid    = "AssumeGrantorRoles"
        Effect = "Allow"
        Action = ["sts:AssumeRole"]
        Resource = [
          aws_iam_role.mesh_lf_grantor.arn,
          aws_iam_role.mesh_kms_grantor.arn,
          "arn:aws:iam::*:role/GlueConsumerRole"
        ]
      },
      {
        Sid    = "EventBridgePutEvents"
        Effect = "Allow"
        Action = ["events:PutEvents"]
        Resource = [aws_cloudwatch_event_bus.mesh_central.arn]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-subscription-provisioner:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "subscription_provisioner" {
  function_name    = "mesh-subscription-provisioner"
  description      = "Saga steps A/B/C: LF grant, KMS grant, Glue resource link creation."
  filename         = data.archive_file.subscription_provisioner.output_path
  source_code_hash = data.archive_file.subscription_provisioner.output_base64sha256
  handler          = "subscription_provisioner.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.subscription_provisioner_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_PRODUCTS_TABLE      = aws_dynamodb_table.mesh_products.name
      MESH_SUBSCRIPTIONS_TABLE = aws_dynamodb_table.mesh_subscriptions.name
      CENTRAL_EVENT_BUS_NAME   = aws_cloudwatch_event_bus.mesh_central.name
      LF_GRANTOR_ROLE_ARN      = aws_iam_role.mesh_lf_grantor.arn
      KMS_GRANTOR_ROLE_ARN     = aws_iam_role.mesh_kms_grantor.arn
      CENTRAL_ACCOUNT_ID       = data.aws_caller_identity.current.account_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.subscription_provisioner]

  tags = local.mandatory_tags
}

# Allow Step Functions to invoke subscription_provisioner
resource "aws_lambda_permission" "sfn_subscription_provisioner" {
  statement_id  = "AllowSFNInvokeSubscriptionProvisioner"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.subscription_provisioner.function_name
  principal     = "states.amazonaws.com"
  source_arn    = "arn:aws:states:${local.region}:${local.account_id}:stateMachine:subscription-provisioner"
}

# ── subscription_compensator ──────────────────────────────────────────────────

data "archive_file" "subscription_compensator" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/subscription_compensator.py"
  output_path = "${path.module}/.build/subscription_compensator.zip"
}

resource "aws_cloudwatch_log_group" "subscription_compensator" {
  name              = "/aws/lambda/mesh-subscription-compensator"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "subscription_compensator_lambda" {
  name               = "MeshSubscriptionCompensatorLambdaRole"
  description        = "Execution role for subscription_compensator Lambda (saga rollback)."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "subscription_compensator_lambda" {
  name = "MeshSubscriptionCompensatorLambdaPolicy"
  role = aws_iam_role.subscription_compensator_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBReadWrite"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.mesh_subscriptions.arn,
          "${aws_dynamodb_table.mesh_subscriptions.arn}/index/*"
        ]
      },
      {
        Sid    = "AssumeGrantorRoles"
        Effect = "Allow"
        Action = ["sts:AssumeRole"]
        Resource = [
          aws_iam_role.mesh_lf_grantor.arn,
          aws_iam_role.mesh_kms_grantor.arn
        ]
      },
      {
        Sid    = "EventBridgePutEvents"
        Effect = "Allow"
        Action = ["events:PutEvents"]
        Resource = [aws_cloudwatch_event_bus.mesh_central.arn]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-subscription-compensator:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "subscription_compensator" {
  function_name    = "mesh-subscription-compensator"
  description      = "Saga compensator: revokes partial LF/KMS grants and marks subscription FAILED."
  filename         = data.archive_file.subscription_compensator.output_path
  source_code_hash = data.archive_file.subscription_compensator.output_base64sha256
  handler          = "subscription_compensator.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.subscription_compensator_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_SUBSCRIPTIONS_TABLE = aws_dynamodb_table.mesh_subscriptions.name
      CENTRAL_EVENT_BUS_NAME   = aws_cloudwatch_event_bus.mesh_central.name
      LF_GRANTOR_ROLE_ARN      = aws_iam_role.mesh_lf_grantor.arn
      KMS_GRANTOR_ROLE_ARN     = aws_iam_role.mesh_kms_grantor.arn
      CENTRAL_ACCOUNT_ID       = data.aws_caller_identity.current.account_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.subscription_compensator]

  tags = local.mandatory_tags
}

# Allow Step Functions to invoke subscription_compensator
resource "aws_lambda_permission" "sfn_subscription_compensator" {
  statement_id  = "AllowSFNInvokeSubscriptionCompensator"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.subscription_compensator.function_name
  principal     = "states.amazonaws.com"
  source_arn    = "arn:aws:states:${local.region}:${local.account_id}:stateMachine:subscription-provisioner"
}

###############################################################################
# ── Section 3: Event Handler Lambdas ────────────────────────────────────────
###############################################################################

# ── audit_writer ──────────────────────────────────────────────────────────────

data "archive_file" "audit_writer" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/audit_writer.py"
  output_path = "${path.module}/.build/audit_writer.zip"
}

resource "aws_cloudwatch_log_group" "audit_writer" {
  name              = "/aws/lambda/mesh-audit-writer"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "audit_writer_lambda" {
  name               = "MeshAuditWriterLambdaRole"
  description        = "Execution role for audit_writer Lambda (append-only to audit log)."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "audit_writer_lambda" {
  name = "MeshAuditWriterLambdaPolicy"
  role = aws_iam_role.audit_writer_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AuditLogAppendOnly"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = [aws_dynamodb_table.mesh_audit_log.arn]
      },
      {
        Sid    = "ExplicitDenyAuditMutation"
        Effect = "Deny"
        Action = ["dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"]
        Resource = [aws_dynamodb_table.mesh_audit_log.arn]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-audit-writer:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "audit_writer" {
  function_name    = "mesh-audit-writer"
  description      = "Append-only audit log writer — PutItem to mesh-audit-log only."
  filename         = data.archive_file.audit_writer.output_path
  source_code_hash = data.archive_file.audit_writer.output_base64sha256
  handler          = "audit_writer.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.audit_writer_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_AUDIT_LOG_TABLE = aws_dynamodb_table.mesh_audit_log.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.audit_writer]

  tags = local.mandatory_tags
}

# Allow EventBridge to invoke audit_writer
resource "aws_lambda_permission" "eventbridge_audit_writer" {
  statement_id  = "AllowEventBridgeInvokeAuditWriter"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.audit_writer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.all_events_audit.arn
}

# ── event_validator ───────────────────────────────────────────────────────────

data "archive_file" "event_validator" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/event_validator.py"
  output_path = "${path.module}/.build/event_validator.zip"
}

resource "aws_cloudwatch_log_group" "event_validator" {
  name              = "/aws/lambda/mesh-event-validator"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "event_validator_lambda" {
  name               = "MeshEventValidatorLambdaRole"
  description        = "Execution role for event_validator Lambda."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "event_validator_lambda" {
  name = "MeshEventValidatorLambdaPolicy"
  role = aws_iam_role.event_validator_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBDedup"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = [aws_dynamodb_table.mesh_event_dedup.arn]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-event-validator:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "event_validator" {
  function_name    = "mesh-event-validator"
  description      = "Validates event sources and performs deduplication via mesh-event-dedup table."
  filename         = data.archive_file.event_validator.output_path
  source_code_hash = data.archive_file.event_validator.output_base64sha256
  handler          = "event_validator.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.event_validator_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_EVENT_DEDUP_TABLE = aws_dynamodb_table.mesh_event_dedup.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.event_validator]

  tags = local.mandatory_tags
}

# ── freshness_monitor ─────────────────────────────────────────────────────────

data "archive_file" "freshness_monitor" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/freshness_monitor.py"
  output_path = "${path.module}/.build/freshness_monitor.zip"
}

resource "aws_cloudwatch_log_group" "freshness_monitor" {
  name              = "/aws/lambda/mesh-freshness-monitor"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "freshness_monitor_lambda" {
  name               = "MeshFreshnessMonitorLambdaRole"
  description        = "Execution role for freshness_monitor Lambda (daily cron)."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "freshness_monitor_lambda" {
  name = "MeshFreshnessMonitorLambdaPolicy"
  role = aws_iam_role.freshness_monitor_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBRead"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
        Resource = [
          aws_dynamodb_table.mesh_products.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*",
          aws_dynamodb_table.mesh_quality_scores.arn,
          "${aws_dynamodb_table.mesh_quality_scores.arn}/index/*"
        ]
      },
      {
        Sid    = "EventBridgePutEvents"
        Effect = "Allow"
        Action = ["events:PutEvents"]
        Resource = [aws_cloudwatch_event_bus.mesh_central.arn]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-freshness-monitor:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "freshness_monitor" {
  function_name    = "mesh-freshness-monitor"
  description      = "Daily cron: checks SLA freshness for all active products and emits FreshnessViolation events."
  filename         = data.archive_file.freshness_monitor.output_path
  source_code_hash = data.archive_file.freshness_monitor.output_base64sha256
  handler          = "freshness_monitor.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.freshness_monitor_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_PRODUCTS_TABLE       = aws_dynamodb_table.mesh_products.name
      MESH_QUALITY_SCORES_TABLE = aws_dynamodb_table.mesh_quality_scores.name
      CENTRAL_EVENT_BUS_NAME    = aws_cloudwatch_event_bus.mesh_central.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.freshness_monitor]

  tags = local.mandatory_tags
}

# Allow EventBridge Scheduler to invoke freshness_monitor
resource "aws_lambda_permission" "scheduler_freshness_monitor" {
  statement_id  = "AllowSchedulerInvokeFreshnessMonitor"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.freshness_monitor.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = "arn:aws:scheduler:${local.region}:${local.account_id}:schedule/*"
}

###############################################################################
# ── Section 4: Lifecycle Lambdas ─────────────────────────────────────────────
###############################################################################

# ── product_deprecation ───────────────────────────────────────────────────────

data "archive_file" "product_deprecation" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/product_deprecation.py"
  output_path = "${path.module}/.build/product_deprecation.zip"
}

resource "aws_cloudwatch_log_group" "product_deprecation" {
  name              = "/aws/lambda/mesh-product-deprecation"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "product_deprecation_lambda" {
  name               = "MeshProductDeprecationLambdaRole"
  description        = "Execution role for product_deprecation Lambda."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "product_deprecation_lambda" {
  name = "MeshProductDeprecationLambdaPolicy"
  role = aws_iam_role.product_deprecation_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBReadWrite"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.mesh_products.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*",
          aws_dynamodb_table.mesh_subscriptions.arn,
          "${aws_dynamodb_table.mesh_subscriptions.arn}/index/*"
        ]
      },
      {
        Sid    = "EventBridgePutEvents"
        Effect = "Allow"
        Action = ["events:PutEvents"]
        Resource = [aws_cloudwatch_event_bus.mesh_central.arn]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-product-deprecation:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "product_deprecation" {
  function_name    = "mesh-product-deprecation"
  description      = "Marks a data product as DEPRECATED and notifies active subscribers."
  filename         = data.archive_file.product_deprecation.output_path
  source_code_hash = data.archive_file.product_deprecation.output_base64sha256
  handler          = "product_deprecation.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.product_deprecation_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_PRODUCTS_TABLE      = aws_dynamodb_table.mesh_products.name
      MESH_SUBSCRIPTIONS_TABLE = aws_dynamodb_table.mesh_subscriptions.name
      CENTRAL_EVENT_BUS_NAME   = aws_cloudwatch_event_bus.mesh_central.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.product_deprecation]

  tags = local.mandatory_tags
}

# ── retirement ────────────────────────────────────────────────────────────────

data "archive_file" "retirement" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/retirement.py"
  output_path = "${path.module}/.build/retirement.zip"
}

resource "aws_cloudwatch_log_group" "retirement" {
  name              = "/aws/lambda/mesh-retirement"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "retirement_lambda" {
  name               = "MeshRetirementLambdaRole"
  description        = "Execution role for retirement Lambda."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "retirement_lambda" {
  name = "MeshRetirementLambdaPolicy"
  role = aws_iam_role.retirement_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBReadWrite"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query", "dynamodb:Scan"]
        Resource = [
          aws_dynamodb_table.mesh_products.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*",
          aws_dynamodb_table.mesh_subscriptions.arn,
          "${aws_dynamodb_table.mesh_subscriptions.arn}/index/*"
        ]
      },
      {
        Sid    = "EventBridgePutEvents"
        Effect = "Allow"
        Action = ["events:PutEvents"]
        Resource = [aws_cloudwatch_event_bus.mesh_central.arn]
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-retirement:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "retirement" {
  function_name    = "mesh-retirement"
  description      = "Retires a data product: revokes all active subscriptions and archives the product record."
  filename         = data.archive_file.retirement.output_path
  source_code_hash = data.archive_file.retirement.output_base64sha256
  handler          = "retirement.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.retirement_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_PRODUCTS_TABLE      = aws_dynamodb_table.mesh_products.name
      MESH_SUBSCRIPTIONS_TABLE = aws_dynamodb_table.mesh_subscriptions.name
      CENTRAL_EVENT_BUS_NAME   = aws_cloudwatch_event_bus.mesh_central.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.retirement]

  tags = local.mandatory_tags
}

###############################################################################
# ── Section 5: Integration Stub ─────────────────────────────────────────────
###############################################################################

# ── datazone_connector ────────────────────────────────────────────────────────

data "archive_file" "datazone_connector" {
  type        = "zip"
  source_file = "${path.root}/../../../lambdas/datazone_connector.py"
  output_path = "${path.module}/.build/datazone_connector.zip"
}

resource "aws_cloudwatch_log_group" "datazone_connector" {
  name              = "/aws/lambda/mesh-datazone-connector"
  retention_in_days = 14
  kms_key_id        = aws_kms_key.mesh_central.arn
  tags              = local.mandatory_tags
}

resource "aws_iam_role" "datazone_connector_lambda" {
  name               = "MeshDataZoneConnectorLambdaRole"
  description        = "Execution role for datazone_connector Lambda."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.mandatory_tags
}

resource "aws_iam_role_policy" "datazone_connector_lambda" {
  name = "MeshDataZoneConnectorLambdaPolicy"
  role = aws_iam_role.datazone_connector_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBRead"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
        Resource = [
          aws_dynamodb_table.mesh_products.arn,
          "${aws_dynamodb_table.mesh_products.arn}/index/*",
          aws_dynamodb_table.mesh_domains.arn
        ]
      },
      {
        Sid    = "DataZoneRead"
        Effect = "Allow"
        Action = [
          "datazone:GetAsset",
          "datazone:ListAssets",
          "datazone:GetDomain",
          "datazone:CreateAsset",
          "datazone:UpdateAsset"
        ]
        Resource = "*"
      },
      {
        Sid    = "KMSDecrypt"
        Effect = "Allow"
        Action = ["kms:Decrypt", "kms:DescribeKey"]
        Resource = [aws_kms_key.mesh_central.arn]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/mesh-datazone-connector:*"]
      }
    ]
  })
}

resource "aws_lambda_function" "datazone_connector" {
  function_name    = "mesh-datazone-connector"
  description      = "Integration stub: syncs mesh product catalog with AWS DataZone domain."
  filename         = data.archive_file.datazone_connector.output_path
  source_code_hash = data.archive_file.datazone_connector.output_base64sha256
  handler          = "datazone_connector.handler"
  runtime          = "python3.12"
  role             = aws_iam_role.datazone_connector_lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      MESH_PRODUCTS_TABLE = aws_dynamodb_table.mesh_products.name
      MESH_DOMAINS_TABLE  = aws_dynamodb_table.mesh_domains.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.datazone_connector]

  tags = local.mandatory_tags
}

###############################################################################
# API Gateway Lambda permissions (catalog routes)
# The existing api_gateway.tf uses count guards on variables; now that Lambdas
# exist in this module, we add direct permissions here. The api_gateway.tf
# permissions based on variables remain for backward compat — these are
# additional unconditional permissions wired to the real functions.
###############################################################################

resource "aws_lambda_permission" "apigw_catalog_writer" {
  statement_id  = "AllowAPIGWInvokeCatalogWriter"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.catalog_writer.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mesh_api.execution_arn}/*/*"
}

resource "aws_lambda_permission" "apigw_catalog_search_direct" {
  statement_id  = "AllowAPIGWInvokeCatalogSearchDirect"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.catalog_search.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mesh_api.execution_arn}/*/*"
}

resource "aws_lambda_permission" "apigw_catalog_browse_direct" {
  statement_id  = "AllowAPIGWInvokeCatalogBrowseDirect"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.catalog_browse.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mesh_api.execution_arn}/*/*"
}

resource "aws_lambda_permission" "apigw_catalog_describe_direct" {
  statement_id  = "AllowAPIGWInvokeCatalogDescribeDirect"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.catalog_describe.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mesh_api.execution_arn}/*/*"
}

resource "aws_lambda_permission" "apigw_subscription_request_direct" {
  statement_id  = "AllowAPIGWInvokeSubscriptionRequestDirect"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.subscription_request.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mesh_api.execution_arn}/*/*"
}
