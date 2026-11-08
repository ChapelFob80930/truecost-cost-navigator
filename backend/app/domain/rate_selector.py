"""Rate selection by plan TYPE (see plan_types.py).

For one hospital + code, take every dollar rate whose plan name belongs to the chosen type(s) and use the
median, reporting the low-high spread. Types are never silently widened: no match -> no rate.
"""
from __future__ import annotations

from statistics import median
from typing import Optional, Sequence, Set

from .calculators import money
from .models import PriceRecord, SelectedRate
from .plan_types import plan_matches


class PlanTypeSelector:
    def select(self, candidates: Sequence[PriceRecord], wanted: Set[str]) -> Optional[SelectedRate]:
        pool = [c for c in candidates if c.is_dollar_rate and plan_matches(c.plan, wanted)]
        if not pool:
            return None
        rates = [c.negotiated_rate for c in pool if c.negotiated_rate is not None]
        mid = money(median(rates))
        closest = min(pool, key=lambda c: abs((c.negotiated_rate or mid) - mid))
        return SelectedRate(record=closest, rate=mid, low=min(rates), high=max(rates),
                            candidates=len(pool), plan_matched=True)
