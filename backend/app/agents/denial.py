"""Denial-letter tools: parse_denial (LLM + grounding), check_against_case (pure code), draft_letter (template).

The LLM is injected as a plain callable so these are testable offline and the model can be swapped.
Guardrails enforced HERE, in code, not just in prompts:
- parse_denial nulls any value that does not literally appear in the text (no inference),
- check_against_case only compares; it never judges medical necessity,
- draft_letter inserts patient-supplied facts only, leaves [PLACEHOLDER]s, cites no law, promises no outcome.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Set

from ..domain.case import Case
from ..domain.plan_types import classify
from .schemas import CaseCheck, CaseFlag, LetterDraft, ParsedDenial

# (system_prompt, text) -> dict matching ParsedDenial. Wired to Gemini in deliverable 4.
LlmExtract = Callable[[str, str], Dict[str, Any]]

_CODE_RE = re.compile(r"\b(?:\d{5}|[A-Z]\d{4})\b")
INSURER_ALIASES = {
    "Independent Health": ["independent health", "iha"],
    "Highmark BCBS": ["highmark", "bcbs", "blue cross", "blue shield", "healthnow"],
    "Univera": ["univera"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


# ------------------------------------------------------------------------------ parse_denial
def ground_to_text(parsed: ParsedDenial, text: str) -> ParsedDenial:
    """Null every value that is not literally present in the text (case/whitespace-insensitive)."""
    hay = _norm(text)
    data = parsed.model_dump()
    for key, val in data.items():
        if isinstance(val, str):
            data[key] = val if val.strip() and _norm(val) in hay else None
        elif isinstance(val, list):
            kept = [v for v in val if isinstance(v, str) and v.strip() and _norm(v) in hay]
            data[key] = kept or None
    return ParsedDenial(**data)


def parse_denial(text: str, llm: LlmExtract, system_prompt: str = "") -> ParsedDenial:
    """`text` must already be PHI-scrubbed. Fields not stated in the text come back null."""
    if not text or not text.strip():
        return ParsedDenial()
    raw = llm(system_prompt, text)
    allowed = set(ParsedDenial.model_fields)
    parsed = ParsedDenial(**{k: v for k, v in (raw or {}).items() if k in allowed})
    return ground_to_text(parsed, text)


# ------------------------------------------------------------------------------ check_against_case
def _codes_in(text: Optional[str]) -> List[str]:
    return list(dict.fromkeys(_CODE_RE.findall((text or "").upper())))


def check_against_case(parsed: ParsedDenial, case: Case) -> CaseCheck:
    flags: List[CaseFlag] = []
    in_confirmed: Optional[bool] = None

    denied = _codes_in(parsed.denied_service_or_code)
    if not case.confirmed_codes:
        flags.append(CaseFlag(kind="no_confirmed_codes",
                              message="You haven't confirmed any billing codes in TrueCost, so the denied service can't be matched."))
    elif not denied:
        flags.append(CaseFlag(kind="code_not_stated",
                              message="The denial doesn't name a billing code, so we can't match it to your confirmed codes. "
                                      "Ask the plan which code it denied."))
    else:
        missing = [c for c in denied if c not in case.confirmed_codes]
        in_confirmed = not missing
        if missing:
            flags.append(CaseFlag(kind="code_not_in_confirmed_codes",
                                  message=f"The denial names {', '.join(missing)}, which is not in the codes you confirmed. "
                                          "Check with your care team that the right code was billed."))

    plan_ok = _plan_consistent(parsed.plan_name, case, flags)
    return CaseCheck(denied_code_in_confirmed=in_confirmed, plan_consistent_with_case=plan_ok, flags=flags)


def _plan_consistent(plan_name: Optional[str], case: Case, flags: List[CaseFlag]) -> Optional[bool]:
    if not plan_name:
        flags.append(CaseFlag(kind="plan_not_stated", message="The denial doesn't name a plan; confirm it is the plan you entered."))
        return None
    if case.insurer is None and not case.plan:
        return None
    name = _norm(plan_name)
    problems: List[str] = []
    if case.insurer is not None and not any(a in name for a in INSURER_ALIASES[case.insurer.value]):
        problems.append(f"insurer ({case.insurer.value})")
    if case.plan and case.plan.upper() in {"HMO", "PPO", "POS"}:
        letter_types: Set[str] = classify(plan_name) - {"Other"}
        if letter_types and case.plan.upper() not in letter_types:
            problems.append(f"plan type ({case.plan})")
    if problems:
        flags.append(CaseFlag(kind="plan_mismatch",
                              message=f"The plan on the denial ('{plan_name}') doesn't seem to match your {' and '.join(problems)}. "
                                      "Check you have the right letter and plan."))
        return False
    return True


# ------------------------------------------------------------------------------ draft_letter
P_NAME, P_MEMBER, P_DATE = "[PATIENT NAME]", "[MEMBER ID]", "[TODAY'S DATE]"
P_CLAIM, P_DENIAL_DATE, P_SERVICE, P_PLAN = "[CLAIM NUMBER]", "[DENIAL DATE]", "[DENIED SERVICE]", "[PLAN NAME]"
P_CLINICIAN = "[CLINICIAN'S STATEMENT - to be written by your care team]"
ALL_PLACEHOLDERS = [P_NAME, P_MEMBER, P_DATE, P_CLAIM, P_DENIAL_DATE, P_SERVICE, P_PLAN, P_CLINICIAN]

_APPEAL = """{date}

