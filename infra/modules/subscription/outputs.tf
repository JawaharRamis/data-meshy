# infra/modules/subscription/outputs.tf
#
# SHARED CONTRACT — consumed by:
#   - Stream 2: subscription provisioner Lambda needs sfn_arn to self-reference
#               and for IAM policy on the EventBridge role.
#   - Stream 4: examples reference the SFN ARN for end-to-end testing.

output "sfn_arn" {
  description = "ARN of the subscription-provisioner Step Functions state machine."
  value       = aws_sfn_state_machine.subscription_provisioner.arn
}

output "sfn_name" {
  description = "Name of the subscription-provisioner Step Functions state machine."
  value       = aws_sfn_state_machine.subscription_provisioner.name
}

output "subscription_sfn_role_arn" {
  description = "ARN of SubscriptionSFNRole (Step Functions execution role)."
  value       = aws_iam_role.subscription_sfn.arn
}

output "subscription_eb_role_arn" {
  description = "ARN of SubscriptionEBRole (EventBridge role to start SFN on SubscriptionApproved)."
  value       = aws_iam_role.subscription_eb.arn
}
