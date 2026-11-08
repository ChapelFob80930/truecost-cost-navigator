"""Offline tests: PHI scrubber, tool loop, output guards, and both agents, using a scripted fake model (no API calls)."""
import json

import pytest

from app.agents import guards
from app.agents.appeal_agent import DENIAL_SCHEMA, NARRATIVE_SCHEMA, run_appeal_agent
from app.agents.assistance_agent import SUMMARY_SCHEMA, run_assistance_agent
from app.agents.llm import ModelTurn, ToolCall, ToolDecl
from app.agents.loop import ToolSpec, run_tool_loop
from app.agents.phi import restore, scrub
from app.domain.case import Case, CaseEstimate


class FakeChat:
    """Scripted model. `turns` is consumed in order; `finals` maps a schema to a dict (or an exception to raise)."""
    model_name = "fake-model"

    def __init__(self, turns=(), finals=None):
        self.turns, self.finals, self.prompts_seen = list(turns), finals or {}, []
        self.calls_made = 0

    def turn(self, system, messages, tools):
        self.calls_made += 1
        if not self.turns:
            return ModelTurn(text="done", calls=[])
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t

    def final_json(self, system, prompt, schema):
        self.prompts_seen.append(prompt)
        out = self.finals.get(id(schema))
        if isinstance(out, Exception):
            raise out
        return out if out is not None else {}


def calls(*names_args):
    return ModelTurn(text=None, calls=[ToolCall(n, a) for n, a in names_args])


# =============================================================================== PHI scrub
RAW = ("Dear Jane Doe, your claim number AB-12345 was denied. Member ID: XYZ98765. MRN: 4455667. DOB: 03/04/1970. "
       "Call (716) 555-1234 or jane@example.com. We mailed 123 Main Street, Buffalo, NY 14201. SSN 123-45-6789.")


def test_scrub_removes_identifiers_and_restore_round_trips():
    s = scrub(RAW)
    for secret in ("Jane Doe", "AB-12345", "XYZ98765", "4455667", "03/04/1970", "555-1234", "jane@example.com",
                   "123 Main Street", "123-45-6789"):
        assert secret not in s.text, secret
    assert restore(s.text, s.mapping) == RAW
    assert {"NAME", "CLAIM", "MEMBER_ID", "MRN", "DOB", "PHONE", "EMAIL", "ADDRESS", "SSN"} <= set(s.found)


def test_scrub_same_value_same_token_and_generic_greetings_untouched():
    s = scrub("Patient: Jane Doe. Dear Member, Patient: Jane Doe called.")
    assert s.text.count("[NAME_1]") == 2 and "Dear Member" in s.text


def test_restore_handles_token_prefixes():
    m = {f"[NAME_{i}]": f"n{i}" for i in (1, 10)}
    assert restore("[NAME_10] [NAME_1]", m) == "n10 n1"


# =============================================================================== loop
def spec(name, fn, props=None):
    return ToolSpec(ToolDecl(name, name, {"type": "OBJECT", "properties": props or {}}), fn)


def test_loop_runs_allowed_tools_logs_trail_and_finishes():
    m = FakeChat([calls(("echo", {"x": 1})), ModelTurn(text="final", calls=[])])
    r = run_tool_loop(m, "sys", "go", {"echo": spec("echo", lambda x: {"got": x}, {"x": {"type": "INTEGER"}})})
    assert r.error is None and r.final_text == "final" and r.calls_used == 1
    assert r.trail[0].tool == "echo" and r.trail[0].output == {"got": 1} and r.trail[0].ok


def test_loop_refuses_tools_not_on_the_allow_list():
    m = FakeChat([calls(("delete_everything", {})), ModelTurn(text="ok", calls=[])])
    r = run_tool_loop(m, "sys", "go", {"echo": spec("echo", lambda: 1)})
    assert not r.trail[0].ok and "not allowed" in r.trail[0].error and r.calls_used == 1


def test_loop_caps_at_five_calls_and_does_not_run_the_sixth():
    ran = []
    m = FakeChat([calls(("t", {}))] * 10)
    r = run_tool_loop(m, "sys", "go", {"t": spec("t", lambda: ran.append(1))})
    assert r.calls_used == 5 and len(ran) == 5 and "limit of 5" in r.error


def test_loop_returns_tool_errors_to_the_model_and_drops_undeclared_args():
    seen = {}

    def boom(a=0):
        seen["a"] = a
        raise ValueError("bad thing")
    m = FakeChat([calls(("boom", {"a": 2, "evil": "x"})), ModelTurn(text="ok", calls=[])])
    r = run_tool_loop(m, "sys", "go", {"boom": spec("boom", boom, {"a": {"type": "INTEGER"}})})
    assert seen == {"a": 2} and not r.trail[0].ok and "ValueError" in r.trail[0].error and r.final_text == "ok"


def test_loop_survives_model_failure():
    r = run_tool_loop(FakeChat([RuntimeError("503")]), "sys", "go", {})
    assert "could not be reached" in r.error and r.trail == []


