"""Explainability: reasons are written by code from the real values, and they must match what the agent actually did."""
import json

import pytest

from app.agents.appeal_agent import DENIAL_SCHEMA, NARRATIVE_SCHEMA, run_appeal_agent
from app.agents.assistance import evaluate_program
from app.agents.assistance_agent import SUMMARY_SCHEMA, run_assistance_agent
from app.agents.llm import ModelTurn
from app.agents.schemas import AssistanceProgram
from app.domain.case import Case

from .test_agent_loop import APPEAL_CASE, CASE, LETTER, FakeChat, calls


def program(**kw):
    base = dict(program_name="Prog", sponsor="Sponsor", type="hospital charity care", eligibility_summary="x",
                fpl_threshold_pct=200, applies_to="both", how_to_apply="Call", contact="555",
                source_url="https://example.test/p", retrieved_date="2026-11-07", verified=True)
    base.update(kw)
    return AssistanceProgram(**base)


def by_name(criteria):
    return {c.criterion: c for c in criteria}


# ------------------------------------------------------------------ the shared rule check
def test_income_below_limit_may_apply_and_shows_both_numbers():
    ev = evaluate_program(program(fpl_threshold_pct=200), 150.0, False)
    assert ev.decision == "may_apply" and ev.basis == "income_at_or_below_listed_threshold"
    c = by_name(ev.criteria)["Income limit"]
    assert c.result == "met" and "150%" in c.your_value and "200%" in c.program_rule
    assert "150%" in ev.reason and "200%" in ev.reason


def test_income_above_limit_is_ruled_out_with_the_reason_and_the_cause():
    ev = evaluate_program(program(fpl_threshold_pct=100), 150.0, None)
    assert ev.decision == "ruled_out" and ev.ruled_out_by == "income"
    assert by_name(ev.criteria)["Income limit"].result == "not_met" and "above" in ev.reason


def test_wrong_coverage_is_ruled_out_before_income_is_even_compared():
    ev = evaluate_program(program(applies_to="uninsured"), 50.0, False)   # insured patient, uninsured-only program
    assert ev.decision == "ruled_out" and ev.ruled_out_by == "coverage"
    assert by_name(ev.criteria)["Insurance status"].result == "not_met" and "Income limit" not in by_name(ev.criteria)


def test_unknown_coverage_does_not_rule_a_program_out():
    ev = evaluate_program(program(applies_to="uninsured"), 50.0, None)
    assert ev.decision == "may_apply" and by_name(ev.criteria)["Insurance status"].result == "unknown"


def test_missing_income_needs_more_info_and_no_limit_is_not_ruled_out():
    assert evaluate_program(program(), None, False).decision == "need_more_info"
    ev = evaluate_program(program(fpl_threshold_pct=None), None, False)
    assert ev.decision == "may_apply" and by_name(ev.criteria)["Income limit"].result == "not_applicable"


def test_unverified_program_is_skipped():
    ev = evaluate_program(program(verified=False), 50.0, False)
    assert ev.decision == "skipped" and ev.criteria[0].result == "not_met"


# ------------------------------------------------------------------ assistance agent explanation
@pytest.fixture
def two_programs(tmp_path):
    fpl = tmp_path / "fpl.json"
    fpl.write_text(json.dumps({"status": "TEST", "year": 2000, "first_person_amount": 10000, "each_additional_person": 4000,
                               "source_url": "https://example.test/fpl"}))
    rows = [program(program_name="Wide Aid", fpl_threshold_pct=200, sponsor="Roswell Park"),
            program(program_name="Narrow Aid", fpl_threshold_pct=100),
            program(program_name="Placeholder", verified=False)]
    prog = tmp_path / "programs.json"
    prog.write_text(json.dumps({"programs": [json.loads(r.model_dump_json()) for r in rows]}))
    return fpl, prog


def run(files, turns=(), final="ok", case=CASE):
    fpl, prog = files
    m = FakeChat(list(turns) or [ModelTurn(text="done", calls=[])], {id(SUMMARY_SCHEMA): {"summary_plain": final}})
    return run_assistance_agent(m, case, fpl_path=fpl, programs_path=prog)


def test_explanation_has_a_reason_for_every_step_with_real_numbers(two_programs):
    r = run(two_programs)
    ex = r.explanation
    assert [s.n for s in ex.steps] == list(range(1, len(ex.steps) + 1))
    assert all(s.action and s.why and s.result and s.who for s in ex.steps)
    fpl_step = next(s for s in ex.steps if "Poverty" in s.action)
    assert "$27,000" in fpl_step.result and "150%" in fpl_step.result and fpl_step.source == "https://example.test/fpl"
    search = next(s for s in ex.steps if "Searched" in s.action)
    assert "2 verified programs checked: 1 may apply" in search.result and "1 ruled out" in search.result
    assert "1 more not shown because they aren't verified" in search.result


