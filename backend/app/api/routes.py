"""Thin HTTP layer: validate input, call a service, shape the output. No business logic here.

Contract routes live at the root (/extract, /estimate, /simulate). Reference lookups stay under /api/v1.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Request

from ..container import Container
from ..domain.models import Payer
from ..domain.plan_types import available_types
from ..agents.schemas import (AppealDraftRequest, AppealDraftResponse, AssistanceMatchRequest,
                              AssistanceMatchResponse)
from ..schemas import (CodeOut, EstimateRequest, EstimateResponse, ExtractedCodeOut, ExtractRequest,
                       ExtractResponse, HospitalOut, SimulateRequest, SimulateResponse)

router = APIRouter()
ref = APIRouter(prefix="/api/v1")


def get_container(request: Request) -> Container:
    return request.app.state.container


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
def extract_codes(body: ExtractRequest, c: Container = Depends(get_container)):
    """Suggest billing codes from a doctor's note. The patient must confirm them."""
    found = c.extractor.extract(body.note)
    return ExtractResponse(
        extractor=c.extractor.name,
        codes=[ExtractedCodeOut(code=x.code, type=x.code_type, description=x.description,
                                confidence=x.confidence, evidence=x.evidence) for x in found])


@router.post("/estimate", response_model=EstimateResponse, tags=["estimate"])
def estimate(body: EstimateRequest, c: Container = Depends(get_container)):
    """Rank hospitals by the patient's estimated out-of-pocket cost."""
    return c.estimator.estimate(body)


@router.post("/simulate", response_model=SimulateResponse, tags=["estimate"])
def simulate(body: SimulateRequest, c: Container = Depends(get_container)):
    """Month-by-month out-of-pocket cost across the care journey at one hospital."""
    return c.estimator.simulate(body)


@ref.post("/assistance/match", response_model=AssistanceMatchResponse, tags=["agents"])
def assistance_match(body: AssistanceMatchRequest, c: Container = Depends(get_container)):
    """Financial Assistance Matcher: programs the patient MAY want to ask about (never an eligibility verdict)."""
    return c.agents.match(body)


@ref.post("/appeal/draft", response_model=AppealDraftResponse, tags=["agents"])
def appeal_draft(body: AppealDraftRequest, c: Container = Depends(get_container)):
    """Appeal Letter Drafter: a DRAFT letter with [PLACEHOLDER] fields, for review with the care team."""
    return c.agents.draft(body)
