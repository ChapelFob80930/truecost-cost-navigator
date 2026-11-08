"""Output guards: deterministic checks on anything the LLM wrote. A failed check swaps in a code-written fallback."""
from __future__ import annotations

import re
from typing import Iterable, Optional, Set

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_VERDICT = re.compile(
    r"(?i)\byou (?:do )?qualify\b|\byou(?:'re| are) (?:eligible|approved|covered)\b|\byou will (?:qualify|be approved|get)\b|"
    r"\byou(?:'ll| will) be eligible\b|\bguarantee|\bdefinitely (?:qualify|eligible)")
_LEGAL_OR_PROMISE = re.compile(
    r"(?i)§|\bU\.?S\.?C\b|\bC\.?F\.?R\b|\bERISA\b|\bAffordable Care Act\b|\bACA\b|\bstatute|\bregulation|\bby law\b|"
    r"\bfederal law\b|\bstate law\b|\byour rights under\b|\bwill (?:be )?(?:approved|overturned|reversed|win)\b|\bguarantee")
_CLINICAL = re.compile(r"(?i)\byou (?:should|need to|must) (?:get|have|start|take|undergo|stop)\b|\bprognosis\b|\bsurvival\b|"
                       r"\byou (?:have|likely have) cancer\b")


def _norm_num(s: str) -> str:
    s = s.replace(",", "")
    return s.rstrip("0").rstrip(".") if "." in s else s


def numbers_in(text: str) -> Set[str]:
    return {_norm_num(n) for n in _NUM.findall(text or "")}


def numbers_grounded(text: str, source_blobs: Iterable[str], extra: Iterable[int] = ()) -> bool:
    """True if every number in `text` appears in the source material (or is in `extra`)."""
    allowed: Set[str] = {str(e) for e in extra}
    for blob in source_blobs:
        allowed |= numbers_in(blob)
    return numbers_in(text) <= allowed


def has_verdict(text: str) -> bool:
    return bool(_VERDICT.search(text or ""))


def has_legal_or_promise(text: str) -> bool:
    return bool(_LEGAL_OR_PROMISE.search(text or ""))


def has_clinical_advice(text: str) -> bool:
    return bool(_CLINICAL.search(text or ""))


def safe_text(text: Optional[str], source_blobs: Iterable[str], fallback: str, *, max_len: int = 900,
              extra_numbers: Iterable[int] = ()) -> tuple[str, Optional[str]]:
    """Return (text_to_show, reason_if_replaced). Replaces the LLM text with `fallback` if any guard trips."""
    t = (text or "").strip()
    blobs = list(source_blobs)
    if not t:
        return fallback, "empty"
    if len(t) > max_len:
        return fallback, "too long"
    if has_verdict(t):
        return fallback, "eligibility verdict"
    if has_legal_or_promise(t):
        return fallback, "legal citation or promise"
    if has_clinical_advice(t):
        return fallback, "clinical advice"
    if not numbers_grounded(t, blobs, extra_numbers):
        return fallback, "number not in source"
    return t, None