def test_every_verified_program_is_explained_matched_or_not(two_programs):
    checks = {c.program_name: c for c in run(two_programs).explanation.program_checks}
    assert set(checks) == {"Wide Aid", "Narrow Aid"}                     # placeholder row is never shown
    assert checks["Wide Aid"].decision == "may_apply" and checks["Narrow Aid"].decision == "ruled_out"
    assert "150%" in checks["Narrow Aid"].reason and "100%" in checks["Narrow Aid"].reason
    assert checks["Narrow Aid"].source_url == "https://example.test/p"


def test_explanation_agrees_with_the_matches_returned(two_programs):
    r = run(two_programs)
    listed = {m.program_name for m in r.matches}
    may = {c.program_name for c in r.explanation.program_checks if c.decision in ("may_apply", "need_more_info")}
    assert listed == may


def test_a_narrow_ai_search_cannot_hide_a_program(two_programs):
    turns = [calls(("lookup_assistance", {"fpl_percent": 150.0, "hospital_or_program": "nothing-matches-this"})),
             ModelTurn(text="done", calls=[])]
    r = run(two_programs, turns)
    assert "Wide Aid" in {m.program_name for m in r.matches}


def test_who_did_each_step_is_honest(two_programs):
    skipped = run(two_programs).explanation.steps[0].who
    assert "skipped it" in skipped
    turns = [calls(("get_case", {})), ModelTurn(text="done", calls=[])]
    chose = run(two_programs, turns).explanation.steps[0].who
    assert "AI chose" in chose


def test_ai_wording_status_reflects_the_guard(two_programs):
    ok = run(two_programs, final="Your income is about 150% of the poverty level. You may qualify for 1 program. Please confirm.")
    assert "passed our checks" in ok.explanation.steps[-1].result and "AI wrote it" in ok.explanation.steps[-1].who
    bad = run(two_programs, final="You qualify for $9,999 in aid.")
    assert "replaced" in bad.explanation.steps[-1].who.lower() and "failed a check" in bad.explanation.steps[-1].result


def test_missing_income_is_called_out_in_what_was_not_checked(two_programs):
    r = run(two_programs, case=Case())
    assert any("No household size or income" in n for n in r.explanation.not_checked)
    assert any("don't know whether you have insurance" in n for n in r.explanation.not_checked)
    assert all(c.decision == "need_more_info" for c in r.explanation.program_checks)
    assert "Skipped the income comparison" in [s.action for s in r.explanation.steps]


# ------------------------------------------------------------------ appeal agent explanation
def appeal(letter=LETTER, case=APPEAL_CASE, parse=None):
    parse = parse if parse is not None else {"denial_reason": "not medically necessary", "denied_service_or_code": "CPT 77066",
                                              "date": "03/04/2026", "claim_or_reference_number": "AB-12345", "plan_name": "Univera PPO Plus"}
    narrative = {"summary_plain": "Your plan denied CPT 77066.", "documentation_checklist": ["Imaging report"],
                 "next_steps": ["Confirm the deadline with your plan."]}
    m = FakeChat(finals={id(DENIAL_SCHEMA): parse, id(NARRATIVE_SCHEMA): narrative})
    return run_appeal_agent(m, case, letter)


def test_appeal_explanation_covers_every_stage_with_reasons():
    ex = appeal().explanation
    assert [s.action for s in ex.steps] == ["Hid private details before the AI read the letter", "Read the denial letter",
                                            "Compared the letter with your case", "Built the letter from a fixed template",
                                            "Wrote the summary, document checklist and next steps"]
    assert all(s.why and s.result and s.who for s in ex.steps)
    assert "private detail" in ex.steps[0].result and "[NAME_1]" in ex.steps[0].why
    assert "an appeal deadline" in ex.steps[1].result and "Not stated" in ex.steps[1].result      # letter has no deadline
    assert "word for word" in ex.steps[1].who
    assert ex.not_checked


def test_appeal_explanation_reports_a_case_mismatch_in_plain_words():
    ex = appeal(letter=LETTER.replace("77066", "19083"),
                parse={"denied_service_or_code": "CPT 19083", "plan_name": "Univera PPO Plus"}).explanation
    assert "19083" in ex.steps[2].result and "not in the codes you confirmed" in ex.steps[2].result


def test_appeal_explanation_when_the_letter_could_not_be_read():
    m = FakeChat(finals={id(DENIAL_SCHEMA): RuntimeError("down"), id(NARRATIVE_SCHEMA): RuntimeError("down")})
    ex = run_appeal_agent(m, APPEAL_CASE, LETTER).explanation
    assert "couldn't be read automatically" in ex.steps[1].result
    assert "Standard wording used" in ex.steps[-1].result


# ------------------------------------------------------------------ the pre-recorded demo carries explanations too
def test_cached_demo_outputs_include_explanations():
    from app.agents.cached import load_cached
    from app.agents.schemas import AppealDraftResponse, AssistanceMatchResponse
    a = load_cached("sarah_assistance", AssistanceMatchResponse)
    b = load_cached("sarah_appeal", AppealDraftResponse)
    assert len(a.explanation.steps) >= 5 and a.explanation.program_checks and a.explanation.not_checked
    assert len(b.explanation.steps) == 5
