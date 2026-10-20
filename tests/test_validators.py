"""Regression tests for the rule checks, using published examples and cases found during the correctness audit."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from claimpilot.schemas import ClaimExtraction, Extracted, Route, ServiceLine
from claimpilot.validators import CPT_RE, icd10_is_valid, npi_is_valid, route_from_findings, validate


@pytest.mark.parametrize("npi,ok", [
    ("1234567893", True),   # worked example in the CMS NPI check-digit documentation
    ("1245319599", True), ("1993999998", True),
    ("1234567890", False),  # wrong check digit
    ("123456789", False), ("12345678AB", False), ("12345678930", False),
    ("3234567893", False),  # NPIs start with 1 or 2
])
def test_npi(npi, ok):
    assert npi_is_valid(npi) is ok


@pytest.mark.parametrize("code,ok", [
    ("E11.9", True), ("I10", True), ("M17.11", True), ("S93.401A", True), ("Z00.129", True), ("J45.909", True),
    ("U07.1", True), ("U09.9", True),       # chapter 22: COVID-19 codes start with U
    ("T36.1X1A", True), ("X71.0XXA", True),  # X placeholder before a 7th character; X as a first letter
    ("J45.9X9X", False),                    # X is never a valid 7th character (found by the eval set)
    ("J45.9X9", False),                     # placeholder without a 7th character
    ("11.9", False), ("EE1.9", False), ("E11.99999", False), ("", False),
])
def test_icd10_format(code, ok):
    assert icd10_is_valid(code) is ok


@pytest.mark.parametrize("code,ok", [("99213", True), ("J1030", True), ("0001U", True), ("A0425", True),
                                     ("1234", False), ("W1234", False), ("992134", False)])
def test_cpt_hcpcs_format(code, ok):
    assert bool(CPT_RE.match(code)) is ok


def _claim(**over):
    f = lambda v, c=0.99: Extracted(value=v, confidence=c)  # noqa: E731
    base = dict(
        insured_id=f("CHP100000001"), patient_last_name=f("DOE"), patient_first_name=f("JANE"), patient_middle_initial=f("A"),
        patient_birth_date=f("1980-01-01"), patient_sex=f("F"), insured_last_name=f("DOE"), insured_first_name=f("JANE"),
        patient_relationship=f("self"), insured_group_number=f("G1"), insured_birth_date=f("1980-01-01"), plan_name=f("PPO"),
        diagnosis_codes=["E11.9"], diagnosis_confidence=0.99,
        service_lines=[ServiceLine(line_number=1, date_from="2026-10-01", date_to="2026-10-01", place_of_service="11",
                                   procedure_code="99213", modifiers=[], diagnosis_pointer="A", charge=145.0, units=1,
                                   rendering_npi="1234567893", confidence=0.99)],
        additional_claim_info=f(None), federal_tax_id=f("93-4417285"), patient_account_no=f("A1"), total_charge=f("145.00"),
        billing_provider_name=f("CLINIC"), billing_provider_npi=f("1245319599"), signature_date=f("2026-10-01"),
        document_quality="clean", extraction_notes="")
    base.update(over)
    return ClaimExtraction(**base)


TODAY = date(2026, 10, 19)


def test_clean_claim_has_no_findings():
    assert validate(_claim(), today=TODAY) == []


def test_total_mismatch_goes_to_review():
    fs = validate(_claim(total_charge=Extracted(value="170.00", confidence=0.99)), today=TODAY)
    assert [f.rule_id for f in fs] == ["AMT-001"] and route_from_findings(fs) == Route.needs_review


def test_bad_rendering_npi_is_returned_when_legible():
    line = _claim().service_lines[0].model_copy(update={"rendering_npi": "1234567890"})
    fs = validate(_claim(service_lines=[line]), today=TODAY)
    assert [f.rule_id for f in fs] == ["NPI-002"] and route_from_findings(fs) == Route.return_to_provider


def test_invalid_code_on_an_unreadable_field_is_reviewed_not_returned():
    fs = validate(_claim(diagnosis_codes=["J45.9X9X"], diagnosis_confidence=0.5), today=TODAY)
    assert "DX-001" in [f.rule_id for f in fs] and route_from_findings(fs) == Route.needs_review


def test_future_date_of_service_is_returned():
    line = _claim().service_lines[0].model_copy(update={"date_from": "2026-12-01", "date_to": "2026-12-01"})
    assert "DATE-002" in [f.rule_id for f in validate(_claim(service_lines=[line]), today=TODAY)]
