"""Repository interface. The service depends on this, never on SQLite directly,
so tests use an in-memory fake and the DB could later move to Postgres."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

from ..domain.models import Hospital, MedicalCode, PriceRecord


class PriceRepository(ABC):
    @abstractmethod
    def list_hospitals(self) -> List[Hospital]: ...

    @abstractmethod
    def list_codes(self, phase: Optional[str] = None) -> List[MedicalCode]: ...

    @abstractmethod
    def get_codes(self, codes: Sequence[str]) -> List[MedicalCode]: ...

    @abstractmethod
    def find_prices(self, codes: Sequence[str], payer: Optional[str] = None) -> List[PriceRecord]:
        """Price rows for these codes. payer=None returns rows of every payer (and payer-less rows)."""

    @abstractmethod
    def list_plans(self, payer: str) -> List[str]: ...
