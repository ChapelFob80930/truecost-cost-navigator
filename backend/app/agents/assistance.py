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
from .schemas import AssistanceProgram

DEFAULT_PROGRAMS_PATH = PROJECT_ROOT / "backend" / "data" / "assistance_programs.json"


@dataclass
class ProgramMatch:
    program: AssistanceProgram
    basis: str  # income_at_or_below_listed_threshold | no_income_threshold_listed | need_more_information


@dataclass
class LookupResult:
    matches: List[ProgramMatch] = field(default_factory=list)
    above_threshold: List[str] = field(default_factory=list)   # names excluded because income is over the listed line
    skipped_unverified: List[str] = field(default_factory=list)
    found_nothing: bool = True                                   # True -> say "I couldn't find this"


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
        if not p.verified:
            result.skipped_unverified.append(p.program_name)
            continue
        if uninsured is not None and p.applies_to != "both" and (p.applies_to == "uninsured") != uninsured:
            continue
        if p.fpl_threshold_pct is None:
            result.matches.append(ProgramMatch(p, "no_income_threshold_listed"))
        elif fpl_percent is None:
            result.matches.append(ProgramMatch(p, "need_more_information"))
        elif fpl_percent <= p.fpl_threshold_pct:
            result.matches.append(ProgramMatch(p, "income_at_or_below_listed_threshold"))
        else:
            result.above_threshold.append(p.program_name)
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
