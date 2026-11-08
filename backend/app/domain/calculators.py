"""Cost calculators (Strategy pattern).

CostCalculator.calculate() takes the allowed amounts of a patient's services IN ORDER and
returns who pays what. Order matters: the first services eat the deductible.

Insured:  deductible first -> coinsurance on the rest -> capped by the out-of-pocket max.
Self-pay: patient pays the full price.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Sequence

from .models import InsuranceProfile, LineCost

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


class CostCalculator(ABC):
    @abstractmethod
    def calculate(self, allowed_amounts: Sequence[Decimal]) -> List[LineCost]:
        ...


class InsuredCostCalculator(CostCalculator):
    def __init__(self, profile: InsuranceProfile):
        self._profile = profile

    def calculate(self, allowed_amounts: Sequence[Decimal]) -> List[LineCost]:
        deductible_left = money(self._profile.deductible_remaining)
        oop_left = money(self._profile.oop_max_remaining)
        coinsurance = self._profile.coinsurance_pct / Decimal(100)
        results: List[LineCost] = []

        for raw in allowed_amounts:
            allowed = money(raw)
            deductible_part = min(deductible_left, allowed)
            deductible_left -= deductible_part
            coinsurance_part = money((allowed - deductible_part) * coinsurance)
            patient = deductible_part + coinsurance_part

            capped = patient > oop_left
            if capped:  # the cap trims coinsurance first, then the deductible portion
                patient = oop_left
                coinsurance_part = max(ZERO, patient - deductible_part)
                deductible_part = patient - coinsurance_part
            oop_left -= patient

            results.append(LineCost(
                allowed=allowed,
                deductible_paid=deductible_part,
                coinsurance_paid=coinsurance_part,
                patient_pays=patient,
                insurer_pays=allowed - patient,
                hit_oop_max=capped or (oop_left == ZERO and patient > ZERO),
            ))
        return results
