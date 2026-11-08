"""Thin HTTP layer: validate input, call a service, shape the output. No business logic here.

Contract routes live at the root (/extract, /estimate, /simulate). Reference lookups stay under /api/v1.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from ..container import Container
from ..events import BUCKETS, Event, clean_meta
from ..exceptions import InvalidStatsQuery
from ..services.note_reader import MAX_BYTES
from ..domain.models import Payer
from ..domain.plan_types import available_types
from ..agents.schemas import (AppealDraftRequest, AppealDraftResponse, AssistanceMatchRequest,
                              AssistanceMatchResponse)
from ..schemas import (CodeOut, EstimateRequest, EstimateResponse, ExtractedCodeOut, ExtractFileResponse,
                       ExtractRequest, ExtractResponse, HospitalOut, SimulateRequest, SimulateResponse)

router = APIRouter()
ref = APIRouter(prefix="/api/v1")


def get_container(request: Request) -> Container:
    return request.app.state.container


def _meta(request: Request, **counts: Any) -> None:
    """Hand a few counts to the usage log (the middleware whitelists them). Never pass note text or personal data."""
    getattr(request.state, "meta", {}).update(counts)


@ref.get("/codes", response_model=List[CodeOut], tags=["reference"])
def list_codes(phase: Optional[str] = None, c: Container = Depends(get_container)):
    """Billing codes the app supports. phase: diagnostic | treatment | diagnosis."""
    return [CodeOut(**vars(x)) for x in c.repo.list_codes(phase)]


@ref.get("/hospitals", response_model=List[HospitalOut], tags=["reference"])
def list_hospitals(c: Container = Depends(get_container)):
    return [HospitalOut(hospital=h.name, address=h.address, lat=h.lat, lng=h.lng) for h in c.repo.list_hospitals()]


@ref.get("/plans", response_model=List[str], tags=["reference"])
def list_plans(insurer: Payer, c: Container = Depends(get_container)):
    """Plan types (HMO / PPO / POS / Other / Any) with published rates for this insurer, for a dropdown."""
    return available_types(c.repo.list_plans(insurer.value))


@router.post("/extract", response_model=ExtractResponse, tags=["ai"])
def extract_codes(body: ExtractRequest, request: Request, c: Container = Depends(get_container)):
    """Suggest billing codes from a doctor's note. The patient must confirm them."""
    found = c.extractor.extract(body.note)
    _meta(request, codes_found=len(found))
    return ExtractResponse(
        extractor=c.extractor.name,
        codes=[ExtractedCodeOut(code=x.code, type=x.code_type, description=x.description,
                                confidence=x.confidence, evidence=x.evidence) for x in found])


@router.post("/extract/file", response_model=ExtractFileResponse, tags=["ai"])
def extract_codes_from_file(request: Request,
                            file: UploadFile = File(..., description="PDF, PNG, JPEG, WEBP, DOCX or TXT, up to 10 MB"),
                            c: Container = Depends(get_container)):
    """Same as /extract, but the doctor's note is an uploaded file (typed, scanned or photographed).
    The text read from the file comes back too, so the patient can check it before confirming codes."""
    data = file.file.read(MAX_BYTES + 1)   # never read more than the cap
    note = c.reader.read(data)
    found = c.extractor.extract(note.text)
    _meta(request, codes_found=len(found), read_method=note.method, file_type=note.file_type)
    return ExtractFileResponse(
        extractor=c.extractor.name, note_text=note.text, read_method=note.method, file_type=note.file_type,
        truncated=note.truncated,
        codes=[ExtractedCodeOut(code=x.code, type=x.code_type, description=x.description,
                                confidence=x.confidence, evidence=x.evidence) for x in found])


@router.post("/estimate", response_model=EstimateResponse, tags=["estimate"])
def estimate(body: EstimateRequest, request: Request, c: Container = Depends(get_container)):
    """Rank hospitals by the patient's estimated out-of-pocket cost."""
    result = c.estimator.estimate(body)
    _meta(request, insurer=body.insurance.insurer.value, plan_type=body.insurance.plan, codes_requested=len(body.codes),
          hospitals_comparable=sum(1 for r in result.results if r.complete),
          hospitals_with_estimates=sum(1 for r in result.results if r.has_estimates))
    return result


@router.post("/simulate", response_model=SimulateResponse, tags=["estimate"])
def simulate(body: SimulateRequest, request: Request, c: Container = Depends(get_container)):
    """Month-by-month out-of-pocket cost across the care journey at one hospital."""
    result = c.estimator.simulate(body)
    _meta(request, insurer=body.insurance.insurer.value, plan_type=body.insurance.plan, codes_requested=len(body.codes))
    return result


@ref.post("/assistance/match", response_model=AssistanceMatchResponse, tags=["agents"])
def assistance_match(body: AssistanceMatchRequest, request: Request, c: Container = Depends(get_container)):
    """Financial Assistance Matcher: programs the patient MAY want to ask about (never an eligibility verdict)."""
    result = c.agents.match(body)
    _meta(request, ai_mode=result.meta.mode, tool_calls=result.meta.tool_calls_used, matches=len(result.matches))
    return result


@ref.post("/assistance/match/stream", tags=["agents"])
def assistance_match_stream(body: AssistanceMatchRequest, c: Container = Depends(get_container)):
    """Same as /assistance/match, but streamed (Server-Sent Events) so the page can show the agent's ReAct trace live.
    Each line is `data: {json}`. Event types: start, step_start, step_done, final (the full response), error."""
    def sse():
        t0, status, meta = time.perf_counter(), 200, {}
        try:
            for ev in c.agents.match_stream(body):
                if ev["type"] == "_keepalive":
                    yield ": keep-alive\n\n"     # an SSE comment: clients ignore it, proxies see traffic
                    continue
                if ev["type"] == "final":
                    r = ev["response"]
                    meta = {"ai_mode": r["meta"]["mode"], "tool_calls": r["meta"]["tool_calls_used"], "matches": len(r["matches"])}
                elif ev["type"] == "error":
                    status = 500
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        finally:
            try:
                c.events.record(Event(endpoint="assistance_stream", status=status,
                                      latency_ms=int((time.perf_counter() - t0) * 1000), meta=clean_meta(meta)))
            except Exception:  # noqa: BLE001 - logging must never break a stream
                pass
    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@ref.post("/appeal/draft", response_model=AppealDraftResponse, tags=["agents"])
def appeal_draft(body: AppealDraftRequest, request: Request, c: Container = Depends(get_container)):
    """Appeal Letter Drafter: a DRAFT letter with [PLACEHOLDER] fields, for review with the care team."""
    result = c.agents.draft(body)
    _meta(request, ai_mode=result.meta.mode, tool_calls=result.meta.tool_calls_used)
    return result


@ref.get("/stats", tags=["meta"])
def usage_stats(window_minutes: int = Query(60, ge=5, le=1440), bucket_minutes: int = Query(5),
                c: Container = Depends(get_container)) -> Dict[str, Any]:
    """Live usage as time-series data (counts, latency, errors per time bucket). From Tiger Data's hypertable when
    the app runs on Postgres. Holds no note text and no personal data."""
    if bucket_minutes not in BUCKETS or window_minutes // bucket_minutes > 300:
        raise InvalidStatsQuery(BUCKETS)
    return c.events.stats(window_minutes, bucket_minutes)
