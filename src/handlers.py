"""AWS Lambda entry points.

process_handler  SQS intake queue <- S3 ObjectCreated events:
                   inbox/<intake_id>/<file>.pdf      single upload
                   batches/<batch_id>/<file>.pdf     one claim of a batch drop
                   batches/<batch_id>/manifest.json  {"expected": N} for drops made without the API
api_handler      HTTP API for the web UI: uploads, batches, samples, status, review actions
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
import uuid
from datetime import datetime, timezone

import boto3

_s3 = boto3.client("s3")
BUCKET = os.environ.get("CLAIMS_BUCKET", "")


def _load_secrets():
    """Pull the OpenAI key (and demo access key) from Secrets Manager once per cold start."""
    arn = os.environ.get("APP_SECRET_ARN")
    if arn and not os.environ.get("OPENAI_API_KEY"):
        s = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"])
        os.environ["OPENAI_API_KEY"] = s["OPENAI_API_KEY"]
        os.environ["DEMO_ACCESS_KEY"] = s.get("DEMO_ACCESS_KEY", "")


_load_secrets()

from claimpilot import batch as batches  # noqa: E402
from claimpilot.fhir_store import store_from_env  # noqa: E402
from claimpilot.notify import notifier_from_env  # noqa: E402
from claimpilot.pipeline import new_intake, run_pipeline  # noqa: E402
from claimpilot.repository import repository_from_env  # noqa: E402

store, repo, notifier = store_from_env(), repository_from_env(), notifier_from_env()


# ---------------------------------------------------------------- pipeline
def process_handler(event, _ctx):
    """SQS batch of S3 events. Failed messages are reported individually, so SQS retries only those
    and moves repeat failures to the DLQ."""
    failures = []
    for msg in event.get("Records", []):
        try:
            body = json.loads(msg["body"])
            for rec in body.get("Records", []):  # absent for the s3:TestEvent sent on setup
                process_object(urllib.parse.unquote_plus(rec["s3"]["object"]["key"]))
        except Exception:  # noqa: BLE001
            failures.append({"itemIdentifier": msg["messageId"]})
    return {"batchItemFailures": failures}


def process_object(key: str):
    if m := re.match(r"batches/([A-Za-z0-9-]+)/manifest\.json$", key):
        manifest = json.loads(_s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
        batches.set_expected(repo, notifier, m.group(1), int(manifest["expected"]))
        return
    if m := re.match(r"batches/([A-Za-z0-9-]+)/([^/]+\.pdf)$", key, re.I):
        batch_id, filename = m.groups()
        intake_id = batches.intake_id_for_key(key)
        batches.ensure_batch(repo, batch_id, None, "s3-drop")
    elif m := re.match(r"inbox/([a-f0-9]+)/([^/]+)$", key):
        intake_id, filename = m.groups()
        batch_id = None
    else:
        return
    existing = repo.get(intake_id)
    if existing and existing.get("status") in ("complete", "error"):
        return  # redelivered event: already processed
    if not existing:
        new_intake(repo, intake_id, filename, "batch" if batch_id else "s3", s3_key=key, batch_id=batch_id)
    pdf = _s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    try:
        r = run_pipeline(intake_id, pdf, filename, store=store, repo=repo, notifier=notifier)
        route, cost = r["route"], r.get("cost_usd")
    except Exception:  # noqa: BLE001 - recorded on the intake by the supervisor; the batch still completes
        route, cost = "error", None
    if batch_id:
        batches.finish_claim(repo, notifier, batch_id, route, cost)


# ---------------------------------------------------------------- API
def _resp(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def _summary(i: dict) -> dict:
    keep = ("intake_id", "filename", "source", "sample", "batch_id", "created_at", "completed_at", "status", "route",
            "cost_usd", "total_ms", "claim_id", "review", "steps")
    out = {k: i.get(k) for k in keep}
    x = i.get("extraction") or {}
    out["patient"] = " ".join(filter(None, [(x.get("patient_first_name") or {}).get("value"),
                                            (x.get("patient_last_name") or {}).get("value")]))
    out["total_charge"] = (x.get("total_charge") or {}).get("value")
    return out


def api_handler(event, _ctx):
    method = event["requestContext"]["http"]["method"]
    path = event["rawPath"]
    if method == "OPTIONS":  # CORS preflight; the ANY route forwards it here. API Gateway adds the CORS headers.
        return {"statusCode": 204}
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    expected = os.environ.get("DEMO_ACCESS_KEY")
    if expected and headers.get("x-demo-key") != expected:
        return _resp(401, {"error": "Missing or invalid demo access key"})
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    data = json.loads(body or "{}")

    if method == "GET" and path == "/samples":
        labels = json.loads(_s3.get_object(Bucket=BUCKET, Key="samples/labels.json")["Body"].read())
        return _resp(200, [{"claim_id": l["claim_id"], "file": l["file"].split("/")[-1], "scenario": l["scenario"],
                            "note": l["note"], "expected_route": l["expected_route"]} for l in labels])

    if method == "POST" and path == "/intakes":
        intake_id = uuid.uuid4().hex[:10]
        if data.get("sample"):  # copy a sample PDF into the inbox -> S3 event starts the pipeline
            fname = os.path.basename(data["sample"])
            new_intake(repo, intake_id, fname, "sample", sample=fname, s3_key=f"inbox/{intake_id}/{fname}")
            _s3.copy_object(Bucket=BUCKET, Key=f"inbox/{intake_id}/{fname}",
                            CopySource={"Bucket": BUCKET, "Key": f"samples/{fname}"})
            return _resp(201, {"intake_id": intake_id})
        fname = re.sub(r"[^A-Za-z0-9._-]", "_", data.get("filename") or "claim.pdf")[:80]
        key = f"inbox/{intake_id}/{fname}"
        new_intake(repo, intake_id, fname, "upload", s3_key=key)
        url = _s3.generate_presigned_url("put_object", ExpiresIn=300,
                                         Params={"Bucket": BUCKET, "Key": key, "ContentType": "application/pdf"})
        return _resp(201, {"intake_id": intake_id, "upload_url": url})

    if method == "GET" and path == "/intakes":
        return _resp(200, [_summary(i) for i in repo.list(200)])

    if method == "POST" and path == "/batches":
        batch_id = batches.new_batch_id()
        if data.get("sample"):  # every sample claim dropped as one batch
            labels = json.loads(_s3.get_object(Bucket=BUCKET, Key="samples/labels.json")["Body"].read())
            files = [l["file"].split("/")[-1] for l in labels]
            batches.ensure_batch(repo, batch_id, len(files), "sample")
            for f in files:
                _s3.copy_object(Bucket=BUCKET, Key=f"batches/{batch_id}/{f}", CopySource={"Bucket": BUCKET, "Key": f"samples/{f}"})
            return _resp(201, {"batch_id": batch_id, "expected": len(files)})
        names = [re.sub(r"[^A-Za-z0-9._-]", "_", n)[:80] for n in (data.get("files") or [])][:200]
        if not names:
            return _resp(400, {"error": "files: a list of PDF file names is required"})
        batches.ensure_batch(repo, batch_id, len(names), "upload")
        uploads = [{"filename": n, "upload_url": _s3.generate_presigned_url(
            "put_object", ExpiresIn=600,
            Params={"Bucket": BUCKET, "Key": f"batches/{batch_id}/{n}", "ContentType": "application/pdf"})} for n in names]
        return _resp(201, {"batch_id": batch_id, "expected": len(names), "uploads": uploads})

    if method == "GET" and path == "/feedback":
        return _resp(200, repo.list_kind("label"))

    if method == "GET" and path == "/batches":
        return _resp(200, repo.list_batches(20))

    m = re.match(r"^/batches/([A-Za-z0-9-]+)$", path)
    if m and method == "GET":
        b = repo.get("B#" + m.group(1))
        if not b:
            return _resp(404, {"error": "not found"})
        b["intakes"] = [_summary(i) for i in repo.list(500) if i.get("batch_id") == m.group(1)]
        return _resp(200, b)

    m = re.match(r"^/intakes/([a-f0-9]+)(/pdf|/review)?$", path)
    if m:
        intake_id, sub = m.groups()
        item = repo.get(intake_id)
        if not item:
            return _resp(404, {"error": "not found"})
        if method == "GET" and not sub:
            item.pop("error_trace", None)
            return _resp(200, item)
        if method == "GET" and sub == "/pdf":
            url = _s3.generate_presigned_url("get_object", ExpiresIn=600,
                                             Params={"Bucket": BUCKET, "Key": item["s3_key"],
                                                     "ResponseContentType": "application/pdf"})
            return _resp(200, {"url": url})
        if method == "POST" and sub == "/review":
            return _resp(200, review(item, data))
    return _resp(404, {"error": f"No route for {method} {path}"})


def review(item: dict, data: dict) -> dict:
    """Human-in-the-loop decision. Deterministic: no model call on this path."""
    from claimpilot import config
    from claimpilot.resolution_agent import ClaimContext, build_fhir_claim
    from claimpilot.schemas import ClaimExtraction, Finding

    from claimpilot.feedback import record_label

    action = data.get("action")
    note = (data.get("note") or "").strip()
    corrections = {k: str(v).strip() for k, v in (data.get("corrections") or {}).items() if str(v).strip()}
    claim = item.get("claim")
    msgs = list(item.get("notifications") or [])
    if action == "approve":
        if claim:
            claim = {**claim, "status": "active"}
        else:
            res = item.get("resolution") or {}
            # records created before box 19 was in the schema
            x = ClaimExtraction.model_validate({"additional_claim_info": {"value": None, "confidence": 1.0},
                                                **item["extraction"]})
            orgs = store.search("Organization", {"identifier": f"{config.NPI_SYSTEM}|{x.billing_provider_npi.value}"})
            if not (res.get("patient_id") and res.get("coverage_id") and res.get("rendering_practitioner_id") and orgs):
                return {"error": "Cannot approve: patient, coverage or provider is unresolved. Return it instead."}
            ctx = ClaimContext(intake_id=item["intake_id"], store=store, extraction=x,
                               findings=[Finding.model_validate(f) for f in item.get("findings", [])])
            claim = build_fhir_claim(ctx, res["patient_id"], res["coverage_id"], res["rendering_practitioner_id"],
                                     orgs[0]["id"], f"Approved by processor override. {note}".strip(), "active")
        store.create(claim)
    elif action == "return":
        if claim:
            claim = {**claim, "status": "cancelled"}
            store.create(claim)
        text = data.get("provider_message") or (item.get("resolution") or {}).get("provider_message") or note
        msg = {"audience": "provider", "subject": f"Cascadia Health Plan: claim intake {item['intake_id']} returned",
               "body": text}
        if notifier.topic_arn:
            notifier._sns.publish(TopicArn=notifier.topic_arn, Subject=msg["subject"][:99],
                                  Message=f"To: provider\n\n{text}")
            msg["sent_via"] = "sns"
        msgs.append(msg)
    else:
        return {"error": "action must be 'approve' or 'return'"}
    rv = {"state": "done", "action": action, "note": note, "by": "demo-processor",
          "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "corrections": corrections}
    record_label(repo, item, action, corrections, note)
    return repo.update(item["intake_id"], review=rv, claim=claim, claim_id=claim["id"] if claim else None,
                       notifications=msgs)
