"""Email the appeal letter from the PATIENT'S OWN Gmail, only after they approve it in the UI.

Flow: the draft is written in the app; the patient clicks "Connect my Gmail" -> Google's consent screen -> our callback stores their refresh token on the
server, keyed by a random session id that only their browser holds. "Create draft in my Gmail" saves the letter as a real
Gmail draft; "Approve and send" then updates that draft with the final approved text and sends it.

Rules enforced here in code (the LLM is never involved and never sees this text, the tokens or the address):
- the recipient is whatever the user typed; nothing here can choose or invent an address,
- an explicit `approved=True` is required on every send,
- a letter that still has [PLACEHOLDER] blanks is refused,
- the only Google permission requested to act is `gmail.compose` (create drafts and send; it cannot read the inbox),
- tokens stay on the server and are never returned to the browser; the patient can disconnect at any time.

Tokens are kept in memory, so a server restart means reconnecting. That is intentional for a demo: nothing sensitive is
written to disk. A real product would use an encrypted store.
"""
from __future__ import annotations

import base64
import re
import secrets
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx

from ..exceptions import DomainError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
SCOPES = "openid email https://www.googleapis.com/auth/gmail.compose"
STATE_TTL = 600            # seconds the consent screen may stay open
MAX_PENDING = 500

_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9 _/'-]{1,40}\]")


class MailerUnavailable(DomainError):
    status_code = 503

    def __init__(self):
        super().__init__("Connecting Gmail isn't set up on this server. Download the draft and send it yourself.")


class NotConnected(DomainError):
    status_code = 401

    def __init__(self):
        super().__init__("Connect your Gmail first, then try again.")


class SendRefused(DomainError):
    status_code = 422


class SendFailed(DomainError):
    status_code = 502


def validate_outgoing(to: str, subject: str, body: str, approved: bool) -> None:
    if not approved:
        raise SendRefused("Nothing was sent: the letter has not been approved.")
    if not _EMAIL.match((to or "").strip()):
        raise SendRefused("Enter a valid email address to send to.")
    if not (subject or "").strip() or not (body or "").strip():
        raise SendRefused("The subject and the letter can't be empty.")
    blanks = sorted(set(_PLACEHOLDER.findall(body)))
    if blanks:
        raise SendRefused("Fill in these blanks before sending: " + ", ".join(blanks))
    if "\n" in subject or "\r" in subject:
        raise SendRefused("The subject must be one line.")


def validate_draft(to: str, subject: str, body: str) -> None:
    if (to or "").strip() and not _EMAIL.match(to.strip()):
        raise SendRefused("That doesn't look like a valid email address.")
    if not (subject or "").strip() or not (body or "").strip():
        raise SendRefused("The subject and the letter can't be empty.")
    if "\n" in subject or "\r" in subject:
        raise SendRefused("The subject must be one line.")


@dataclass
class Connection:
    email: str
    refresh_token: str


