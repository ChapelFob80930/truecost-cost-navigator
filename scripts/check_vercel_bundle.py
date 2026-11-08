"""Check that what Vercel will ship really works, without needing a Vercel account.

CI runs this inside a clean virtual environment that has ONLY api/requirements.txt installed (what Vercel installs), so a
missing dependency or a missing data file fails here instead of after a deploy.

    python scripts/check_vercel_bundle.py [--max-mb 240]
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

REQUIRED_AT_RUNTIME = [
    "truecost.db",
    "backend/data/assistance_programs.json",
    "backend/data/billing_rights.json",
    "backend/data/fpl_guidelines.json",
    "backend/data/cached/sarah_assistance.json",
    "backend/data/cached/sarah_appeal.json",
]


def expand_braces(pattern: str) -> list[str]:
    if pattern.startswith("{") and pattern.endswith("}"):
        return pattern[1:-1].split(",")
    return [pattern]


def main(argv: list[str]) -> int:
    problems: list[str] = []
    max_mb = float(argv[argv.index("--max-mb") + 1]) if "--max-mb" in argv else 240.0

    # 1) every data file the app reads at run time is covered by vercel.json includeFiles
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    fn = cfg["functions"]["api/index.py"]
    included: set[str] = set()
    for pat in expand_braces(fn["includeFiles"]):
        included.update(Path(p).as_posix() for p in glob.glob(pat, recursive=True))
    for need in REQUIRED_AT_RUNTIME:
        if not (ROOT / need).exists():
            problems.append(f"{need} does not exist in the repo")
        elif need not in included:
            problems.append(f"{need} is not covered by vercel.json includeFiles, so it would be missing on Vercel")

    # 2) the function imports with only the runtime dependencies, and answers
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("GEMINI_API_KEY", None)           # no database and no AI key needed to prove the packaging
    try:
        from fastapi.testclient import TestClient
        from api.index import app
        client = TestClient(app)
        h = client.get("/health")
        if h.status_code != 200 or h.json().get("status") != "ok":
            problems.append(f"/health failed: {h.status_code} {h.text[:100]}")
        hosp = client.get("/api/v1/hospitals").json()
        if len(hosp) != 7:
            problems.append(f"expected 7 hospitals, got {len(hosp)}")
        demo = client.post("/api/v1/assistance/match", json={"demo_mode": True, "case": {"household_size": 3, "annual_income": 52000}})
        if demo.status_code != 200 or not demo.json().get("matches"):
            problems.append(f"demo assistance failed: {demo.status_code}")
        est = client.post("/estimate", json={"codes": ["77066"], "insurance": {"insurer": "Univera", "plan": "PPO", "deductible_remaining": 0,
                                                                              "coinsurance_pct": 20, "oop_max_remaining": 5000}})
        if est.status_code != 200 or not est.json().get("results"):
            problems.append(f"estimate failed: {est.status_code}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"could not import or call the function: {type(e).__name__}: {e}")

    # 3) installed size is under Vercel's 250 MB function limit
    site = [Path(p) for p in sys.path if p.endswith("site-packages")]
    if site:
        total = sum(f.stat().st_size for s in site for f in s.rglob("*") if f.is_file())
        mb = total / 1024 / 1024
        print(f"installed dependencies: {mb:.0f} MB (limit {max_mb:.0f} MB)")
        if mb > max_mb:
            problems.append(f"dependencies are {mb:.0f} MB, over the {max_mb:.0f} MB budget")

    if problems:
        print("Vercel bundle check FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("Vercel bundle check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
