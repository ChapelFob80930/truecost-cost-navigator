"""Record the pre-recorded 'Sarah' outputs used by demo mode. Run once while online:  python scripts/record_demo.py"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.appeal_agent import run_appeal_agent  # noqa: E402
from app.agents.assistance_agent import run_assistance_agent  # noqa: E402
from app.agents.cached import CACHE_DIR  # noqa: E402
from app.agents.llm import GeminiChat  # noqa: E402
from app.config import load_dotenv  # noqa: E402
from app.domain.case import Case, CaseEstimate  # noqa: E402

load_dotenv(ROOT / ".env")
model = GeminiChat(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"))

SARAH = Case(
    confirmed_codes=["77065", "76642", "19083", "88305"], insurer="Univera", plan="PPO",
    deductible_remaining=1200, coinsurance_pct=20, oop_max_remaining=4000, household_size=3, annual_income=52000,
    estimates=[CaseEstimate(hospital="Buffalo General Medical Center", patient_oop=1660, total_allowed=3501, complete=True),
               CaseEstimate(hospital="Roswell Park", patient_oop=1846, total_allowed=4428, complete=True),
               CaseEstimate(hospital="ECMC", patient_oop=0, total_allowed=0, complete=False)])
DENIAL = ("Dear Sarah Demo, claim number DEMO-0001: we denied CPT 19083 (ultrasound-guided breast biopsy) on 03/04/2026 "
          "because the service was not medically necessary. Member ID: DEMO12345. Your plan: Univera PPO Plus. "
          "Please send the radiology report if you want us to review this.")

CACHE_DIR.mkdir(parents=True, exist_ok=True)
a = run_assistance_agent(model, SARAH)
(CACHE_DIR / "sarah_assistance.json").write_text(a.model_dump_json(indent=2), encoding="utf-8")
b = run_appeal_agent(model, SARAH, DENIAL, patient_facts={"patient_name": "Sarah Demo"})
(CACHE_DIR / "sarah_appeal.json").write_text(b.model_dump_json(indent=2), encoding="utf-8")
print("recorded:", a.meta.error, "|", b.meta.error)
