"""Explainability for the two agents: WHY each step was taken and WHY each result is what it is.

Design rule: every reason here is written by code from the real values the agent used. A model's own account of
its reasoning is never shown as an explanation, because it can sound convincing and still be wrong.
Where an AI did something (chose a tool, wrote wording), the step says so and says what checked it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..domain.case import Case
from .assistance import evaluate_program, load_programs
from .guards import safe_text
from .schemas import (CaseCheck, Explanation, ExplainStep, FplResult, LetterDraft, ParsedDenial, ProgramCheck, ReactStep,
                      ToolCallRecord)

WHO_TOOL = "The AI chose to run this tool; code did the work"
WHO_CODE = "Code (a fixed rule, no AI)"
DECISION_RANK = {"may_apply": 0, "need_more_info": 1, "ruled_out": 2}


def _ran(trail: List[ToolCallRecord], tool: str) -> bool:
    return any(t.tool == tool and t.ok for t in trail)


def _who(trail: List[ToolCallRecord], tool: str) -> str:
    return WHO_TOOL if _ran(trail, tool) else "Code ran this itself (the AI skipped it)"


# ------------------------------------------------------------------------------ ReAct trace (Thought -> Action -> Observation)
TOOL_PLAIN = {"get_case": "Read the patient's case", "compute_fpl_percent": "Work out income as a percent of the poverty level",
              "lookup_assistance": "Search the verified program table",
              "check_income_level": "Compare income with the highest income limit in the table",
              "write_summary": "Write the plain-language summary"}
TOOL_SOURCE = {"compute_fpl_percent": "https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines",
               "lookup_assistance": "backend/data/assistance_programs.json"}


def default_thought(tool: str, inputs: Dict[str, Any]) -> str:
    """The reason for a step, written by code. Never built from model-supplied values (they could be hostile)."""
    if tool == "get_case":
        return "I need to know the patient's insurance status and household details before I look anything up."
    if tool == "compute_fpl_percent":
        return "Most programs set their income limit as a percent of the poverty level, so I'll work that out from the household size and income."
    if tool == "lookup_assistance":
        if inputs.get("hospital_or_program"):
            return "I'll narrow the search to one hospital or program to be sure I don't miss its own policy."
        return "Now I'll search the verified program table for programs that fit this case."
    if tool == "write_summary":
        return "I have what I need, so I'll explain the results in simple words."
    return "I'll try this tool to get more information."


def _case_line(view: Dict[str, Any]) -> str:
    cover = ("Uninsured" if view.get("uninsured") else
             f"Insured ({view.get('insurer')}{(' ' + view['plan']) if view.get('plan') else ''})" if view.get("insurer")
             else "Insurance not stated")
    hh = (f"household of {view['household_size']}, income ${view['annual_income']:,.0f} a year"
          if view.get("household_size") is not None and view.get("annual_income") is not None
          else "no household size or income entered")
    return f"{cover}; {hh}."


def observe(tool: str, output: Any, ok: bool, error: Optional[str] = None) -> str:
    """What came back, in plain words, written by code from the tool's real output."""
    if not ok:
        return "This step didn't work, so the agent carried on with what it had."
    try:
        if tool == "get_case":
            return _case_line(output)
        if tool == "compute_fpl_percent":
            return (f"{output['fpl_percent']:g}% of the {output['guideline_year']} poverty level "
                    f"(${output['guideline_amount']:,.0f} for a household of {output['household_size']}).")
        if tool == "lookup_assistance":
            names = [m["program_name"] for m in output.get("matches", [])]
            above = len(output.get("above_listed_threshold", []))
            if not names:
                return "Nothing in the verified table matched." + (f" {above} ruled out because income is above their limit." if above else "")
            shown = "; ".join(names[:3]) + (f"; and {len(names) - 3} more" if len(names) > 3 else "")
            return (f"{len(names)} program{'s' if len(names) != 1 else ''} may be worth asking about: {shown}."
                    + (f" {above} ruled out because income is above their limit." if above else ""))
    except Exception:  # noqa: BLE001 - never let a summary break the agent
        pass
    return "Done."


