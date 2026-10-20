"""Processor reviews become labeled examples.

Every human decision on an exception is recorded with the system's original output, the processor's field
corrections and the final decision. scripts/export_feedback.py turns these into eval cases, so the eval set grows
from real production exceptions and every threshold or model change is re-measured against them.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .repository import LABEL_PREFIX, Repository

# Fields a processor can correct in the review panel (the ones that drive matching and payment).
CORRECTABLE = ["insured_id", "patient_last_name", "patient_first_name", "patient_birth_date",
               "billing_provider_npi", "total_charge"]


def record_label(repo: Repository, item: dict, action: str, corrections: dict, note: str) -> dict:
    x = item.get("extraction") or {}
    corrected = {k: (corrections.get(k) if k in corrections else (x.get(k) or {}).get("value")) for k in CORRECTABLE}
    label = {
        "intake_id": LABEL_PREFIX + item["intake_id"], "kind": "label", "source_intake": item["intake_id"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filename": item.get("filename"), "s3_key": item.get("s3_key"), "batch_id": item.get("batch_id"),
        "system_route": item.get("route"), "human_decision": action,
        "expected_route": "auto_accept" if action == "approve" else "return_to_provider",
        "field_corrections": {k: v for k, v in corrections.items() if k in CORRECTABLE},
        "verified_fields": corrected, "note": note,
        "models": {k: u.get("model") for k, u in (item.get("usage") or {}).items()},
    }
    repo.put(label)
    return label
