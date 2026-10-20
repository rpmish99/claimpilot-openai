"""Export processor reviews as eval cases (evals/feedback/labels.json + the claim PDFs).

    python scripts/export_feedback.py                 # from the deployed API (API_URL + DEMO_ACCESS_KEY in .env)
    python scripts/export_feedback.py --local         # from the local store (scripts/local_api.py)
    python evals/run.py --include-feedback            # run the evals with them added

Each label carries the processor's decision (approve -> expected auto_accept, return -> expected
return_to_provider) and the verified values of the fields that drive matching and payment.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
OUT = ROOT / "evals/feedback"


def fetch_remote() -> list[dict]:
    api = os.environ.get("API_URL", "https://am4rtqvz3k.execute-api.us-east-1.amazonaws.com").rstrip("/")
    req = urllib.request.Request(api + "/feedback", headers={"x-demo-key": os.environ["DEMO_ACCESS_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    args = ap.parse_args()
    if args.local:
        from claimpilot.repository import LocalRepository
        labels = LocalRepository(ROOT / ".local_store/intakes").list_kind("label")
    else:
        labels = fetch_remote()
    (OUT / "files").mkdir(parents=True, exist_ok=True)
    s3 = None
    cases = []
    for lab in labels:
        dest = OUT / "files" / f"{lab['source_intake']}.pdf"
        if not dest.exists():
            if args.local:
                src = next((ROOT / ".local_store/inbox" / lab["source_intake"]).glob("*.pdf"), None)
                if not src:
                    continue
                shutil.copy(src, dest)
            else:
                import boto3
                s3 = s3 or boto3.client("s3")
                bucket = f"claimpilot-{boto3.client('sts').get_caller_identity()['Account']}-us-east-1"
                s3.download_file(bucket, lab["s3_key"], str(dest))
        vf = lab["verified_fields"]
        cases.append({
            "claim_id": f"FB-{lab['source_intake']}", "path": str(dest.relative_to(ROOT)), "source": "processor_review",
            "scenario": "processor_review", "note": lab.get("note", ""), "expected_route": lab["expected_route"],
            "system_route_at_review": lab["system_route"], "field_corrections": lab["field_corrections"],
            "printed": {"partial": True, "insured_id": vf.get("insured_id"), "patient_last_name": vf.get("patient_last_name"),
                        "patient_first_name": vf.get("patient_first_name"), "patient_birth_date": vf.get("patient_birth_date"),
                        "billing_npi": vf.get("billing_provider_npi"), "total_charge": vf.get("total_charge")},
        })
    (OUT / "labels.json").write_text(json.dumps(cases, indent=2))
    corrected = sum(1 for c in cases if c["field_corrections"])
    print(f"Exported {len(cases)} reviewed claims ({corrected} with field corrections) -> {OUT / 'labels.json'}")


if __name__ == "__main__":
    main()
