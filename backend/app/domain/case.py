"""The shared, SESSION-ONLY case object both agents read from.

Design rules
- Held in memory / the client's session only. Never written to disk or the database.
- It deliberately has NO name, date of birth, MRN, address or member-ID fields.
- `denial_text` is the one free-text field and may contain identifiers, so it is excluded from `llm_view()`
  (the PHI scrubber, added later, is the only thing allowed to turn it into LLM input).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import Payer


class CaseEstimate(BaseModel):
    """One hospital's result, copied from /estimate. Money values come from code, never from an LLM."""
    hospital: str
    patient_oop: float = Field(..., ge=0)
    total_allowed: float = Field(..., ge=0)
    complete: bool


class Case(BaseModel):
    confirmed_codes: List[str] = Field(default_factory=list, max_length=20,
                                       description="Codes the patient confirmed in the UI")
    # insurance (all optional: an uninsured patient has none)
    uninsured: bool = False
    insurer: Optional[Payer] = None
    plan: Optional[str] = Field(None, description="Plan type: HMO, PPO, POS, Other or Any")
    deductible_remaining: Optional[float] = Field(None, ge=0)
    coinsurance_pct: Optional[float] = Field(None, ge=0, le=100)
    oop_max_remaining: Optional[float] = Field(None, ge=0)
    estimates: List[CaseEstimate] = Field(default_factory=list)
    # financial-assistance inputs (user-entered, optional)
    household_size: Optional[int] = Field(None, ge=1, le=20)
    annual_income: Optional[float] = Field(None, ge=0, le=10_000_000, description="Household income, USD per year")
    # denial letter text the user pasted (may contain identifiers - never sent to an LLM unscrubbed)
    denial_text: Optional[str] = Field(None, max_length=20000)

    @field_validator("confirmed_codes")
    @classmethod
    def _dedupe(cls, v: List[str]) -> List[str]:
        return list(dict.fromkeys(c.strip().upper() for c in v if c.strip()))

    @model_validator(mode="after")
    def _uninsured_has_no_insurance(self):
        if self.uninsured:
            self.insurer = self.plan = None
            self.deductible_remaining = self.coinsurance_pct = self.oop_max_remaining = None
        return self

    @property
    def has_income_info(self) -> bool:
        return self.household_size is not None and self.annual_income is not None

    def llm_view(self) -> Dict[str, Any]:
        """Everything an LLM may see without scrubbing. Excludes denial_text."""
        return self.model_dump(mode="json", exclude={"denial_text"})
