# infra/environments/central/scps.tf
#
# Service Control Policies for the AWS Organization.
# Requires AWS Organizations management account credentials.
# These SCPs are attached to the Domain OU and Platform OU.
#
# Pre-requisite: AWS Organizations must be enabled.
# Note: SCPs only take effect when "Enable all features" is active in Organizations.

###############################################################################
# Domain OU SCP
###############################################################################
resource "aws_organizations_policy" "domain_ou_guardrails" {
  name        = "DataMeshyDomainGuardrails"
  description = "Data Meshy Domain OU guardrails: enforce security controls, cost guardrails, and governance compliance."
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # 1. Deny CloudTrail log deletion
      {
        Sid    = "DenyCloudTrailLogDeletion"
        Effect = "Deny"
        Action = [
          "cloudtrail:DeleteTrail",
          "cloudtrail:StopLogging",
          "cloudtrail:UpdateTrail",
          "logs:DeleteLogGroup",
          "logs:DeleteLogStream"
        ]
        Resource = "*"
      },

      # 2. Deny disabling Lake Formation (prevent bypassing LF controls)
      {
        Sid    = "DenyDisablingLakeFormation"
        Effect = "Deny"
        Action = [
          "lakeformation:DeregisterResource",
          "lakeformation:RevokePermissions",
          "lakeformation:PutDataLakeSettings"
        ]
        Resource = "*"
        Condition = {
          # Allow LF operations only from the mesh's own roles
          ArnNotLike = {
            "aws:PrincipalArn" = [
              "arn:aws:iam::*:role/MeshLFGrantorRole",
              "arn:aws:iam::*:role/TerraformApplyRole",
              "arn:aws:iam::*:role/MeshAdminRole"
            ]
          }
        }
      },

      # 3. Require S3 SSE-KMS (not just any encryption — must be aws:kms)
      {
        Sid      = "RequireS3SSEKMS"
        Effect   = "Deny"
        Action   = ["s3:PutObject"]
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "s3:x-amz-server-side-encryption" = "aws:kms"
          }
        }
      },

      # 4. Deny public S3 buckets
      {
        Sid    = "DenyPublicS3"
        Effect = "Deny"
        Action = [
          "s3:PutBucketAcl",
          "s3:PutObjectAcl",
          "s3:PutBucketPublicAccessBlock"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = [
              "public-read",
              "public-read-write",
              "authenticated-read"
            ]
          }
        }
      },

      # 4b. Deny turning off S3 Block Public Access
      {
        Sid      = "DenyDisableS3BlockPublicAccess"
        Effect   = "Deny"
        Action   = "s3:PutBucketPublicAccessBlock"
        Resource = "*"
        Condition = {
          StringEquals = {
            "s3:PublicAccessBlockConfiguration/BlockPublicAcls"       = "false"
            "s3:PublicAccessBlockConfiguration/IgnorePublicAcls"      = "false"
            "s3:PublicAccessBlockConfiguration/BlockPublicPolicy"     = "false"
            "s3:PublicAccessBlockConfiguration/RestrictPublicBuckets" = "false"
          }
        }
      },

      # 5. Restrict to us-east-1 only (with exemptions for global services)
      {
        Sid    = "RestrictRegionToUsEast1"
        Effect = "Deny"
        NotAction = [
          "iam:*",
          "sts:*",
          "cloudfront:*",
          "route53:*",
          "waf:*",
          "budgets:*",
          "ce:*",
          "support:*",
          "health:*",
          "organizations:*",
          "account:*"
        ]
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "aws:RequestedRegion" = "us-east-1"
          }
        }
      },

      # 6. Deny cross-org AssumeRole (prevents lateral movement between domain accounts)
      {
        Sid      = "DenyCrossOrgAssumeRole"
        Effect   = "Deny"
        Action   = "sts:AssumeRole"
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "aws:PrincipalOrgID" = var.org_id
          }
        }
      },

      # 7. Deny Glue jobs with more than 4 DPUs (cost guardrail)
      # Note: Glue doesn't support DPU checks natively in SCPs — this blocks
      # the MaxCapacity parameter at the API level where feasible.
      # Enforcement is complemented by a Config rule in the monitoring module.
      {
        Sid    = "DenyGlueJobsOver4DPU"
        Effect = "Deny"
        Action = [
          "glue:CreateJob",
          "glue:UpdateJob"
        ]
        Resource = "*"
        Condition = {
          NumericGreaterThan = {
            "glue:MaxCapacity" = "4"
          }
        }
      }
    ]
  })

  tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

###############################################################################
# Platform OU SCP — require MFA for MeshAdminRole
###############################################################################
resource "aws_organizations_policy" "platform_ou_guardrails" {
  name        = "DataMeshyPlatformGuardrails"
  description = "Data Meshy Platform OU guardrails: MFA required for MeshAdminRole assumption."
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RequireMFAForAdminRole"
        Effect   = "Deny"
        Action   = "sts:AssumeRole"
        Resource = "arn:aws:iam::*:role/MeshAdminRole"
        Condition = {
          BoolIfExists = {
            "aws:MultiFactorAuthPresent" = "false"
          }
        }
      }
    ]
  })

  tags = {
    Project     = "data-meshy"
    ManagedBy   = "terraform"
    Environment = var.environment
  }
}

###############################################################################
# NOTE: SCP attachment to OUs requires the OU IDs.
# These are not created here because OU creation is a one-time manual step
# (or managed in the management account's own Terraform state).
# Attach via:
#   aws_organizations_policy_attachment.domain_ou { target_id = "<domain-ou-id>" }
#   aws_organizations_policy_attachment.platform_ou { target_id = "<platform-ou-id>" }
#
# Uncomment and fill in OU IDs once known:
###############################################################################

# variable "domain_ou_id" {
#   description = "AWS Organizations OU ID for domain accounts."
#   type        = string
#   default     = ""
# }

# variable "platform_ou_id" {
#   description = "AWS Organizations OU ID for the platform (central governance) account."
#   type        = string
#   default     = ""
# }

# resource "aws_organizations_policy_attachment" "domain_ou" {
#   count     = var.domain_ou_id != "" ? 1 : 0
#   policy_id = aws_organizations_policy.domain_ou_guardrails.id
#   target_id = var.domain_ou_id
# }

# resource "aws_organizations_policy_attachment" "platform_ou" {
#   count     = var.platform_ou_id != "" ? 1 : 0
#   policy_id = aws_organizations_policy.platform_ou_guardrails.id
#   target_id = var.platform_ou_id
# }
