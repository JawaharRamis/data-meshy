# infra/modules/subscription/variables.tf
#
# Input variables for the subscription module.
# Lambda ARN variables are placeholders — Stream 2 populates them.

variable "environment" {
  description = "Deployment environment label (e.g. portfolio, staging, prod)."
  type        = string
}

variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "central_account_id" {
  description = "AWS account ID of the central governance account."
  type        = string
}

variable "central_event_bus_arn" {
  description = "ARN of the central EventBridge bus (mesh-central-bus)."
  type        = string
}

# ── Lambda ARNs (populated by Stream 2 after merge) ──────────────────────────

variable "provisioner_lambda_arn" {
  description = "ARN of the subscription provisioner Lambda (Stream 2). Empty until Stream 2 merges."
  type        = string
  default     = ""
}

variable "compensator_lambda_arn" {
  description = "ARN of the subscription compensator Lambda (Stream 2). Empty until Stream 2 merges."
  type        = string
  default     = ""
}

# ── IAM role ARNs (from governance module outputs) ────────────────────────────

variable "lf_grantor_role_arn" {
  description = "ARN of MeshLFGrantorRole — used by provisioner Lambda to execute LF BatchGrantPermissions."
  type        = string
}

variable "kms_grantor_role_arn" {
  description = "ARN of MeshKmsGrantorRole — used by provisioner Lambda to create KMS grants for consumer roles."
  type        = string
}

# ── DynamoDB ─────────────────────────────────────────────────────────────────

variable "subscriptions_table_name" {
  description = "Name of the mesh-subscriptions DynamoDB table (already exists from Phase 1)."
  type        = string
  default     = "mesh-subscriptions"
}

variable "subscriptions_table_arn" {
  description = "ARN of the mesh-subscriptions DynamoDB table."
  type        = string
}

variable "central_kms_key_arn" {
  description = "ARN of the central KMS CMK used for DynamoDB and SQS encryption."
  type        = string
}

# ── Tags ─────────────────────────────────────────────────────────────────────

variable "tags" {
  description = "Additional resource tags."
  type        = map(string)
  default     = {}
}
