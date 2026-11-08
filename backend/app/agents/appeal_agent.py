"""Agent 2: Appeal / Denial Letter Drafter.

Fixed, auditable pipeline (3 logged tool calls): parse_denial -> check_against_case -> draft_letter, then one LLM call
for plain-English wording. The pasted letter is PHI-scrubbed before any LLM sees it; tokens are restored only locally.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from ..domain.case import Case
from .denial import check_against_case, draft_letter, parse_denial
from .explain import appeal_explanation
from .guards import safe_text
from .llm import ChatModel
from .loop import to_jsonable
from .phi import restore, scrub
from .prompts import APPEAL_NARRATIVE_PROMPT, APPEAL_SYSTEM_PROMPT, DENIAL_PARSE_PROMPT
from .schemas import AgentMeta, AppealDraftResponse, ParsedDenial, ToolCallRecord

_S = {"type": "STRING", "nullable": True}
DENIAL_SCHEMA = {"type": "OBJECT", "properties": {
    "denial_reason": _S, "denied_service_or_code": _S, "date": _S, "claim_or_reference_number": _S,
    "appeal_deadline_if_stated": _S, "plan_name": _S,
    "missing_documentation_mentioned": {"type": "ARRAY", "items": {"type": "STRING"}, "nullable": True}}}
NARRATIVE_SCHEMA = {"type": "OBJECT", "properties": {
    "summary_plain": {"type": "STRING"},
    "documentation_checklist": {"type": "ARRAY", "items": {"type": "STRING"}},
    "next_steps": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["summary_plain", "documentation_checklist", "next_steps"]}

GENERIC_CHECKLIST = [
    "A written statement from your clinician about why the service is needed",
    "The reports for the denied service (for example the imaging or pathology report)",
    "A copy of the denial letter and any claim or reference number",
]


def _step(trail: List[ToolCallRecord], tool: str, inputs: Dict[str, Any], fn: Callable[[], Any], source: Optional[str] = None):
    t0 = time.perf_counter()
    try:
        out = fn()
        trail.append(ToolCallRecord(step=len(trail) + 1, tool=tool, inputs=inputs, output=to_jsonable(out), source=source,
                                    duration_ms=int((time.perf_counter() - t0) * 1000)))
        return out
    except Exception as e:  # noqa: BLE001
        trail.append(ToolCallRecord(step=len(trail) + 1, tool=tool, inputs=inputs, ok=False,
                                    error=f"{type(e).__name__}: {e}", duration_ms=int((time.perf_counter() - t0) * 1000)))
        return None


def _fallback_summary(p: ParsedDenial) -> str:
    bits = []
    if p.denied_service_or_code:
        bits.append(f"Your plan denied: {p.denied_service_or_code}.")
    bits.append(f"The letter gives this reason: {p.denial_reason}." if p.denial_reason else "The letter does not state a clear reason.")
    bits.append("This is a draft to review with your care team or the hospital billing office. We can't say whether the denial is right.")
    return " ".join(bits)


def _fallback_steps(p: ParsedDenial) -> List[str]:
    deadline = (f"The letter says: \"{p.appeal_deadline_if_stated}\". Confirm this deadline with your plan."
                if p.appeal_deadline_if_stated else "The letter does not state an appeal deadline. Confirm the deadline with your plan.")
    return [deadline, "Confirm with your plan where and how to send an appeal.",
            "Review this draft with your care team or the hospital billing office before sending.",
            "Talk to a patient navigator if you want help."]


def run_appeal_agent(model: ChatModel, case: Case, denial_text: Optional[str], letter_type: str = "internal_appeal",
                     patient_facts: Optional[Dict[str, str]] = None) -> AppealDraftResponse:
    text = denial_text or case.denial_text or ""
    patient_facts = patient_facts or {}
    trail: List[ToolCallRecord] = []
    error: Optional[str] = None

    sc = scrub(text)
    llm_extract = lambda system, t: model.final_json(DENIAL_PARSE_PROMPT, t, DENIAL_SCHEMA)  # noqa: E731
    scrubbed_parsed = _step(trail, "parse_denial", {"text": f"<{len(sc.text)} chars, PHI scrubbed: {sc.found}>"},
                            lambda: parse_denial(sc.text, llm_extract, DENIAL_PARSE_PROMPT))
    parse_ok = scrubbed_parsed is not None
    if scrubbed_parsed is None:
        error = "We couldn't read the letter automatically. The draft below has blanks for you to fill in."
        scrubbed_parsed = ParsedDenial()
    parsed = ParsedDenial(**{k: (restore(v, sc.mapping) if isinstance(v, str) else
                                 [restore(i, sc.mapping) for i in v] if isinstance(v, list) else v)
                             for k, v in scrubbed_parsed.model_dump().items()})

    check = _step(trail, "check_against_case", {"parsed": parsed.model_dump(), "case": case.llm_view()},
                  lambda: check_against_case(parsed, case))
    if check is None:
        from .schemas import CaseCheck
        check = CaseCheck()

    facts = {"claim_number": parsed.claim_or_reference_number, "denial_date": parsed.date,
             "denied_service": parsed.denied_service_or_code, "plan_name": parsed.plan_name,
             "denial_reason": parsed.denial_reason, **{k: v for k, v in patient_facts.items() if v}}
    letter = _step(trail, "draft_letter", {"letter_type": letter_type, "fact_keys": sorted(k for k, v in facts.items() if v)},
                   lambda: draft_letter(letter_type, facts))
    if letter is None:
        letter = draft_letter(letter_type, {})

    # wording from the LLM, using the SCRUBBED parse only; any guard failure -> standard wording
    summary, checklist, steps = _fallback_summary(parsed), GENERIC_CHECKLIST + list(parsed.missing_documentation_mentioned or []), _fallback_steps(parsed)
    replaced: Optional[str] = None
    ai_wording_used = False
    try:
        blob = json.dumps(scrubbed_parsed.model_dump(), ensure_ascii=False)
        raw = model.final_json(APPEAL_SYSTEM_PROMPT, APPEAL_NARRATIVE_PROMPT + blob, NARRATIVE_SCHEMA)
        s, r1 = safe_text(raw.get("summary_plain"), [blob], summary)
        items = [str(i) for i in raw.get("documentation_checklist", [])][:8]
        steps_raw = [str(i) for i in raw.get("next_steps", [])][:8]
        joined_ok = all(safe_text(i, [blob], "", max_len=300)[1] is None for i in items + steps_raw) and items and steps_raw
        if joined_ok:
            checklist, steps = items, steps_raw
        replaced = r1 or (None if joined_ok else "checklist or steps failed a guard")
        summary = s
        ai_wording_used = True
    except Exception as e:  # noqa: BLE001
        error = error or f"The AI wording failed ({type(e).__name__}); showing standard wording."

    summary = restore(summary, sc.mapping)
    checklist = [restore(i, sc.mapping) for i in checklist]
    steps = [restore(i, sc.mapping) for i in steps]
    meta = AgentMeta(mode="live", model=model.model_name, tool_calls_used=len(trail), trail=trail, error=error)
    if replaced:
        meta.disclaimers = meta.disclaimers + [f"Some AI wording was replaced with standard wording ({replaced})."]
    explanation = appeal_explanation(sc.found, parsed, parse_ok, check, letter, ai_wording_used, replaced)
    return AppealDraftResponse(denial_summary_plain=summary, parsed=parsed, case_check=check,
                               documentation_checklist=checklist, letter=letter, next_steps=steps,
                               explanation=explanation, meta=meta)
