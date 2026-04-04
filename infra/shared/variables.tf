variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment label (e.g. portfolio, staging, prod)."
  type        = string
  default     = "portfolio"
}

variable "org_id" {
  description = "AWS Organization ID used in SCPs and bucket policies (e.g. o-xxxxxxxxxx)."
  type        = string
}
