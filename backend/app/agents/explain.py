"""Explainability for the two agents: WHY each step was taken and WHY each result is what it is.

Design rule: every reason here is written by code from the real values the agent used. A model's own account of
its reasoning is never shown as an explanation, because it can sound convincing and still be wrong.
Where an AI did something (chose a tool, wrote wording), the step says so and says what checked it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ..domain.case import Case
from .assistance import evaluate_program, load_programs
from .schemas import CaseCheck, Explanation, ExplainStep, FplResult, LetterDraft, ParsedDenial, ProgramCheck, ToolCallRecord

WHO_TOOL = "The AI chose to run this tool; code did the work"
WHO_CODE = "Code (a fixed rule, no AI)"
DECISION_RANK = {"may_apply": 0, "need_more_info": 1, "ruled_out": 2}


def _ran(trail: List[ToolCallRecord], tool: str) -> bool:
    return any(t.tool == tool and t.ok for t in trail)


def _who(trail: List[ToolCallRecord], tool: str) -> str:
    return WHO_TOOL if _ran(trail, tool) else "Code ran this itself (the AI skipped it)"


# ------------------------------------------------------------------------------ financial assistance matcher
def assistance_explanation(case: Case, fpl: Optional[FplResult], trail: List[ToolCallRecord], uninsured: Optional[bool],
                           programs_path: Optional[Path], hospital_names: List[str], not_found: List[str],
                           ai_wording_used: bool, wording_replaced: Optional[str]) -> Explanation:
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
    if ai_wording_used and not wording_replaced:
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
    return Explanation(steps=steps, program_checks=checks, not_checked=not_checked)


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
