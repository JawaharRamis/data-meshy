#!/usr/bin/env bash
# subscribe_to_customer_orders.sh
#
# Marketing domain: request and activate a subscription to sales/customer_orders.
#
# Prerequisites:
#   - datameshy CLI installed and on PATH (pip install data-meshy)
#   - AWS credentials configured for the MARKETING account
#   - AWS_PROFILE set to the marketing domain profile (or use the default profile)
#
# Environment variables:
#   AWS_PROFILE         — AWS CLI profile for the marketing account (default: default)
#   SUBSCRIPTION_ID     — Populated automatically after step 1; set manually to resume
#   PRODUCER_AWS_PROFILE — AWS CLI profile for the sales/producer account (for step 2)
#
# Usage:
#   # Full flow (marketing + producer profiles available on this machine)
#   AWS_PROFILE=marketing PRODUCER_AWS_PROFILE=sales bash subscribe_to_customer_orders.sh
#
#   # Resume from approval step (subscription already requested)
#   SUBSCRIPTION_ID=<uuid> AWS_PROFILE=marketing PRODUCER_AWS_PROFILE=sales bash subscribe_to_customer_orders.sh

set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-default}"
PRODUCER_AWS_PROFILE="${PRODUCER_AWS_PROFILE:-default}"

echo "=== Step 1: Request subscription to sales/customer_orders ==="
echo "Account profile : ${AWS_PROFILE}"
echo "Requesting columns: order_id, order_date, order_total (non-PII only)"
echo ""

# The CLI writes the subscription_id to stdout on success.
# Capture it and export for use in subsequent steps.
if [[ -z "${SUBSCRIPTION_ID:-}" ]]; then
  SUBSCRIPTION_ID=$(
    AWS_PROFILE="${AWS_PROFILE}" datameshy subscribe request \
      --product sales/customer_orders \
      --columns order_id,order_date,order_total \
      --justification "Marketing attribution model requires order date and total; email excluded (PII)." \
      --output json \
    | python3 -c "import sys, json; print(json.load(sys.stdin)['subscription_id'])"
  )
  echo "Subscription requested. ID: ${SUBSCRIPTION_ID}"
else
  echo "Using existing SUBSCRIPTION_ID: ${SUBSCRIPTION_ID}"
fi

echo ""
echo "=== Step 2: Approve the subscription (producer / sales account) ==="
echo "Account profile : ${PRODUCER_AWS_PROFILE}"
echo "Note: in production, the data product owner approves via the DataZone web UI"
echo "      or the governance team runs this command from the producer account."
echo ""

AWS_PROFILE="${PRODUCER_AWS_PROFILE}" datameshy subscribe approve \
  --subscription-id "${SUBSCRIPTION_ID}"

echo "Subscription approved."

echo ""
echo "=== Step 3: Verify the subscription is ACTIVE (marketing account) ==="
echo ""

AWS_PROFILE="${AWS_PROFILE}" datameshy subscribe list \
  --product sales/customer_orders \
  --status ACTIVE

echo ""
echo "=== Done ==="
echo "The resource link 'marketing_catalog.sales_customer_orders' is now available in Athena."
echo "Run query_customer_orders.py to query the data."
