"""Parity: Postgres (Tiger Data) must give the same answers as the SQLite file.
Skipped unless TEST_DATABASE_URL points at a database loaded by scripts/migrate_to_postgres.py."""
import os

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT
from app.container import Container
from app.main import create_app
from app.repositories.sqlite_repo import SqlitePriceRepository

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN or not (PROJECT_ROOT / "truecost.db").exists(),
                                reason="set TEST_DATABASE_URL (and have truecost.db) to run the Postgres parity tests")

CODES = ["77066", "76642", "19083", "88305", "19301", "96413", "77385", "582", "N63.20"]


@pytest.fixture(scope="module")
def repos():
    from app.repositories.postgres_repo import PostgresPriceRepository
    sq, pg = SqlitePriceRepository(PROJECT_ROOT / "truecost.db"), PostgresPriceRepository(DSN)
    yield sq, pg
    pg.close()


def rows(recs):
    return sorted((r.hospital, r.code, r.payer or "", r.plan or "", str(r.rate_type), r.negotiated_rate, r.cash_price,
                   r.gross_charge, r.setting, r.notes or "") for r in recs)


def test_reference_data_identical(repos):
    sq, pg = repos
    assert sq.list_hospitals() == pg.list_hospitals()
    assert sq.list_codes() == pg.list_codes()
    for phase in ("diagnostic", "treatment", "diagnosis"):
        assert sq.list_codes(phase) == pg.list_codes(phase)
    assert sq.get_codes(CODES) == pg.get_codes(CODES) or sorted(sq.get_codes(CODES), key=lambda c: c.code) == sorted(pg.get_codes(CODES), key=lambda c: c.code)
    assert sq.get_codes([]) == pg.get_codes([]) == []


@pytest.mark.parametrize("payer", ["Independent Health", "Highmark BCBS", "Univera"])
def test_prices_and_plans_identical(repos, payer):
    sq, pg = repos
    assert sq.list_plans(payer) == pg.list_plans(payer)
    assert rows(sq.find_prices(CODES, payer)) == rows(pg.find_prices(CODES, payer))
    assert rows(sq.find_prices(CODES)) == rows(pg.find_prices(CODES))


def test_estimates_and_simulation_identical_through_the_api(repos):
    sq, pg = repos
    a = TestClient(create_app(container=Container.from_repo(sq)))
    b = TestClient(create_app(container=Container.from_repo(pg)))
    ins = {"insurer": "Independent Health", "plan": "Any", "deductible_remaining": 1200, "coinsurance_pct": 20, "oop_max_remaining": 4000}
    for codes in (["77066", "76642"], ["77066", "19083", "88305"], ["77066", "77048", "19301", "96413", "77385", "582"]):
        body = {"codes": codes, "insurance": ins, "patient_zip": "14226"}
        assert a.post("/estimate", json=body).json() == b.post("/estimate", json=body).json()
    sim = {"codes": ["77066", "19083"], "insurance": ins, "hospital": "Roswell Park"}
    assert a.post("/simulate", json=sim).json() == b.post("/simulate", json=sim).json()
    assert b.get("/health").json() == {"status": "ok", "db": "postgres"}
    assert a.get("/health").json() == {"status": "ok", "db": "sqlite"}