class GmailService:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, http: Optional[httpx.Client] = None):
        self._id, self._secret, self._redirect = client_id, client_secret, redirect_uri
        self._http = http or httpx.Client(timeout=20)
        self._lock = threading.Lock()
        self._pending: Dict[str, Tuple[str, float]] = {}      # oauth state -> (session id, expires at)
        self._connections: Dict[str, Connection] = {}         # session id -> connection

    # ---------------------------------------------------------------- connect
    def begin(self) -> Tuple[str, str]:
        """Start a connection. Returns (session_id, google_consent_url). The session id is the browser's secret."""
        session_id, state = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            self._pending = {s: v for s, v in self._pending.items() if v[1] > now}
            if len(self._pending) >= MAX_PENDING:
                raise SendFailed("Too many people are connecting right now. Try again in a minute.")
            self._pending[state] = (session_id, now + STATE_TTL)
        query = urlencode({"client_id": self._id, "redirect_uri": self._redirect, "response_type": "code",
                           "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": state})
        return session_id, f"{AUTH_URL}?{query}"

    def complete(self, state: str, code: str) -> str:
        """Called from Google's redirect. Exchanges the code, stores the token. Returns the connected email."""
        with self._lock:
            entry = self._pending.pop(state or "", None)           # single use
        if entry is None or entry[1] < time.time():
            raise SendRefused("This connection link expired. Go back and click Connect again.")
        try:
            r = self._http.post(TOKEN_URL, data={"code": code, "client_id": self._id, "client_secret": self._secret,
                                                 "redirect_uri": self._redirect, "grant_type": "authorization_code"})
            if r.status_code != 200 or not r.json().get("refresh_token"):
                raise SendFailed("Google did not give us permission to send. Please try connecting again.")
            tok = r.json()
            info = self._http.get(USERINFO_URL, headers={"Authorization": f"Bearer {tok['access_token']}"})
            email = info.json().get("email") if info.status_code == 200 else None
        except httpx.HTTPError as e:
            raise SendFailed(f"Couldn't reach Google ({type(e).__name__}). Please try again.") from e
        if not email:
            raise SendFailed("Couldn't confirm which Gmail account was connected. Please try again.")
        with self._lock:
            self._connections[entry[0]] = Connection(email=email, refresh_token=tok["refresh_token"])
        return email

    def status(self, session_id: Optional[str]) -> Optional[str]:
        """The connected email for this browser session, or None. Never returns a token."""
        with self._lock:
            c = self._connections.get(session_id or "")
        return c.email if c else None

    def disconnect(self, session_id: Optional[str]) -> None:
        with self._lock:
            c = self._connections.pop(session_id or "", None)
        if c:
            try:
                self._http.post(REVOKE_URL, data={"token": c.refresh_token})   # best effort
            except httpx.HTTPError:
                pass

    # ---------------------------------------------------------------- draft + send
    def _conn(self, session_id: Optional[str]) -> Connection:
        with self._lock:
            c = self._connections.get(session_id or "")
        if c is None:
            raise NotConnected()
        return c

    def _authed(self, session_id: Optional[str], c: Connection, method: str, url: str, payload: dict) -> httpx.Response:
        """One Google call with a fresh access token. Network trouble -> SendFailed; revoked permission -> NotConnected."""
        try:
            t = self._http.post(TOKEN_URL, data={"client_id": self._id, "client_secret": self._secret,
                                                 "refresh_token": c.refresh_token, "grant_type": "refresh_token"})
            if t.status_code != 200:
                self.disconnect(session_id)
                raise NotConnected()           # permission was revoked or expired: ask them to connect again
            return self._http.request(method, url, headers={"Authorization": f"Bearer {t.json()['access_token']}"}, json=payload)
        except httpx.HTTPError as e:
            raise SendFailed(f"Couldn't reach Gmail ({type(e).__name__}). Nothing was sent.") from e

    @staticmethod
    def _raw(sender: str, to: str, subject: str, body: str) -> str:
        msg = EmailMessage()
        if to.strip():
            msg["To"] = to.strip()
        msg["From"], msg["Subject"] = sender, subject.strip()
        msg.set_content(body)
        return base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def create_draft(self, session_id: Optional[str], to: str, subject: str, body: str) -> Tuple[str, str]:
        """Save the letter as a draft in the patient's own Gmail. Sends nothing. Returns (draft_id, from_email)."""
        c = self._conn(session_id)
        r = self._authed(session_id, c, "POST", DRAFTS_URL, {"message": {"raw": self._raw(c.email, to, subject, body)}})
        if r.status_code != 200:
            raise SendFailed("Gmail did not accept the draft. Nothing was saved.")
        return r.json().get("id", ""), c.email

    def send(self, session_id: Optional[str], to: str, subject: str, body: str,
             draft_id: Optional[str] = None) -> Tuple[str, str]:
        """Send from the patient's own account. With a draft_id, the draft is first updated to the approved text, then sent
        (so no stray draft is left behind). Returns (message_id, from_email)."""
        c = self._conn(session_id)
        raw = self._raw(c.email, to, subject, body)
        if draft_id:
            u = self._authed(session_id, c, "PUT", f"{DRAFTS_URL}/{draft_id}", {"message": {"raw": raw}})
            if u.status_code != 200:
                raise SendFailed("Couldn't update the Gmail draft with your approved text. Nothing was sent.")
            r = self._authed(session_id, c, "POST", f"{DRAFTS_URL}/send", {"id": draft_id})
        else:
            r = self._authed(session_id, c, "POST", SEND_URL, {"raw": raw})
        if r.status_code != 200:
            raise SendFailed("Gmail did not accept the message. Nothing was sent.")
        return r.json().get("id", ""), c.email