class ReactRecorder:
    """Builds the ReAct trace as the agent runs, and hands each step to `emit` (the live stream) as it happens.

    The AI's own sentence before a tool call is used as the Thought only if it passes the same guards as all AI text
    (no verdicts, no legal claims, no clinical advice, no number that isn't in the case or earlier results).
    Otherwise a code-written thought is shown, and the step says so."""

    def __init__(self, case: Case, emit: Optional[Callable[[Dict[str, Any]], None]] = None):
        self._case_blob = json.dumps(case.llm_view(), ensure_ascii=False)
        self._seen: List[str] = []
        self._emit = emit
        self.steps: List[ReactStep] = []

    def _send(self, event: Dict[str, Any]) -> None:
        if self._emit is not None:
            try:
                self._emit(event)
            except Exception:  # noqa: BLE001 - a closed connection must not stop the agent
                pass

    def _thought(self, tool: str, inputs: Dict[str, Any], raw: Optional[str]) -> tuple[str, str]:
        fallback = default_thought(tool, inputs)
        text = re.sub(r"^\s*thought\s*:\s*", "", (raw or "").strip(), flags=re.I)
        text = re.sub(r"\s+", " ", text)
        if not text:
            return fallback, "code"
        shown, replaced = safe_text(text, [self._case_blob, *self._seen], fallback, max_len=260)
        return shown, ("code" if replaced else "ai")

    def start(self, tool: str, inputs: Dict[str, Any], raw_thought: Optional[str] = None, run_by: str = "ai",
              forced_thought: Optional[str] = None) -> ReactStep:
        thought, by = (forced_thought, "code") if forced_thought else self._thought(tool, inputs, raw_thought)
        step = ReactStep(n=len(self.steps) + 1, thought=thought, thought_by=by, tool=tool, action=TOOL_PLAIN.get(tool, tool),
                         inputs=inputs, source=TOOL_SOURCE.get(tool), run_by=run_by)  # type: ignore[arg-type]
        self.steps.append(step)
        self._send({"type": "step_start", "step": step.model_dump(mode="json")})
        return step

    def finish(self, step: ReactStep, output: Any, ok: bool = True, error: Optional[str] = None,
               observation: Optional[str] = None) -> None:
        step.ok = ok
        step.observation = observation if observation is not None else observe(step.tool, output, ok, error)
        if ok and output is not None:
            self._seen.append(json.dumps(output, ensure_ascii=False, default=str))
        self._send({"type": "step_done", "n": step.n, "observation": step.observation, "ok": step.ok})

    # callbacks for run_tool_loop(on_event=...)
    def on_loop_event(self, kind: str, payload: Any) -> None:
        if kind == "action":
            self.start(payload["tool"], payload["inputs"], payload.get("thought"), run_by="ai")
        elif kind == "observation" and self.steps:
            rec: ToolCallRecord = payload
            self.finish(self.steps[-1], rec.output, rec.ok, rec.error)

    def run_by_code(self, tool: str, inputs: Dict[str, Any], output: Any, thought: str, observation: Optional[str] = None,
                    ok: bool = True) -> None:
        step = self.start(tool, inputs, run_by="code", forced_thought=thought)
        self.finish(step, output, ok, observation=observation)