def test_loop_stops_after_too_many_turns():
    m = FakeChat([calls(("t", {}))] * 50)
    r = run_tool_loop(m, "sys", "go", {"t": spec("t", lambda: 1)}, max_calls=100, max_turns=3)
    assert "too many steps" in r.error and r.calls_used == 3


# =============================================================================== guards
def test_guards():
    assert guards.has_verdict("Good news, you qualify for aid") and not guards.has_verdict("you may qualify")
    assert guards.has_legal_or_promise("Under ERISA you can appeal") and guards.has_legal_or_promise("this will be approved")
    assert guards.has_clinical_advice("you should start chemotherapy") and not guards.has_clinical_advice("ask your care team")
    assert guards.numbers_grounded("about 150%", ["fpl 150.0 percent"]) and not guards.numbers_grounded("about 90%", ["fpl 150"])
    text, why = guards.safe_text("you qualify for $5,000", ["x"], "fallback")
    assert text == "fallback" and why == "eligibility verdict"
    assert guards.safe_text("ask the billing office", ["x"], "fb") == ("ask the billing office", None)


# =============================================================================== assistance agent
@pytest.fixture
def files(tmp_path):
    fpl = tmp_path / "fpl.json"
    fpl.write_text(json.dumps({"status": "TEST", "year": 2000, "first_person_amount": 10000, "each_additional_person": 4000,
                               "source_url": "https://example.test/fpl"}))
    prog = tmp_path / "programs.json"
    prog.write_text(json.dumps({"programs": [dict(
        program_name="Test Charity Care", sponsor="Roswell Park", type="hospital charity care", eligibility_summary="x",
        fpl_threshold_pct=200, applies_to="both", how_to_apply="Call the financial counselor", contact="555",
        source_url="https://example.test/fap", retrieved_date="2026-11-07", verified=True)]}))
    return fpl, prog


CASE = Case(household_size=3, annual_income=27000, confirmed_codes=["77066"],
            estimates=[CaseEstimate(hospital="Roswell Park", patient_oop=400, total_allowed=900, complete=True),
                       CaseEstimate(hospital="ECMC", patient_oop=0, total_allowed=0, complete=False)])


def test_assistance_full_loop_builds_response_from_tool_results(files):
    fpl, prog = files
    m = FakeChat(
        [calls(("get_case", {})), calls(("compute_fpl_percent", {"household_size": 3, "annual_income": 27000})),
         calls(("lookup_assistance", {"fpl_percent": 150.0})), ModelTurn(text="done", calls=[])],
        {id(SUMMARY_SCHEMA): {"summary_plain": "Your income is about 150% of the poverty level. You may qualify for 1 program. Please confirm with the billing office."}})
    r = run_assistance_agent(m, CASE, fpl_path=fpl, programs_path=prog)
    assert r.fpl.fpl_percent == 150.0 and r.meta.tool_calls_used == 3 and r.meta.model == "fake-model"
    assert [t.tool for t in r.meta.trail] == ["get_case", "compute_fpl_percent", "lookup_assistance"]
    assert r.matches[0].program_name == "Test Charity Care" and r.matches[0].match_basis == "income_at_or_below_listed_threshold"
    assert "may qualify" in r.matches[0].why and r.matches[0].source_url == "https://example.test/fap"
    assert "150%" in r.summary_plain
    assert r.not_found == ["I couldn't find a verified financial assistance policy for ECMC."]   # Roswell row exists, ECMC doesn't


def test_assistance_replaces_summary_with_invented_number_or_verdict(files):
    fpl, prog = files
    for bad in ("You qualify for $9,999 in aid.", "Your income is 42% of the poverty level."):
        m = FakeChat([ModelTurn(text="done", calls=[])], {id(SUMMARY_SCHEMA): {"summary_plain": bad}})
        r = run_assistance_agent(m, CASE, fpl_path=fpl, programs_path=prog)
        assert bad not in r.summary_plain and "may qualify" in r.summary_plain
        assert any("replaced" in d for d in r.meta.disclaimers)


def test_assistance_code_fills_in_when_model_skips_tools(files):
    fpl, prog = files
    r = run_assistance_agent(FakeChat([ModelTurn(text="done", calls=[])], {id(SUMMARY_SCHEMA): {"summary_plain": "ok"}}),
                             CASE, fpl_path=fpl, programs_path=prog)
    assert r.fpl.fpl_percent == 150.0 and len(r.matches) == 1


def test_assistance_survives_model_outage_and_says_nothing_found_for_placeholder_table(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"programs": []}))
    m = FakeChat([RuntimeError("down")], {id(SUMMARY_SCHEMA): RuntimeError("down")})
    r = run_assistance_agent(m, Case(), programs_path=empty)
    assert r.matches == [] and "couldn't find this" in r.summary_plain.lower() and r.meta.error
    assert r.fpl is None


def test_assistance_without_income_marks_thresholded_programs_as_need_more_info(files):
    _, prog = files
    r = run_assistance_agent(FakeChat([ModelTurn(text="done", calls=[])], {id(SUMMARY_SCHEMA): {"summary_plain": "ok"}}),
                             Case(), programs_path=prog)
    assert r.matches[0].match_basis == "need_more_information" and r.fpl is None


