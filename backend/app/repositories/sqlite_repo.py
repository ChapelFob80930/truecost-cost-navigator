from __future__ import annotations

import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Sequence

from ..domain.models import Hospital, MedicalCode, PriceRecord
from .base import PriceRepository


def _dec(value) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


class SqlitePriceRepository(PriceRepository):
    """Read-only access to truecost.db. One short-lived connection per call keeps it
    thread-safe under FastAPI's worker threads."""

    backend = "sqlite"

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        if not self._db_path.exists():
            raise FileNotFoundError(f"Database not found: {self._db_path}. Run scripts/build_db.py first.")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _marks(items: Sequence) -> str:
        return ",".join("?" * len(items))

    def list_hospitals(self) -> List[Hospital]:
        with closing(self._connect()) as con:
            rows = con.execute("SELECT hospital, address, lat, lng FROM hospitals ORDER BY hospital").fetchall()
        return [Hospital(r["hospital"], r["address"], r["lat"], r["lng"]) for r in rows]

    def list_codes(self, phase: Optional[str] = None) -> List[MedicalCode]:
        sql, args = "SELECT code, code_type, description, phase FROM codes", []
        if phase:
            sql, args = sql + " WHERE phase = ?", [phase]
        with closing(self._connect()) as con:
            rows = con.execute(sql + " ORDER BY code_type, code", args).fetchall()
        return [MedicalCode(r["code"], r["code_type"], r["description"], r["phase"]) for r in rows]

    def get_codes(self, codes: Sequence[str]) -> List[MedicalCode]:
        if not codes:
            return []
        with closing(self._connect()) as con:
            rows = con.execute(
                f"SELECT code, code_type, description, phase FROM codes WHERE code IN ({self._marks(codes)})",
                list(codes)).fetchall()
        return [MedicalCode(r["code"], r["code_type"], r["description"], r["phase"]) for r in rows]

    def find_prices(self, codes: Sequence[str], payer: Optional[str] = None) -> List[PriceRecord]:
        if not codes:
            return []
        sql = f"SELECT * FROM prices WHERE code IN ({self._marks(codes)})"
        args: list = list(codes)
        if payer:
            sql += " AND (payer = ? OR payer IS NULL)"
            args.append(payer)
        with closing(self._connect()) as con:
            rows = con.execute(sql, args).fetchall()
        return [PriceRecord(
            hospital=r["hospital"], code=r["code"], code_type=r["code_type"], description=r["description"],
            setting=r["setting"], payer=r["payer"], plan=r["plan"], rate_type=r["rate_type"],
            negotiated_rate=_dec(r["negotiated_rate"]), cash_price=_dec(r["cash_price"]),
            gross_charge=_dec(r["gross_charge"]), notes=r["notes"]) for r in rows]

    def list_plans(self, payer: str) -> List[str]:
        with closing(self._connect()) as con:
            rows = con.execute("SELECT DISTINCT plan FROM prices WHERE payer = ? AND plan IS NOT NULL ORDER BY plan",
                               [payer]).fetchall()
        return [r["plan"] for r in rows]
