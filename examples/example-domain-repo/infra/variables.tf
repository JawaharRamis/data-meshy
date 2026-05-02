variable "domain" {
  type        = string
  description = "Domain name (e.g., sales). Used as a prefix for all resources."
}

variable "account_id" {
  type        = string
  description = "AWS account ID for this domain."
}

variable "owner" {
  type        = string
  description = "Domain owner email address."
}

variable "aws_region" {
  type        = string
  description = "AWS region to deploy into."
  default     = "us-east-1"
}

variable "central_event_bus_arn" {
  type        = string
  description = "ARN of the central EventBridge bus in the governance account."
}
