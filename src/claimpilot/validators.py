"""Deterministic front-end edits. Policy lives here, in code, not in a prompt.

Each rule has a stable ID, a severity and the route it forces. The supervisor treats these as
authoritative: the agent can explain a finding but can never override one.
"""
from __future__ import annotations

import re
from datetime import date

from .schemas import ClaimExtraction, Finding, Route, Severity

CONFIDENCE_THRESHOLD = 0.85

ICD10_RE = re.compile(r"^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")  # chapter 22 (U07.1 COVID-19, U09.9) is valid
CPT_RE = re.compile(r"^(\d{4}[0-9A-Z]|[A-V]\d{4})$")
EIN_RE = re.compile(r"^\d{2}-?\d{7}$")


def npi_is_valid(npi: str | None) -> bool:
    """NPI: 10 digits starting with 1 or 2, with a Luhn check digit computed over the prefix 80840 + the first 9 digits."""
    if not npi or not re.fullmatch(r"[12]\d{9}", npi):
        return False
    digits = "80840" + npi
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def icd10_is_valid(code: str) -> bool:
    """Format check only; it does not look the code up. A production build would check the CMS code set.

    ICD-10-CM codes are 3 to 7 characters. Letter X is a placeholder that pads a code out to its 7th
    character (e.g. T36.1X1A), so it can appear in positions 4-6 only when a 7th character follows, and it is
    never itself a valid 7th character.
    """
    code = code.strip().upper()
    if not ICD10_RE.match(code):
        return False
    body = code.replace(".", "")
    if len(body) == 7 and body[6] == "X":
        return False
    return not ("X" in body[3:] and len(body) != 7)


def _parse(d: str | None) -> date | None:
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


