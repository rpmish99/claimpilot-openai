#!/usr/bin/env bash
# Build ClaimPilot on AWS from scratch (or update it). Safe to re-run.
#   bash scripts/deploy.sh            # deploy
#   DRY_RUN=1 bash scripts/deploy.sh  # print what would happen, change nothing
# Reads .env (OPENAI_API_KEY required). Takes ~30 min the first time (HealthLake creation).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
REGION=us-east-1
STACK=claimpilot
SECRET=claimpilot/app
PY=.venv/bin/python
DRY=${DRY_RUN:-}

run() { if [ -n "$DRY" ]; then echo "[dry-run] $*"; else "$@"; fi; }
envget() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
envset() { # envset KEY VALUE
  [ -n "$DRY" ] && { echo "[dry-run] .env: $1=$2"; return; }
  touch .env; grep -v -E "^$1=" .env > .env.tmp || true; echo "$1=$2" >> .env.tmp; mv .env.tmp .env
}

[ -f .env ] || { echo "Missing .env. Copy .env.example and set OPENAI_API_KEY."; exit 1; }
for t in aws sam; do command -v $t >/dev/null || { echo "$t not found"; exit 1; }; done
[ -x $PY ] || { echo "Missing .venv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"; exit 1; }
OPENAI_API_KEY=$(envget OPENAI_API_KEY)
[ -n "$OPENAI_API_KEY" ] || { echo "OPENAI_API_KEY is empty in .env"; exit 1; }

# SSO sessions: aws works from the cache but SAM/boto3 need exported creds.
aws sts get-caller-identity >/dev/null 2>&1 || { echo "Not logged in. Run: aws sso login"; exit 1; }
eval "$(aws configure export-credentials --format env)"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="claimpilot-${ACCOUNT}-${REGION}"
EMAIL=$(envget NOTIFICATION_EMAIL); EMAIL=${EMAIL:-mishrar1212@gmail.com}
ORIGIN=${ALLOWED_ORIGIN:-$(envget ALLOWED_ORIGIN)}; ORIGIN=${ORIGIN:-https://main.dyc07h1e3zuao.amplifyapp.com}
echo "Account $ACCOUNT, region $REGION, notify $EMAIL, origin $ORIGIN"

echo "== 1/6 Secret ($SECRET)"
DEMO_KEY=$(envget DEMO_ACCESS_KEY)
if [ -z "$DEMO_KEY" ]; then DEMO_KEY=$(openssl rand -hex 12); envset DEMO_ACCESS_KEY "$DEMO_KEY"; echo "Generated a new DEMO_ACCESS_KEY (saved to .env)"; fi
SECRET_JSON=$(OPENAI_API_KEY="$OPENAI_API_KEY" DEMO_KEY="$DEMO_KEY" $PY -c 'import json,os;print(json.dumps({"OPENAI_API_KEY":os.environ["OPENAI_API_KEY"],"DEMO_ACCESS_KEY":os.environ["DEMO_KEY"]}))')
if aws secretsmanager describe-secret --secret-id $SECRET --region $REGION >/dev/null 2>&1; then
  run aws secretsmanager put-secret-value --secret-id $SECRET --secret-string "$SECRET_JSON" --region $REGION >/dev/null
else
  run aws secretsmanager create-secret --name $SECRET --secret-string "$SECRET_JSON" --region $REGION --tags Key=project,Value=claimpilot >/dev/null
fi
SECRET_ARN=$([ -n "$DRY" ] && echo "arn:aws:secretsmanager:${REGION}:${ACCOUNT}:secret:${SECRET}-DRYRUN" || aws secretsmanager describe-secret --secret-id $SECRET --region $REGION --query ARN --output text)
envset APP_SECRET_ARN "$SECRET_ARN"

echo "== 2/6 HealthLake datastore (first creation takes 20-30 min, ~\$0.27/hr while it exists)"
run $PY scripts/healthlake.py create
run $PY scripts/healthlake.py status --wait
run $PY scripts/healthlake.py load
HL_ID=$([ -n "$DRY" ] && echo DRYRUN || envget HEALTHLAKE_DATASTORE_ID)

echo "== 3/6 Build and deploy the stack"
run sam build --template-file template.yaml
run sam deploy --stack-name $STACK --region $REGION --resolve-s3 --capabilities CAPABILITY_IAM \
  --no-confirm-changeset --no-fail-on-empty-changeset --tags project=claimpilot \
  --parameter-overrides HealthLakeDatastoreId=$HL_ID AppSecretArn=$SECRET_ARN NotificationEmail=$EMAIL \
  ExtractionModel=$(envget EXTRACTION_MODEL) EscalationModel=$(envget ESCALATION_MODEL) ResolutionModel=$(envget RESOLUTION_MODEL) \
  AllowedOrigin=$ORIGIN MaxConcurrentClaims=6

echo "== 4/6 Sample claims"
run aws s3 sync data/claims s3://$BUCKET/samples/ --region $REGION --only-show-errors
run aws s3 cp data/labels.json s3://$BUCKET/samples/labels.json --region $REGION --only-show-errors

echo "== 5/6 API URL"
API_URL=$([ -n "$DRY" ] && echo https://DRYRUN.execute-api.$REGION.amazonaws.com || aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query "Stacks[0].Outputs[?contains(OutputKey,'Api')].OutputValue | [0]" --output text)
echo "API: $API_URL"
envset API_URL "$API_URL"

echo "== 6/6 Web UI (Amplify)"
APP_ID=$(aws amplify list-apps --region $REGION --query "apps[?name=='claimpilot'].appId | [0]" --output text 2>/dev/null || true)
if [ -n "$APP_ID" ] && [ "$APP_ID" != "None" ]; then
  ENVVARS=$(aws amplify get-app --app-id $APP_ID --region $REGION --query app.environmentVariables --output json | API_URL="$API_URL" $PY -c 'import json,os,sys;d=json.load(sys.stdin) or {};d["VITE_API_URL"]=os.environ["API_URL"];print(json.dumps(d))')
  run aws amplify update-app --app-id $APP_ID --region $REGION --environment-variables "$ENVVARS" >/dev/null
  run aws amplify start-job --app-id $APP_ID --branch-name main --job-type RELEASE --region $REGION >/dev/null
  echo "Amplify app $APP_ID updated with the new API URL; a rebuild was started."
else
  echo "No Amplify app named 'claimpilot'. Create it in the console (see docs/redeploy.md), with VITE_API_URL=$API_URL"
fi

cat <<MSG

Done.
  Demo key:  $DEMO_KEY   (also in .env)
  API:       $API_URL
  Reminder:  confirm the SNS subscription email sent to $EMAIL.
  Tear down: bash scripts/teardown.sh
MSG
