"""AgentService: the use-case layer for the two agents (routes stay thin; the model is injected)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..agents.appeal_agent import run_appeal_agent
from ..agents.assistance_agent import run_assistance_agent
from ..agents.cached import CACHE_DIR, load_cached
from ..agents.llm import ChatModel
from ..agents.schemas import (AppealDraftRequest, AppealDraftResponse, AssistanceMatchRequest, AssistanceMatchResponse)
from ..exceptions import DomainError


class AgentUnavailable(DomainError):
    status_code = 503

    def __init__(self):
        super().__init__("The AI assistant isn't configured on this server. Turn on the demo mode to see a pre-recorded example.")


class AgentService:
    def __init__(self, model: Optional[ChatModel], fpl_path: Optional[Path] = None,
                 programs_path: Optional[Path] = None, cache_dir: Path = CACHE_DIR):
        self._model, self._fpl, self._programs, self._cache = model, fpl_path, programs_path, cache_dir

    def match(self, req: AssistanceMatchRequest) -> AssistanceMatchResponse:
        if req.demo_mode:
            return load_cached("sarah_assistance", AssistanceMatchResponse, self._cache)
        if self._model is None:
            raise AgentUnavailable()
        return run_assistance_agent(self._model, req.case, req.hospitals, self._fpl, self._programs)

    def draft(self, req: AppealDraftRequest) -> AppealDraftResponse:
        if req.demo_mode:
            return load_cached("sarah_appeal", AppealDraftResponse, self._cache)
        if self._model is None:
            raise AgentUnavailable()
        return run_appeal_agent(self._model, req.case, req.denial_text, req.letter_type, req.patient_facts)
