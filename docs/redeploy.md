# Rebuild or remove the AWS deployment

Everything except the web UI hosting is created and removed by two scripts. Both need an AWS login (`aws sso login`) and read `.env`.

## Rebuild

```bash
bash scripts/deploy.sh
```

Re-running it is safe; it updates what exists. `DRY_RUN=1` prints the steps without changing anything.

What it does: creates or updates the `claimpilot/app` secret from `OPENAI_API_KEY` and `DEMO_ACCESS_KEY` in `.env` (a demo key is generated if missing); creates the HealthLake datastore, waits for it, and loads `data/fhir/seed_bundle.json`; builds and deploys the SAM stack; uploads the sample claims; writes `API_URL`, `APP_SECRET_ARN` and the HealthLake values back to `.env`; and, if an Amplify app named `claimpilot` exists, points it at the new API and starts a rebuild.

Prerequisites: AWS CLI and SAM CLI, `.venv` with `requirements-dev.txt` installed, and `OPENAI_API_KEY` in `.env`. Optional `.env` keys: `NOTIFICATION_EMAIL`, `ALLOWED_ORIGIN`.

Time and cost: about 30 minutes, nearly all of it HealthLake creation. HealthLake costs about $0.27/hr (~$195/month) while it exists, so tear down when you are done. Everything else is pay-per-use and near zero when idle.

After it finishes:
1. Confirm the SNS subscription email, or notifications won't arrive.
2. The API URL changes on every rebuild. If you manage Amplify by hand, set `VITE_API_URL` to the new `API_URL` and redeploy.

## Web UI on Amplify

`teardown.sh` does not touch the Amplify app. After a teardown its UI loads but cannot reach a backend. To remove it: `aws amplify delete-app --app-id dyc07h1e3zuao`. To create it again, in the Amplify console choose *Host web app*, connect the GitHub repo `rpmish99/claimpilot-openai`, branch `main`, set `AMPLIFY_MONOREPO_APP_ROOT=web` and `VITE_API_URL=<API_URL>`, name it `claimpilot`. If its URL differs from the one in `template.yaml`, pass it as `ALLOWED_ORIGIN` to `deploy.sh` (CORS).

## Remove

```bash
bash scripts/teardown.sh        # asks you to type "delete"
```

Deletes the stack, the HealthLake datastore, the secret (immediately, so the name can be reused), and the Lambda log groups, and clears the stale values in `.env`.

Lost on teardown: the DynamoDB intake history and review labels, and the S3 inbox. Kept: all code, the synthetic data, the evals, and `.env`. Not removed: the Amplify app, the SAM-managed artifact bucket (`aws-sam-cli-managed-default` or the `--resolve-s3` one; it costs cents), and OpenAI-side data (Evals runs, traces).

Without HealthLake, local runs still work: leave `HEALTHLAKE_ENDPOINT` empty and the pipeline uses the in-memory FHIR store.
