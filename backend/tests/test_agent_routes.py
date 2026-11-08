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


# ----------------------------------------------------------------------------- connect Gmail + send (after approval)
import httpx
from urllib.parse import parse_qs, urlparse
from app.services.mailer import GmailService


def google_stub(calls):
    """Stands in for Google: token, userinfo, send and revoke endpoints."""
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, str(req.url), req.content.decode() if req.content else ""))
        url = str(req.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            if b"authorization_code" in req.content:
                return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT"})
            return httpx.Response(200, json={"access_token": "AT2"})
        if "userinfo" in url:
            return httpx.Response(200, json={"email": "patient@gmail.com"})
        if url.endswith("/drafts") and req.method == "POST":
            return httpx.Response(200, json={"id": "draft-1"})
        if url.endswith("/drafts/draft-1") and req.method == "PUT":
            return httpx.Response(200, json={"id": "draft-1"})
        if url.endswith("/drafts/send"):
            return httpx.Response(200, json={"id": "msg-from-draft"})
        if "messages/send" in url:
            return httpx.Response(200, json={"id": "msg-1"})
        return httpx.Response(200, json={})
    return handler


def gmail_client(repo, calls=None):
    calls = [] if calls is None else calls
    svc = GmailService("cid", "secret", "http://x/cb", http=httpx.Client(transport=httpx.MockTransport(google_stub(calls))))
    return TestClient(create_app(container=Container.from_repo(repo, mailer=svc))), calls


def connect(c):
    r = c.post("/api/v1/gmail/connect").json()
    state = parse_qs(urlparse(r["auth_url"]).query)["state"][0]
    cb = c.get("/api/v1/gmail/callback", params={"code": "abc", "state": state})
    return r, state, cb


SEND = {"to": "appeals@plan.example", "subject": "Appeal", "body": "Dear plan, please review.", "approved": True}


def test_connect_flow_then_send_from_the_users_own_account(repo):
    c, calls = gmail_client(repo)
    r, state, cb = connect(c)
    q = parse_qs(urlparse(r["auth_url"]).query)
    assert "gmail.compose" in q["scope"][0] and "gmail.readonly" not in q["scope"][0]   # no inbox access
    assert cb.status_code == 200 and "patient@gmail.com" in cb.text and "RT" not in cb.text   # no token in the page
    h = {"X-Gmail-Session": r["session_id"]}
    assert c.get("/api/v1/gmail/status", headers=h).json() == {"available": True, "connected": True, "email": "patient@gmail.com"}
    out = c.post("/api/v1/appeal/send", json=SEND, headers=h).json()
    assert out["sent"] and out["sent_from"] == "patient@gmail.com"
    sent = [x for x in calls if "messages/send" in x[1]][0]
    assert sent[1].endswith("messages/send")


def test_status_never_returns_a_token_and_other_browsers_are_not_connected(repo):
    c, _ = gmail_client(repo)
    r, _, _ = connect(c)
    assert "RT" not in c.get("/api/v1/gmail/status", headers={"X-Gmail-Session": r["session_id"]}).text
    assert c.get("/api/v1/gmail/status", headers={"X-Gmail-Session": "someone-else"}).json()["connected"] is False
    assert c.get("/api/v1/gmail/status").json()["connected"] is False


def test_send_needs_a_connected_gmail(repo):
    c, calls = gmail_client(repo)
    r = c.post("/api/v1/appeal/send", json=SEND)
    assert r.status_code == 401 and "Connect your Gmail" in r.json()["detail"]
    assert not [x for x in calls if "messages/send" in x[1]]


def test_state_is_single_use_and_unknown_state_is_rejected(repo):
    c, _ = gmail_client(repo)
    r, state, _ = connect(c)
    again = c.get("/api/v1/gmail/callback", params={"code": "abc", "state": state})
    assert again.status_code == 422 and "expired" in again.text
    assert c.get("/api/v1/gmail/callback", params={"code": "abc", "state": "forged"}).status_code == 422
    assert c.get("/api/v1/gmail/callback", params={"error": "access_denied"}).status_code == 400


def test_disconnect_forgets_the_user_and_revokes(repo):
    c, calls = gmail_client(repo)
    r, _, _ = connect(c)
    h = {"X-Gmail-Session": r["session_id"]}
    c.post("/api/v1/gmail/disconnect", headers=h)
    assert c.get("/api/v1/gmail/status", headers=h).json()["connected"] is False
    assert any("revoke" in x[1] for x in calls)


@pytest.mark.parametrize("change,msg", [({"approved": False}, "not been approved"),
                                        ({"to": "not-an-email"}, "valid email"),
                                        ({"body": "Member ID [MEMBER ID] denied"}, "[MEMBER ID]"),
                                        ({"subject": "a\nBcc: x@y.z"}, "one line")])
def test_send_is_refused_and_nothing_is_sent(repo, change, msg):
    c, calls = gmail_client(repo)
    r, _, _ = connect(c)
    res = c.post("/api/v1/appeal/send", json={**SEND, **change}, headers={"X-Gmail-Session": r["session_id"]})
    assert res.status_code == 422 and msg in res.json()["detail"]
    assert not [x for x in calls if "messages/send" in x[1]]


def test_gmail_not_configured_is_a_clean_503_and_status_says_unavailable(repo):
    c = TestClient(create_app(container=Container.from_repo(repo)))
    assert c.post("/api/v1/gmail/connect").status_code == 503
    assert c.post("/api/v1/appeal/send", json=SEND).status_code == 503
    assert c.get("/api/v1/gmail/status").json() == {"available": False, "connected": False, "email": None}


def test_draft_then_approved_send_updates_the_draft_and_sends_it(repo):
    c, calls = gmail_client(repo)
    r, _, _ = connect(c)
    h = {"X-Gmail-Session": r["session_id"]}
    d = c.post("/api/v1/gmail/draft", headers=h, json={"subject": "Appeal", "body": "Draft with [MEMBER ID] blank"})
    assert d.status_code == 200 and d.json()["draft_id"] == "draft-1" and d.json()["saved_in"] == "patient@gmail.com"
    assert not [x for x in calls if x[1].endswith("/drafts/send") or "messages/send" in x[1]]      # a draft sends nothing
    out = c.post("/api/v1/appeal/send", headers=h, json={**SEND, "draft_id": "draft-1"}).json()
    assert out["message_id"] == "msg-from-draft"
    kinds = [(x[0], x[1].rsplit("/", 1)[-1]) for x in calls if "/drafts" in x[1]]
    assert kinds == [("POST", "drafts"), ("PUT", "draft-1"), ("POST", "send")]                     # create, update, send


def test_draft_needs_a_connected_gmail_and_a_valid_address(repo):
    c, _ = gmail_client(repo)
    assert c.post("/api/v1/gmail/draft", json={"subject": "a", "body": "b"}).status_code == 401
    r, _, _ = connect(c)
    h = {"X-Gmail-Session": r["session_id"]}
    assert c.post("/api/v1/gmail/draft", headers=h, json={"to": "nope", "subject": "a", "body": "b"}).status_code == 422
    assert c.post("/api/v1/gmail/draft", headers=h, json={"to": "", "subject": "a", "body": "b"}).status_code == 200