# =============================================================================== appeal agent
LETTER = ("Dear Jane Doe, claim number AB-12345 for CPT 77066 was denied on 03/04/2026 because the service was not "
          "medically necessary. Member ID: XYZ98765. Your plan: Univera PPO Plus.")
APPEAL_CASE = Case(confirmed_codes=["77066"], insurer="Univera", plan="PPO")


def appeal_model(parse, narrative):
    return FakeChat(finals={id(DENIAL_SCHEMA): parse, id(NARRATIVE_SCHEMA): narrative})


GOOD_PARSE = {"denial_reason": "not medically necessary", "denied_service_or_code": "CPT 77066", "date": "03/04/2026",
              "claim_or_reference_number": "[CLAIM_1]", "plan_name": "Univera PPO Plus",
              "appeal_deadline_if_stated": "within 30 days"}                    # invented: not in the letter


def test_appeal_scrubs_before_llm_restores_locally_and_nulls_invented_deadline():
    m = appeal_model(GOOD_PARSE, {"summary_plain": "Your plan denied a mammogram as not medically necessary.",
                                  "documentation_checklist": ["A statement from your clinician"],
                                  "next_steps": ["Confirm the appeal deadline with your plan"]})
    r = run_appeal_agent(m, APPEAL_CASE, LETTER, patient_facts={"patient_name": "Jane Doe"})
    sent = " ".join(m.prompts_seen)
    for secret in ("Jane Doe", "AB-12345", "XYZ98765"):
        assert secret not in sent, secret                                       # nothing private reached the model
    assert r.parsed.claim_or_reference_number == "AB-12345"                     # restored locally
    assert "AB-12345" in r.letter.body and "Jane Doe" in r.letter.body
    assert r.parsed.appeal_deadline_if_stated is None                             # "within 30 days" was never in the letter
    assert r.case_check.denied_code_in_confirmed is True and r.case_check.plan_consistent_with_case is True
    assert [t.tool for t in r.meta.trail] == ["parse_denial", "check_against_case", "draft_letter"]
    assert "[MEMBER ID]" in r.letter.placeholders and "[CLINICIAN'S STATEMENT - to be written by your care team]" in r.letter.placeholders


def test_appeal_flags_a_denied_code_the_patient_never_confirmed():
    m = appeal_model({**GOOD_PARSE, "denied_service_or_code": "CPT 19083"}, {"summary_plain": "x", "documentation_checklist": ["a"], "next_steps": ["b"]})
    r = run_appeal_agent(m, APPEAL_CASE, LETTER.replace("77066", "19083"))
    assert r.case_check.denied_code_in_confirmed is False and r.case_check.flags[0].kind == "code_not_in_confirmed_codes"


def test_appeal_replaces_wording_that_cites_law_or_promises_outcome():
    m = appeal_model(GOOD_PARSE, {"summary_plain": "Under ERISA your appeal will be approved.",
                                  "documentation_checklist": ["Cite the Affordable Care Act"],
                                  "next_steps": ["You will win"]})
    r = run_appeal_agent(m, APPEAL_CASE, LETTER)
    blob = (r.denial_summary_plain + " ".join(r.documentation_checklist + r.next_steps)).lower()
    assert "erisa" not in blob and "affordable care act" not in blob and "will win" not in blob
    assert any("replaced" in d for d in r.meta.disclaimers)
    assert r.letter.body and "guarantee" not in r.letter.body.lower()


def test_appeal_degrades_gracefully_when_the_model_fails():
    m = appeal_model(RuntimeError("down"), RuntimeError("down"))
    r = run_appeal_agent(m, APPEAL_CASE, LETTER)
    assert r.meta.error and r.letter.body and "[PATIENT NAME]" in r.letter.body       # blanks to fill, no crash
    assert r.parsed.denial_reason is None


def test_insured_patient_is_not_shown_uninsured_only_programs(files):
    _, prog = files
    rows = json.loads(prog.read_text())["programs"]
    rows.append({**rows[0], "program_name": "Uninsured Only Aid", "applies_to": "uninsured", "sponsor": "State"})
    prog.write_text(json.dumps({"programs": rows}))
    insured = Case(insurer="Univera", plan="PPO", household_size=3, annual_income=27000)
    r = run_assistance_agent(FakeChat([ModelTurn(text="done", calls=[])], {id(SUMMARY_SCHEMA): {"summary_plain": "ok"}}),
                             insured, programs_path=prog, fpl_path=files[0])
    assert "Uninsured Only Aid" not in [m.program_name for m in r.matches]
    un = Case(uninsured=True, household_size=3, annual_income=27000)
    r = run_assistance_agent(FakeChat([ModelTurn(text="done", calls=[])], {id(SUMMARY_SCHEMA): {"summary_plain": "ok"}}),
                             un, programs_path=prog, fpl_path=files[0])
    assert "Uninsured Only Aid" in [m.program_name for m in r.matches]
