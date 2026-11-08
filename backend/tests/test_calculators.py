from decimal import Decimal as D

import pytest

from app.domain.calculators import InsuredCostCalculator
from app.domain.models import InsuranceProfile, Payer
from app.exceptions import InvalidInsuranceError


def profile(ded, pct, oop):
    return InsuranceProfile(Payer.UNIVERA, D(str(ded)), D(str(pct)), D(str(oop)))


def test_deductible_then_coinsurance():
    # $1,000 deductible left, 20% coinsurance, $300 service -> all of it goes to the deductible
    [c] = InsuredCostCalculator(profile(1000, 20, 5000)).calculate([D("300")])
    assert c.patient_pays == D("300.00") and c.deductible_paid == D("300.00") and c.coinsurance_paid == D("0.00")


def test_deductible_met_part_way():
    # $100 deductible left, 20% coinsurance, $500 service -> 100 + 20% of 400 = 180
    [c] = InsuredCostCalculator(profile(100, 20, 5000)).calculate([D("500")])
    assert (c.deductible_paid, c.coinsurance_paid, c.patient_pays, c.insurer_pays) == (
        D("100.00"), D("80.00"), D("180.00"), D("320.00"))


def test_order_matters_deductible_used_by_first_service():
    first, second = InsuredCostCalculator(profile(200, 10, 5000)).calculate([D("150"), D("300")])
    assert first.patient_pays == D("150.00")                      # all deductible
    assert second.deductible_paid == D("50.00") and second.patient_pays == D("75.00")  # 50 + 10% of 250


def test_out_of_pocket_max_caps_patient_cost():
    a, b = InsuredCostCalculator(profile(0, 50, 200)).calculate([D("300"), D("300")])
    assert a.patient_pays == D("150.00") and not a.hit_oop_max
    assert b.patient_pays == D("50.00") and b.hit_oop_max         # only $50 of room left
    assert b.insurer_pays == D("250.00")


def test_after_cap_patient_pays_nothing():
    results = InsuredCostCalculator(profile(0, 100, 100)).calculate([D("100"), D("500")])
    assert results[1].patient_pays == D("0.00") and results[1].hit_oop_max


def test_zero_and_full_coinsurance():
    assert InsuredCostCalculator(profile(0, 0, 999)).calculate([D("400")])[0].patient_pays == D("0.00")
    assert InsuredCostCalculator(profile(0, 100, 999)).calculate([D("400")])[0].patient_pays == D("400.00")


def test_rounding_to_cents():
    [c] = InsuredCostCalculator(profile(0, 33, 999)).calculate([D("100.01")])
    assert c.coinsurance_paid == D("33.00")


@pytest.mark.parametrize("ded,pct,oop", [(-1, 20, 100), (0, 120, 100), (0, -5, 100), (0, 20, -1)])
def test_invalid_insurance_rejected(ded, pct, oop):
    with pytest.raises(InvalidInsuranceError):
        profile(ded, pct, oop)
