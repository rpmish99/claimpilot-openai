"""Local stand-in for API Gateway + Lambda + S3, for development and as an offline demo fallback.

    python scripts/local_api.py            # http://localhost:8790
    cd web && npm run dev                  # UI at http://localhost:5173 (VITE_API_URL defaults to :8790)

Same routes as src/handlers.py. Uses HealthLake if HEALTHLAKE_ENDPOINT is set in .env, else the
local FHIR store. Pipeline runs in a background thread per intake (like the S3-triggered Lambda).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from claimpilot import batch as batches  # noqa: E402
from claimpilot import config  # noqa: E402
from claimpilot.fhir_store import store_from_env  # noqa: E402
from claimpilot.notify import Notifier  # noqa: E402
from claimpilot.pipeline import new_intake, run_pipeline  # noqa: E402
from claimpilot.repository import LocalRepository  # noqa: E402
from claimpilot.resolution_agent import ClaimContext, build_fhir_claim  # noqa: E402
from claimpilot.schemas import ClaimExtraction, Finding  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
INBOX = ROOT / ".local_store/inbox"
store, repo, notifier = store_from_env(), LocalRepository(ROOT / ".local_store/intakes"), Notifier()
LABELS = json.loads((ROOT / "data/labels.json").read_text())


POOL = ThreadPoolExecutor(4)  # like the queue consumer's MaximumConcurrency


def start(intake_id: str, path: Path, batch_id: str | None = None):
    def work():
        route, cost = "error", None
        try:
            r = run_pipeline(intake_id, path.read_bytes(), path.name, store=store, repo=repo, notifier=notifier)
            route, cost = r["route"], r.get("cost_usd")
        except Exception as e:  # recorded on the intake by the supervisor
            print(f"[{intake_id}] pipeline error: {e}")
        if batch_id:
            batches.finish_claim(repo, notifier, batch_id, route, cost)
    if batch_id:
        POOL.submit(work)
    else:
        threading.Thread(target=work, daemon=True).start()


def summary(i: dict) -> dict:
    keep = ("intake_id", "filename", "source", "sample", "batch_id", "created_at", "completed_at", "status", "route",
            "cost_usd", "total_ms", "claim_id", "review", "steps")
    out = {k: i.get(k) for k in keep}
    x = i.get("extraction") or {}
    out["patient"] = " ".join(filter(None, [(x.get("patient_first_name") or {}).get("value"),
                                            (x.get("patient_last_name") or {}).get("value")]))
    out["total_charge"] = (x.get("total_charge") or {}).get("value")
    return out


def review(item: dict, data: dict) -> dict:
    from datetime import datetime, timezone
    from claimpilot.feedback import record_label
    action, note = data.get("action"), (data.get("note") or "").strip()
    corrections = {k: str(v).strip() for k, v in (data.get("corrections") or {}).items() if str(v).strip()}
    claim, msgs = item.get("claim"), list(item.get("notifications") or [])
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
        msgs.append({"audience": "provider", "subject": f"Cascadia Health Plan: claim intake {item['intake_id']} returned",
                     "body": text})
    else:
        return {"error": "action must be 'approve' or 'return'"}
    rv = {"state": "done", "action": action, "note": note, "by": "demo-processor",
          "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "corrections": corrections}
    record_label(repo, item, action, corrections, note)
    return repo.update(item["intake_id"], review=rv, claim=claim, claim_id=claim["id"] if claim else None,
                       notifications=msgs)


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type, x-demo-key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/samples":
            return self._send(200, [{"claim_id": l["claim_id"], "file": l["file"].split("/")[-1], "scenario": l["scenario"],
                                     "note": l["note"], "expected_route": l["expected_route"]} for l in LABELS])
        if p == "/intakes":
            return self._send(200, [summary(i) for i in repo.list(200)])
        if p == "/batches":
            return self._send(200, repo.list_batches(20))
        if p == "/feedback":
            return self._send(200, repo.list_kind("label"))
        m = re.match(r"^/batches/([A-Za-z0-9-]+)$", p)
        if m:
            b = repo.get("B#" + m.group(1))
            if not b:
                return self._send(404, {})
            b["intakes"] = [summary(i) for i in repo.list(500) if i.get("batch_id") == m.group(1)]
            return self._send(200, b)
        m = re.match(r"^/intakes/([a-f0-9]+)(/pdf)?$", p)
        if m:
            item = repo.get(m.group(1))
            if not item:
                return self._send(404, {"error": "not found"})
            if m.group(2):
                return self._send(200, {"url": f"http://localhost:{PORT}/files/{item['intake_id']}/{item['filename']}"})
            return self._send(200, item)
        m = re.match(r"^/files/([a-f0-9]+)/(.+)$", p)
        if m:
            f = INBOX / m.group(1) / m.group(2)
            return self._send(200, f.read_bytes(), "application/pdf") if f.exists() else self._send(404, {})
        self._send(404, {"error": p})

    def do_POST(self):
        p = self.path
        data = json.loads(self._body() or b"{}")
        if p == "/intakes":
            intake_id = uuid.uuid4().hex[:10]
            if data.get("sample"):
                fname = Path(data["sample"]).name
                dest = INBOX / intake_id / fname
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / "data/claims" / fname, dest)
                new_intake(repo, intake_id, fname, "sample", sample=fname)
                start(intake_id, dest)
                return self._send(201, {"intake_id": intake_id})
            fname = re.sub(r"[^A-Za-z0-9._-]", "_", data.get("filename") or "claim.pdf")[:80]
            new_intake(repo, intake_id, fname, "upload")
            return self._send(201, {"intake_id": intake_id, "upload_url": f"http://localhost:{PORT}/upload/{intake_id}/{fname}"})
        if p == "/batches":
            batch_id = batches.new_batch_id()
            if data.get("sample"):
                files = [l["file"].split("/")[-1] for l in LABELS]
                batches.ensure_batch(repo, batch_id, len(files), "sample")
                for f in files:
                    key = f"batches/{batch_id}/{f}"
                    intake_id = batches.intake_id_for_key(key)
                    dest = INBOX / intake_id / f
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(ROOT / "data/claims" / f, dest)
                    new_intake(repo, intake_id, f, "batch", batch_id=batch_id)
                    start(intake_id, dest, batch_id)
                return self._send(201, {"batch_id": batch_id, "expected": len(files)})
            names = [re.sub(r"[^A-Za-z0-9._-]", "_", n)[:80] for n in (data.get("files") or [])]
            batches.ensure_batch(repo, batch_id, len(names), "upload")
            return self._send(201, {"batch_id": batch_id, "expected": len(names), "uploads": [
                {"filename": n, "upload_url": f"http://localhost:{PORT}/upload-batch/{batch_id}/{n}"} for n in names]})
        m = re.match(r"^/intakes/([a-f0-9]+)/review$", p)
        if m:
            item = repo.get(m.group(1))
            return self._send(200, review(item, data)) if item else self._send(404, {})
        self._send(404, {"error": p})

    def do_PUT(self):
        m = re.match(r"^/upload-batch/([A-Za-z0-9-]+)/(.+)$", self.path)
        if m:
            batch_id, fname = m.groups()
            intake_id = batches.intake_id_for_key(f"batches/{batch_id}/{fname}")
            dest = INBOX / intake_id / fname
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self._body())
            new_intake(repo, intake_id, fname, "batch", batch_id=batch_id)
            start(intake_id, dest, batch_id)
            return self._send(200, {})
        m = re.match(r"^/upload/([a-f0-9]+)/(.+)$", self.path)
        if not m:
            return self._send(404, {})
        dest = INBOX / m.group(1) / m.group(2)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._body())
        start(m.group(1), dest)
        self._send(200, {})

    def log_message(self, fmt, *args):
        if "/intakes/" not in self.path:  # keep polling noise out of the console
            super().log_message(fmt, *args)


if __name__ == "__main__":
    mode = "HealthLake" if config and __import__("os").environ.get("HEALTHLAKE_ENDPOINT") else "local FHIR store"
    print(f"ClaimPilot local API on http://localhost:{PORT}  ({mode}; extraction={config.EXTRACTION_MODEL}, "
          f"resolution={config.RESOLUTION_MODEL})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
