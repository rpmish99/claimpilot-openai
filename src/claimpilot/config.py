"""Runtime configuration (env vars) and model pricing for cost tracking."""
from __future__ import annotations

import os

# Extraction cascade: the fast model reads every claim; code escalates to the stronger model only when the
# first read looks risky (poor scan, low confidence, or a rule failure we must not blame on the provider).
EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "gpt-6-luna")
EXTRACTION_EFFORT = os.environ.get("EXTRACTION_EFFORT", "low")
ESCALATION_MODEL = os.environ.get("ESCALATION_MODEL", "gpt-6.1-sol")  # empty string disables the cascade
ESCALATION_EFFORT = os.environ.get("ESCALATION_EFFORT", "medium")
ESCALATE_BELOW_CONFIDENCE = float(os.environ.get("ESCALATE_BELOW_CONFIDENCE", "0.9"))
RESOLUTION_MODEL = os.environ.get("RESOLUTION_MODEL", "gpt-6-luna")
RESOLUTION_EFFORT = os.environ.get("RESOLUTION_EFFORT", "low")

# Synthetic data only: traces may include payloads. Set to "false" for anything resembling PHI.
TRACE_SENSITIVE = os.environ.get("TRACE_INCLUDE_SENSITIVE_DATA", "true").lower() == "true"

MEMBER_SYSTEM = "https://cascadiahealth.example/member-id"
NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"
PAYER_ORG_ID = "org-cascadia-health-plan"

# USD per 1M tokens: (input, cached_input, output). Confirm against platform.openai.com/pricing;
# override with MODEL_PRICES='{"model": [in, cached, out]}'.
_DEFAULT_PRICES: dict[str, tuple[float, float, float]] = {  # developers.openai.com/api/docs/pricing, Oct 2026
    "gpt-6-astra": (10.00, 1.00, 50.00),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.5": (5.00, 0.50, 30.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.2": (1.75, 0.175, 14.00),
    "gpt-5-mini": (0.25, 0.025, 2.00),
}


def prices() -> dict[str, tuple[float, float, float]]:
    import json
    p = dict(_DEFAULT_PRICES)
    if os.environ.get("MODEL_PRICES"):
        p.update({k: tuple(v) for k, v in json.loads(os.environ["MODEL_PRICES"]).items()})
    return p


def cost_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float | None:
    table = prices()
    key = model if model in table else next((k for k in sorted(table, key=len, reverse=True) if model.startswith(k)), None)
    if not key:
        return None
    pin, pcache, pout = table[key]
    return round(((input_tokens - cached_tokens) * pin + cached_tokens * pcache + output_tokens * pout) / 1e6, 6)
