# infra/modules/governance/api_gateway.tf
#
# HTTP API Gateway v2 — exposes the mesh governance plane and subscription
# workflow to CLI / consumer tooling.
#
# Auth: IAM (SigV4) on all routes — no API keys / cognito.
# Routes added in Phase 2 (Stream 1):
#   POST   /subscriptions                — submit a new subscription request
#   GET    /subscriptions                — list subscriptions (filterable)
#   POST   /subscriptions/{id}/approve   — approve a pending subscription
#   POST   /subscriptions/{id}/revoke    — revoke an active subscription
#
# Routes added in Phase 3 (Stream 1 — catalog-cli):
#   GET    /catalog/search               — search products by keyword/domain/tag/classification
#   GET    /catalog/browse               — browse all products grouped by domain
#
# Lambda integration ARNs are passed in as variables — Stream 2 fills them.
# When a Lambda ARN is empty (initial deploy), the route is created but the
# integration points to a placeholder that returns 503 so `terraform plan`
# succeeds without Stream 2 being deployed.

###############################################################################
# HTTP API
###############################################################################
resource "aws_apigatewayv2_api" "mesh_api" {
  name          = "mesh-governance-api"
  protocol_type = "HTTP"
  description   = "Data Meshy governance and subscription management API. All routes require IAM (SigV4) auth."

  cors_configuration {
    allow_headers = ["content-type", "x-amz-date", "authorization", "x-api-key", "x-amz-security-token"]
    allow_methods = ["GET", "POST", "DELETE", "OPTIONS"]
    allow_origins = []
    max_age       = 300
  }

  tags = merge(local.mandatory_tags, {
    Name = "mesh-governance-api"
  })
}

###############################################################################
# Default stage — auto-deploy enabled
###############################################################################
resource "aws_apigatewayv2_stage" "mesh_api_default" {
  api_id      = aws_apigatewayv2_api.mesh_api.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 100
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.mesh_api_access_logs.arn
    format = jsonencode({
      requestId               = "$context.requestId"
      sourceIp                = "$context.identity.sourceIp"
      requestTime             = "$context.requestTime"
      protocol                = "$context.protocol"
      httpMethod              = "$context.httpMethod"
      resourcePath            = "$context.resourcePath"
      routeKey                = "$context.routeKey"
      status                  = "$context.status"
      responseLength          = "$context.responseLength"
      integrationErrorMessage = "$context.integrationErrorMessage"
    })
  }

  tags = local.mandatory_tags
}

resource "aws_cloudwatch_log_group" "mesh_api_access_logs" {
  name              = "/aws/apigateway/mesh-governance-api"
  retention_in_days = 90

  tags = merge(local.mandatory_tags, {
    Name = "mesh-governance-api-access-logs"
  })
}

###############################################################################
# IAM authorizer — SigV4 (AWS_IAM) is enforced at the route level.
# HTTP API v2 uses route-level auth_type = "AWS_IAM" (no explicit authorizer
# resource needed for AWS_IAM — it is built in).
###############################################################################

###############################################################################
# Lambda integrations — Phase 6: all Lambdas now exist in lambdas.tf.
# Integration URIs reference aws_lambda_function resources directly.
# Variable-based count guards removed; resources are unconditional.
###############################################################################

resource "aws_apigatewayv2_integration" "subscription_create" {
  api_id                 = aws_apigatewayv2_api.mesh_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.subscription_request.arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
  description            = "Subscription create handler (subscription_request Lambda)"
}

resource "aws_apigatewayv2_integration" "subscription_list" {
  api_id                 = aws_apigatewayv2_api.mesh_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.subscription_request.arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
  description            = "Subscription list handler (subscription_request Lambda — list path)"
}

resource "aws_apigatewayv2_integration" "subscription_approve" {
  api_id                 = aws_apigatewayv2_api.mesh_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.subscription_request.arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
  description            = "Subscription approve/revoke handler (subscription_request Lambda — approve/revoke path)"
}

###############################################################################
# Routes — AWS_IAM auth on all routes
###############################################################################

resource "aws_apigatewayv2_route" "post_subscriptions" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "POST /subscriptions"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.subscription_create.id}"
}

resource "aws_apigatewayv2_route" "get_subscriptions" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "GET /subscriptions"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.subscription_list.id}"
}

resource "aws_apigatewayv2_route" "post_subscription_approve" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "POST /subscriptions/{id}/approve"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.subscription_approve.id}"
}

resource "aws_apigatewayv2_route" "post_subscription_revoke" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "POST /subscriptions/{id}/revoke"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.subscription_approve.id}"
}

###############################################################################
# Catalog Lambda integrations — direct ARN references (Phase 6)
###############################################################################

resource "aws_apigatewayv2_integration" "catalog_search" {
  api_id                 = aws_apigatewayv2_api.mesh_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.catalog_search.arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
  description            = "Catalog search handler (catalog_search Lambda)"
}

resource "aws_apigatewayv2_integration" "catalog_browse" {
  api_id                 = aws_apigatewayv2_api.mesh_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.catalog_browse.arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
  description            = "Catalog browse handler (catalog_browse Lambda)"
}

resource "aws_apigatewayv2_integration" "catalog_describe" {
  api_id                 = aws_apigatewayv2_api.mesh_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.catalog_describe.arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
  description            = "Catalog describe handler (catalog_describe Lambda)"
}

###############################################################################
# Catalog routes — GET /catalog/search, GET /catalog/browse, GET /catalog/{domain}/{product}
###############################################################################

resource "aws_apigatewayv2_route" "get_catalog_search" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "GET /catalog/search"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.catalog_search.id}"
}

resource "aws_apigatewayv2_route" "get_catalog_browse" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "GET /catalog/browse"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.catalog_browse.id}"
}

resource "aws_apigatewayv2_route" "get_catalog_describe" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "GET /catalog/{domain}/{product}"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.catalog_describe.id}"
}

###############################################################################
# Lambda permissions — allow APIGW to invoke each Lambda
# Catalog Lambda permissions are defined in lambdas.tf (apigw_*_direct resources).
# Subscription Lambda permissions use a wildcard source_arn to cover all routes.
###############################################################################

resource "aws_lambda_permission" "apigw_subscription_all_routes" {
  statement_id  = "AllowAPIGWInvokeSubscriptionAllRoutes"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.subscription_request.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mesh_api.execution_arn}/*/*"
}
