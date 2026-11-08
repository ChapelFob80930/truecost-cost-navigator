"""Best-effort PHI scrubber. Text goes to the LLM only after scrub(); tokens are swapped back LOCALLY by restore().

LIMITS (say this out loud to judges): this is pattern-based. It catches labelled identifiers (member ID, MRN, DOB,
claim number, "Patient: Jane Doe"), phones, emails, SSNs and street addresses. It cannot catch an unlabelled name in
free prose. The demo uses made-up patients only; a production system would need a real de-identification service.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

_DATE = r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})"
_STOP_NAMES = {"member", "patient", "sir", "madam", "customer", "subscriber", "insured", "team", "department"}

# (kind, compiled regex, group index holding the sensitive value)
_PATTERNS: List[Tuple[str, "re.Pattern[str]", int]] = [
    ("SSN", re.compile(r"\b(\d{3}-\d{2}-\d{4})\b"), 1),
    ("EMAIL", re.compile(r"\b([\w.+-]+@[\w-]+\.[\w.-]+)\b"), 1),
    ("PHONE", re.compile(r"(\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b)"), 1),
    ("DOB", re.compile(rf"(?i)\b(?:DOB|date of birth|born(?: on)?)\b\s*[:\-]?\s*({_DATE})"), 1),
    ("MRN", re.compile(r"(?i)\b(?:MRN|medical record(?: number| no\.?)?)\b\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,})"), 1),
    ("MEMBER_ID", re.compile(r"(?i)\b(?:member|subscriber|policy|group)\s*(?:id|number|no\.?|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{4,})"), 1),
    ("CLAIM", re.compile(r"(?i)\b(?:claim|reference|ref)\s*(?:number|no\.?|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,})"), 1),
    ("ADDRESS", re.compile(r"(\b\d{1,5}\s+[A-Za-z0-9.' ]{2,40}?\s(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Way|Court|Ct)\b\.?"
                           r"(?:,?\s+[A-Za-z .]+,?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?)?)"), 1),
    ("NAME", re.compile(r"\b(?:Patient|Member|Name|Insured|Subscriber|Dear|Mr\.|Mrs\.|Ms\.|Mx\.)\s*:?\s+"
                        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"), 1),
]


@dataclass
class ScrubResult:
    text: str
    mapping: Dict[str, str] = field(default_factory=dict)   # token -> original value (kept only in memory)

    @property
    def found(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for tok in self.mapping:
            kind = tok.strip("[]").rsplit("_", 1)[0]
            counts[kind] = counts.get(kind, 0) + 1
        return counts


def scrub(text: str) -> ScrubResult:
    mapping: Dict[str, str] = {}
    reverse: Dict[str, str] = {}
    counters: Dict[str, int] = {}

    def token_for(kind: str, value: str) -> str:
        key = f"{kind}|{value}"
        if key not in reverse:
            counters[kind] = counters.get(kind, 0) + 1
            tok = f"[{kind}_{counters[kind]}]"
            reverse[key], mapping[tok] = tok, value
        return reverse[key]

    for kind, pattern, grp in _PATTERNS:
        def repl(m: "re.Match[str]", kind=kind, grp=grp) -> str:
            value = m.group(grp)
            if kind == "NAME" and value.split()[0].lower() in _STOP_NAMES:
                return m.group(0)
            if value.startswith("[") and value.endswith("]"):          # already a token
                return m.group(0)
            start, end = m.span(grp)
            whole = m.group(0)
            return whole[: start - m.start()] + token_for(kind, value) + whole[end - m.start():]
        text = pattern.sub(repl, text)
    return ScrubResult(text=text, mapping=mapping)


def restore(text: str, mapping: Dict[str, str]) -> str:
    """Swap tokens back to the original values. Longest tokens first so [NAME_10] never matches [NAME_1]."""
    for tok in sorted(mapping, key=len, reverse=True):
        text = text.replace(tok, mapping[tok])
    return text
