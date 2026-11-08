"""AgentService: the use-case layer for the two agents (routes stay thin; the model is injected)."""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from ..agents.appeal_agent import run_appeal_agent
from ..agents.assistance_agent import run_assistance_agent
from ..agents.cached import CACHE_DIR, load_cached
from ..agents.llm import ChatModel
from ..agents.schemas import (AppealDraftRequest, AppealDraftResponse, AssistanceMatchRequest, AssistanceMatchResponse)
from ..exceptions import DomainError


log = logging.getLogger("truecost.agents")
STREAM_TIMEOUT_S = 150
KEEPALIVE_S = 8        # send a keep-alive this often while waiting, so proxies (e.g. a tunnel's ~100 s limit) keep the stream open


class AgentUnavailable(DomainError):
    status_code = 503

    def __init__(self):
        super().__init__("The AI assistant isn't configured on this server. Turn on the demo mode to see a pre-recorded example.")


class AgentService:
    def __init__(self, model: Optional[ChatModel], fpl_path: Optional[Path] = None,
                 programs_path: Optional[Path] = None, cache_dir: Path = CACHE_DIR, replay_pause: float = 0.5):
        self._model, self._fpl, self._programs, self._cache = model, fpl_path, programs_path, cache_dir
        self._pause = replay_pause          # seconds between steps when replaying a pre-recorded run

    def match(self, req: AssistanceMatchRequest) -> AssistanceMatchResponse:
        if req.demo_mode:
            return load_cached("sarah_assistance", AssistanceMatchResponse, self._cache)
        if self._model is None:
            raise AgentUnavailable()
        return run_assistance_agent(self._model, req.case, req.hospitals, self._fpl, self._programs)

    def match_stream(self, req: AssistanceMatchRequest) -> Iterator[Dict[str, Any]]:
        """The same agent run as match(), as a stream of events so a UI can show the ReAct trace live:
        start, then step_start / step_done for each Thought -> Action -> Observation, then final (the full response).
        Failures become an `error` event, because a stream's HTTP status is already sent when work begins."""
        if req.demo_mode:
            try:
                resp = load_cached("sarah_assistance", AssistanceMatchResponse, self._cache)
            except DomainError as e:
                yield {"type": "error", "detail": str(e)}
                return
            yield {"type": "start", "mode": "cached"}
            for st in resp.explanation.react:           # pre-recorded: replay with short pauses so it reads like a live run
                yield {"type": "step_start", "step": {**st.model_dump(mode="json"), "observation": ""}}
                time.sleep(self._pause)
                yield {"type": "step_done", "n": st.n, "observation": st.observation, "ok": st.ok}
                time.sleep(self._pause * 0.6)
            yield {"type": "final", "response": resp.model_dump(mode="json")}
            return
        if self._model is None:
            yield {"type": "error", "detail": str(AgentUnavailable())}
            return

        q: "queue.Queue[Any]" = queue.Queue()
        done = object()

        def work() -> None:
            try:
                resp = run_assistance_agent(self._model, req.case, req.hospitals, self._fpl, self._programs, on_event=q.put)
                q.put({"type": "final", "response": resp.model_dump(mode="json")})
            except Exception:  # noqa: BLE001 - never leak provider errors to the client
                log.exception("assistance stream failed")
                q.put({"type": "error", "detail": "The assistant ran into a problem. Please try again."})
            finally:
                q.put(done)

        threading.Thread(target=work, name="assistance-stream", daemon=True).start()
        yield {"type": "start", "mode": "live"}
        started = time.monotonic()
        while True:
            try:
                item = q.get(timeout=KEEPALIVE_S)
            except queue.Empty:
                if time.monotonic() - started > STREAM_TIMEOUT_S:
                    yield {"type": "error", "detail": "The assistant took too long. Please try again."}
                    return
                yield {"type": "_keepalive"}
                continue
            if item is done:
                return
            yield item

    def draft(self, req: AppealDraftRequest) -> AppealDraftResponse:
        if req.demo_mode:
            return load_cached("sarah_appeal", AppealDraftResponse, self._cache)
        if self._model is None:
            raise AgentUnavailable()
        return run_appeal_agent(self._model, req.case, req.denial_text, req.letter_type, req.patient_facts)
