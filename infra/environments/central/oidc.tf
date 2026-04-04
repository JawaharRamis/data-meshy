# infra/environments/central/oidc.tf
#
# GitHub Actions OIDC provider and role outputs.
# The OIDC provider and IAM roles are created in the governance module (iam.tf).
# This file re-exports them and provides environment-specific documentation.
#
# The OIDC provider (aws_iam_openid_connect_provider.github_actions) is defined
# in infra/modules/governance/iam.tf to keep all IAM in one place.
# This file exposes the outputs needed by the GitHub Actions workflows.

###############################################################################
# Outputs for GitHub Actions workflow configuration
###############################################################################

output "github_actions_oidc_provider_arn" {
  description = "OIDC provider ARN — set as AWS_OIDC_PROVIDER_ARN in GitHub Actions secrets."
  value       = module.governance.github_actions_oidc_provider_arn
}

output "terraform_plan_role_arn_for_ci" {
  description = "TerraformPlanRole ARN — set as TF_PLAN_ROLE_ARN in GitHub Actions variables."
  value       = module.governance.terraform_plan_role_arn
}

output "terraform_apply_role_arn_for_ci" {
  description = "TerraformApplyRole ARN — set as TF_APPLY_ROLE_ARN in GitHub Actions variables (main branch only)."
  value       = module.governance.terraform_apply_role_arn
}
