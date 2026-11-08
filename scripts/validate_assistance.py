"""Audit the curated assistance + billing-rights files. Exit code 1 if a row claims to be verified but isn't."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.assistance import DEFAULT_PROGRAMS_PATH, audit_billing_rights, audit_programs, load_programs  # noqa: E402

rights = ROOT / "backend" / "data" / "billing_rights.json"
progs = load_programs()
print(f"assistance_programs.json: {len(progs)} rows, {sum(p.verified for p in progs)} verified, "
      f"{sum(not p.verified for p in progs)} placeholder (ignored by the app)")
problems = audit_programs(DEFAULT_PROGRAMS_PATH) + audit_billing_rights(rights)
for p in problems:
    print("PROBLEM:", p)
print("OK" if not problems else f"{len(problems)} problem(s)")
sys.exit(1 if problems else 0)
