aws_region  = "us-east-1"
environment = "portfolio"

# Replace with your AWS Organization ID (e.g. o-xxxxxxxxxx)
org_id = "o-REPLACE_WITH_ORG_ID"

# Replace with actual domain account IDs when domains are onboarded.
# These are listed explicitly in the EventBridge bus resource policy — no wildcards.
# Example:
# domain_account_ids = ["123456789012", "210987654321"]
domain_account_ids = []

# GitHub OIDC — replace with your GitHub org/user name
github_org  = "REPLACE_WITH_GITHUB_ORG"
github_repo = "data-meshy"

# Optional: set to receive SNS email alerts
# alert_email = "platform-team@example.com"
alert_email = ""
