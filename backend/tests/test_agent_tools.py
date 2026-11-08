"""Tests for the agent tools. FPL/program numbers below are FAKE test fixtures, not real guideline values."""
import json

import pytest

from app.agents import denial as d
from app.agents.assistance import lookup_assistance
from app.agents.fpl import (DEFAULT_FPL_PATH, FplDataNotConfigured, FplError, compute_fpl_percent, load_guidelines)
from app.agents.schemas import ParsedDenial
from app.agents.tools import ToolContext, get_case
from app.domain.case import Case


# =============================================================================== FPL
@pytest.fixture
def fpl_file(tmp_path):
    p = tmp_path / "fpl.json"
    p.write_text(json.dumps({"status": "TEST", "year": 2000, "first_person_amount": 10000,
                             "each_additional_person": 4000, "source_url": "https://example.test/fpl"}))
    return p


def pct(n, income, path):
    return compute_fpl_percent(n, income, path).fpl_percent


def test_fpl_basic_and_household_scaling(fpl_file):
    assert pct(1, 9000, fpl_file) == 90.0
    assert pct(3, 27000, fpl_file) == 150.0           # guideline 10000 + 2*4000 = 18000
    assert pct(8, 38000, fpl_file) == 100.0           # 10000 + 7*4000 = 38000: exactly at the line
    assert pct(20, 86000, fpl_file) == 100.0          # max household: 10000 + 19*4000


def test_fpl_zero_and_huge_income(fpl_file):
    assert pct(2, 0, fpl_file) == 0.0
    assert pct(1, 5_000_000, fpl_file) == 50000.0


def test_fpl_rounds_half_up_to_one_decimal(fpl_file):
    assert pct(1, 5, fpl_file) == 0.1                  # 0.05 -> 0.1
    assert pct(1, 4, fpl_file) == 0.0                  # 0.04 -> 0.0
    assert pct(1, 15555, fpl_file) == 155.6            # 155.55 -> 155.6 (float-safe via Decimal)


def test_fpl_result_records_source_year_and_status(fpl_file):
    r = compute_fpl_percent(1, 10000, fpl_file)
    assert (r.guideline_year, r.guideline_amount, r.source_url, r.data_status) == (2000, 10000.0, "https://example.test/fpl", "TEST")


@pytest.mark.parametrize("size", [0, -1, 21, 2.5, "3", None, True])
def test_fpl_rejects_bad_household_size(size, fpl_file):
    with pytest.raises(FplError):
        compute_fpl_percent(size, 1000, fpl_file)


@pytest.mark.parametrize("income", [-1, None, True])
def test_fpl_rejects_bad_income(income, fpl_file):
    with pytest.raises(FplError):
        compute_fpl_percent(2, income, fpl_file)


def test_fpl_unconfigured_or_missing_file_fails_gracefully(tmp_path):
    todo = tmp_path / "todo.json"
    todo.write_text(json.dumps({"year": None, "first_person_amount": None, "each_additional_person": None,
                                "source_url": "https://example.test"}))
    with pytest.raises(FplDataNotConfigured):
        compute_fpl_percent(1, 1000, todo)
    with pytest.raises(FplDataNotConfigured):
        compute_fpl_percent(1, 1000, tmp_path / "nope.json")


def test_shipped_fpl_file_is_complete_and_cites_a_source():
    data = load_guidelines(DEFAULT_FPL_PATH)
    assert data["source_url"].startswith("https://") and data["year"] >= 2025
    assert data["first_person_amount"] > 0 and data["each_additional_person"] > 0
    assert data["status"] in ("VERIFIED", "FETCHED-NEEDS-HUMAN-CONFIRM")


# =============================================================================== lookup_assistance
def prog(**kw):
    base = dict(program_name="Test Program", sponsor="Test Hospital", type="hospital charity care",
                eligibility_summary="x", fpl_threshold_pct=200, applies_to="both", how_to_apply="call",
                contact="555", source_url="https://example.test", retrieved_date="2026-01-01", verified=True)
    return {**base, **kw}


@pytest.fixture
def programs(tmp_path):
    def make(*rows):
        p = tmp_path / "programs.json"
        p.write_text(json.dumps({"programs": list(rows)}))
        return p
    return make


def test_missing_table_means_nothing_found(tmp_path):
    r = lookup_assistance(150, path=tmp_path / "none.json")
    assert r.found_nothing and r.matches == []


def test_threshold_boundaries(programs):
    p = programs(prog(program_name="A", fpl_threshold_pct=200))
    assert lookup_assistance(200.0, path=p).matches[0].basis == "income_at_or_below_listed_threshold"   # at the line
    r = lookup_assistance(200.1, path=p)
    assert r.found_nothing and r.above_threshold == ["A"]


def test_no_threshold_and_unknown_income(programs):
    p = programs(prog(program_name="NoLine", fpl_threshold_pct=None), prog(program_name="Line", fpl_threshold_pct=250))
    by = {m.program.program_name: m.basis for m in lookup_assistance(None, path=p).matches}
    assert by == {"NoLine": "no_income_threshold_listed", "Line": "need_more_information"}


