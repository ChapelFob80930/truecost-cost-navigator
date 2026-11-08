"""Pre-recorded outputs for the demo patient 'Sarah' (no API calls, no Wi-Fi). Produced by scripts/record_demo.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel

from ..config import PROJECT_ROOT
from ..exceptions import DomainError

CACHE_DIR = PROJECT_ROOT / "backend" / "data" / "cached"
T = TypeVar("T", bound=BaseModel)


class CachedOutputMissing(DomainError):
    status_code = 503


def load_cached(name: str, model: Type[T], cache_dir: Path = CACHE_DIR) -> T:
    path = Path(cache_dir) / f"{name}.json"
    if not path.exists():
        raise CachedOutputMissing(f"No pre-recorded demo output '{name}'. Run scripts/record_demo.py once while online.")
    out = model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    out.meta.mode = "cached"                                  # type: ignore[attr-defined]
    out.meta.model = None                                     # type: ignore[attr-defined]
    out.meta.disclaimers = out.meta.disclaimers + ["This is a pre-recorded demo result, not a live AI answer."]  # type: ignore[attr-defined]
    return out