To: {plan} Appeals Department

Re: Request to reconsider a denied service
Member: {name}    Member ID: {member}
Claim / reference number: {claim}
Date of denial: {denial_date}
Service denied: {service}

To whom it may concern,

I am writing to ask for a review of the decision described above.{reason_line}

I am asking the plan to reconsider this decision. A statement from my clinician about this service is below.

{clinician}

{documents_line}Please send me the written result of this review, and tell me if you need anything else from me to complete it.

Sincerely,
{name}

(Draft for review with my care team or the hospital billing office before sending.)
"""

_DOC_REQUEST = """{date}

To: {plan} Member Services

Re: Request for information about a denied service
Member: {name}    Member ID: {member}
Claim / reference number: {claim}
Date of denial: {denial_date}
Service denied: {service}

To whom it may concern,

I received the notice described above.{reason_line} Please send me:
- the specific reason for the denial, in writing,
- the plan documents or clinical criteria used to make the decision,
- the deadline and the steps for asking for a review.

{clinician}

Sincerely,
{name}

(Draft for review with my care team or the hospital billing office before sending.)
"""


def draft_letter(letter_type: str, facts: Dict[str, Optional[str]]) -> LetterDraft:
    """Fill the template from facts only. Recognised keys: patient_name, member_id, date, claim_number,
    denial_date, denied_service, plan_name, denial_reason, documents_attached. Missing -> [PLACEHOLDER]."""
    if letter_type not in ("internal_appeal", "documentation_request"):
        raise ValueError(f"Unknown letter type: {letter_type}")

    def f(key: str, placeholder: str) -> str:
        v = (facts.get(key) or "").strip()
        return v if v else placeholder

    reason = (facts.get("denial_reason") or "").strip()
    docs = (facts.get("documents_attached") or "").strip()
    values = {
        "date": f("date", P_DATE), "plan": f("plan_name", P_PLAN), "name": f("patient_name", P_NAME),
        "member": f("member_id", P_MEMBER), "claim": f("claim_number", P_CLAIM),
        "denial_date": f("denial_date", P_DENIAL_DATE), "service": f("denied_service", P_SERVICE),
        "reason_line": f' The notice gave this reason: "{reason}".' if reason else "",
        "clinician": P_CLINICIAN,
        "documents_line": f"I am including: {docs}.\n\n" if docs else "",
    }
    template = _APPEAL if letter_type == "internal_appeal" else _DOC_REQUEST
    # single pass: text inserted for one field is never re-scanned, so patient input can't act as a template
    body = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)
    return LetterDraft(letter_type=letter_type, body=body,
                       placeholders=[p for p in ALL_PLACEHOLDERS if p in body])
