"""Check that what Vercel will ship really works, without needing a Vercel account.

A Vercel service is built from its own folder only (here backend/), so this script copies backend/ to a temporary folder,
runs the entry point (index.py) from THERE, and calls the API. That catches the two classic mistakes: a data file that is
not inside the service folder, and code that assumes the repository layout.

CI runs it in a clean virtual environment that has ONLY backend/requirements.txt installed:

    python scripts/check_vercel_bundle.py [--max-mb 240]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "backend"
REQUIRED_IN_SERVICE = [
    "index.py", "requirements.txt", "app/main.py",
    "data/assistance_programs.json", "data/billing_rights.json", "data/fpl_guidelines.json",
    "data/cached/sarah_assistance.json", "data/cached/sarah_appeal.json",
]

PROBE = r'''
import json, os, sys
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient
from index import app
c = TestClient(app)
out = {"health": c.get("/health").status_code}
out["hospitals"] = len(c.get("/api/v1/hospitals").json())
d = c.post("/api/v1/assistance/match", json={"demo_mode": True, "case": {"household_size": 3, "annual_income": 52000}})
out["demo"] = [d.status_code, len(d.json().get("matches", [])) if d.status_code == 200 else d.text[:120]]
live = c.post("/api/v1/assistance/match", json={"case": {"household_size": 3, "annual_income": 52000}})   # no AI key here: must say so clearly
out["no_ai_key"] = live.status_code
e = c.post("/estimate", json={"codes": ["77066"], "insurance": {"insurer": "Univera", "plan": "PPO", "deductible_remaining": 0, "coinsurance_pct": 20, "oop_max_remaining": 5000}})
out["estimate"] = [e.status_code, len(e.json().get("results", [])) if e.status_code == 200 else e.text[:120]]
print("PROBE " + json.dumps(out))
'''


def main(argv: list[str]) -> int:
    problems: list[str] = []
    max_mb = float(argv[argv.index("--max-mb") + 1]) if "--max-mb" in argv else 240.0

    # 1) the Vercel configuration says what we expect
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    svc = cfg.get("services", {}).get("backend", {})
    if svc.get("root") != "backend" or svc.get("entrypoint") != "index:app":
        problems.append("vercel.json: services.backend must have root 'backend' and entrypoint 'index:app'")
    if cfg.get("rewrites") != [{"source": "/(.*)", "destination": {"service": "backend"}}]:
        problems.append("vercel.json: the only public rewrite must send /(.*) to the backend service")
    for key in ("functions", "regions", "builds", "installCommand", "buildCommand"):
        if key in cfg:
            problems.append(f"vercel.json: '{key}' is not valid at the top level when services are used; move it into the service")

    # 2) run the service from a copy of ONLY its own folder
    tmp = Path(tempfile.mkdtemp(prefix="vercel-service-"))
    try:
        root = tmp / "backend"
        shutil.copytree(SERVICE, root, ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache", "*.pyc", ".env*"))
        for need in REQUIRED_IN_SERVICE:
            if not (root / need).exists():
                problems.append(f"{need} is not inside backend/, so it would be missing on Vercel")
        env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "GEMINI_API_KEY", "PGPASSWORD")}
        env["DB_PATH"] = str(ROOT / "truecost.db")      # stands in for Tiger Data (the SQLite file is not part of the service)
        proc = subprocess.run([sys.executable, "-c", PROBE], cwd=root, env=env, capture_output=True, text=True, timeout=180)
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE ")), None)
        if proc.returncode != 0 or line is None:
            problems.append("the service did not start from its own folder:\n" + (proc.stderr or proc.stdout)[-600:])
        else:
            res = json.loads(line[6:])
            print("probe:", res)
            if res["health"] != 200:
                problems.append(f"/health returned {res['health']}")
            if res["hospitals"] != 7:
                problems.append(f"expected 7 hospitals, got {res['hospitals']}")
            if res["demo"][0] != 200 or not res["demo"][1]:
                problems.append(f"demo assistance failed from the service folder: {res['demo']}")
            if res["no_ai_key"] != 503:
                problems.append(f"without an AI key the assistant should answer 503, got {res['no_ai_key']}")
            if res["estimate"][0] != 200 or not res["estimate"][1]:
                problems.append(f"estimate failed: {res['estimate']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 3) installed size is under Vercel's 250 MB function limit
    site = [Path(p) for p in sys.path if p.endswith("site-packages")]
    if site:
        mb = sum(f.stat().st_size for s in site for f in s.rglob("*") if f.is_file()) / 1024 / 1024
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
