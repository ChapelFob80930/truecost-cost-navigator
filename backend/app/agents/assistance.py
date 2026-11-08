"""lookup_assistance: reads the curated program table (JSON). Never guesses.

Matching is deterministic:
- a program is considered only if `verified` is true (placeholder rows are skipped),
- `hospital_or_program` must appear (case-insensitive) in the program name or sponsor,
- applies_to must fit the patient's insured/uninsured status when that is known,
- if the row lists an FPL threshold, the patient's FPL percent must be at or below it
  (TODO-VERIFY per program: whether each official threshold is "at or below" or "below").
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

from ..config import PROJECT_ROOT
from .schemas import AssistanceProgram, CriterionCheck, IncomeNote

DEFAULT_PROGRAMS_PATH = PROJECT_ROOT / "backend" / "data" / "assistance_programs.json"


@dataclass
class ProgramMatch:
    program: AssistanceProgram
    basis: str  # income_at_or_below_listed_threshold | no_income_threshold_listed | need_more_information


@dataclass
class ProgramEvaluation:
    """Result of checking ONE program against ONE case. The single source of truth for 'does it apply':
    lookup_assistance (what is returned) and the explanation (why) both use it, so they cannot disagree."""
    decision: str                      # may_apply | need_more_info | ruled_out | skipped
    basis: Optional[str]               # set when decision is may_apply / need_more_info
    reason: str
    criteria: List[CriterionCheck]
    ruled_out_by: Optional[str] = None  # "coverage" | "income"


_COVERAGE_WORDS = {"insured": "people who have insurance", "uninsured": "uninsured patients", "both": "patients with or without insurance"}


def evaluate_program(p: AssistanceProgram, fpl_percent: Optional[float], uninsured: Optional[bool]) -> ProgramEvaluation:
    if not p.verified:
        return ProgramEvaluation("skipped", None, "Not shown: we haven't verified this program against an official source yet.",
                                 [CriterionCheck(criterion="Verified source", your_value="n/a",
                                                 program_rule="Only programs checked against an official source are shown", result="not_met")])
    criteria: List[CriterionCheck] = [CriterionCheck(criterion="Verified source", your_value="n/a",
                                                     program_rule="Checked against an official source", result="met")]

    # 1) coverage
    mine = {True: "Uninsured", False: "Has insurance", None: "Not stated"}[uninsured]
    rule = f"Open to {_COVERAGE_WORDS.get(p.applies_to, p.applies_to)}"
    if p.applies_to == "both":
        cov = "met"
    elif uninsured is None:
        cov = "unknown"
    else:
        cov = "met" if (p.applies_to == "uninsured") == uninsured else "not_met"
    criteria.append(CriterionCheck(criterion="Insurance status", your_value=mine, program_rule=rule, result=cov))
    if cov == "not_met":
        return ProgramEvaluation("ruled_out", None,
                                 f"Ruled out: this program is for {_COVERAGE_WORDS[p.applies_to]}, and your case says: {mine.lower()}.",
                                 criteria, "coverage")

    # 2) income
    if p.fpl_threshold_pct is None:
        criteria.append(CriterionCheck(criterion="Income limit", your_value="n/a", program_rule="No income limit listed in our table",
                                       result="not_applicable"))
        return ProgramEvaluation("may_apply", "no_income_threshold_listed",
                                 "May be worth asking about: no income limit is listed, so we can't rule it out.", criteria)
    limit = f"At or below {p.fpl_threshold_pct:g}% of the Federal Poverty Level"
    if fpl_percent is None:
        criteria.append(CriterionCheck(criterion="Income limit", your_value="Not entered", program_rule=limit, result="unknown"))
        return ProgramEvaluation("need_more_info", "need_more_information",
                                 "Can't tell yet: this program has an income limit and household size and income haven't been entered.",
                                 criteria)
    ok = fpl_percent <= p.fpl_threshold_pct
    criteria.append(CriterionCheck(criterion="Income limit", your_value=f"About {fpl_percent:g}% of the Federal Poverty Level",
                                   program_rule=limit, result="met" if ok else "not_met"))
    if ok:
        return ProgramEvaluation("may_apply", "income_at_or_below_listed_threshold",
                                 f"May be worth asking about: your income (about {fpl_percent:g}% of the poverty level) "
                                 f"is at or below the listed limit of {p.fpl_threshold_pct:g}%.", criteria)
    return ProgramEvaluation("ruled_out", None,
                             f"Ruled out: your income (about {fpl_percent:g}% of the poverty level) is above the listed "
                             f"limit of {p.fpl_threshold_pct:g}%.", criteria, "income")


@dataclass
class LookupResult:
    matches: List[ProgramMatch] = field(default_factory=list)
    above_threshold: List[str] = field(default_factory=list)   # names excluded because income is over the listed line
    skipped_unverified: List[str] = field(default_factory=list)
    found_nothing: bool = True                                   # True -> say "I couldn't find this"


HIGH_INCOME_SUGGESTIONS = [
    "Ask each hospital for a written cost estimate before you schedule, and compare it with the estimates in this app.",
    "Call your insurer to confirm the hospital is in your network and to ask what your plan covers before you go.",
    "Ask the billing office whether they offer a payment plan so you can spread a bill over several months.",
    "If you have a Health Savings Account or a Flexible Spending Account, ask your employer or plan administrator whether it can pay for these services.",
    "After a visit, ask for an itemized bill and check it against the services you received.",
    "Check how much of your deductible and out-of-pocket maximum is left this year, because that changes what you pay.",
]


def income_context(fpl_percent: Optional[float], programs: List[AssistanceProgram], uninsured: Optional[bool]) -> Optional[IncomeNote]:
    """If income is above the limit of EVERY income-based program that could apply, say so plainly (and what else to try).
    Returns None when income is unknown, or at or below at least one program's limit."""
    if fpl_percent is None:
        return None
    limits = []
    for p in programs:
        ev = evaluate_program(p, fpl_percent, uninsured)
        if ev.decision == "skipped" or ev.ruled_out_by == "coverage" or p.fpl_threshold_pct is None:
            continue
        limits.append(p.fpl_threshold_pct)
    if not limits or fpl_percent <= max(limits):
        return None
    top = max(limits)
    times = fpl_percent / 100.0
    times_text = f"{times:,.0f}" if times >= 10 else f"{times:.1f}"
    return IncomeNote(
        headline="Your income is well above the poverty level",
        detail=(f"Your income is about {fpl_percent:,.0f}% of the Federal Poverty Level, roughly {times_text} times the poverty line. "
                f"The income-based programs in our table go up to {top:g}%, so they are unlikely to apply to you. "
                "Programs that list no income limit are still shown below. Confirm with each program."),
        highest_limit_pct=top, times_poverty_line=round(times, 1), suggestions=list(HIGH_INCOME_SUGGESTIONS))


