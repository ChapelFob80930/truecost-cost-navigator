"""Postgres / Tiger Data implementation of PriceRepository. Same interface and same results as SqlitePriceRepository.

Notes
- Uses a small connection pool (cloud round-trips are slower than a local file).
- `COLLATE "C"` on every ORDER BY keeps ordering identical to SQLite's binary ordering.
- Read-only by design; loading happens in scripts/migrate_to_postgres.py.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, List, Optional, Sequence

from ..domain.models import Hospital, MedicalCode, PriceRecord
from .base import PriceRepository


def _dec(value) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


class PostgresPriceRepository(PriceRepository):
    backend = "postgres"

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 5):
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        # Cloud databases close idle connections, so: test a connection before handing it out, recycle old ones,
        # use TCP keepalives, and never wait forever for a connection.
        self._pool = ConnectionPool(
            dsn, min_size=min_size, max_size=max_size, open=True,
            check=ConnectionPool.check_connection, max_idle=120, max_lifetime=600, timeout=15,
            kwargs={"row_factory": dict_row, "autocommit": True, "connect_timeout": 10,
                    "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3})
        self._pool.wait(timeout=20)          # fail fast with a clear error if the database is unreachable

    def close(self) -> None:
        self._pool.close()

    @staticmethod
    def _marks(items: Sequence) -> str:
        return ",".join(["%s"] * len(items))

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list:
        with self._pool.connection() as conn:
            return conn.execute(sql, list(params)).fetchall()

    def list_hospitals(self) -> List[Hospital]:
        rows = self._rows('SELECT hospital, address, lat, lng FROM hospitals ORDER BY hospital COLLATE "C"')
        return [Hospital(r["hospital"], r["address"], r["lat"], r["lng"]) for r in rows]

    def list_codes(self, phase: Optional[str] = None) -> List[MedicalCode]:
        sql, args = "SELECT code, code_type, description, phase FROM codes", []
        if phase:
            sql, args = sql + " WHERE phase = %s", [phase]
        rows = self._rows(sql + ' ORDER BY code_type COLLATE "C", code COLLATE "C"', args)
        return [MedicalCode(r["code"], r["code_type"], r["description"], r["phase"]) for r in rows]

    def get_codes(self, codes: Sequence[str]) -> List[MedicalCode]:
        if not codes:
            return []
        rows = self._rows(f"SELECT code, code_type, description, phase FROM codes WHERE code IN ({self._marks(codes)})", codes)
        return [MedicalCode(r["code"], r["code_type"], r["description"], r["phase"]) for r in rows]

    def find_prices(self, codes: Sequence[str], payer: Optional[str] = None) -> List[PriceRecord]:
        if not codes:
            return []
        sql = f"SELECT * FROM prices WHERE code IN ({self._marks(codes)})"
        args: list = list(codes)
        if payer:
            sql += " AND (payer = %s OR payer IS NULL)"
            args.append(payer)
        return [PriceRecord(
            hospital=r["hospital"], code=r["code"], code_type=r["code_type"], description=r["description"],
            setting=r["setting"], payer=r["payer"], plan=r["plan"], rate_type=r["rate_type"],
            negotiated_rate=_dec(r["negotiated_rate"]), cash_price=_dec(r["cash_price"]),
            gross_charge=_dec(r["gross_charge"]), notes=r["notes"]) for r in self._rows(sql, args)]

    def list_plans(self, payer: str) -> List[str]:
        rows = self._rows('SELECT plan FROM (SELECT DISTINCT plan FROM prices WHERE payer = %s AND plan IS NOT NULL) p ORDER BY plan COLLATE "C"', [payer])
        return [r["plan"] for r in rows]
