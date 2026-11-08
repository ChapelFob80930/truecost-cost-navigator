import json

import pytest
from fastapi.testclient import TestClient

from app.agents.cached import CACHE_DIR
from app.container import Container
from app.main import create_app
from app.services.agent_service import AgentService
from .test_agent_loop import FakeChat  # scripted model from the loop tests
from app.agents.llm import ModelTurn
from app.agents.assistance_agent import SUMMARY_SCHEMA
from app.agents.appeal_agent import DENIAL_SCHEMA, NARRATIVE_SCHEMA

CASE = {"confirmed_codes": ["77066"], "household_size": 3, "annual_income": 27000}


def app_client(repo, model=None):
    return TestClient(create_app(container=Container.from_repo(repo, agent_model=model)))


def test_no_model_and_not_demo_returns_503_with_a_helpful_message(repo):
    r = app_client(repo).post("/api/v1/assistance/match", json={"case": CASE})
    assert r.status_code == 503 and "demo mode" in r.json()["detail"]


def test_demo_mode_needs_no_model_and_is_labelled_cached(repo):
    c = app_client(repo)                                    # no model at all
    a = c.post("/api/v1/assistance/match", json={"case": CASE, "demo_mode": True}).json()
    assert a["meta"]["mode"] == "cached" and a["meta"]["model"] is None
    assert any("pre-recorded" in d for d in a["meta"]["disclaimers"]) and a["meta"]["handoff"]
    b = c.post("/api/v1/appeal/draft", json={"case": CASE, "denial_text": "x denied", "demo_mode": True}).json()
    assert b["meta"]["mode"] == "cached" and b["letter"]["body"] and "[MEMBER ID]" in b["letter"]["placeholders"]


def test_missing_cached_file_is_a_clean_503(repo, tmp_path):
    c = TestClient(create_app(container=Container.from_repo(repo, agent_service=AgentService(None, cache_dir=tmp_path))))
    r = c.post("/api/v1/assistance/match", json={"case": CASE, "demo_mode": True})
    assert r.status_code == 503 and "record_demo" in r.json()["detail"]


def test_live_match_route_with_scripted_model(repo):
    m = FakeChat([ModelTurn(text="done", calls=[])], {id(SUMMARY_SCHEMA): {"summary_plain": "ok"}})
    r = app_client(repo, m).post("/api/v1/assistance/match", json={"case": CASE}).json()
    assert r["meta"]["mode"] == "live" and r["meta"]["model"] == "fake-model" and r["fpl"]["fpl_percent"] > 0
    by = {m["program_name"]: m for m in r["matches"]}                                   # real, verified rows from the shipped table
    roswell = next(v for k, v in by.items() if k.startswith("Roswell Park Financial Assistance Program (uninsured"))
    assert roswell["match_basis"] == "income_at_or_below_listed_threshold" and roswell["source_url"].startswith("https://")
    assert "716-845-4782" in roswell["contact"] and "400%" in roswell["eligibility_summary"]
    assert all("you qualify" not in m["why"].lower() for m in r["matches"])


def test_live_appeal_route_with_scripted_model(repo):
    m = FakeChat(finals={id(DENIAL_SCHEMA): {"denial_reason": "not medically necessary"},
                         id(NARRATIVE_SCHEMA): {"summary_plain": "Denied.", "documentation_checklist": ["a"], "next_steps": ["b"]}})
    r = app_client(repo, m).post("/api/v1/appeal/draft", json={
        "case": {"confirmed_codes": ["77066"]}, "denial_text": "Claim denied: the service was not medically necessary."}).json()
    assert r["parsed"]["denial_reason"] == "not medically necessary" and r["meta"]["tool_calls_used"] == 3
    assert "[PATIENT NAME]" in r["letter"]["body"]


@pytest.mark.parametrize("path,body", [
    ("/api/v1/appeal/draft", {"case": {}}),                                    # no denial text
    ("/api/v1/assistance/match", {"case": {"household_size": 0}}),
    ("/api/v1/assistance/match", {"case": {"annual_income": -5}}),
    ("/api/v1/appeal/draft", {"case": {}, "denial_text": "x" * 20001}),
    ("/api/v1/appeal/draft", {"case": {}, "denial_text": "x", "letter_type": "lawsuit"}),
])
def test_bad_requests_are_422(repo, path, body):
    assert app_client(repo).post(path, json=body).status_code == 422


def test_cached_files_are_valid_and_contain_no_real_identifiers():
    for name in ("sarah_assistance", "sarah_appeal"):
        text = (CACHE_DIR / f"{name}.json").read_text(encoding="utf-8")
        assert "AIza" not in text and "AQ." not in text           # no API key material
        json.loads(text)
