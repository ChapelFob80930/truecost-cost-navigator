"""Labelled estimates for rates a hospital did not publish for the patient's insurer."""
from fastapi.testclient import TestClient

from app.container import Container
from app.domain.models import Hospital
from app.domain.plan_types import classify
from app.main import create_app

from .conftest import FakeRepo, rec


class GapRepo(FakeRepo):
    HOSPITALS = [Hospital("Full", "1 F St", 42.90, -78.87), Hospital("Gappy", "2 G St", 43.00, -78.80),
                 Hospital("Bare", "3 B St", 42.80, -78.70)]
    PRICES = [
        rec("Full", "77066", "Independent Health", "Article 43 PPO", 200),
        rec("Full", "19081", "Independent Health", "Article 43 PPO", 1000),
        # Gappy publishes mammogram for IHA but no biopsy; two other insurers publish the biopsy
        rec("Gappy", "77066", "Independent Health", "IHA Article 43", 210),
        rec("Gappy", "19081", "Highmark BCBS", "PPO B/C", 1000),
        rec("Gappy", "19081", "Univera", "PPO Univera", 1200),
        rec("Gappy", "19081", "Univera", "HMO For Individuals", 9999),  # wrong plan type: must not be used
        # Bare: gross charges only, nobody else's rates either
        rec("Bare", "77066", gross=400),
        rec("Bare", "19081", gross=1800),
    ]


def _client():
    return TestClient(create_app(container=Container.from_repo(GapRepo())))


def _body(**extra):
    b = {"codes": ["77066", "19081"],
         "insurance": {"insurer": "Independent Health", "plan": "PPO", "deductible_remaining": 0,
                       "coinsurance_pct": 20, "oop_max_remaining": 100000}}
    b.update(extra)
    return b


def _by_name(resp):
    return {r["hospital"]: r for r in resp.json()["results"]}


def test_missing_rate_is_estimated_and_labelled():
    r = _by_name(_client().post("/estimate", json=_body()))
    gappy = r["Gappy"]
    assert gappy["complete"] is True and gappy["has_estimates"] is True
    est = next(l for l in gappy["lines"] if l["code"] == "19081")
    assert est["price_source"] == "estimated"
    assert est["rate"] == 1100.0                      # median of Highmark 1000 and Univera 1200 (PPO only)
    assert "Not published for Independent Health" in est["estimate_note"]
    pub = next(l for l in gappy["lines"] if l["code"] == "77066")
    assert pub["price_source"] == "published" and pub["estimate_note"] is None
    assert r["Full"]["has_estimates"] is False


def test_estimate_never_used_when_no_other_insurer_publishes():
    bare = _by_name(_client().post("/estimate", json=_body()))["Bare"]
    assert bare["complete"] is False and bare["has_estimates"] is False and bare["lines"] == []


def test_estimates_can_be_switched_off():
    gappy = _by_name(_client().post("/estimate", json=_body(include_estimates=False)))["Gappy"]
    assert gappy["complete"] is False and gappy["has_estimates"] is False
    assert [u["code"] for u in gappy["unavailable"]] == ["19081"]


def test_caveat_added_only_when_an_estimate_is_shown():
    on = _client().post("/estimate", json=_body()).json()["caveats"]
    off = _client().post("/estimate", json=_body(include_estimates=False)).json()["caveats"]
    assert any("estimated" in c for c in on) and not any("estimated" in c for c in off)


def test_simulate_marks_estimate_caveat():
    body = _body(hospital="Gappy")
    body["pathway"] = [{"code": "77066", "month": 1}, {"code": "19081", "month": 2}]
    resp = _client().post("/simulate", json=body).json()
    assert [t["code"] for t in resp["timeline"]] == ["77066", "19081"]
    assert any("estimated" in c for c in resp["caveats"])


def test_article_43_44_names_map_to_ppo_and_hmo():
    assert classify("IHA Article 43") == {"PPO"}
    assert classify("Univera Article 44") == {"HMO"}
    assert classify("Commercial IHA 44") == {"HMO"}
    assert classify("Article 44 / Article 43 HMO/PPO") == {"HMO", "PPO"}
    assert classify("Choice Plus") == {"Other"}


# ------------------------------------------------------------------ diagnosis codes are not steps on the cost timeline
def test_simulate_ignores_diagnosis_codes_in_the_pathway():
    from app.domain.models import MedicalCode

    class WithIcd(GapRepo):
        CODES = FakeRepo.CODES + [MedicalCode("N63.0", "ICD10", "Unspecified lump in unspecified breast", "diagnosis")]

    client = TestClient(create_app(container=Container.from_repo(WithIcd())))
    body = _body(hospital="Full")
    body["codes"] = ["N63.0", "77066", "19081"]
    body["pathway"] = [{"code": "77066", "month": 1}, {"code": "N63.0", "month": 1}, {"code": "19081", "month": 2}]
    r = client.post("/simulate", json=body)
    assert r.status_code == 200
    out = r.json()
    assert [t["code"] for t in out["timeline"]] == ["77066", "19081"]
    assert out["unavailable"] == []                 # no fake "not found" for a diagnosis


def test_simulate_with_only_diagnosis_steps_is_a_clear_error():
    from app.domain.models import MedicalCode

    class WithIcd(GapRepo):
        CODES = FakeRepo.CODES + [MedicalCode("N63.0", "ICD10", "Unspecified lump in unspecified breast", "diagnosis")]

    client = TestClient(create_app(container=Container.from_repo(WithIcd())))
    body = _body(hospital="Full")
    body["codes"] = ["N63.0", "77066"]
    body["pathway"] = [{"code": "N63.0", "month": 1}]
    r = client.post("/simulate", json=body)
    assert r.status_code == 422 and "procedure" in r.json()["detail"]
