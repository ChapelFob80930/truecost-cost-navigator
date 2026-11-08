"""The allow-listed tool surface for both agents. The loop (deliverable 4) may call ONLY these."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..domain.case import Case
from .assistance import lookup_assistance
from .denial import check_against_case, draft_letter, parse_denial
from .fpl import compute_fpl_percent


@dataclass
class ToolContext:
    case: Case
    fpl_path: Optional[Path] = None
    programs_path: Optional[Path] = None


def get_case(ctx: ToolContext) -> Dict[str, Any]:
    """The case as an LLM may see it: no denial text, no identifiers."""
    return ctx.case.llm_view()


__all__ = ["ToolContext", "get_case", "compute_fpl_percent", "lookup_assistance", "parse_denial",
           "check_against_case", "draft_letter"]
