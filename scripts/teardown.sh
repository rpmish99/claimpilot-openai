#!/usr/bin/env bash
# Remove every billable ClaimPilot resource. Rebuild any time with scripts/deploy.sh.
#   bash scripts/teardown.sh          # asks first
#   bash scripts/teardown.sh --yes    # no prompt
# Leaves the Amplify app alone (delete it with: aws amplify delete-app --app-id <id>).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
REGION=us-east-1; STACK=claimpilot; SECRET=claimpilot/app

aws sts get-caller-identity >/dev/null 2>&1 || { echo "Not logged in. Run: aws sso login"; exit 1; }
eval "$(aws configure export-credentials --format env)"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="claimpilot-${ACCOUNT}-${REGION}"

echo "This deletes, in account $ACCOUNT ($REGION): the '$STACK' stack (Lambda, API, queues, DynamoDB intake history,"
echo "S3 bucket contents), the HealthLake datastore, and the '$SECRET' secret. Code and .env are kept."
if [ "${1:-}" != "--yes" ]; then read -r -p "Type 'delete' to continue: " a; [ "$a" = delete ] || { echo Aborted.; exit 1; }; fi

echo "Emptying s3://${BUCKET} ..."
aws s3 rm "s3://${BUCKET}" --recursive --region $REGION --only-show-errors || true

echo "Deleting stack ..."
aws cloudformation delete-stack --stack-name $STACK --region $REGION
aws cloudformation wait stack-delete-complete --stack-name $STACK --region $REGION

echo "Deleting HealthLake datastore (stops ~\$0.27/hr) ..."
.venv/bin/python scripts/healthlake.py delete

echo "Deleting secret immediately (no recovery window, so the name can be reused) ..."
aws secretsmanager delete-secret --secret-id $SECRET --force-delete-without-recovery --region $REGION >/dev/null || true

echo "Deleting Lambda log groups ..."
for g in $(aws logs describe-log-groups --log-group-name-prefix /aws/lambda/claimpilot --region $REGION --query 'logGroups[].logGroupName' --output text); do
  aws logs delete-log-group --log-group-name "$g" --region $REGION || true
done

echo "Clearing stale values from .env ..."
grep -v -E '^(HEALTHLAKE_ENDPOINT|HEALTHLAKE_DATASTORE_ID|APP_SECRET_ARN|API_URL)=' .env > .env.tmp && mv .env.tmp .env
echo "HEALTHLAKE_ENDPOINT=" >> .env

echo "Done. Rebuild with: bash scripts/deploy.sh"