def load_programs(path: Optional[Path] = None) -> List[AssistanceProgram]:
    path = Path(path or DEFAULT_PROGRAMS_PATH)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw["programs"] if isinstance(raw, dict) else raw
    return [AssistanceProgram(**r) for r in rows]


def lookup_assistance(fpl_percent: Optional[float], hospital_or_program: Optional[str] = None,
                      uninsured: Optional[bool] = None, path: Optional[Path] = None) -> LookupResult:
    needle = (hospital_or_program or "").strip().lower()
    result = LookupResult()
    for p in load_programs(path):
        if needle and needle not in p.program_name.lower() and needle not in p.sponsor.lower():
            continue
        ev = evaluate_program(p, fpl_percent, uninsured)
        if ev.decision == "skipped":
            result.skipped_unverified.append(p.program_name)
        elif ev.decision == "ruled_out":
            if ev.ruled_out_by == "income":
                result.above_threshold.append(p.program_name)
        else:
            result.matches.append(ProgramMatch(p, ev.basis or "no_income_threshold_listed"))
    result.found_nothing = not result.matches
    return result


# ------------------------------------------------------------------------------ audit (guards the curated files)
PLACEHOLDER_DATE = date(1900, 1, 1)


def _row_problems(label: str, row: dict, text_fields: List[str]) -> List[str]:
    problems: List[str] = []
    if not row.get("verified"):
        return problems
    blob = " ".join(str(row.get(f, "")) for f in text_fields)
    if "TODO-VERIFY" in blob:
        problems.append(f"{label}: marked verified but still contains TODO-VERIFY")
    if not str(row.get("source_url", "")).startswith("https://"):
        problems.append(f"{label}: verified rows need an https source_url")
    if str(row.get("retrieved_date", "")) in ("", str(PLACEHOLDER_DATE)):
        problems.append(f"{label}: verified rows need a real retrieved_date")
    return problems


def audit_programs(path: Optional[Path] = None) -> List[str]:
    """Problems that make a curated file unsafe to trust. Empty list = OK (an all-placeholder file is OK)."""
    path = Path(path or DEFAULT_PROGRAMS_PATH)
    if not path.exists():
        return [f"{path}: file not found"]
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw["programs"] if isinstance(raw, dict) else raw
    problems: List[str] = []
    fields = ["program_name", "sponsor", "eligibility_summary", "how_to_apply", "contact", "source_url"]
    for r in rows:
        problems += _row_problems(r.get("program_name", "?"), r, fields)
        try:
            AssistanceProgram(**r)
        except Exception as e:  # noqa: BLE001 - report any schema violation
            problems.append(f"{r.get('program_name', '?')}: schema error: {str(e)[:120]}")
    return problems


def audit_billing_rights(path: Path) -> List[str]:
    path = Path(path)
    if not path.exists():
        return [f"{path}: file not found"]
    raw = json.loads(path.read_text(encoding="utf-8"))
    problems: List[str] = []
    for r in raw.get("entries", []):
        problems += _row_problems(r.get("id", "?"), r, ["topic", "plain_summary", "citation", "source_url"])
    return problems