def validate(x: ClaimExtraction, today: date | None = None) -> list[Finding]:
    today = today or date.today()
    out: list[Finding] = []

    def add(rule, sev, route, field, msg):
        out.append(Finding(rule_id=rule, severity=sev, route=route, field=field, message=msg))

    # --- required fields
    required = {
        "insured_id": x.insured_id, "patient_last_name": x.patient_last_name,
        "patient_first_name": x.patient_first_name, "patient_birth_date": x.patient_birth_date,
        "billing_provider_npi": x.billing_provider_npi, "total_charge": x.total_charge,
    }
    for name, f in required.items():
        if not f.value:
            add("REQ-001", Severity.error, Route.needs_review, name, f"Required field {name} is blank or unreadable.")
    if not x.service_lines:
        add("REQ-002", Severity.error, Route.return_to_provider, "service_lines", "No service lines found in box 24.")
    if not x.diagnosis_codes:
        add("REQ-003", Severity.error, Route.return_to_provider, "diagnosis_codes", "No diagnosis codes in box 21.")

    # --- legibility / confidence
    for name, f in x.model_dump().items():
        if isinstance(f, dict) and "confidence" in f and f.get("value") and f["confidence"] < CONFIDENCE_THRESHOLD:
            add("CONF-001", Severity.warning, Route.needs_review, name,
                f"Low extraction confidence ({f['confidence']:.2f}) for {name}.")
    for ln in x.service_lines:
        if ln.confidence < CONFIDENCE_THRESHOLD:
            add("CONF-001", Severity.warning, Route.needs_review, f"service_lines[{ln.line_number}]",
                f"Low extraction confidence ({ln.confidence:.2f}) for service line {ln.line_number}.")
    if x.diagnosis_codes and x.diagnosis_confidence < CONFIDENCE_THRESHOLD:
        add("CONF-001", Severity.warning, Route.needs_review, "diagnosis_codes",
            f"Low extraction confidence ({x.diagnosis_confidence:.2f}) for diagnosis codes.")

    # --- identifiers
    if x.billing_provider_npi.value and not npi_is_valid(x.billing_provider_npi.value):
        add("NPI-001", Severity.error, Route.return_to_provider, "billing_provider_npi",
            f"Billing NPI {x.billing_provider_npi.value} fails the NPI check-digit test.")
    for ln in x.service_lines:
        if ln.rendering_npi and not npi_is_valid(ln.rendering_npi):
            add("NPI-002", Severity.error, Route.return_to_provider, f"service_lines[{ln.line_number}].rendering_npi",
                f"Rendering NPI {ln.rendering_npi} on line {ln.line_number} fails the NPI check-digit test.")
    if x.federal_tax_id.value and not EIN_RE.match(x.federal_tax_id.value):
        add("TAX-001", Severity.error, Route.return_to_provider, "federal_tax_id", "Federal tax ID is not a valid EIN format.")

    # --- codes
    letters = "ABCDEFGHIJKL"
    for i, code in enumerate(x.diagnosis_codes):
        if not icd10_is_valid(code):
            add("DX-001", Severity.error, Route.return_to_provider, f"diagnosis_codes[{letters[i]}]",
                f"Diagnosis {letters[i]} '{code}' is not a valid ICD-10-CM code format.")
    for ln in x.service_lines:
        if ln.procedure_code and not CPT_RE.match(ln.procedure_code):
            add("PX-001", Severity.error, Route.return_to_provider, f"service_lines[{ln.line_number}].procedure_code",
                f"Procedure code '{ln.procedure_code}' on line {ln.line_number} is not a valid CPT/HCPCS format.")
        for p in (ln.diagnosis_pointer or ""):
            if p.upper() not in letters[: len(x.diagnosis_codes)]:
                add("DX-002", Severity.error, Route.return_to_provider, f"service_lines[{ln.line_number}].diagnosis_pointer",
                    f"Line {ln.line_number} points to diagnosis '{p}', which is not listed in box 21.")

    # --- dates
    dob = _parse(x.patient_birth_date.value)
    for ln in x.service_lines:
        d_from, d_to = _parse(ln.date_from), _parse(ln.date_to) or _parse(ln.date_from)
        if not d_from:
            add("DATE-001", Severity.error, Route.needs_review, f"service_lines[{ln.line_number}].date_from",
                f"Line {ln.line_number} has no readable date of service.")
            continue
        if d_from > today:
            add("DATE-002", Severity.error, Route.return_to_provider, f"service_lines[{ln.line_number}].date_from",
                f"Line {ln.line_number} date of service {d_from} is in the future.")
        if d_to and d_to < d_from:
            add("DATE-003", Severity.error, Route.return_to_provider, f"service_lines[{ln.line_number}]",
                f"Line {ln.line_number} 'to' date is before the 'from' date.")
        if dob and d_from < dob:
            add("DATE-004", Severity.error, Route.needs_review, "patient_birth_date",
                "Date of service is before the patient's birth date.")

    # --- money
    try:
        total = float((x.total_charge.value or "").replace(",", "").replace("$", ""))
    except ValueError:
        total = None
    line_sum = round(sum(ln.charge or 0 for ln in x.service_lines), 2)
    if total is not None and abs(total - line_sum) > 0.005:
        add("AMT-001", Severity.error, Route.needs_review, "total_charge",
            f"Box 28 total ${total:,.2f} does not equal the sum of line charges ${line_sum:,.2f}.")
    return _legibility_first(out, x)


def _field_confidence(x: ClaimExtraction, field: str) -> float:
    if field.startswith("service_lines["):
        n = int(field[len("service_lines["):].split("]")[0])
        return next((ln.confidence for ln in x.service_lines if ln.line_number == n), 1.0)
    if field.startswith("diagnosis_codes"):
        return x.diagnosis_confidence
    f = getattr(x, field, None)
    return getattr(f, "confidence", 1.0)


def _legibility_first(findings: list[Finding], x: ClaimExtraction) -> list[Finding]:
    """A content error on a field we could not read clearly is probably our misread, not the
    provider's mistake: send it to a processor (who can read the image) instead of bouncing it."""
    for f in findings:
        if f.route == Route.return_to_provider and _field_confidence(x, f.field) < CONFIDENCE_THRESHOLD:
            f.route = Route.needs_review
            f.message += " Low legibility: verify against the image before returning to the provider."
    return findings


def route_from_findings(findings: list[Finding]) -> Route:
    active = [f for f in findings if not f.cleared_by]
    if any(f.route == Route.return_to_provider for f in active):
        return Route.return_to_provider
    if active:
        return Route.needs_review
    return Route.auto_accept