def test_unverified_rows_are_never_matched(programs):
    p = programs(prog(program_name="Placeholder", verified=False))
    r = lookup_assistance(100, path=p)
    assert r.found_nothing and r.skipped_unverified == ["Placeholder"]


def test_hospital_filter_matches_name_or_sponsor_case_insensitive(programs):
    p = programs(prog(program_name="Roswell Charity Care", sponsor="Roswell Park"),
                 prog(program_name="Mercy Aid", sponsor="Catholic Health"))
    assert [m.program.program_name for m in lookup_assistance(100, "roswell", path=p).matches] == ["Roswell Charity Care"]
    assert [m.program.program_name for m in lookup_assistance(100, "CATHOLIC", path=p).matches] == ["Mercy Aid"]
    assert lookup_assistance(100, "ECMC", path=p).found_nothing


def test_applies_to_filter(programs):
    p = programs(prog(program_name="U", applies_to="uninsured"), prog(program_name="I", applies_to="insured"),
                 prog(program_name="B", applies_to="both"))
    names = lambda un: sorted(m.program.program_name for m in lookup_assistance(100, uninsured=un, path=p).matches)
    assert names(True) == ["B", "U"] and names(False) == ["B", "I"] and names(None) == ["B", "I", "U"]


# =============================================================================== parse_denial
TEXT = ("Claim number AB-1234. We denied CPT 77066 on 03/04/2026 because the service was not medically necessary. "
        "Your plan: Univera PPO Plus. Please send the radiology report.")


def fake_llm(payload):
    return lambda system, text: payload


def test_parse_denial_keeps_grounded_values_and_nulls_invented_ones():
    out = d.parse_denial(TEXT, fake_llm({
        "denial_reason": "not medically necessary", "denied_service_or_code": "77066", "date": "03/04/2026",
        "claim_or_reference_number": "AB-1234", "plan_name": "Univera PPO Plus",
        "appeal_deadline_if_stated": "within 60 days",                     # invented: not in the text
        "missing_documentation_mentioned": ["radiology report", "pathology report"]}))
    assert out.denial_reason == "not medically necessary" and out.claim_or_reference_number == "AB-1234"
    assert out.appeal_deadline_if_stated is None
    assert out.missing_documentation_mentioned == ["radiology report"]       # invented item dropped


def test_parse_denial_ignores_unknown_keys_and_empty_text_skips_llm():
    out = d.parse_denial(TEXT, fake_llm({"denial_reason": "not medically necessary", "diagnosis": "cancer"}))
    assert out.denial_reason and not hasattr(out, "diagnosis")

    def boom(*_):
        raise AssertionError("LLM must not be called for empty text")
    assert d.parse_denial("   ", boom) == ParsedDenial()


# =============================================================================== check_against_case
def den(**kw):
    return ParsedDenial(**kw)


def kinds(check):
    return [f.kind for f in check.flags]


def test_code_in_confirmed():
    c = d.check_against_case(den(denied_service_or_code="CPT 77066", plan_name="Univera PPO"),
                             Case(confirmed_codes=["77066", "76642"], insurer="Univera", plan="PPO"))
    assert c.denied_code_in_confirmed is True and c.plan_consistent_with_case is True and c.flags == []


def test_code_not_in_confirmed_is_flagged():
    c = d.check_against_case(den(denied_service_or_code="19083", plan_name="Univera"), Case(confirmed_codes=["77066"], insurer="Univera"))
    assert c.denied_code_in_confirmed is False and kinds(c) == ["code_not_in_confirmed_codes"]
    assert "19083" in c.flags[0].message


def test_multiple_denied_codes_all_must_be_confirmed():
    c = d.check_against_case(den(denied_service_or_code="77066 and 19083", plan_name="x"), Case(confirmed_codes=["77066"]))
    assert c.denied_code_in_confirmed is False and "19083" in c.flags[0].message and "77066" not in c.flags[0].message


def test_service_described_without_a_code():
    c = d.check_against_case(den(denied_service_or_code="diagnostic mammogram"), Case(confirmed_codes=["77066"]))
    assert c.denied_code_in_confirmed is None and "code_not_stated" in kinds(c)


def test_no_confirmed_codes():
    c = d.check_against_case(den(denied_service_or_code="77066"), Case())
    assert c.denied_code_in_confirmed is None and "no_confirmed_codes" in kinds(c)


def test_plan_insurer_alias_and_mismatch():
    ok = d.check_against_case(den(plan_name="Blue Cross Blue Shield PPO"), Case(insurer="Highmark BCBS", plan="PPO"))
    assert ok.plan_consistent_with_case is True
    bad = d.check_against_case(den(plan_name="Univera Healthcare"), Case(insurer="Independent Health"))
    assert bad.plan_consistent_with_case is False and "plan_mismatch" in kinds(bad)


