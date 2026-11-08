from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    db_path: Path
    cors_origins: tuple
    gemini_api_key: Optional[str] = None
    database_url: Optional[str] = None   # Postgres / Tiger Data; if unset the app uses the SQLite file
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_fallback_model: Optional[str] = "gemini-3.5-flash-lite"   # used only if the main model keeps failing; empty = off
    google_client_id: Optional[str] = None       # with the secret, turns on "Connect my Gmail"
    google_client_secret: Optional[str] = None
    google_redirect_uri: str = "http://127.0.0.1:8000/api/v1/gmail/callback"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        return cls(
            db_path=Path(os.getenv("DB_PATH", PROJECT_ROOT / "truecost.db")),
            cors_origins=tuple(os.getenv("CORS_ORIGINS", "*").split(",")),
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            database_url=os.getenv("DATABASE_URL") or None,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            gemini_fallback_model=(os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite") or None),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID") or None,
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET") or None,
            google_redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/v1/gmail/callback"),
        )
