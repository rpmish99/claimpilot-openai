"""Deterministic corroboration: verify low-confidence identity fields against the system of record.

If the Extraction Agent was unsure about a field, but the system of record agrees with what was read,
code clears the CONF-001 finding:
  - identity (box 1a, 2, 3): the matched Patient/Coverage agree exactly (member ID may differ only by
    look-alike characters, and only once name AND birth date match exactly);
  - billing provider (box 33/33a): the NPI passes its check digit and belongs to a network Organization
    whose name matches box 33.
The agent's say-so is never enough on its own.
"""
from __future__ import annotations

from .fhir_store import FhirStore
from .schemas import ClaimExtraction, Finding

LOOKALIKE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"})
IDENTITY_FIELDS = {"insured_id", "patient_last_name", "patient_first_name", "patient_birth_date"}


def _n(v: str | None) -> str:
    return (v or "").strip().upper().replace(" ", "")


def _provider_verified(x: ClaimExtraction, store: FhirStore) -> dict[str, str]:
    """Billing NPI/name are verified if the NPI passes its check digit, belongs to a network Organization,
    and that Organization's name matches box 33."""
    from . import config
    from .validators import npi_is_valid
    npi = _n(x.billing_provider_npi.value)
    if not npi_is_valid(npi):
        return {}
    orgs = store.search("Organization", {"identifier": f"{config.NPI_SYSTEM}|{npi}"})
    if orgs and _n(orgs[0].get("name")) == _n(x.billing_provider_name.value):
        ref = f"Verified against FHIR Organization/{orgs[0]['id']}"
        return {"billing_provider_npi": ref, "billing_provider_name": ref}
    return {}


def corroborate(findings: list[Finding], x: ClaimExtraction, store: FhirStore,
                patient_id: str | None, coverage_id: str | None) -> list[Finding]:
    verified: dict[str, str] = {}
    if any(f.rule_id == "CONF-001" and f.field.startswith("billing_provider") for f in findings):
        verified.update(_provider_verified(x, store))
    if patient_id and coverage_id and any(f.rule_id == "CONF-001" and f.field in IDENTITY_FIELDS for f in findings):
        verified.update(_identity_verified(x, store, patient_id, coverage_id))
    if not verified:
        return findings
    return [f.model_copy(update={"cleared_by": verified[f.field]}) if f.rule_id == "CONF-001" and f.field in verified
            else f for f in findings]


def _identity_verified(x: ClaimExtraction, store: FhirStore, patient_id: str, coverage_id: str) -> dict[str, str]:
    pat, cov = store.read("Patient", patient_id), store.read("Coverage", coverage_id)
    if not pat or not cov or not cov.get("beneficiary", {}).get("reference", "").endswith("/" + patient_id):
        return {}
    name = (pat.get("name") or [{}])[0]
    exact = {
        "patient_last_name": _n(name.get("family")) == _n(x.patient_last_name.value),
        "patient_first_name": _n((name.get("given") or [""])[0]) == _n(x.patient_first_name.value),
        "patient_birth_date": pat.get("birthDate") == x.patient_birth_date.value,
    }
    if not all(exact.values()):  # identity not fully confirmed: verify nothing
        return {}
    exact["insured_id"] = _n(cov.get("subscriberId")).translate(LOOKALIKE) == _n(x.insured_id.value).translate(LOOKALIKE)
    ref = f"Verified against FHIR Patient/{patient_id} and Coverage/{coverage_id}"
    return {k: ref for k, ok in exact.items() if ok}
