# infra/environments/central/identity_center.tf
#
# IAM Identity Center (SSO) permission sets and group assignments.
# Identity Center is an Organization-level service; this resource must be
# applied from the management/central account with SSO enabled.
#
# Pre-requisite: IAM Identity Center must be enabled in the AWS Organization.

data "aws_ssoadmin_instances" "this" {}

locals {
  sso_instance_arn  = tolist(data.aws_ssoadmin_instances.this.arns)[0]
  identity_store_id = tolist(data.aws_ssoadmin_instances.this.identity_store_ids)[0]
}

###############################################################################
# Permission Sets
###############################################################################

# MeshPlatformAdmin -> central: MeshAdminRole
resource "aws_ssoadmin_permission_set" "mesh_platform_admin" {
  name             = "MeshPlatformAdmin"
  description      = "Platform engineering access. Maps to MeshAdminRole in the central account. MFA required."
  instance_arn     = local.sso_instance_arn
  session_duration = "PT1H"

  tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

resource "aws_ssoadmin_permission_set_inline_policy" "mesh_platform_admin" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.mesh_platform_admin.arn

  inline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AssumeAdminRole"
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = module.governance.mesh_admin_role_arn
      }
    ]
  })
}

# DomainAdmin -> domain: DomainAdminRole
resource "aws_ssoadmin_permission_set" "domain_admin" {
  name             = "DomainAdmin"
  description      = "Domain owner/admin access. Maps to DomainAdminRole in domain accounts."
  instance_arn     = local.sso_instance_arn
  session_duration = "PT8H"

  tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

resource "aws_ssoadmin_managed_policy_attachment" "domain_admin_read_only" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.domain_admin.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# DomainDataEngineer -> domain: DomainDataEngineerRole
resource "aws_ssoadmin_permission_set" "domain_data_engineer" {
  name             = "DomainDataEngineer"
  description      = "Domain data engineer. Read/write S3, Glue jobs, catalog. Maps to DomainDataEngineerRole."
  instance_arn     = local.sso_instance_arn
  session_duration = "PT8H"

  tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

# DomainConsumer -> domain: DomainConsumerRole
resource "aws_ssoadmin_permission_set" "domain_consumer" {
  name             = "DomainConsumer"
  description      = "Read-only consumer access via LF grants and Athena. Maps to DomainConsumerRole."
  instance_arn     = local.sso_instance_arn
  session_duration = "PT8H"

  tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

# GovernanceViewer -> central: GovernanceReadRole
resource "aws_ssoadmin_permission_set" "governance_viewer" {
  name             = "GovernanceViewer"
  description      = "Governance team read-only access to all mesh metadata. Maps to GovernanceReadRole."
  instance_arn     = local.sso_instance_arn
  session_duration = "PT8H"

  tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

resource "aws_ssoadmin_permission_set_inline_policy" "governance_viewer" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.governance_viewer.arn

  inline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AssumeGovernanceReadRole"
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = module.governance.governance_read_role_arn
      }
    ]
  })
}

###############################################################################
# Identity Store Groups
###############################################################################

resource "aws_identitystore_group" "platform_engineers" {
  display_name      = "platform-engineers"
  description       = "Platform engineering team — MeshPlatformAdmin access to central account."
  identity_store_id = local.identity_store_id
}

resource "aws_identitystore_group" "sales_engineers" {
  display_name      = "sales-engineers"
  description       = "Sales domain data engineers."
  identity_store_id = local.identity_store_id
}

resource "aws_identitystore_group" "sales_admins" {
  display_name      = "sales-admins"
  description       = "Sales domain admins / product owners."
  identity_store_id = local.identity_store_id
}

resource "aws_identitystore_group" "marketing_analysts" {
  display_name      = "marketing-analysts"
  description       = "Marketing domain consumers — read-only via Athena."
  identity_store_id = local.identity_store_id
}

resource "aws_identitystore_group" "governance_team" {
  display_name      = "governance-team"
  description       = "Central governance / compliance team — GovernanceViewer access."
  identity_store_id = local.identity_store_id
}
