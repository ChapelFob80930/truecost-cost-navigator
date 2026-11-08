import pytest
from pydantic import ValidationError

from app.agents.schemas import (AgentMeta, AppealDraftRequest, AssistanceMatchResponse, LetterDraft, MatchedProgram,
                                ParsedDenial)
from app.domain.case import Case


def test_case_defaults_and_code_cleanup():
    c = Case(confirmed_codes=[" 77066", "77066", "76642 "])
    assert c.confirmed_codes == ["77066", "76642"] and not c.has_income_info


def test_uninsured_clears_insurance_fields():
    c = Case(uninsured=True, insurer="Univera", plan="PPO", deductible_remaining=500, coinsurance_pct=20)
    assert c.insurer is None and c.plan is None and c.deductible_remaining is None


def test_income_needs_both_fields():
    assert not Case(household_size=3).has_income_info
    assert Case(household_size=3, annual_income=0).has_income_info          # zero income is valid


@pytest.mark.parametrize("bad", [{"household_size": 0}, {"annual_income": -1}, {"coinsurance_pct": 120},
                                 {"insurer": "Aetna"}])
def test_case_rejects_bad_values(bad):
    with pytest.raises(ValidationError):
        Case(**bad)


def test_llm_view_never_contains_denial_text():
    c = Case(denial_text="Jane Doe, member ID 123 was denied", household_size=2)
    assert "denial_text" not in c.llm_view() and "Jane" not in str(c.llm_view())


def test_parsed_denial_all_null_is_valid():
    p = ParsedDenial()
    assert all(v is None for v in p.model_dump().values())


def test_letter_placeholders_must_appear_in_body():
    LetterDraft(letter_type="internal_appeal", body="Dear [PLAN NAME], member [MEMBER ID]", placeholders=["[MEMBER ID]"])
    with pytest.raises(ValidationError):
        LetterDraft(letter_type="internal_appeal", body="Dear plan", placeholders=["[MEMBER ID]"])


def test_appeal_request_needs_some_denial_text():
    with pytest.raises(ValidationError):
        AppealDraftRequest(case=Case())
    assert AppealDraftRequest(case=Case(denial_text="denied")).letter_type == "internal_appeal"
    assert AppealDraftRequest(case=Case(), denial_text="denied")


def test_unverified_program_cannot_be_a_match():
    m = MatchedProgram(program_name="X", sponsor="Y", type="nonprofit", match_basis="no_income_threshold_listed",
                       why="may qualify", source_url="https://example.org", next_step="call", verified=False)
    with pytest.raises(ValidationError):
        AssistanceMatchResponse(matches=[m], summary_plain="s")


def test_meta_caps_tool_calls_at_five_and_has_disclaimer_and_handoff():
    assert AgentMeta().disclaimers and "navigator" in AgentMeta().handoff
    with pytest.raises(ValidationError):
        AgentMeta(tool_calls_used=6)