# ------------------------------------------------------------------------------ financial assistance matcher
def assistance_explanation(case: Case, fpl: Optional[FplResult], trail: List[ToolCallRecord], uninsured: Optional[bool],
                           programs_path: Optional[Path], hospital_names: List[str], not_found: List[str],
                           ai_wording_used: bool, wording_replaced: Optional[str],
                           react: Optional[List[ReactStep]] = None, income_note: Optional[Any] = None) -> Explanation:
    programs = load_programs(programs_path)
    pct = fpl.fpl_percent if fpl else None
    evals = [(p, evaluate_program(p, pct, uninsured)) for p in programs]
    checks = sorted((ProgramCheck(program_name=p.program_name, sponsor=p.sponsor, decision=ev.decision, reason=ev.reason,
                                  criteria=ev.criteria, source_url=p.source_url)
                     for p, ev in evals if ev.decision != "skipped"),
                    key=lambda c: (DECISION_RANK[c.decision], c.program_name))
    count = {d: sum(1 for c in checks if c.decision == d) for d in DECISION_RANK}
    unverified = sum(1 for _, ev in evals if ev.decision == "skipped")

    cover = ("Uninsured" if case.uninsured else
             f"Insured ({case.insurer.value}{(' ' + case.plan) if case.plan else ''})" if case.insurer else "Insurance not stated")
    household = (f"household of {case.household_size}, income ${case.annual_income:,.0f} a year" if case.has_income_info
                 else "no household size or income entered")

    steps: List[ExplainStep] = []

    def add(action: str, why: str, result: str, who: str, source: Optional[str] = None) -> None:
        steps.append(ExplainStep(n=len(steps) + 1, action=action, why=why, result=result, who=who, source=source))

    add("Read your case details",
        "To know your insurance status and household income. The AI never sees names or ID numbers.",
        f"{cover}; {household}.", _who(trail, "get_case"))
    if fpl:
        people = "person" if fpl.household_size == 1 else "people"
        status = "" if fpl.data_status == "VERIFIED" else f" (guideline table status: {fpl.data_status})"
        add("Compared your income to the Federal Poverty Level",
            "Most assistance programs set their income limit as a percent of the poverty level.",
            f"${fpl.annual_income:,.0f} for {fpl.household_size} {people} is about {fpl.fpl_percent:g}% of the {fpl.guideline_year} "
            f"guideline (${fpl.guideline_amount:,.0f} for a household that size){status}.",
            _who(trail, "compute_fpl_percent"), fpl.source_url)
    else:
        add("Skipped the income comparison",
            "It needs both a household size and an income.",
            "Programs with an income limit are marked 'need more information' instead of being ruled in or out.", WHO_CODE)
    result = (f"{len(checks)} verified program{'s' if len(checks) != 1 else ''} checked: {count['may_apply']} may apply, "
              f"{count['need_more_info']} need more information, {count['ruled_out']} ruled out")
    if unverified:
        result += f". {unverified} more not shown because they aren't verified yet"
    add("Searched the verified program table",
        "Only programs checked against an official source are shown. The agent never guesses or uses its own memory.",
        result + ".", "Code (a full search of the whole table always runs, whatever the AI asked for)",
        "backend/data/assistance_programs.json")
    add("Checked each program's rules against your case",
        "Each program sets its own limits. Checking one rule at a time shows exactly why a program is or isn't listed.",
        "See the program-by-program checks.", WHO_CODE)
    if hospital_names:
        missing = " ".join(not_found) if not_found else ""
        add("Looked for each hospital's own assistance policy",
            "Hospitals run their own charity-care programs, separate from public programs.",
            f"Hospitals checked: {', '.join(hospital_names)}." + (f" {missing}" if missing else " A verified policy was found for each."),
            WHO_CODE, "backend/data/assistance_programs.json")
    if income_note is not None and fpl is not None:
        add("Compared your income with the highest income limit",
            "If income is above every income limit, income-based programs are unlikely to apply, and the person is better served by other ways to lower the bill.",
            f"The highest income limit in the table is {income_note.highest_limit_pct:g}% of the poverty level. Yours is about "
            f"{fpl.fpl_percent:,.0f}%, so income-based programs are unlikely to apply.", WHO_CODE)
    if income_note is not None:
        wording, who = ("Written by code from your numbers, so the message is fixed and can't promise or rule out help.", WHO_CODE)
    elif ai_wording_used and not wording_replaced:
        wording, who = ("The AI's wording passed our checks: no promise that you qualify, and no number that isn't in the results above.",
                        "The AI wrote it; code checked it")
    elif ai_wording_used:
        wording, who = (f"The AI's wording failed a check ({wording_replaced}), so standard wording is shown instead.",
                        "Code (replaced the AI's wording)")
    else:
        wording, who = "Standard wording used (the AI wasn't available).", WHO_CODE
    add("Wrote the plain-language summary", "To explain the results in simple words.", wording, who)

    not_checked = ["Other rules a program may have (assets, county of residence, immigration status, what you already owe) aren't "
                   "checked here. Each program decides these itself.",
                   "Whether a program covers your specific tests isn't checked.",
                   "Income limits come from each program's published policy on the date shown at its source. Confirm they haven't changed."]
    if uninsured is None:
        not_checked.insert(0, "We don't know whether you have insurance, so programs for only insured or only uninsured patients weren't filtered out.")
    if not case.has_income_info:
        not_checked.insert(0, "No household size or income was entered, so income limits couldn't be compared.")
    if fpl and fpl.data_status != "VERIFIED":
        not_checked.insert(0, f"The poverty-level table is marked '{fpl.data_status}', so treat the percent as approximate.")
    return Explanation(steps=steps, react=list(react or []), program_checks=checks, not_checked=not_checked)


