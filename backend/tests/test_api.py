INS = {"insurer": "Univera", "plan": "PPO", "deductible_remaining": 0, "coinsurance_pct": 20, "oop_max_remaining": 5000}


def est(client, **body):
    body.setdefault("insurance", INS)
    return client.post("/estimate", json=body)


def by_name(r, name):
    return next(h for h in r["results"] if h["hospital"] == name)


def test_health_and_reference(client):
    assert client.get("/health").json()["status"] == "ok"
    assert len(client.get("/api/v1/codes").json()) == 3
    assert len(client.get("/api/v1/hospitals").json()) == 3
    assert client.get("/api/v1/plans?insurer=Univera").json() == ["HMO", "PPO", "Any"]


def test_contract_shape_and_ranking(client):
    r = est(client, codes=["77066"], patient_zip="14226").json()
    assert set(r) == {"results", "caveats"} and r["caveats"]
    ranked = [h for h in r["results"] if h["complete"]]
    assert [h["hospital"] for h in ranked] == ["Beta", "Alpha"]                  # 20% of 200 < 20% of 300
    assert ranked[0]["patient_oop"] == 40.0 and ranked[0]["savings_vs_highest"] == 20.0
    assert ranked[0]["lines"][0]["rule"] and ranked[0]["lines"][0]["rate"] == 200.0
    assert all(h["drive_minutes"] is not None for h in r["results"])
    # incomplete hospitals come after complete ones
    assert [h["complete"] for h in r["results"]] == [True, True, False]


def test_exact_plan_never_blends(client):
    r = est(client, codes=["77066"], insurance={**INS, "plan": "HMO"}).json()
    beta = by_name(r, "Beta")
    assert beta["lines"][0]["rate"] == 220.0
    assert by_name(r, "Alpha")["complete"] is False                              # Alpha has no HMO rate
    assert "HMO" in by_name(r, "Alpha")["unavailable"][0]["reason"]


def test_any_and_combined_types_use_median_across_plans(client):
    beta = by_name(est(client, codes=["77066"], insurance={**INS, "plan": "Any"}).json(), "Beta")
    assert beta["lines"][0]["rate"] == 210.0 and "median of 2" in beta["lines"][0]["rule"]      # PPO 200, HMO 220
    beta = by_name(est(client, codes=["77066"], insurance={**INS, "plan": "HMO/PPO"}).json(), "Beta")
    assert beta["lines"][0]["rate"] == 210.0


def test_unknown_plan_fails_loudly(client):
    resp = est(client, codes=["77066"], insurance={**INS, "plan": "Gold"})
    assert resp.status_code == 422 and "HMO" in resp.json()["detail"]


def test_gross_only_hospital_is_unavailable(client):
    gamma = by_name(est(client, codes=["77066"]).json(), "Gamma")
    assert gamma["complete"] is False and gamma["savings_vs_highest"] is None
    assert "gross charge only" in gamma["unavailable"][0]["reason"]


def test_inpatient_drg_unavailable_but_outpatient_still_ranked(client):
    r = est(client, codes=["77066", "582"]).json()
    alpha = by_name(r, "Alpha")
    assert alpha["complete"] is True                                              # DRG doesn't block comparison
    assert any(u["code"] == "582" and "contact hospital" in u["reason"] for u in alpha["unavailable"])
    assert [l["code"] for l in alpha["lines"]] == ["77066"]


def test_deductible_carries_across_services(client):
    ins = {**INS, "deductible_remaining": 1100}
    alpha = by_name(est(client, codes=["77066", "19081"], insurance=ins).json(), "Alpha")
    first, second = alpha["lines"]
    assert first["patient_pays"] == 300.0
    assert second["patient_pays"] == 800.0 + 0.2 * 200


def test_oop_max_caps_total(client):
    ins = {**INS, "coinsurance_pct": 100, "oop_max_remaining": 250}
    alpha = by_name(est(client, codes=["77066", "19081"], insurance=ins).json(), "Alpha")
    assert alpha["patient_oop"] == 250.0