def test_plan_type_mismatch_only_when_both_sides_name_a_type():
    assert d.check_against_case(den(plan_name="Univera HMO"), Case(insurer="Univera", plan="PPO")).plan_consistent_with_case is False
    assert d.check_against_case(den(plan_name="Univera Access"), Case(insurer="Univera", plan="PPO")).plan_consistent_with_case is True
    assert d.check_against_case(den(plan_name="Univera HMO"), Case(insurer="Univera", plan="Any")).plan_consistent_with_case is True


def test_plan_not_stated_or_case_has_no_insurance():
    assert "plan_not_stated" in kinds(d.check_against_case(den(), Case(insurer="Univera")))
    assert d.check_against_case(den(plan_name="Univera PPO"), Case(uninsured=True)).plan_consistent_with_case is None


# =============================================================================== draft_letter
def test_letter_with_no_facts_is_all_placeholders():
    L = d.draft_letter("internal_appeal", {})
    for p in (d.P_NAME, d.P_MEMBER, d.P_DATE, d.P_CLAIM, d.P_DENIAL_DATE, d.P_SERVICE, d.P_PLAN, d.P_CLINICIAN):
        assert p in L.body and p in L.placeholders


def test_letter_uses_only_supplied_facts():
    L = d.draft_letter("internal_appeal", {"patient_name": "Sarah Demo", "claim_number": "AB-1234",
                                            "denied_service": "CPT 77066", "denial_reason": "not medically necessary"})
    assert "Sarah Demo" in L.body and "AB-1234" in L.body and 'not medically necessary' in L.body
    assert d.P_NAME not in L.placeholders and d.P_CLAIM not in L.placeholders and d.P_MEMBER in L.placeholders


def test_letter_never_invents_medical_or_legal_content():
    body = d.draft_letter("internal_appeal", {"patient_name": "A"}).body.lower()
    for banned in ("guarantee", "will be approved", "must approve", "erisa", " law", "regulation", "statute", "diagnos"):
        assert banned not in body, banned
    assert d.P_CLINICIAN.lower() in body                    # medical-necessity statement is left to the clinician


def test_patient_text_is_not_treated_as_a_template():
    L = d.draft_letter("internal_appeal", {"patient_name": "{member}", "member_id": "M-1"})
    assert "{member}" in L.body and L.body.count("M-1") == 1


def test_documentation_request_and_unknown_type():
    assert "Request for information" in d.draft_letter("documentation_request", {}).body
    with pytest.raises(ValueError):
        d.draft_letter("lawsuit", {})


# =============================================================================== get_case
def test_get_case_hides_denial_text():
    out = get_case(ToolContext(Case(confirmed_codes=["77066"], denial_text="Jane Doe MRN 998")))
    assert out["confirmed_codes"] == ["77066"] and "denial_text" not in out and "Jane" not in json.dumps(out)


# =============================================================================== curated data files
from app.agents.assistance import (DEFAULT_PROGRAMS_PATH, audit_billing_rights, audit_programs,  # noqa: E402
                                   load_programs)


def test_shipped_table_loads_and_every_verified_row_is_complete():
    rows = load_programs(DEFAULT_PROGRAMS_PATH)
    verified = [r for r in rows if r.verified]
    assert len(verified) >= 5
    assert all(r.source_url.startswith("https://") and "TODO" not in r.program_name + r.eligibility_summary for r in verified)
    assert all(r.verified is False for r in rows if "TODO-VERIFY" in r.program_name)
    names = {m.program.program_name for m in lookup_assistance(100, path=DEFAULT_PROGRAMS_PATH).matches}
    assert not any("TODO" in n for n in names)                                    # placeholders can never match


def test_shipped_files_pass_audit_and_billing_rights_are_verified_from_official_sources():
    assert audit_programs(DEFAULT_PROGRAMS_PATH) == []
    rights = DEFAULT_PROGRAMS_PATH.parent / "billing_rights.json"
    assert audit_billing_rights(rights) == []
    entries = json.loads(rights.read_text(encoding="utf-8"))["entries"]
    assert entries and all(e["verified"] for e in entries)                         # no placeholder rows are left
    assert len({e["id"] for e in entries}) == len(entries)                         # unique ids
    official = {"cms.gov", "dfs.ny.gov", "health.ny.gov"}                          # only official sources may back a stated right
    assert all(e["source_url"].split("/")[2].removeprefix("www.") in official for e in entries)
    assert not any("TODO" in json.dumps(e) for e in entries)


def test_audit_rejects_verified_rows_that_are_not_really_verified(programs):
    bad = programs(prog(program_name="TODO-VERIFY: X", verified=True, source_url="TODO-VERIFY", retrieved_date="1900-01-01"))
    problems = audit_programs(bad)
    assert any("TODO-VERIFY" in p for p in problems) and any("https" in p for p in problems) and any("retrieved_date" in p for p in problems)
    good = programs(prog(program_name="Real", verified=True, source_url="https://example.org/x", retrieved_date="2026-11-07"))
    assert audit_programs(good) == []


def test_audit_flags_schema_errors(programs):
    assert any("schema error" in p for p in audit_programs(programs(prog(applies_to="sometimes"))))