# ------------------------------------------------------------------------------ appeal letter drafter
FIELD_LABELS = {"denial_reason": "the reason", "denied_service_or_code": "the denied service", "date": "the denial date",
                "claim_or_reference_number": "the claim number", "appeal_deadline_if_stated": "an appeal deadline",
                "plan_name": "the plan name", "missing_documentation_mentioned": "documents the plan asked for"}


def appeal_explanation(scrub_found: Dict[str, int], parsed: ParsedDenial, parse_ok: bool, check: CaseCheck,
                       letter: LetterDraft, ai_wording_used: bool, wording_replaced: Optional[str]) -> Explanation:
    steps: List[ExplainStep] = []

    def add(action: str, why: str, result: str, who: str) -> None:
        steps.append(ExplainStep(n=len(steps) + 1, action=action, why=why, result=result, who=who))

    total = sum(scrub_found.values())
    kinds = ", ".join(f"{n} {k.lower().replace('_', ' ')}" for k, n in sorted(scrub_found.items()))
    add("Hid private details before the AI read the letter",
        "Names, member IDs, phone numbers and similar details shouldn't be sent to the AI. It only sees placeholders like [NAME_1]; "
        "the real values are put back afterwards, on our side.",
        f"Replaced {total} private detail{'s' if total != 1 else ''} ({kinds})." if total else "No private details were detected.",
        WHO_CODE)

    if parse_ok:
        data = parsed.model_dump()
        found = [FIELD_LABELS[k] for k in FIELD_LABELS if data.get(k)]
        absent = [FIELD_LABELS[k] for k in FIELD_LABELS if not data.get(k)]
        result = ("Found: " + (", ".join(found) if found else "nothing clear") + "."
                  + (f" Not stated in the letter: {', '.join(absent)}." if absent else ""))
    else:
        result = "The letter couldn't be read automatically, so the draft below has blanks for you to fill in."
    add("Read the denial letter",
        "To pull out only the facts the letter actually states, so the draft uses your real details and nothing is guessed.",
        result, "The AI read it; code then removed any value that doesn't appear word for word in the letter")

    problems = [f.message for f in check.flags]
    add("Compared the letter with your case",
        "A mismatch can mean the wrong code was billed or the letter is for a different plan. This only compares facts; "
        "it never judges whether the denial is right.",
        " ".join(problems) if problems else "No mismatches found: the denied code and plan match your case.", WHO_CODE)

    blanks = ", ".join(letter.placeholders) if letter.placeholders else "none"
    add("Built the letter from a fixed template",
        "A fixed template means the letter can only contain facts from your letter or that you typed. Nothing is invented, "
        "no law is cited, and no result is promised. Anything missing becomes a blank for you to fill in.",
        f"{len(letter.placeholders)} blank{'s' if len(letter.placeholders) != 1 else ''} left to fill in: {blanks}.", WHO_CODE)

    if ai_wording_used and not wording_replaced:
        wording, who = ("The AI's wording passed our checks: no verdicts, no legal claims, no numbers that aren't in the letter.",
                        "The AI wrote it; code checked it")
    elif ai_wording_used:
        wording, who = (f"Some AI wording failed a check ({wording_replaced}), so standard wording is shown instead.",
                        "Code (replaced the AI's wording)")
    else:
        wording, who = "Standard wording used (the AI wasn't available).", WHO_CODE
    add("Wrote the summary, document checklist and next steps",
        "To explain the letter in simple words and list what to ask your care team for.", wording, who)

    not_checked = ["Whether the denial is correct. This tool can't judge medical necessity or your plan's rules.",
                   "Your plan's appeal deadline, unless the letter states one. Confirm it with your plan."]
    return Explanation(steps=steps, not_checked=not_checked)
