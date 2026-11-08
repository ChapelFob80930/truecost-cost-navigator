"""Time-series usage log: privacy whitelist, middleware, failure safety, and the stats shape."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.container import Container
from app.events import Event, EventStore, MemoryEventStore, clean_meta
from app.main import create_app

from .conftest import FakeRepo

NOTE = "Patient needs a bilateral diagnostic mammogram and a stereotactic biopsy of the left breast."
INS = {"insurer": "Univera", "plan": "PPO", "deductible_remaining": 0, "coinsurance_pct": 20, "oop_max_remaining": 100000}


def make(events=None):
    store = events or MemoryEventStore()
    return TestClient(create_app(container=Container.from_repo(FakeRepo(), events=store))), store


# ------------------------------------------------------------------ privacy: only counts and fixed words get through
def test_clean_meta_keeps_whitelisted_numbers_and_fixed_words():
    kept = clean_meta({"codes_found": 3, "insurer": "Univera", "plan_type": "HMO/PPO", "ai_mode": "live", "read_method": "gemini-ocr"})
    assert kept == {"codes_found": 3, "insurer": "Univera", "plan_type": "HMO/PPO", "ai_mode": "live", "read_method": "gemini-ocr"}


def test_clean_meta_drops_free_text_unknown_keys_and_bad_values():
    dirty = {"note": "Jane Doe SSN 123-45-6789", "insurer": "Jane Doe 123-45-6789", "plan_type": "my secret plan",
             "ai_mode": "Jane", "codes_found": "3", "matches": True, "tool_calls": -1, "annual_income": 52000,
             "file_type": "../../etc/passwd"}
    assert clean_meta(dirty) == {}


# ------------------------------------------------------------------ middleware records the right things
def test_estimate_call_is_recorded_with_counts_only():
    client, store = make()
    assert client.post("/estimate", json={"codes": ["77066", "19081"], "insurance": INS}).status_code == 200
    (e,) = list(store._events)
    assert e.endpoint == "estimate" and e.status == 200 and e.latency_ms >= 0
    assert e.meta["insurer"] == "Univera" and e.meta["plan_type"] == "PPO" and e.meta["codes_requested"] == 2
    assert e.meta["hospitals_comparable"] == 2 and e.meta["hospitals_with_estimates"] == 0


def test_note_text_never_reaches_the_event():
    client, store = make()
    client.post("/extract", json={"note": NOTE})
    blob = repr([vars(e) for e in store._events])
    assert "mammogram" not in blob and "biopsy" not in blob and "Patient" not in blob
    assert next(iter(store._events)).meta == {"codes_found": 2}


def test_file_upload_is_recorded_with_type_only():
    client, store = make()
    client.post("/extract/file", files={"file": ("n.txt", NOTE.encode(), "text/plain")})
    e = next(iter(store._events))
    assert e.endpoint == "extract_file" and e.meta == {"codes_found": 2, "read_method": "text", "file_type": "txt"}


def test_errors_are_recorded_with_their_status():
    client, store = make()
    r = client.post("/estimate", json={"codes": ["77066"], "insurance": {**INS, "plan": "Platinum"}})
    assert r.status_code == 422
    assert next(iter(store._events)).status == 422


def test_untracked_and_get_requests_are_not_recorded():
    client, store = make()
    client.get("/health"); client.get("/api/v1/hospitals"); client.get("/api/v1/stats")
    assert list(store._events) == []


def test_agent_calls_record_mode_and_tool_count():
    client, store = make()
    body = {"case": {"uninsured": True, "household_size": 3, "annual_income": 52000}, "demo_mode": True}
    assert client.post("/api/v1/assistance/match", json=body).status_code == 200
    e = next(iter(store._events))
    assert e.endpoint == "assistance" and e.meta["ai_mode"] == "cached" and e.meta["matches"] >= 1


# ------------------------------------------------------------------ the log can never break a request
class ExplodingStore(EventStore):
    def record(self, event):
        raise RuntimeError("database is on fire")

    def stats(self, window_minutes=60, bucket_minutes=5):
        raise RuntimeError("nope")


def test_a_failing_event_store_does_not_break_the_request():
    client, _ = make(ExplodingStore())
    r = client.post("/estimate", json={"codes": ["77066"], "insurance": INS})
    assert r.status_code == 200 and r.json()["results"]


# ------------------------------------------------------------------ stats
def test_stats_shape_and_numbers():
    client, store = make()
    for _ in range(3):
        client.post("/estimate", json={"codes": ["77066"], "insurance": INS})
    client.post("/extract", json={"note": NOTE})
    s = client.get("/api/v1/stats?window_minutes=30&bucket_minutes=5").json()
    assert s["backend"] == "memory" and s["time_series"] is False
    assert s["totals"]["requests"] == 4 and s["totals"]["errors"] == 0 and s["totals"]["p95_ms"] is not None
    assert {r["endpoint"]: r["requests"] for r in s["by_endpoint"]} == {"estimate": 3, "extract": 1}
    assert sum(p["requests"] for p in s["series"]) == 4
    assert 6 <= len(s["series"]) <= 8 and s["last_event_at"]


def test_stats_series_has_no_gaps_and_empty_log_is_fine():
    s = MemoryEventStore().stats(60, 5)
    assert s["totals"]["requests"] == 0 and s["totals"]["p50_ms"] is None and s["last_event_at"] is None
    assert all(p["requests"] == 0 for p in s["series"]) and len(s["series"]) >= 12


def test_old_events_fall_outside_the_window():
    store = MemoryEventStore()
    store.record(Event("estimate", 200, 10, ts=datetime.now(timezone.utc) - timedelta(hours=3)))
    store.record(Event("estimate", 500, 30))
    t = store.stats(60, 5)["totals"]
    assert t["requests"] == 1 and t["errors"] == 1


def test_stats_rejects_bad_bucket_sizes():
    client, _ = make()
    assert client.get("/api/v1/stats?bucket_minutes=7").status_code == 422
    assert client.get("/api/v1/stats?window_minutes=1440&bucket_minutes=1").status_code == 422   # 1,440 buckets
    assert client.get("/api/v1/stats?window_minutes=2").status_code == 422
