from decimal import Decimal as D
from typing import List, Optional, Sequence

import pytest
from fastapi.testclient import TestClient

from app.container import Container
from app.domain.models import Hospital, MedicalCode, PriceRecord
from app.main import create_app
from app.repositories.base import PriceRepository


def rec(hospital, code, payer=None, plan=None, rate=None, cash=None, gross=None, rate_type="negotiated_dollar",
        code_type="CPT", setting="outpatient"):
    return PriceRecord(hospital=hospital, code=code, code_type=code_type, description=f"desc {code}",
                       setting=setting, payer=payer, plan=plan,
                       rate_type=rate_type if rate is not None or rate_type == "percent_of_charges" else None,
                       negotiated_rate=None if rate is None else D(str(rate)),
                       cash_price=None if cash is None else D(str(cash)),
                       gross_charge=None if gross is None else D(str(gross)), notes=None)


class FakeRepo(PriceRepository):
    """Tiny in-memory dataset: 3 hospitals, 3 codes."""

    HOSPITALS = [Hospital("Alpha", "1 A St", 42.90, -78.87), Hospital("Beta", "2 B St", 43.00, -78.80),
                 Hospital("Gamma", "3 G St", 42.80, -78.70)]
    CODES = [MedicalCode("77066", "CPT", "Diagnostic mammogram, bilateral", "diagnostic"),
             MedicalCode("19081", "CPT", "Breast biopsy, stereotactic", "diagnostic"),
             MedicalCode("582", "MS-DRG", "Mastectomy inpatient", "treatment")]
    PRICES = [
        rec("Alpha", "77066", "Univera", "PPO", 300, cash=150, gross=500),
        rec("Alpha", "19081", "Univera", "PPO", 1000, cash=600, gross=2000),
        rec("Beta", "77066", "Univera", "PPO", 200, cash=120, gross=450),
        rec("Beta", "19081", "Univera", "PPO", 1500, cash=700, gross=2500),
        rec("Beta", "77066", "Univera", "HMO", 220),
        # Gamma only lists gross charges (like ECMC): no insurer rates
        rec("Gamma", "77066", gross=400),
        rec("Gamma", "19081", gross=1800),
        # formula-based inpatient rate
        rec("Alpha", "582", "Univera", "PPO", None, rate_type="percent_of_charges", code_type="MS-DRG", setting="inpatient"),
    ]

    def list_hospitals(self): return list(self.HOSPITALS)
    def list_codes(self, phase=None): return [c for c in self.CODES if phase in (None, c.phase)]
    def get_codes(self, codes: Sequence[str]): return [c for c in self.CODES if c.code in codes]
    def list_plans(self, payer): return sorted({p.plan for p in self.PRICES if p.payer == payer and p.plan})

    def find_prices(self, codes: Sequence[str], payer: Optional[str] = None) -> List[PriceRecord]:
        return [p for p in self.PRICES if p.code in codes and (payer is None or p.payer in (payer, None))]


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def client(repo):
    return TestClient(create_app(container=Container.from_repo(repo)))
