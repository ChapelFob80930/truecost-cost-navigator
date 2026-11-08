"""Prefilled Gmail compose link: the patient reviews the letter in TrueCost, then sends it themselves from Gmail.

No Google sign-in, no stored credentials, nothing leaves the browser until THEY press Send in Gmail.
Keep this in step with `gmailComposeUrl` in lovable/api.ts.
"""
from __future__ import annotations

from typing import Tuple
from urllib.parse import quote

COMPOSE = "https://mail.google.com/mail/?view=cm&fs=1"
MAX_URL = 1800      # very long links can fail in browsers/Gmail; above this we prefill To+Subject only and ask them to paste


def compose_url(to: str, subject: str, body: str) -> Tuple[str, bool]:
    """Returns (url, body_included). If the letter doesn't fit in a link, the body is left out and body_included is False."""
    base = f"{COMPOSE}&to={quote((to or '').strip(), safe='@')}&su={quote(subject or '', safe='')}"
    full = f"{base}&body={quote(body or '', safe='')}"
    return (full, True) if len(full) <= MAX_URL else (base, False)
