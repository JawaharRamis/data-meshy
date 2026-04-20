# infra/modules/subscription/main.tf
#
# Subscription module — wires all subscription infrastructure components.
#
# This module provisions:
#   - SubscriptionSFNRole (iam.tf)
#   - SubscriptionEBRole (iam.tf)
#   - subscription-provisioner Step Functions state machine (step_functions.tf)
#   - EventBridge rule: SubscriptionApproved → SFN (step_functions.tf)
#
# It does NOT create the Lambda functions (owned by Stream 2) or the
# DynamoDB table (already exists from Phase 1 governance module).
#
# Shared contracts produced:
#   - sfn_arn → consumed by Stream 2 (subscription provisioner Lambda wires back)

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.30.0"
    }
  }
}
