# Component: Monitoring

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03 | **Stale check**: Phase 2

## Navigation
<-- [Architecture](../architecture/OVERVIEW.md) | [^ Docs home](../README.md)

## What this is

A Terraform module that provisions CloudWatch alarms, CloudWatch log groups, and AWS Budgets for a domain account. This is the observability layer that ensures pipeline failures, Lambda errors, DLQ backlog, and cost overruns surface as SNS alerts to the domain team. Deployed once per domain account alongside the pipeline and Lambda infrastructure.

## Where to find it

```
infra/modules/monitoring/
  main.tf        # All resources: CloudWatch alarms, log groups, AWS Budgets budget
  variables.tf   # Module inputs: domain name, environment, alarm ARN, function names, budget thresholds
```

## How it works

### CloudWatch Log Groups

Two types of log groups are created, both with 30-day retention:

- **Lambda log groups** (`/aws/lambda/{function_name}`): one per Lambda function listed in `lambda_function_names`. These capture stdout/stderr from every Lambda invocation.
- **Step Functions log group** (`/data-meshy/{domain}/pipeline`): a single log group for the domain's Step Functions state machine execution logs. Only created if `state_machine_arn` is provided.

### CloudWatch Alarms

Four categories of alarms, all with a 5-minute evaluation period and `notBreaching` for missing data:

| Alarm | Metric | Namespace | Threshold | Condition |
|-------|--------|-----------|-----------|-----------|
| Lambda errors | `Errors` | `AWS/Lambda` | > 1 in 5 min | `Sum` statistic per function |
| DLQ depth | `ApproximateNumberOfMessagesVisible` | `AWS/SQS` | > 0 | Any message in a DLQ is an incident |
| SFN failures | `ExecutionsFailed` | `AWS/States` | > 0 in 5 min | Any failed state machine execution |
| Glue failures | `glue.driver.aggregate.numFailedTasks` | `Glue` | > 0 | Any failed Glue task (JobRunId=ALL, Type=gauge) |

All alarms send notifications to the SNS topic specified in `alarm_notification_arn` on both ALARM and OK state transitions. This means the team gets notified when an alarm fires and when it recovers.

### AWS Budgets

A single monthly cost budget is created per domain account. The budget limit is the maximum value from the `budget_thresholds` list (default: $100). Individual threshold notifications are created for each value in the list:

| Default threshold | Notification type |
|-------------------|-------------------|
| $20 | `ACTUAL > $20` |
| $50 | `ACTUAL > $50` |
| $100 | `ACTUAL > $100` (also the budget limit) |

The budget is scoped to the domain using a cost filter on the `Domain` tag (`user:Domain${var.domain}`). Notifications go to either `budget_email_recipients` (if provided) or the `alarm_notification_arn` SNS topic as a fallback.

## Key interactions

- **SNS topic from governance module**: The `alarm_notification_arn` variable is expected to come from the central governance module's `quality_alert_sns_topic_arn` output, or a dedicated ops SNS topic. All alarms and budget notifications route through this single topic.
- **Lambda function names from domain infra**: The `lambda_function_names` list is populated by the domain account's infrastructure module, which knows the names of all deployed Lambda handlers.
- **DLQ ARNs from event mesh**: The `dlq_queue_arns` map comes from the event processing setup. Each DLQ name is mapped to its ARN so the alarm can reference the correct `QueueName` dimension.
- **Step Functions ARN from pipeline module**: The `state_machine_arn` is the output of the Step Functions deployment. If empty (no state machine), the SFN alarm and log group are skipped.
- **Budget alerts go to billing contacts**: When `budget_email_recipients` is set, budget notifications go directly to those email addresses. When not set, they fall back to the SNS topic.

## Configuration

### Module variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `domain` | `string` | (required) | Domain name (e.g., `sales`). Used in alarm naming, log group paths, and budget cost filters. |
| `environment` | `string` | `"dev"` | Deployment environment (`dev`, `staging`, `prod`). Applied as a tag. |
| `aws_region` | `string` | `"us-east-1"` | AWS region for monitoring resources. |
| `alarm_notification_arn` | `string` | (required) | SNS topic ARN for alarm notifications. Should come from the governance module or a dedicated ops topic. |
| `lambda_function_names` | `list(string)` | `[]` | List of Lambda function names in this domain to monitor for errors. |
| `dlq_queue_arns` | `map(string)` | `{}` | Map of DLQ queue name to ARN. Keys must match the actual SQS queue name (used as the `QueueName` dimension). |
| `state_machine_arn` | `string` | `""` | Step Functions state machine ARN. If empty, SFN alarm and log group are not created. |
| `glue_job_names` | `list(string)` | `[]` | List of Glue job names to monitor for failures. |
| `budget_thresholds` | `list(number)` | `[20, 50, 100]` | Monthly budget threshold amounts in USD. The budget limit is set to the maximum value. |
| `budget_email_recipients` | `list(string)` | `[]` | Email addresses for AWS Budgets alerts. If empty, budget notifications go to the SNS topic instead. |
| `tags` | `map(string)` | `{}` | Additional tags merged with mandatory tags (`Project=data-meshy`, `ManagedBy=terraform`, `Environment`, `Domain`). |

### Mandatory tags applied to all resources

| Tag | Value |
|-----|-------|
| `Project` | `data-meshy` |
| `ManagedBy` | `terraform` |
| `Environment` | value of `var.environment` |
| `Domain` | value of `var.domain` |

## Gotchas and constraints

- **AWS Budgets has a slight delay**: Budget evaluations run periodically (typically every 8-12 hours), not in real time. A cost spike may not trigger an alert until the next evaluation cycle. Do not rely on budgets for real-time cost control.
- **SNS subscriptions must be confirmed**: When budget notifications go to email recipients, each recipient receives a confirmation email from AWS and must confirm the subscription before alerts are delivered. This is a one-time action per email-topic pair.
- **Budget cost filter uses exact tag match**: The cost filter matches `user:Domain$${var.domain}`. The `$` is intentional -- AWS Budgets cost filter syntax uses `$` to separate the tag key from the value. Ensure the `Domain` tag is applied to all billable resources in the domain account.
- **DLQ alarm keys must be actual queue names**: The `dlq_queue_arns` map keys are used as the `QueueName` CloudWatch dimension. If the key does not exactly match the SQS queue name, the alarm will never fire.
- **Lambda log groups require function names, not ARNs**: `lambda_function_names` must contain just the function name (e.g., `catalog_writer`), not the full ARN.
- **Single budget per domain**: The module creates exactly one budget resource. If you need separate budgets for different cost centers, deploy additional monitoring modules or modify the budget resource.
- **Terraform provider requirements**: Requires Terraform >= 1.6.0 and AWS provider >= 5.30.0.

## See also

- [GOVERNANCE.md](./GOVERNANCE.md) -- central governance module that provides the SNS topic ARN
- [SECURITY.md](./SECURITY.md) -- security controls and encryption for domain accounts
- [LAMBDAS.md](./LAMBDAS.md) -- Lambda handlers whose errors these alarms monitor
- [PIPELINE-TEMPLATES.md](./PIPELINE-TEMPLATES.md) -- Glue jobs whose failures these alarms monitor
