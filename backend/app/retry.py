"""Retry for temporary AI-provider failures (503 "high demand", 429 rate limits, timeouts).

Only transient errors are retried; anything else (bad request, auth) fails at once so real bugs are not hidden.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Sequence, TypeVar

log = logging.getLogger("truecost.retry")
T = TypeVar("T")
TRANSIENT_CODES = {429, 500, 502, 503, 504}
TRANSIENT_WORDS = ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "OVERLOADED", "HIGH DEMAND", "TIMED OUT", "TIMEOUT", "CONNECTION RESET")


def is_transient(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int) and code in TRANSIENT_CODES:
        return True
    text = str(exc).upper()
    return any(w in text for w in TRANSIENT_WORDS)


def retry_transient(fn: Callable[[], T], delays: Sequence[float] = (1.0, 2.5)) -> T:
    """Call fn; on a transient error wait and try again, once per delay. Raises the last error if all attempts fail."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if attempt >= len(delays) or not is_transient(e):
                raise
            log.warning("temporary AI error, retrying in %.1fs: %s", delays[attempt], str(e)[:120])
            time.sleep(delays[attempt])
            attempt += 1
