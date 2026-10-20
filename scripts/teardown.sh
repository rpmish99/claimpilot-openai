#!/usr/bin/env bash
# Remove every billable ClaimPilot resource. Run after the panel.
#   bash scripts/teardown.sh
set -euo pipefail
REGION=us-east-1
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="claimpilot-${ACCOUNT}-${REGION}"
cd "$(dirname "$0")/.."

echo "Emptying s3://${BUCKET} ..."
aws s3 rm "s3://${BUCKET}" --recursive --region "$REGION" || true

echo "Deleting SAM stack ..."
sam delete --stack-name claimpilot --region "$REGION" --no-prompts

echo "Deleting HealthLake datastore (stops ~\$0.27/hr) ..."
.venv/bin/python scripts/healthlake.py delete

echo "Scheduling secret deletion (7-day recovery window) ..."
aws secretsmanager delete-secret --secret-id claimpilot/app --recovery-window-in-days 7 --region "$REGION" || true

echo "Done. Delete the Amplify app in the console (or: aws amplify delete-app --app-id <id>) if no longer needed."
