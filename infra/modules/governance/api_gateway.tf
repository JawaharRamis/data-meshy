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
    allow_headers  = ["content-type", "x-amz-date", "authorization", "x-api-key", "x-amz-security-token"]
    allow_methods  = ["GET", "POST", "DELETE", "OPTIONS"]
    allow_origins  = ["*"]
    max_age        = 300
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
# Lambda integrations
# When Stream 2 merges, the *_lambda_arn variables are populated.
# Until then, routes exist but integrations point to empty ARNs which APIGW
# will reject at invocation time (not at plan/apply time).
# Use count/for_each guard so integration resources are skipped when ARN is "".
###############################################################################

resource "aws_apigatewayv2_integration" "subscription_create" {
  count = var.subscription_provisioner_lambda_arn != "" ? 1 : 0

  api_id                 = aws_apigatewayv2_api.mesh_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.subscription_provisioner_lambda_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
  description            = "Subscription create handler (Stream 2: subscription_create Lambda)"
}

resource "aws_apigatewayv2_integration" "subscription_list" {
  count = var.subscription_lister_lambda_arn != "" ? 1 : 0

  api_id                 = aws_apigatewayv2_api.mesh_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.subscription_lister_lambda_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
  description            = "Subscription list handler (Stream 2: subscription_list Lambda)"
}

resource "aws_apigatewayv2_integration" "subscription_approve" {
  count = var.subscription_approver_lambda_arn != "" ? 1 : 0

  api_id                 = aws_apigatewayv2_api.mesh_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.subscription_approver_lambda_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
  description            = "Subscription approve/revoke handler (Stream 2: subscription_approver Lambda)"
}

###############################################################################
# Routes — AWS_IAM auth on all routes
# Routes are created unconditionally so the API structure exists from day 1.
# Integrations are conditional (above); routes without integrations return 404.
###############################################################################

resource "aws_apigatewayv2_route" "post_subscriptions" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "POST /subscriptions"
  authorization_type = "AWS_IAM"
  target             = length(aws_apigatewayv2_integration.subscription_create) > 0 ? "integrations/${aws_apigatewayv2_integration.subscription_create[0].id}" : null
}

resource "aws_apigatewayv2_route" "get_subscriptions" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "GET /subscriptions"
  authorization_type = "AWS_IAM"
  target             = length(aws_apigatewayv2_integration.subscription_list) > 0 ? "integrations/${aws_apigatewayv2_integration.subscription_list[0].id}" : null
}

resource "aws_apigatewayv2_route" "post_subscription_approve" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "POST /subscriptions/{id}/approve"
  authorization_type = "AWS_IAM"
  target             = length(aws_apigatewayv2_integration.subscription_approve) > 0 ? "integrations/${aws_apigatewayv2_integration.subscription_approve[0].id}" : null
}

resource "aws_apigatewayv2_route" "post_subscription_revoke" {
  api_id             = aws_apigatewayv2_api.mesh_api.id
  route_key          = "POST /subscriptions/{id}/revoke"
  authorization_type = "AWS_IAM"
  target             = length(aws_apigatewayv2_integration.subscription_approve) > 0 ? "integrations/${aws_apigatewayv2_integration.subscription_approve[0].id}" : null
}

###############################################################################
# Lambda permissions — allow APIGW to invoke each Lambda
###############################################################################

resource "aws_lambda_permission" "apigw_subscription_create" {
  count = var.subscription_provisioner_lambda_arn != "" ? 1 : 0

  statement_id  = "AllowAPIGWInvokeSubscriptionCreate"
  action        = "lambda:InvokeFunction"
  function_name = var.subscription_provisioner_lambda_arn
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mesh_api.execution_arn}/*/*/subscriptions"
}

resource "aws_lambda_permission" "apigw_subscription_list" {
  count = var.subscription_lister_lambda_arn != "" ? 1 : 0

  statement_id  = "AllowAPIGWInvokeSubscriptionList"
  action        = "lambda:InvokeFunction"
  function_name = var.subscription_lister_lambda_arn
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mesh_api.execution_arn}/*/*/subscriptions"
}

resource "aws_lambda_permission" "apigw_subscription_approve" {
  count = var.subscription_approver_lambda_arn != "" ? 1 : 0

  statement_id  = "AllowAPIGWInvokeSubscriptionApprove"
  action        = "lambda:InvokeFunction"
  function_name = var.subscription_approver_lambda_arn
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.mesh_api.execution_arn}/*/*/subscriptions/*"
}
