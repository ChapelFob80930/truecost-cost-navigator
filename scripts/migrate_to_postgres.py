"""Copy truecost.db (SQLite) into Postgres / Tiger Data.

  python scripts/migrate_to_postgres.py                      # uses DATABASE_URL from .env
  python scripts/migrate_to_postgres.py --dsn "postgres://..." --sqlite truecost.db

Safe to re-run: it replaces the contents of prices, hospitals and codes inside one transaction, then verifies
row counts and a checksum against the SQLite file. The connection string (it contains a password) is never printed.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import psycopg  # noqa: E402

from app.config import load_dotenv  # noqa: E402

SCHEMA = ROOT / "backend" / "data" / "postgres_schema.sql"
PRICE_COLS = ["hospital", "code", "code_type", "description", "setting", "payer", "plan", "rate_type",
              "negotiated_rate", "cash_price", "gross_charge", "notes"]
NUMERIC = {"negotiated_rate", "cash_price", "gross_charge"}


def conv(col: str, v):
    return None if v is None else (Decimal(str(v)) if col in NUMERIC else v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--sqlite", default=str(ROOT / "truecost.db"))
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    dsn = args.dsn or os.getenv("DATABASE_URL")
    if not dsn:
        print("No connection string. Set DATABASE_URL in .env or pass --dsn.")
        return 2

    src = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    hospitals = src.execute("SELECT hospital, address, lat, lng FROM hospitals").fetchall()
    codes = src.execute("SELECT code, code_type, description, phase FROM codes").fetchall()
    prices = src.execute(f"SELECT {', '.join(PRICE_COLS)} FROM prices").fetchall()
    src_sum = src.execute("SELECT ROUND(COALESCE(SUM(negotiated_rate),0),2) FROM prices").fetchone()[0]
    print(f"SQLite: {len(hospitals)} hospitals, {len(codes)} codes, {len(prices)} prices")

    with psycopg.connect(dsn) as conn:                       # one transaction: all or nothing
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
        conn.execute("TRUNCATE prices, hospitals, codes RESTART IDENTITY")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO hospitals (hospital, address, lat, lng) VALUES (%s,%s,%s,%s)", hospitals)
            cur.executemany("INSERT INTO codes (code, code_type, description, phase) VALUES (%s,%s,%s,%s)", codes)
            cur.executemany(f"INSERT INTO prices ({', '.join(PRICE_COLS)}) VALUES ({', '.join(['%s'] * len(PRICE_COLS))})",
                            [tuple(conv(c, v) for c, v in zip(PRICE_COLS, row)) for row in prices])
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("hospitals", "codes", "prices")}
        pg_sum = conn.execute("SELECT ROUND(COALESCE(SUM(negotiated_rate),0),2) FROM prices").fetchone()[0]

    ok = (counts == {"hospitals": len(hospitals), "codes": len(codes), "prices": len(prices)}
          and abs(Decimal(str(src_sum)) - Decimal(str(pg_sum))) < Decimal("0.01"))
    print(f"Postgres: {counts}; checksum SQLite={src_sum} Postgres={pg_sum}")
    print("MIGRATION OK" if ok else "MIGRATION MISMATCH - do not use this database")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
