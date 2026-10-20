"""Evaluate ClaimPilot against the labeled synthetic claims.

    python evals/run.py                                   # models from .env, local FHIR store
    python evals/run.py --runs 3                          # repeat to see run-to-run variance
    python evals/run.py --extraction-model gpt-5.2 --resolution-model gpt-5.2 --tag single-model

Metrics
  field accuracy     extracted values vs. what is printed on the form (13 fields per claim)
  routing accuracy   final route vs. expected route
  match accuracy     patient/coverage resolved to the right FHIR ids
  false auto-accept  claims auto-accepted that should not have been (the key safety metric)
  latency / cost     end to end, from API usage
Writes evals/results/<tag>.jsonl and appends a row to evals/results/summary.md.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

ap = argparse.ArgumentParser()
ap.add_argument("--runs", type=int, default=1)
ap.add_argument("--extraction-model")
ap.add_argument("--resolution-model")
ap.add_argument("--escalation-model", help="'none' disables the extraction cascade")
ap.add_argument("--threshold", type=float, help="escalate below this field confidence (default 0.9)")
ap.add_argument("--include-feedback", action="store_true", help="also run processor-review labels from evals/feedback/")
ap.add_argument("--tag")
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--only", help="comma-separated claim ids")
args = ap.parse_args()
if args.extraction_model:
    os.environ["EXTRACTION_MODEL"] = args.extraction_model
if args.resolution_model:
    os.environ["RESOLUTION_MODEL"] = args.resolution_model
if args.threshold is not None:
    os.environ["ESCALATE_BELOW_CONFIDENCE"] = str(args.threshold)
if args.escalation_model:
    os.environ["ESCALATION_MODEL"] = "" if args.escalation_model == "none" else args.escalation_model
os.environ.pop("HEALTHLAKE_ENDPOINT", None)  # evals use the local store: fast, isolated, repeatable

from claimpilot import config  # noqa: E402
from claimpilot.fhir_store import LocalFhirStore  # noqa: E402
from claimpilot.notify import Notifier  # noqa: E402
from claimpilot.pipeline import new_intake, run_pipeline  # noqa: E402
from claimpilot.repository import LocalRepository  # noqa: E402

RESULTS = ROOT / "evals/results"
HARD = {"degraded_fax", "handwritten_single_line", "handwritten_multi_line", "phone_photo_skewed", "lowres_fax_dependent"}
RESULTS.mkdir(parents=True, exist_ok=True)
labels = json.loads((ROOT / "data/labels.json").read_text())
FEEDBACK = ROOT / "evals/feedback/labels.json"
if args.include_feedback and FEEDBACK.exists():
    labels += json.loads(FEEDBACK.read_text())
if args.only:
    labels = [l for l in labels if l["claim_id"] in args.only.split(",")]
tag = args.tag or f"{config.EXTRACTION_MODEL}>{config.ESCALATION_MODEL or 'none'}+{config.RESOLUTION_MODEL}"
store = LocalFhirStore(ROOT / "data/fhir/seed_bundle.json")  # fresh in-memory store, no persisted claims
repo = LocalRepository(ROOT / ".local_store/eval_intakes")


def norm(v):
    if v is None:
        return None
    return str(v).strip().upper().replace(" ", "")


def money(v):
    try:
        return round(float(str(v).replace(",", "").replace("$", "")), 2)
    except (TypeError, ValueError):
        return None


def score_fields(x: dict, p: dict) -> dict[str, bool]:
    if p.get("partial"):  # processor-review labels verify only some fields
        g = lambda k: (x.get(k) or {}).get("value")  # noqa: E731
        keys = {"insured_id": "insured_id", "patient_last_name": "patient_last_name", "patient_first_name": "patient_first_name",
                "patient_birth_date": "patient_birth_date", "billing_npi": "billing_provider_npi", "total_charge": "total_charge"}
        return {k: (money(g(f)) == money(p[k]) if k == "total_charge" else norm(g(f)) == norm(p[k]))
                for k, f in keys.items() if p.get(k) is not None}
    g = lambda k: (x.get(k) or {}).get("value")  # noqa: E731
    lines = x.get("service_lines") or []
    s = {
        "insured_id": norm(g("insured_id")) == norm(p["insured_id"]),
        "patient_last_name": norm(g("patient_last_name")) == norm(p["patient_last_name"]),
        "patient_first_name": norm(g("patient_first_name")) == norm(p["patient_first_name"]),
        "insured_last_name": norm(g("insured_last_name")) == norm(p["insured_last_name"]),
        "diagnosis_codes": [norm(c) for c in x.get("diagnosis_codes") or []] == [norm(c) for c in p["diagnosis_codes"]],
        "cpt_codes": [norm(l.get("procedure_code")) for l in lines] == [norm(c) for c in p["cpt_codes"]],
        "line_charges": [money(l.get("charge")) for l in lines] == [money(c) for c in p["line_charges"]],
        "total_charge": money(g("total_charge")) == money(p["total_charge"]),
        "rendering_npi": all(norm(l.get("rendering_npi")) == norm(p["rendering_npi"]) for l in lines) and bool(lines),
        "billing_npi": norm(g("billing_provider_npi")) == norm(p["billing_npi"]),
        "federal_tax_id": norm(g("federal_tax_id")) == norm(p["federal_tax_id"]),
        "patient_account_no": norm(g("patient_account_no")) == norm(p["patient_account_no"]),
    }
    if p["patient_birth_date"]:
        s["patient_birth_date"] = g("patient_birth_date") == p["patient_birth_date"]
    else:  # obscured on purpose: correct behaviour is blank or flagged low-confidence
        f = x.get("patient_birth_date") or {}
        s["patient_birth_date"] = (not f.get("value")) or f.get("confidence", 1) < 0.85
    return s


def run_one(label: dict, run: int) -> dict:
    pdf = ROOT / label["path"] if label.get("path") else ROOT / "data" / label["file"]
    intake_id = uuid.uuid4().hex[:10]
    new_intake(repo, intake_id, pdf.name, "eval")
    try:
        r = run_pipeline(intake_id, pdf.read_bytes(), pdf.name, store=store, repo=repo, notifier=Notifier())
    except Exception as e:  # noqa: BLE001
        return {"claim_id": label["claim_id"], "run": run, "error": str(e)}
    res = r.get("resolution") or {}
    fields = score_fields(r.get("extraction") or {}, label["printed"])
    exp = label.get("expected_match") or {"patient_id": None, "coverage_id": None}
    x = r.get("extraction") or {}
    v = lambda k: (x.get(k) or {}).get("value")  # noqa: E731
    match_ok = (exp["patient_id"] is None or res.get("patient_id") == exp["patient_id"]) and \
               (exp["coverage_id"] is None or res.get("coverage_id") == exp["coverage_id"])
    return {
        "claim_id": label["claim_id"], "scenario": label.get("scenario", "processor_review"), "run": run,
        "expected_route": label["expected_route"], "route": r.get("route"),
        "route_ok": r.get("route") == label["expected_route"],
        "false_auto_accept": r.get("route") == "auto_accept" and label["expected_route"] != "auto_accept",
        "false_return": r.get("route") == "return_to_provider" and label["expected_route"] != "return_to_provider",
        "unneeded_review": r.get("route") == "needs_review" and label["expected_route"] == "auto_accept",
        "match_ok": match_ok, "fields": fields, "field_acc": sum(fields.values()) / len(fields),
        "ms": r.get("total_ms"), "cost": r.get("cost_usd"), "reasons": r.get("decision_reasons"),
        "corrections": res.get("corrections"), "guardrail": r.get("guardrail"),
        "intake_id": intake_id, "source": label.get("source", "synthetic"),
        "values": {"insured_id": v("insured_id"), "expected_insured_id": label["printed"].get("insured_id"),
                   "total_charge": v("total_charge"), "expected_total_charge": label["printed"].get("total_charge"),
                   "billing_npi": v("billing_provider_npi"), "expected_billing_npi": label["printed"].get("billing_npi")},
        "escalated": bool((r.get("cascade") or {}).get("escalated")),
        "escalation_reasons": (r.get("cascade") or {}).get("reasons"),
    }


jobs = [(l, i) for i in range(args.runs) for l in labels]
print(f"Evaluating {len(labels)} claims x {args.runs} run(s)  [{tag}]")
with ThreadPoolExecutor(args.workers) as ex:
    rows = list(ex.map(lambda j: run_one(*j), jobs))

ok = [r for r in rows if "error" not in r]
for r in sorted(rows, key=lambda r: (r["claim_id"], r["run"])):
    if "error" in r:
        print(f"  {r['claim_id']}  ERROR {r['error'][:120]}")
        continue
    miss = [k for k, v in r["fields"].items() if not v]
    print(f"  {r['claim_id']} {r['scenario']:<26} {'✓' if r['route_ok'] else '✗'} {r['route']:<19} "
          f"(exp {r['expected_route']:<19}) fields {r['field_acc']:.0%} {'↑sol ' if r['escalated'] else ''}"
          f"{('miss: ' + ','.join(miss)) if miss else ''}")

ms = sorted(r["ms"] for r in ok)
summary = {
    "tag": tag, "when": datetime.now().isoformat(timespec="minutes"), "n": len(rows), "errors": len(rows) - len(ok),
    "routing_acc": sum(r["route_ok"] for r in ok) / max(len(ok), 1),
    "false_auto_accept": sum(r["false_auto_accept"] for r in ok),
    "false_return": sum(r["false_return"] for r in ok),
    "unneeded_review": sum(r["unneeded_review"] for r in ok),
    "match_acc": sum(r["match_ok"] for r in ok) / max(len(ok), 1),
    "field_acc": statistics.mean(r["field_acc"] for r in ok) if ok else 0,
    "p50_s": ms[len(ms) // 2] / 1000 if ms else 0,
    "p95_s": ms[min(len(ms) - 1, int(len(ms) * 0.95))] / 1000 if ms else 0,
    "avg_cost": statistics.mean((r["cost"] or 0) for r in ok) if ok else 0,
    "escalation_rate": sum(r["escalated"] for r in ok) / max(len(ok), 1),
    "hard_scan_field_acc": statistics.mean([r["field_acc"] for r in ok if r["scenario"] in HARD] or [0]),
    "threshold": float(os.environ.get("ESCALATE_BELOW_CONFIDENCE", "0.9")),
}
print("\n" + json.dumps(summary, indent=1))
with (RESULTS / f"{tag.replace('/', '_')}.jsonl").open("w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
md = RESULTS / "summary.md"
if not md.exists():
    md.write_text("| run | n | routing acc | false auto-accepts | false returns | unneeded reviews | FHIR match acc | field acc | hard-scan field acc | escalated | p50 s | p95 s | avg $/claim |\n"
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
with md.open("a") as f:
    f.write(f"| {tag} ({summary['when']}) | {summary['n']} | {summary['routing_acc']:.0%} | {summary['false_auto_accept']} | {summary['false_return']} | {summary['unneeded_review']} | "
            f"{summary['match_acc']:.0%} | {summary['field_acc']:.1%} | {summary['hard_scan_field_acc']:.1%} | "
            f"{summary['escalation_rate']:.0%} | {summary['p50_s']:.1f} | {summary['p95_s']:.1f} | "
            f"${summary['avg_cost']:.4f} |\n")
