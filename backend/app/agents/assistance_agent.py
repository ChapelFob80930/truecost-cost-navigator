"""Agent 1: Financial Assistance Matcher. Real tool-use loop; the final response is ASSEMBLED BY CODE from tool results.
The LLM only writes the plain-English summary, and that summary is guarded (no verdicts, no ungrounded numbers)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..domain.case import Case
from .assistance import LookupResult, ProgramMatch, load_programs, lookup_assistance
from .explain import assistance_explanation
from .fpl import compute_fpl_percent
from .guards import safe_text
from .llm import ChatModel, ToolDecl
from .loop import ToolSpec, run_tool_loop, to_jsonable
from .prompts import ASSISTANCE_SUMMARY_PROMPT, ASSISTANCE_SYSTEM_PROMPT
from .schemas import AgentMeta, AssistanceMatchResponse, FplResult, MatchedProgram
from .tools import ToolContext, get_case

SUMMARY_SCHEMA = {"type": "OBJECT", "properties": {"summary_plain": {"type": "STRING"}}, "required": ["summary_plain"]}
BASIS_RANK = {"income_at_or_below_listed_threshold": 0, "no_income_threshold_listed": 1, "need_more_information": 2}
BILLING_QUESTIONS = [
    "Can you send me the financial assistance application and tell me which documents you need?",
    "Does this help apply to the tests and visits I'm planning, and does it cover bills I already have?",
    "Is there a deadline to apply, and can I ask for help after I get a bill?",
    "Who can I talk to if I have trouble filling out the form?",
]


def coverage_status(case: Case) -> Optional[bool]:
    """True = uninsured, False = known to have insurance, None = unknown (no filter on insured/uninsured programs)."""
    if case.uninsured:
        return True
    return False if case.insurer is not None else None


def _tools(ctx: ToolContext, state: Dict[str, Any]) -> Dict[str, ToolSpec]:
    def _fpl(household_size: int, annual_income: float):
        r = compute_fpl_percent(int(household_size), float(annual_income), ctx.fpl_path)
        state["fpl"] = r
        return r

    def _lookup(fpl_percent: Optional[float] = None, hospital_or_program: Optional[str] = None):
        r = lookup_assistance(fpl_percent, hospital_or_program, coverage_status(ctx.case), ctx.programs_path)
        state["lookups"].append(r)
        return {"matches": [{**to_jsonable(m.program), "basis": m.basis} for m in r.matches],
                "above_listed_threshold": r.above_threshold, "found_nothing": r.found_nothing}

    obj = lambda props, req=(): {"type": "OBJECT", "properties": props, "required": list(req)}  # noqa: E731
    return {
        "get_case": ToolSpec(ToolDecl("get_case", "Get the patient's case (no private identifiers).", obj({})),
                             lambda: get_case(ctx)),
        "compute_fpl_percent": ToolSpec(
            ToolDecl("compute_fpl_percent", "Income as a percent of the Federal Poverty Level. Use only values from the case.",
                     obj({"household_size": {"type": "INTEGER"}, "annual_income": {"type": "NUMBER"}},
                         ["household_size", "annual_income"])),
            _fpl, source="https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines"),
        "lookup_assistance": ToolSpec(
            ToolDecl("lookup_assistance", "Look up assistance programs in the curated, verified table. "
                     "hospital_or_program optionally filters by hospital or program name.",
                     obj({"fpl_percent": {"type": "NUMBER"}, "hospital_or_program": {"type": "STRING"}})),
            _lookup, source="backend/data/assistance_programs.json"),
    }


def _why(m: ProgramMatch, fpl: Optional[FplResult]) -> str:
    p = m.program
    if m.basis == "income_at_or_below_listed_threshold" and fpl is not None:
        return (f"{p.sponsor} lists an income limit of {p.fpl_threshold_pct:g}% of the Federal Poverty Level. "
                f"Your income is about {fpl.fpl_percent:g}% of it, so you may qualify. Confirm with the program.")
    if m.basis == "no_income_threshold_listed":
        return f"{p.sponsor} does not list an income limit in our table, so it may be worth asking about. Confirm with the program."
    return (f"{p.sponsor} lists an income limit, but we don't have your household size and income yet. "
            "Add them to see if it may apply, then confirm with the program.")


def _fallback_summary(matches: List[MatchedProgram], fpl: Optional[FplResult]) -> str:
    if not matches:
        return ("I couldn't find this. We have no verified assistance program in our table that matches your situation. "
                "Please ask the hospital billing office or a patient navigator about financial help.")
    inc = (f"Your income is about {fpl.fpl_percent:g}% of the Federal Poverty Level. " if fpl else
           "Add your household size and income for a closer match. ")
    return (f"{inc}You may qualify for {len(matches)} program{'s' if len(matches) != 1 else ''} worth asking about. "
            "We can't tell you that you qualify. Please confirm with each program or the hospital billing office, "
            "or talk to a patient navigator.")


def run_assistance_agent(model: ChatModel, case: Case, hospitals: Optional[List[str]] = None,
                         fpl_path: Optional[Path] = None, programs_path: Optional[Path] = None) -> AssistanceMatchResponse:
    ctx = ToolContext(case, fpl_path, programs_path)
    state: Dict[str, Any] = {"fpl": None, "lookups": []}
    loop = run_tool_loop(model, ASSISTANCE_SYSTEM_PROMPT,
                         "Find assistance programs this patient may want to ask about. Start with get_case.",
                         _tools(ctx, state))

    # code, not the LLM, guarantees the FPL value and the lookup when the model skipped them
    fpl: Optional[FplResult] = state["fpl"]
    if fpl is None and case.has_income_info:
        try:
            fpl = compute_fpl_percent(case.household_size, case.annual_income, fpl_path)
        except Exception as e:  # noqa: BLE001
            loop.error = loop.error or f"Could not compute the poverty-level percent: {e}"
    # always run one complete, unfiltered search in code, so a narrow tool call by the AI can never hide a program
    state["lookups"].append(lookup_assistance(fpl.fpl_percent if fpl else None, None, coverage_status(case), programs_path))

    found: Dict[str, ProgramMatch] = {}
    for lk in state["lookups"]:
        lk: LookupResult
        for m in lk.matches:
            found.setdefault(m.program.program_name, m)
    ordered = sorted(found.values(), key=lambda m: (BASIS_RANK[m.basis], m.program.program_name))
    matches = [MatchedProgram(program_name=m.program.program_name, sponsor=m.program.sponsor, type=m.program.type,
                              match_basis=m.basis, why=_why(m, fpl), eligibility_summary=m.program.eligibility_summary,
                              contact=m.program.contact, source_url=m.program.source_url,
                              next_step=m.program.how_to_apply, questions_for_billing_office=BILLING_QUESTIONS,
                              verified=m.program.verified) for m in ordered]

    verified_hospital_rows = [p for p in load_programs(programs_path) if p.verified and p.type == "hospital charity care"]
    names = hospitals or [e.hospital for e in case.estimates]
    not_found = [f"I couldn't find a verified financial assistance policy for {h}." for h in names
                 if not any(h.lower() in (p.sponsor + " " + p.program_name).lower() for p in verified_hospital_rows)]
    if not matches:
        not_found.insert(0, "I couldn't find a verified program in our table that matches your situation.")

    fallback = _fallback_summary(matches, fpl)
    summary, replaced = fallback, None
    ai_wording_used = False
    if loop.error is None or matches:
        try:
            facts = json.dumps({"fpl": to_jsonable(fpl), "matches": [m.model_dump(mode="json") for m in matches],
                                "not_found": not_found}, ensure_ascii=False)
            raw = model.final_json(ASSISTANCE_SYSTEM_PROMPT, ASSISTANCE_SUMMARY_PROMPT + facts, SUMMARY_SCHEMA)
            summary, replaced = safe_text(raw.get("summary_plain"), [facts, json.dumps(case.llm_view())], fallback,
                                          extra_numbers=[len(matches)])
            ai_wording_used = True
        except Exception as e:  # noqa: BLE001
            loop.error = loop.error or f"The AI summary failed ({type(e).__name__}); showing a standard summary."
    meta = AgentMeta(mode="live", model=model.model_name, tool_calls_used=loop.calls_used, trail=loop.trail, error=loop.error)
    if replaced:
        meta.disclaimers = meta.disclaimers + [f"The AI's wording was replaced with standard wording ({replaced})."]
    explanation = assistance_explanation(case, fpl, loop.trail, coverage_status(case), programs_path, names, not_found,
                                         ai_wording_used, replaced)
    return AssistanceMatchResponse(fpl=fpl, matches=matches, not_found=not_found, summary_plain=summary,
                                   explanation=explanation, meta=meta)
