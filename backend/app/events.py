"""Usage events as time-series data.

Every call to the main endpoints is recorded as one small event (when, which endpoint, status, latency and a few
counts). On Tiger Data the events live in a TimescaleDB hypertable, and `GET /api/v1/stats` reads them back with
`time_bucket`. Without Postgres an in-memory store gives the same API (it resets on restart).

Privacy: events never hold note text, incomes, names or any free text. `clean_meta` keeps only whitelisted keys,
and each value must be a number or one of a fixed set of words, so a stray string cannot get through.
Reliability: recording is fire-and-forget on a background thread. A slow or failed database never slows or
breaks a request; if the queue is full the event is dropped.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional

log = logging.getLogger("truecost.events")

TRACKED = {"/extract": "extract", "/extract/file": "extract_file", "/estimate": "estimate", "/simulate": "simulate",
           "/api/v1/assistance/match": "assistance", "/api/v1/appeal/draft": "appeal"}
BUCKETS = (1, 5, 15, 30, 60)
_PLAN = re.compile(r"^(HMO|PPO|POS|Other|Any)(/(HMO|PPO|POS|Other|Any))*$")
_INSURERS = {"Independent Health", "Highmark BCBS", "Univera"}

# key -> validator. Anything not listed, or failing its check, is dropped.
_META_RULES = {
    "codes_found": lambda v: isinstance(v, int) and 0 <= v <= 100,
    "codes_requested": lambda v: isinstance(v, int) and 0 <= v <= 100,
    "hospitals_comparable": lambda v: isinstance(v, int) and 0 <= v <= 50,
    "hospitals_with_estimates": lambda v: isinstance(v, int) and 0 <= v <= 50,
    "tool_calls": lambda v: isinstance(v, int) and 0 <= v <= 20,
    "matches": lambda v: isinstance(v, int) and 0 <= v <= 100,
    "insurer": lambda v: v in _INSURERS,
    "plan_type": lambda v: isinstance(v, str) and bool(_PLAN.match(v)),
    "ai_mode": lambda v: v in ("live", "cached"),
    "read_method": lambda v: v in ("text", "docx", "pdf-text", "gemini-ocr"),
    "file_type": lambda v: v in ("pdf", "png", "jpeg", "webp", "docx", "txt"),
}


def clean_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {k: v for k, v in (meta or {}).items()
            if k in _META_RULES and not isinstance(v, bool) and _META_RULES[k](v)}


@dataclass
class Event:
    endpoint: str
    status: int
    latency_ms: int
    meta: Dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _bucket_start(ts: datetime, bucket_minutes: int) -> datetime:
    step = bucket_minutes * 60
    return datetime.fromtimestamp(int(ts.timestamp()) // step * step, tz=timezone.utc)


def _percentile(values: List[int], p: float) -> Optional[int]:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo))


def _fill(points: Dict[datetime, Dict[str, Any]], start: datetime, end: datetime, bucket_minutes: int) -> List[Dict[str, Any]]:
    """One point per bucket, zeros where nothing happened, so a chart has no gaps."""
    out, cur = [], _bucket_start(start, bucket_minutes)
    while cur <= end:
        p = points.get(cur, {})
        out.append({"bucket": cur.isoformat().replace("+00:00", "Z"), "requests": p.get("requests", 0),
                    "errors": p.get("errors", 0), "avg_ms": p.get("avg_ms")})
        cur += timedelta(minutes=bucket_minutes)
    return out


class EventStore(ABC):
    backend = "memory"
    hypertable = False

    @abstractmethod
    def record(self, event: Event) -> None: ...

    @abstractmethod
    def stats(self, window_minutes: int = 60, bucket_minutes: int = 5) -> Dict[str, Any]: ...

    def close(self) -> None:  # pragma: no cover - optional
        pass


class MemoryEventStore(EventStore):
    """Default when there is no Postgres. Keeps the latest events in memory; resets on restart."""
    backend = "memory"

    def __init__(self, max_events: int = 5000):
        self._events: Deque[Event] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def record(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)

    def stats(self, window_minutes: int = 60, bucket_minutes: int = 5) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        since = now - timedelta(minutes=window_minutes)
        with self._lock:
            events = [e for e in self._events if e.ts >= since]
            latest = self._events[-1].ts if self._events else None
        lat = [e.latency_ms for e in events]
        by_ep: Dict[str, List[int]] = {}
        points: Dict[datetime, List[Event]] = {}
        for e in events:
            by_ep.setdefault(e.endpoint, []).append(e.latency_ms)
            points.setdefault(_bucket_start(e.ts, bucket_minutes), []).append(e)
        series = {b: {"requests": len(es), "errors": sum(1 for e in es if e.status >= 500),
                      "avg_ms": round(sum(e.latency_ms for e in es) / len(es))} for b, es in points.items()}
        return {
            "backend": self.backend, "time_series": False, "window_minutes": window_minutes, "bucket_minutes": bucket_minutes,
            "totals": {"requests": len(events), "errors": sum(1 for e in events if e.status >= 500),
                       "p50_ms": _percentile(lat, 0.5), "p95_ms": _percentile(lat, 0.95),
                       "ai_live": sum(1 for e in events if e.meta.get("ai_mode") == "live"),
                       "ai_cached": sum(1 for e in events if e.meta.get("ai_mode") == "cached")},
            "by_endpoint": sorted(({"endpoint": k, "requests": len(v), "avg_ms": round(sum(v) / len(v))} for k, v in by_ep.items()),
                                  key=lambda r: (-r["requests"], r["endpoint"])),
            "series": _fill(series, since, now, bucket_minutes),
            "last_event_at": latest.isoformat().replace("+00:00", "Z") if latest else None,
        }


_DDL = """
CREATE TABLE IF NOT EXISTS app_events (
  ts          timestamptz NOT NULL DEFAULT now(),
  endpoint    text        NOT NULL,
  status      integer     NOT NULL,
  latency_ms  integer     NOT NULL,
  meta        jsonb       NOT NULL DEFAULT '{}'::jsonb
)"""
_INDEX = "CREATE INDEX IF NOT EXISTS ix_app_events_endpoint_ts ON app_events (endpoint, ts DESC)"
_HYPERTABLE = "SELECT create_hypertable('app_events', 'ts', if_not_exists => TRUE, migrate_data => TRUE)"
_RETENTION = "SELECT add_retention_policy('app_events', INTERVAL '30 days', if_not_exists => TRUE)"


class PostgresEventStore(EventStore):
    """Tiger Data (TimescaleDB). Writes go through a background thread; reads use time_bucket."""
    backend = "tiger"

    def __init__(self, pool, max_queue: int = 2000):
        self._pool = pool
        self._q: "queue.Queue[Event]" = queue.Queue(maxsize=max_queue)
        self.dropped = 0
        self._ensure_schema()
        self._thread = threading.Thread(target=self._worker, name="event-writer", daemon=True)
        self._thread.start()

    def _ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(_DDL)
            conn.execute(_INDEX)
            try:
                conn.execute(_HYPERTABLE)
                self.hypertable = True
                try:
                    conn.execute(_RETENTION)
                except Exception:  # noqa: BLE001 - retention is optional
                    log.warning("could not set the 30-day retention policy")
            except Exception as e:  # noqa: BLE001 - plain Postgres: the table still works
                self.hypertable = False
                log.warning("app_events is a plain table, not a hypertable (%s)", str(e)[:120])

    def record(self, event: Event) -> None:
        try:
            self._q.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until queued events are written (tests and shutdown)."""
        end = time.time() + timeout
        while time.time() < end:
            if self._q.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return False

    def _worker(self) -> None:
        while True:
            e = self._q.get()
            try:
                with self._pool.connection() as conn:
                    conn.execute("INSERT INTO app_events (ts, endpoint, status, latency_ms, meta) VALUES (%s,%s,%s,%s,%s)",
                                 [e.ts, e.endpoint, e.status, e.latency_ms, json.dumps(e.meta)])
            except Exception:  # noqa: BLE001 - logging must never hurt the app
                log.warning("could not write an event", exc_info=True)
            finally:
                self._q.task_done()

    def stats(self, window_minutes: int = 60, bucket_minutes: int = 5) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        since = now - timedelta(minutes=window_minutes)
        bucket = ("time_bucket(make_interval(mins => %s), ts)" if self.hypertable
                  else "date_bin(make_interval(mins => %s), ts, TIMESTAMPTZ '2000-01-01 00:00:00+00')")
        with self._pool.connection() as conn:
            tot = conn.execute(
                "SELECT count(*) AS n, count(*) FILTER (WHERE status >= 500) AS errors, "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50, "
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95, "
                "count(*) FILTER (WHERE meta->>'ai_mode' = 'live') AS ai_live, "
                "count(*) FILTER (WHERE meta->>'ai_mode' = 'cached') AS ai_cached "
                "FROM app_events WHERE ts >= %s", [since]).fetchone()
            by_ep = conn.execute(
                "SELECT endpoint, count(*) AS n, round(avg(latency_ms))::int AS avg_ms FROM app_events "
                "WHERE ts >= %s GROUP BY endpoint ORDER BY n DESC, endpoint", [since]).fetchall()
            rows = conn.execute(
                f"SELECT {bucket} AS bucket, count(*) AS n, count(*) FILTER (WHERE status >= 500) AS errors, "
                "round(avg(latency_ms))::int AS avg_ms FROM app_events WHERE ts >= %s GROUP BY 1 ORDER BY 1",
                [bucket_minutes, since]).fetchall()
            last = conn.execute("SELECT max(ts) AS ts FROM app_events").fetchone()
        points = {r["bucket"].astimezone(timezone.utc): {"requests": r["n"], "errors": r["errors"], "avg_ms": r["avg_ms"]} for r in rows}
        return {
            "backend": self.backend, "time_series": self.hypertable, "window_minutes": window_minutes, "bucket_minutes": bucket_minutes,
            "totals": {"requests": tot["n"], "errors": tot["errors"],
                       "p50_ms": None if tot["p50"] is None else round(tot["p50"]),
                       "p95_ms": None if tot["p95"] is None else round(tot["p95"]),
                       "ai_live": tot["ai_live"], "ai_cached": tot["ai_cached"]},
            "by_endpoint": [{"endpoint": r["endpoint"], "requests": r["n"], "avg_ms": r["avg_ms"]} for r in by_ep],
            "series": _fill(points, since, now, bucket_minutes),
            "last_event_at": last["ts"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if last and last["ts"] else None,
        }
