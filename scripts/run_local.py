"""Run the ClaimPilot pipeline on one or more claim PDFs from the command line.

    python scripts/run_local.py data/claims/CLM-1004_member_id_letter_o.pdf
    python scripts/run_local.py --all

Uses AWS HealthLake if HEALTHLAKE_ENDPOINT is set in .env, else the local FHIR store.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from claimpilot.fhir_store import store_from_env  # noqa: E402
from claimpilot.notify import Notifier  # noqa: E402
from claimpilot.pipeline import new_intake, run_pipeline  # noqa: E402
from claimpilot.repository import LocalRepository  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    files = sorted((ROOT / "data/claims").glob("*.pdf")) if args.all else [Path(f) for f in args.files]
    store, repo, notifier = store_from_env(), LocalRepository(ROOT / ".local_store/intakes"), Notifier()
    for f in files:
        intake_id = uuid.uuid4().hex[:10]
        new_intake(repo, intake_id, f.name, "cli")
        r = run_pipeline(intake_id, f.read_bytes(), f.name, store=store, repo=repo, notifier=notifier)
        res = r.get("resolution") or {}
        print(f"\n=== {f.name}  ->  {r['route'].upper()}   ({r['total_ms']/1000:.1f}s, ${r['cost_usd']:.4f})")
        print("  reasons:   ", "; ".join(r["decision_reasons"]))
        for fd in r.get("findings", []):
            print(f"  finding:    {fd['rule_id']} {fd['message']}")
        for ev in r.get("agent_events", []):
            print(f"  tool:       {ev['tool']}({json.dumps(ev['args'])[:110]})")
        for c in res.get("corrections", []):
            print(f"  corrected:  {c['field']}: {c['original']} -> {c['corrected']} ({c['reason']})")
        print("  claim:     ", r.get("claim_id"))
        print("  summary:   ", res.get("processor_summary"))
        if res.get("provider_message"):
            print("  provider:  ", res["provider_message"])


if __name__ == "__main__":
    main()
