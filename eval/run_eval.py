"""Extraction eval. Runs eval/notes.json through each (model, prompt) combo + the keyword baseline.

  python eval/run_eval.py --models gemini-3.8-flash gemini-3.1-flash-lite --prompts v1 v2
  python eval/run_eval.py --baseline-only          # no API calls

Writes eval/results/<timestamp>.json and eval/results/latest.md (the table for the slide).
Allowed codes come from truecost.db if it exists, else a STAND-IN list (ICD codes are placeholders).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import load_dotenv  # noqa: E402
from app.domain.models import MedicalCode  # noqa: E402
from app.repositories.base import PriceRepository  # noqa: E402
from app.services.extraction import KeywordCodeExtractor  # noqa: E402
from scoring import score_note, summarize  # noqa: E402

# STAND-IN allowed list. Replace by building truecost.db with the real 2027 ICD-10-CM codes.
_STAND_IN = [
    ("77065", "CPT", "Diagnostic mammography, unilateral", "diagnostic"),
    ("77066", "CPT", "Diagnostic mammography, bilateral", "diagnostic"),
    ("77067", "CPT", "Screening mammography, bilateral", "diagnostic"),
    ("76641", "CPT", "Ultrasound, breast, complete, unilateral", "diagnostic"),
    ("76642", "CPT", "Ultrasound, breast, limited, unilateral", "diagnostic"),
    ("77048", "CPT", "MRI breast with and without contrast, unilateral", "diagnostic"),
    ("77049", "CPT", "MRI breast with and without contrast, bilateral", "diagnostic"),
    ("19081", "CPT", "Breast biopsy, stereotactic guidance", "diagnostic"),
    ("19083", "CPT", "Breast biopsy, ultrasound guidance", "diagnostic"),
    ("19085", "CPT", "Breast biopsy, MRI guidance", "diagnostic"),
    ("88305", "CPT", "Surgical pathology, gross and microscopic exam", "diagnostic"),
    ("19301", "CPT", "Partial mastectomy (lumpectomy)", "treatment"),
    ("19303", "CPT", "Mastectomy, simple, complete", "treatment"),
    ("38525", "CPT", "Biopsy or excision of deep axillary lymph nodes", "treatment"),
    ("96413", "CPT", "Chemotherapy IV infusion, up to 1 hour", "treatment"),
    ("77385", "CPT", "IMRT treatment delivery, simple", "treatment"),
    ("R92.8", "ICD10", "Other abnormal and inconclusive findings on diagnostic imaging of breast", "diagnosis"),
    ("N63.10", "ICD10", "Unspecified lump in the right breast, unspecified quadrant", "diagnosis"),
    ("N63.20", "ICD10", "Unspecified lump in the left breast, unspecified quadrant", "diagnosis"),
    ("C50.911", "ICD10", "Malignant neoplasm of unspecified site of right female breast", "diagnosis"),
    ("C50.912", "ICD10", "Malignant neoplasm of unspecified site of left female breast", "diagnosis"),
]


class StaticRepo(PriceRepository):
    def __init__(self, codes):
        self._codes = codes

    def list_codes(self, phase=None):
        return [c for c in self._codes if phase in (None, c.phase)]

    def get_codes(self, codes):
        return [c for c in self._codes if c.code in set(codes)]

    def list_hospitals(self): return []
    def find_prices(self, codes, payer=None): return []
    def list_plans(self, payer): return []


def load_repo(db: Path):
    if db.exists():
        from app.repositories.sqlite_repo import SqlitePriceRepository
        repo = SqlitePriceRepository(db)
        if any(c.code_type == "ICD10" for c in repo.list_codes()):
            return repo, f"db:{db.name}"
    return StaticRepo([MedicalCode(*c) for c in _STAND_IN]), "STAND-IN list (no ICD-10 rows in db)"


def run_combo(label, extractor, notes, allowed):
    per_note = []
    for n in notes:
        error = None
        found = []
        for attempt in range(5):   # exponential backoff for 429 / 503
            try:
                found = extractor.extract(n["text"])
                error = None
                break
            except Exception as e:  # noqa: BLE001
                error, found = f"{type(e).__name__}: {str(e)[:200]}", []
                time.sleep(5 * 2 ** attempt)
        raw = getattr(extractor, "last_raw", None) or [{"code": f.code} for f in found]
        out = [{"code": f.code, "confidence": f.confidence, "evidence": f.evidence} for f in found]
        s = score_note(n, out, raw, allowed)
        s["error"], s["details"] = error, out
        per_note.append(s)
        print(f"[{label}] note {n['id']}: {s['output']}{'  ERROR ' + error[:80] if error else ''}", flush=True)
        time.sleep(0.3)
    scored = [p for p in per_note if not p["error"]]   # failed calls are excluded, never counted as "no codes"
    summary = summarize(scored)
    summary["errors"] = len(per_note) - len(scored)
    return {"label": label, "summary": summary, "per_note": per_note}


def markdown(results, notes_meta, allowed_src):
    L = ["# Extraction eval", "", f"Allowed codes: {allowed_src}.  Answer keys: {notes_meta['answer_keys']}", "",
         "## Overall", "", "| Model / prompt | Precision | Recall | Hallucinated-code rate | must_not hits | Conf-cap violations | Perfect notes | API errors |",
         "|---|---|---|---|---|---|---|---|"]
    for r in results:
        s = r["summary"]
        L.append(f"| {r['label']} | {s['precision']:.2f} | {s['recall']:.2f} | {s['hallucinated_code_rate']:.2f} | "
                 f"{s['must_not_include_hits']} | {s['conf_cap_violations']} | {s['notes_perfect']}/{s['notes_total']} | {s['errors']} |")
    L += ["", "## Per note (P = precision, R = recall)", "",
          "| # | Scenario | " + " | ".join(r["label"] for r in results) + " |",
          "|---|---|" + "---|" * len(results)]
    for i, n in enumerate(results[0]["per_note"]):
        cells = []
        for r in results:
            p = r["per_note"][i]
            flags = ("  MUST-NOT:" + ",".join(p["must_not_hits"]) if p["must_not_hits"] else "") + \
                    ("  CAP:" + ",".join(p["conf_cap_violations"]) if p["conf_cap_violations"] else "") + \
                    ("  ERROR" if p["error"] else "")
            cells.append(f"P{p['precision']:.2f} R{p['recall']:.2f} [{', '.join(p['output']) or '-'}]{flags}")
        L.append(f"| {n['id']} | {n['scenario']} | " + " | ".join(cells) + " |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=["gemini-3.8-flash", "gemini-3.1-flash-lite"])
    ap.add_argument("--prompts", nargs="*", default=["v1", "v2"])
    ap.add_argument("--baseline-only", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "truecost.db"))
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    data = json.loads((ROOT / "eval" / "notes.json").read_text(encoding="utf-8"))
    notes = data["notes"]
    repo, src = load_repo(Path(args.db))
    allowed = [c.code for c in repo.list_codes()]

    results = [run_combo("keyword-fallback", KeywordCodeExtractor(repo), notes, allowed)]
    if not args.baseline_only:
        import os
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            sys.exit("GEMINI_API_KEY missing (put it in .env) or pass --baseline-only")
        from app.services.gemini_extractor import GeminiCodeExtractor
        for model in args.models:
            for pv in args.prompts:
                ex = GeminiCodeExtractor(repo, key, model, prompt_version=pv)
                results.append(run_combo(ex.name, ex, notes, allowed))

    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{stamp}.json").write_text(json.dumps(
        {"allowed_source": src, "meta": data["meta"], "results": results}, indent=2), encoding="utf-8")
    md = markdown(results, data["meta"], src)
    (out_dir / "latest.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
