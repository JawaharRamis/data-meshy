##############################################################################
# monitoring/variables.tf
#
# Variables for the monitoring module.
# CloudWatch alarms, log groups, and AWS Budgets.
##############################################################################

variable "domain" {
  description = "Domain name (e.g. sales). Used in alarm naming and log group paths."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region for monitoring resources."
  type        = string
  default     = "us-east-1"
}

variable "alarm_notification_arn" {
  description = "SNS topic ARN for alarm notifications (from governance module quality_alert_sns_topic_arn or a dedicated ops topic)."
  type        = string
}

variable "lambda_function_names" {
  description = "List of Lambda function names in this domain to monitor for errors."
  type        = list(string)
  default     = []
}

variable "dlq_queue_arns" {
  description = "Map of DLQ queue name -> ARN to monitor for message count > 0."
  type        = map(string)
  default     = {}
}

variable "state_machine_arn" {
  description = "Step Functions state machine ARN to monitor for execution failures."
  type        = string
  default     = ""
}

variable "glue_job_names" {
  description = "List of Glue job names to monitor for failures."
  type        = list(string)
  default     = []
}

variable "budget_thresholds" {
  description = "List of monthly budget threshold amounts (USD) for AWS Budgets alerts."
  type        = list(number)
  default     = [20, 50, 100]
}

variable "budget_email_recipients" {
  description = "List of email addresses for AWS Budgets alerts."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Additional tags to merge with the mandatory set."
  type        = map(string)
  default     = {}
}