def test_unknown_zip_gives_null_drive_time_and_caveat(client):
    r = est(client, codes=["77066"], patient_zip="90210").json()
    assert all(h["drive_minutes"] is None for h in r["results"])
    assert any("90210" in c for c in r["caveats"])


def test_errors(client):
    assert est(client, codes=["99999"]).status_code == 404
    assert client.post("/estimate", json={"codes": ["77066"]}).status_code == 422
    assert est(client, codes=["77066"], insurance={**INS, "coinsurance_pct": 150}).status_code == 422
    assert est(client, codes=[]).status_code == 422
    assert est(client, codes=["77066"], insurance={**INS, "insurer": "Aetna"}).status_code == 422


def test_duplicate_codes_are_deduplicated(client):
    r = est(client, codes=["77066", "77066"]).json()
    assert len(by_name(r, "Alpha")["lines"]) == 1


# ---- /simulate -----------------------------------------------------------------------------------
def sim(client, **body):
    body.setdefault("insurance", INS)
    return client.post("/simulate", json=body)


def test_simulate_defaults_to_cheapest_and_builds_curve(client):
    ins = {**INS, "deductible_remaining": 100, "coinsurance_pct": 50, "oop_max_remaining": 600}
    r = sim(client, codes=["77066", "19081"], insurance=ins).json()
    assert r["hospital"] == "Alpha"                    # Alpha: 100 + 50% of 200 = 200, + 50% of 1000 = 500 -> 700 -> capped 600
    t = r["timeline"]
    assert [e["code"] for e in t] == ["77066", "19081"]
    assert t[0]["cumulative_oop"] == 200.0 and t[1]["cumulative_oop"] == 600.0
    assert r["oop_max_reached_month"] == 1


def test_simulate_pathway_repeat_and_month_of_cap(client):
    ins = {**INS, "coinsurance_pct": 100, "oop_max_remaining": 700}
    r = sim(client, hospital="Alpha", codes=["77066"], insurance=ins,
            pathway=[{"code": "77066", "month": 1, "repeat": 4, "step": "Imaging"}]).json()
    assert [e["cost"] for e in r["timeline"]] == [300.0, 300.0, 100.0, 0.0]   # cap hit in month 3
    assert r["timeline"][-1]["cumulative_oop"] == 700.0
    assert r["oop_max_reached_month"] == 3


def test_simulate_unavailable_step_and_bad_hospital(client):
    r = sim(client, hospital="Alpha", codes=["77066", "582"]).json()
    assert [u["code"] for u in r["unavailable"]] == ["582"]
    assert sim(client, hospital="Nowhere", codes=["77066"]).status_code == 404


# ---- /extract ------------------------------------------------------------------------------------
def test_extract_endpoint_contract(client):
    note = "Patient needs a bilateral diagnostic mammogram and a stereotactic biopsy of the left breast."
    r = client.post("/extract", json={"note": note}).json()
    assert {"77066", "19081"} <= {c["code"] for c in r["codes"]}
    assert set(r["codes"][0]) == {"code", "type", "description", "confidence", "evidence"}
    assert "77065" not in {c["code"] for c in r["codes"]}


def test_icd10_codes_are_ignored_not_priced(client, repo):
    from app.domain.models import MedicalCode
    repo.CODES = repo.CODES + [MedicalCode("N63.0", "ICD10", "Breast lump", "diagnosis")]
    r = est(client, codes=["N63.0", "77066"]).json()
    assert by_name(r, "Alpha")["complete"] is True and [l["code"] for l in by_name(r, "Alpha")["lines"]] == ["77066"]
    assert est(client, codes=["N63.0"]).status_code == 422


def test_preflight_allows_private_network_access(client):
    r = client.options("/estimate", headers={"Origin": "https://compare-and-choose-hub.lovable.app",
                                              "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Private-Network": "true"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in ("*", "https://compare-and-choose-hub.lovable.app")
    assert r.headers.get("access-control-allow-private-network") == "true"


def test_root_is_friendly_not_404(client):
    r = client.get("/")
    assert r.status_code == 200 and r.json()["docs"] == "/docs"
