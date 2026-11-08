"""Code extraction from a doctor's note.

CodeExtractor is the interface. KeywordCodeExtractor is a deterministic fallback that also doubles
as the demo safety net if the live LLM call fails. The AI teammate adds an LLM-backed subclass
(e.g. ClaudeCodeExtractor) and registers it in container.py - no other file changes.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..repositories.base import PriceRepository


@dataclass(frozen=True)
class ExtractedCode:
    code: str
    code_type: str
    description: str
    evidence: str
    confidence: float


class CodeExtractor(ABC):
    name = "base"

    @abstractmethod
    def extract(self, note: str) -> List[ExtractedCode]: ...


class KeywordCodeExtractor(CodeExtractor):
    name = "keyword-fallback"

    # (code, regex) - first match wins per code; patterns are intentionally conservative.
    RULES: List[Tuple[str, str]] = [
        ("77066", r"(bilateral|both breasts?)[^.]{0,40}diagnostic mammo|diagnostic mammo[^.]{0,40}(bilateral|both)"),
        ("77065", r"(unilateral|left|right)[^.]{0,40}diagnostic mammo|diagnostic mammo[^.]{0,40}(unilateral|left|right)"),
        ("77067", r"screening mammo"),
        ("76641", r"complete breast (ultrasound|sonogram)|breast (ultrasound|sonogram)[^.]{0,30}complete"),
        ("76642", r"limited breast (ultrasound|sonogram)|(targeted|focused) breast (ultrasound|sonogram)|breast (ultrasound|sonogram)"),
        ("77049", r"(bilateral|both breasts?)[^.]{0,30}breast mri|breast mri[^.]{0,30}(bilateral|both)"),
        ("77048", r"breast mri|mri of the breast"),
        ("19081", r"stereotactic[^.]{0,40}biops|biops[^.]{0,40}stereotactic"),
        ("19083", r"ultrasound[- ]guided[^.]{0,40}biops|biops[^.]{0,40}ultrasound[- ]guid"),
        ("19085", r"mri[- ]guided[^.]{0,40}biops|biops[^.]{0,40}mri[- ]guid"),
        ("88305", r"patholog"),
        ("19301", r"lumpectomy|partial mastectomy"),
        ("19303", r"(simple |total )?mastectomy"),
        ("38525", r"(axillary|sentinel)?\s*lymph node (biopsy|excision|dissection)"),
        ("96413", r"chemotherapy|chemo infusion"),
        ("77385", r"radiation therapy|\bimrt\b|radiotherapy"),
    ]

    def __init__(self, repo: PriceRepository):
        self._repo = repo

    def extract(self, note: str) -> List[ExtractedCode]:
        text = note.lower()
        found: List[Tuple[str, str]] = []
        for code, pattern in self.RULES:
            m = re.search(pattern, text)
            if m:
                found.append((code, note[max(0, m.start() - 20): m.end() + 20].strip()))
        # 77066 vs 77065 / 77048 vs 77049 / 19303 vs 19301: keep the more specific code only
        codes = {c for c, _ in found}
        drop = set()
        if "77066" in codes:
            drop.add("77065")
        if "77049" in codes:
            drop.add("77048")
        if "19301" in codes:
            drop.add("19303")
        if "76641" in codes:
            drop.add("76642")
        known = {c.code: c for c in self._repo.get_codes([c for c, _ in found])}
        return [ExtractedCode(code=c, code_type=known[c].code_type, description=known[c].description,
                              evidence=ev, confidence=0.6)
                for c, ev in found if c not in drop and c in known]
