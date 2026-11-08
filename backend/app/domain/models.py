"""Domain entities. Plain, immutable, framework-free (no FastAPI / SQLite imports here)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

from ..exceptions import InvalidInsuranceError


class Payer(str, Enum):
    INDEPENDENT_HEALTH = "Independent Health"
    HIGHMARK_BCBS = "Highmark BCBS"
    UNIVERA = "Univera"


@dataclass(frozen=True)
class Hospital:
    name: str
    address: Optional[str]
    lat: Optional[float]
    lng: Optional[float]


@dataclass(frozen=True)
class MedicalCode:
    code: str
    code_type: str  # CPT | MS-DRG | ICD10
    description: str
    phase: str  # diagnostic | treatment | diagnosis


@dataclass(frozen=True)
class PriceRecord:
    hospital: str
    code: str
    code_type: str
    description: Optional[str]
    setting: str  # outpatient | inpatient
    payer: Optional[str]
    plan: Optional[str]
    rate_type: Optional[str]  # negotiated_dollar | percent_of_charges | None
    negotiated_rate: Optional[Decimal]
    cash_price: Optional[Decimal]
    gross_charge: Optional[Decimal]
    notes: Optional[str]

    @property
    def is_dollar_rate(self) -> bool:
        return self.rate_type == "negotiated_dollar" and self.negotiated_rate is not None


@dataclass(frozen=True)
class InsuranceProfile:
    """What the patient still owes before insurance pays 100% (all amounts in USD)."""

    payer: Payer
    deductible_remaining: Decimal
    coinsurance_pct: Decimal  # patient's share after the deductible, 0-100
    oop_max_remaining: Decimal
    plan: Optional[str] = None

    def __post_init__(self) -> None:
        if self.deductible_remaining < 0 or self.oop_max_remaining < 0:
            raise InvalidInsuranceError("Deductible and out-of-pocket max cannot be negative.")
        if not (0 <= self.coinsurance_pct <= 100):
            raise InvalidInsuranceError("Coinsurance must be between 0 and 100 percent.")


@dataclass(frozen=True)
class LineCost:
    """How one service's allowed amount splits between patient and insurer."""

    allowed: Decimal
    deductible_paid: Decimal
    coinsurance_paid: Decimal
    patient_pays: Decimal
    insurer_pays: Decimal
    hit_oop_max: bool = False


@dataclass(frozen=True)
class SelectedRate:
    """The single rate chosen for (hospital, code, payer) plus the spread it was chosen from."""

    record: PriceRecord
    rate: Decimal
    low: Decimal
    high: Decimal
    candidates: int
    plan_matched: bool
