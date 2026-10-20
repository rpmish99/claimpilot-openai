"""Publish local eval results to the OpenAI Evals dashboard (platform.openai.com/evaluations).

    python evals/push_to_openai_evals.py "evals/results/cascade t=0.90.jsonl" "evals/results/luna only.jsonl" ...

The local harness (evals/run.py) runs the real pipeline: PDF -> agents -> FHIR -> routing. This script
uploads its outputs as one Eval run per configuration, graded by OpenAI string-check graders, so routing,
field accuracy and the two safety checks are visible and comparable on the platform next to Traces.
Synthetic data only; do not push PHI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
STATE = ROOT / "evals/results/openai_eval.json"
FIELDS = ["claim_id", "scenario", "run", "route", "expected_route", "wrong_auto_accept", "wrong_return",
          "insured_id", "expected_insured_id", "total_charge", "expected_total_charge", "billing_npi",
          "expected_billing_npi", "escalated", "cost_usd"]


def item(row: dict) -> dict:
    v = row.get("values") or {}
    s = lambda x: "" if x is None else str(x)  # noqa: E731
    money = lambda x: "" if x in (None, "") else f"{float(str(x).replace(',', '')):.2f}"  # noqa: E731
    return {
        "claim_id": row["claim_id"], "scenario": row.get("scenario", ""), "run": str(row.get("run", 0)),
        "route": s(row.get("route")), "expected_route": s(row.get("expected_route")),
        "wrong_auto_accept": str(bool(row.get("false_auto_accept"))).lower(),
        "wrong_return": str(bool(row.get("false_return"))).lower(),
        "insured_id": s(v.get("insured_id")), "expected_insured_id": s(v.get("expected_insured_id")),
        "total_charge": money(v.get("total_charge")), "expected_total_charge": money(v.get("expected_total_charge")),
        "billing_npi": s(v.get("billing_npi")), "expected_billing_npi": s(v.get("expected_billing_npi")),
        "escalated": str(bool(row.get("escalated"))).lower(), "cost_usd": f"{row.get('cost') or 0:.5f}",
    }


def get_eval(client: OpenAI) -> str:
    if STATE.exists():
        return json.loads(STATE.read_text())["eval_id"]
    check = lambda name, a, b: {"type": "string_check", "name": name, "input": "{{item.%s}}" % a,  # noqa: E731
                                "reference": b if b in ("true", "false") else "{{item.%s}}" % b, "operation": "eq"}
    ev = client.evals.create(
        name="ClaimPilot: claims intake routing",
        metadata={"project": "claimpilot", "data": "synthetic"},
        data_source_config={"type": "custom", "include_sample_schema": False, "item_schema": {
            "type": "object", "properties": {f: {"type": "string"} for f in FIELDS}, "required": FIELDS}},
        testing_criteria=[
            check("Correct route", "route", "expected_route"),
            check("No wrong auto-accept", "wrong_auto_accept", "false"),
            check("No wrong return to provider", "wrong_return", "false"),
            check("Member ID read correctly", "insured_id", "expected_insured_id"),
            check("Total charge read correctly", "total_charge", "expected_total_charge"),
            check("Billing NPI read correctly", "billing_npi", "expected_billing_npi"),
        ],
    )
    STATE.write_text(json.dumps({"eval_id": ev.id}))
    return ev.id


def main(paths: list[str]):
    client = OpenAI()
    eval_id = get_eval(client)
    for p in paths:
        rows = [json.loads(line) for line in Path(p).read_text().splitlines() if line.strip()]
        rows = [r for r in rows if "error" not in r]
        run = client.evals.runs.create(
            eval_id, name=Path(p).stem,
            data_source={"type": "jsonl", "source": {"type": "file_content", "content": [{"item": item(r)} for r in rows]}})
        print(f"{Path(p).stem}: {len(rows)} items -> run {run.id}  {getattr(run, 'report_url', '')}")
    print(f"Eval {eval_id}: https://platform.openai.com/evaluations/{eval_id}")


if __name__ == "__main__":
    main(sys.argv[1:])
