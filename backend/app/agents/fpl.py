"""Federal Poverty Level calculator. Pure code; the guideline amounts live in a data file, never in this module."""
from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

from ..config import DATA_DIR
from ..exceptions import DomainError
from .schemas import FplResult

DEFAULT_FPL_PATH = DATA_DIR / "fpl_guidelines.json"
MAX_HOUSEHOLD = 20


class FplError(DomainError):
    status_code = 422


class FplDataNotConfigured(FplError):
    status_code = 503


def load_guidelines(path: Optional[Path] = None) -> dict:
    path = Path(path or DEFAULT_FPL_PATH)
    if not path.exists():
        raise FplDataNotConfigured(f"FPL guideline file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("year", "first_person_amount", "each_additional_person", "source_url"):
        if data.get(key) in (None, ""):
            raise FplDataNotConfigured(f"FPL guideline file has no value for '{key}' (TODO-VERIFY from the official HHS source).")
    if data["first_person_amount"] <= 0 or data["each_additional_person"] < 0:
        raise FplDataNotConfigured("FPL guideline amounts must be positive.")
    return data


def guideline_amount(household_size: int, data: dict) -> Decimal:
    """HHS format: first person amount + (n - 1) x each-additional-person amount."""
    return Decimal(str(data["first_person_amount"])) + Decimal(str(data["each_additional_person"])) * (household_size - 1)


def compute_fpl_percent(household_size: int, annual_income: float, path: Optional[Path] = None) -> FplResult:
    """Income as a percent of the poverty guideline for this household size, rounded to 0.1 point."""
    if isinstance(household_size, bool) or not isinstance(household_size, int) or not (1 <= household_size <= MAX_HOUSEHOLD):
        raise FplError(f"Household size must be a whole number from 1 to {MAX_HOUSEHOLD}.")
    if isinstance(annual_income, bool) or annual_income is None or annual_income < 0:
        raise FplError("Annual income must be zero or more.")
    data = load_guidelines(path)
    amount = guideline_amount(household_size, data)
    pct = (Decimal(str(annual_income)) / amount * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return FplResult(household_size=household_size, annual_income=float(annual_income), fpl_percent=float(pct),
                     guideline_year=int(data["year"]), guideline_amount=float(amount), source_url=data["source_url"],
                     data_status=str(data.get("status", "UNKNOWN")))
