"""API contracts (Pydantic). These are what the frontend and AI teammates code against."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from .domain.models import Payer


class InsuranceIn(BaseModel):
    insurer: Payer
    plan: str = Field(..., min_length=1, description="Plan type: HMO, PPO, POS, Other, Any, or a combination like 'HMO/PPO' (see GET /api/v1/plans)")
    deductible_remaining: float = Field(..., ge=0)
    coinsurance_pct: float = Field(..., ge=0, le=100, description="Patient share after deductible, 0-100")
    oop_max_remaining: float = Field(..., ge=0)


class EstimateRequest(BaseModel):
    codes: List[str] = Field(..., min_length=1, max_length=20,
                             description="Codes in the order services happen (order uses up the deductible)")
    insurance: InsuranceIn
    patient_zip: Optional[str] = Field(None, max_length=10)
    include_estimates: bool = Field(True, description="When a hospital publishes no rate for the chosen insurer, "
                                    "fill the gap with a labelled estimate from the other insurers' published rates at that hospital")

    @field_validator("codes")
    @classmethod
    def _clean(cls, v: List[str]) -> List[str]:
        out = list(dict.fromkeys(c.strip() for c in v if c.strip()))
        if not out:
            raise ValueError("At least one code is required.")
        return out


class LineOut(BaseModel):
    code: str
    description: Optional[str] = None
    rate: float
    patient_pays: float
    rule: str
    price_source: str = Field("published", description="'published' (hospital's own rate for this insurer) or 'estimated'")
    estimate_note: Optional[str] = Field(None, description="Why and how the estimate was made (only when price_source='estimated')")


class UnavailableOut(BaseModel):
    code: str
    reason: str


class HospitalResult(BaseModel):
    hospital: str
    address: Optional[str]
    lat: Optional[float]
    lng: Optional[float]
    complete: bool = Field(..., description="True if every outpatient code was priced. Only complete hospitals are comparable.")
    total_allowed: float
    patient_oop: float
    has_estimates: bool = Field(False, description="True if at least one line is an estimate, not a published rate. Show a badge.")
    savings_vs_highest: Optional[float] = Field(None, description="Null for incomplete hospitals")
    drive_minutes: Optional[int]
    lines: List[LineOut]
    unavailable: List[UnavailableOut]


class EstimateResponse(BaseModel):
    results: List[HospitalResult]
    caveats: List[str]


# ---- simulate ---------------------------------------------------------------------------------
class PathwayStep(BaseModel):
    code: str
    month: int = Field(..., ge=1, le=36)
    step: Optional[str] = Field(None, description="Label; defaults to the code description")
    repeat: int = Field(1, ge=1, le=12, description="Occurrences, one per consecutive month (e.g. 6 chemo cycles)")


class SimulateRequest(EstimateRequest):
    hospital: Optional[str] = Field(None, description="Defaults to the cheapest comparable hospital")
    pathway: Optional[List[PathwayStep]] = Field(None, description="Ordered care journey. Defaults to codes in order.")


class TimelineEntry(BaseModel):
    month: int
    step: str
    code: str
    cost: float
    cumulative_oop: float


class SimulateResponse(BaseModel):
    hospital: str
    timeline: List[TimelineEntry]
    oop_max_reached_month: Optional[int]
    unavailable: List[UnavailableOut]
    caveats: List[str]


# ---- reference + extract ----------------------------------------------------------------------
class CodeOut(BaseModel):
    code: str
    code_type: str
    description: str
    phase: str


class HospitalOut(BaseModel):
    hospital: str
    address: Optional[str]
    lat: Optional[float]
    lng: Optional[float]


class ExtractRequest(BaseModel):
    note: str = Field(..., min_length=3, max_length=20000)


class ExtractedCodeOut(BaseModel):
    code: str
    type: str
    description: str
    confidence: float
    evidence: str


class ExtractResponse(BaseModel):
    codes: List[ExtractedCodeOut]
    extractor: str = Field(..., description="Extractor name incl. exact model string")


class ExtractFileResponse(ExtractResponse):
    note_text: str = Field(..., description="The text read from the file. Show it so the patient can check it (OCR can misread).")
    read_method: str = Field(..., description="text | docx | pdf-text | gemini-ocr")
    file_type: str = Field(..., description="pdf | png | jpeg | webp | docx | txt")
    truncated: bool = Field(False, description="True if the note was longer than 20,000 characters and was cut")
