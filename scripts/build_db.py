"""Build truecost.db (SQLite) for TrueCost from the raw files in ./data.

Tables: prices, hospitals, codes  (schema from the team spec).

Run (from project root):  .venv/bin/python scripts/build_db.py

Rules
- Only target breast-cancer CPT/HCPCS codes (+ MS-DRG 582/583) are kept.
- Facility fees only (professional billing rows are skipped).
- Payers limited to Independent Health, Highmark BCBS, Univera; Medicare/Medicaid/Essential plans dropped.
- One rate per (hospital, code, setting, payer, plan). Preference order:
    1) imaging code + note "Radiology"   2) no modifier, no note   3) no modifier   4) has modifier (TC...)
  ties -> first row in the file.
- Payer-less rows are kept with payer NULL (gross charge + cash price, for uninsured mode).
- DRG/formula rates are not dollars -> rate_type 'percent_of_charges', negotiated_rate NULL.
- The untrusted services-charges XLSX is NOT used.
"""
import csv
import io
import itertools
import json
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("TRUECOST_DATA", ROOT / "data"))   # folder holding the raw price files
DB = Path(os.environ.get("TRUECOST_DB", ROOT / "truecost.db"))  # output file (set to a temp path to test a rebuild)
csv.field_size_limit(10**9)

# ------------------------------------------------------------------ target codes
CODES = [  # code, type, description, phase
    ("77065", "CPT", "Diagnostic mammography, unilateral", "diagnostic"),
    ("77066", "CPT", "Diagnostic mammography, bilateral", "diagnostic"),
    ("77067", "CPT", "Screening mammography, bilateral", "diagnostic"),
    ("76641", "CPT", "Breast ultrasound, complete, one breast", "diagnostic"),
    ("76642", "CPT", "Breast ultrasound, limited, one breast", "diagnostic"),
    ("77048", "CPT", "Breast MRI with/without contrast, one breast", "diagnostic"),
    ("77049", "CPT", "Breast MRI with/without contrast, both breasts", "diagnostic"),
    ("19081", "CPT", "Breast biopsy, stereotactic guidance, first lesion", "diagnostic"),
    ("19083", "CPT", "Breast biopsy, ultrasound guidance, first lesion", "diagnostic"),
    ("19085", "CPT", "Breast biopsy, MRI guidance, first lesion", "diagnostic"),
    ("88305", "CPT", "Surgical pathology, tissue exam (level IV)", "diagnostic"),
    ("19301", "CPT", "Partial mastectomy (lumpectomy)", "treatment"),
    ("19303", "CPT", "Simple complete mastectomy", "treatment"),
    ("38525", "CPT", "Biopsy/excision of deep axillary lymph nodes", "treatment"),
    ("96413", "CPT", "Chemotherapy IV infusion, first hour", "treatment"),
    ("77385", "CPT", "Radiation therapy delivery (IMRT), simple", "treatment"),
    ("582", "MS-DRG", "Mastectomy for malignancy with CC/MCC (inpatient)", "treatment"),
    ("583", "MS-DRG", "Mastectomy for malignancy without CC/MCC (inpatient)", "treatment"),
]
TARGET = {c[0] for c in CODES}
IMAGING = {c for c in TARGET if c[:2] in ("76", "77") and len(c) == 5}
CPT_TYPES = {"CPT", "HCPCS", "CPT/HCPCS"}

# ------------------------------------------------------------------ hospitals
# lat/lng are APPROXIMATE (typed from memory, ~street level) - verify before the demo.
HOSPITALS = {
    "Roswell Park": ("665 Elm St, Buffalo, NY 14203", 42.8977, -78.8667),
    "ECMC": ("462 Grider St, Buffalo, NY 14215", 42.9276, -78.8304),
    "Mercy Hospital of Buffalo": ("565 Abbott Rd, Buffalo, NY 14220", 42.8480, -78.8132),
    "Kenmore Mercy Hospital": ("2950 Elmwood Ave, Kenmore, NY 14217", 42.9780, -78.8807),
    "Mount St. Mary's Hospital": ("5300 Military Rd, Lewiston, NY 14092", 43.1541, -79.0325),
}

CMS_FILES = {
    "Mercy Hospital of Buffalo": "16-0756336_Mercy-Hospital-of-Buffalo_StandardCharges.csv",
    "Kenmore Mercy Hospital": "16-0762843_Kenmore-Mercy-Hospital_StandardCharges.csv",
    "Mount St. Mary's Hospital": "16-1523353_Mount-St-Marys-Hospital_StandardCharges.csv",
}
# Kaleida publishes ONE system-wide file for all campuses -> same prices under each site name.
# lat/lng APPROXIMATE - verify before the demo.
KALEIDA_FILE = "161533232_kaleida-health_standardcharges.json"
KALEIDA_SITES = {
    "Buffalo General Medical Center": ("100 High St, Buffalo, NY 14203", 42.9009, -78.8660),
    "Millard Fillmore Suburban Hospital": ("1540 Maple Rd, Williamsville, NY 14221", 42.9935, -78.7302),
}
ROSWELL_FILE = "161552370_roswell-park-cancer-institute_standardcharges.json"
ECMC_FILE = "ECMC-Charge-Master-01012026.csv"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)



def safe_lines(path, skip=0):
    """Yield binary lines. Reads go through `tail -c +N` because Python itself gets 'Operation timed
    out' (macOS privacy block) on files under ~/Desktop while shell tools do not. If the stream
    breaks, restart from the last byte read."""
    if os.name == "nt" or not shutil.which("tail"):  # Windows / no `tail`: plain buffered read
        with open(path, "rb", buffering=1 << 22) as fh:
            for n, line in enumerate(fh, 1):
                if n > skip:
                    yield line
        return
    offset, n = 0, 0
    while True:
        proc = subprocess.Popen(["tail", "-c", f"+{offset + 1}", str(path)], stdout=subprocess.PIPE,
                                bufsize=1 << 20)
        try:
            for line in proc.stdout:
                offset += len(line)
                n += 1
                if n > skip:
                    yield line
            if proc.wait() == 0:
                return
            log(f"  tail failed; retrying from byte {offset:,}")
        finally:
            proc.kill()
        time.sleep(5)


def read_bytes(path):
    return b"".join(safe_lines(path))

# ------------------------------------------------------------------ payer mapping
EXCLUDE = re.compile(r"MEDICARE|MEDICAID|\bMCD\b|\bMCR\b|ESSENTIAL|SENIOR|CHILD HEALTH|MEDISOURCE|ENCOMPASS 65|PASSPORT ADV", re.I)


def map_payer(payer, plan):
    """Return the canonical insurer name, or None if not one of our 3 commercial insurers."""
    p = (payer or "").upper()
    if EXCLUDE.search(p) or EXCLUDE.search((plan or "").upper()):
        return None
    if "INDEPENDENT HEALTH" in p:
        return "Independent Health"
    if "UNIVERA" in p:
        return "Univera"
    if re.search(r"\bBCBS\b|BLUE CROSS|BLUE SHIELD|HIGHMARK", p):
        return "Highmark BCBS"
    return None


ACRONYMS = {"Ppo": "PPO", "Hmo": "HMO", "Pos": "POS", "Iha": "IHA", "Bc": "BC", "Cdm": "CDM",
            "Ooa": "OOA", "Nova": "NOVA", "Aqua": "AQUA", "Yls": "YLS"}


def clean_plan(plan):
    s = re.sub(r"\s*\[\d+\]\s*", " ", plan or "").strip()
    if not s:
        return None
    return re.sub(r"[A-Za-z]+", lambda m: ACRONYMS.get(m.group(0).title(), m.group(0).title()), s)


def num(x):
    try:
        v = float(str(x).replace("$", "").replace(",", "").strip())
        return v
    except (TypeError, ValueError):
        return None


def score(code, modifier, dept):
    if code in IMAGING and dept and dept.lower() == "radiology":
        return 0
    if not modifier and not dept:
        return 1
    if not modifier:
        return 2
    return 3


class Collector:
    """Keeps the best-scoring row per (hospital, code, setting, payer, plan)."""

    def __init__(self):
        self.best = {}
        self.n = 0

    def add(self, row, sc):
        key = (row["hospital"], row["code"], row["setting"], row["payer"], row["plan"])
        self.n += 1
        cur = self.best.get(key)
        if cur is None or sc < cur[0]:
            self.best[key] = (sc, row)


# ------------------------------------------------------------------ Catholic Health CMS CSVs
LINE_RX = re.compile(rb'(?:^|,)"?(' + "|".join(sorted(TARGET)).encode() + rb')"?(?=,|$)')


def read_cms(hospital, fname, col):
    path = DATA / fname
    log(f"{hospital}: scanning {path.name}")
    header = next(csv.reader([list(itertools.islice(safe_lines(path), 3))[2].decode("utf-8", "replace").strip()]))
    ix = {h: i for i, h in enumerate(header)}
    W = len(header)
    seen = hits = 0
    if True:
        for line in safe_lines(path, skip=3):
            seen += 1
            if not LINE_RX.search(line):
                continue
            r = next(csv.reader([line.decode("utf-8", "replace")]))
            r = (r + [""] * W)[:W]
            get = lambda c: r[ix[c]].strip() if c in ix else ""
            if get("billing_class").lower() == "professional":
                continue
            code = ctype = None
            for k in range(1, 5):
                c, t = get(f"code|{k}"), get(f"code|{k}|type").upper()
                if c in TARGET and (t in CPT_TYPES or (t == "MS-DRG" and c in ("582", "583"))):
                    code, ctype = c, ("MS-DRG" if t == "MS-DRG" else t)
                    break
            if not code:
                continue
            setting = "inpatient" if (ctype == "MS-DRG" or get("setting").lower() == "inpatient") else "outpatient"
            raw_payer, raw_plan = get("payer_name"), get("plan_name")
            payer = None
            plan = None
            if raw_payer:
                payer = map_payer(raw_payer, raw_plan)
                if not payer:
                    continue
                plan = clean_plan(raw_plan)
            dollar = num(get("standard_charge|negotiated_dollar"))
            pct = num(get("standard_charge|negotiated_percentage"))
            algo = get("standard_charge|negotiated_algorithm")
            gross, cash = num(get("standard_charge|gross")), num(get("standard_charge|discounted_cash"))
            if payer:
                if dollar is not None:
                    rtype, rate, note = "negotiated_dollar", dollar, None
                elif pct is not None or algo:
                    rtype, rate = "percent_of_charges", None
                    note = (f"{pct}% of charges" if pct is not None else "") + ((" | " + algo[:120]) if algo else "")
                else:
                    continue
            else:
                if gross is None and cash is None:
                    continue
                rtype, rate, note = None, None, None
            modifier = get("modifiers")
            row = dict(hospital=hospital, code=code, code_type=ctype, description=get("description"),
                       setting=setting, payer=payer, plan=plan, rate_type=rtype, negotiated_rate=rate,
                       cash_price=cash, gross_charge=gross,
                       notes="; ".join(x for x in [f"modifier {modifier}" if modifier else "", note or "",
                                                   get("standard_charge|methodology")] if x) or None)
            col.add(row, score(code, modifier, None))
            hits += 1
    log(f"{hospital}: {seen:,} lines scanned, {hits:,} matching rows")


# ------------------------------------------------------------------ Roswell Park JSON
def load_json_file(path):
    return json.loads(read_bytes(Path(path)).decode("utf-8-sig"))


def read_roswell(col):
    read_cms_json(col, DATA / ROSWELL_FILE, ["Roswell Park"])


def read_cms_json(col, path, hospitals):
    """CMS JSON price file (Roswell Park, Kaleida...). Prices are added once per name in `hospitals`
    (a system-wide file can cover several campuses that share one price list)."""
    d = load_json_file(path)
    for name in hospitals:
        _read_cms_json_one(col, d, name)


def _read_cms_json_one(col, d, hospital):
    gross = {}  # code -> (gross, cash, description, setting)
    entries = []
    for it in d["standard_charge_information"]:
        match = None
        for c in it.get("code_information", []):
            t, v = c.get("type", "").upper(), str(c.get("code", ""))
            if v in TARGET and (t in CPT_TYPES or (t == "MS-DRG" and v in ("582", "583"))):
                match = (v, "MS-DRG" if t == "MS-DRG" else t)
                break
        if not match:
            continue
        entries.append((match, it))
        # entry type 1: gross charge + cash price, no insurer rates -> merged by code
        for sc in it.get("standard_charges", []):
            if sc.get("gross_charge") is not None and match[0] not in gross:
                gross[match[0]] = (sc.get("gross_charge"), sc.get("discounted_cash"), it.get("description"))
    for (code, ctype), it in entries:
        g = gross.get(code, (None, None, None))
        for sc in it.get("standard_charges", []):
            mods = sc.get("modifier_code") or []
            modifier = ",".join(mods) if isinstance(mods, list) else str(mods)
            setting = "inpatient" if (ctype == "MS-DRG" or sc.get("setting") == "inpatient") else "outpatient"
            for p in sc.get("payers_information") or []:
                payer = map_payer(p.get("payer_name"), p.get("plan_name"))
                if not payer:
                    continue
                dollar = num(p.get("standard_charge_dollar"))
                pct = num(p.get("standard_charge_percentage"))
                if dollar is not None:
                    rtype, rate, note = "negotiated_dollar", dollar, None
                elif pct is not None or p.get("standard_charge_algorithm"):
                    rtype, rate = "percent_of_charges", None
                    note = f"{pct}% of charges" if pct is not None else str(p.get("standard_charge_algorithm"))[:120]
                else:
                    continue
                dept = p.get("additional_payer_notes")
                row = dict(hospital=hospital, code=code, code_type=ctype,
                           description=it.get("description") or g[2], setting=setting, payer=payer,
                           plan=clean_plan(p.get("plan_name")), rate_type=rtype, negotiated_rate=rate,
                           cash_price=g[1], gross_charge=g[0],
                           notes="; ".join(x for x in [f"modifier {modifier}" if modifier else "",
                                                       f"dept {dept}" if dept else "", note or "",
                                                       p.get("methodology") or ""] if x) or None)
                col.add(row, score(code, modifier, dept))
    for code, (gc, cash, desc) in gross.items():  # payer-less gross/cash row per code
        ctype = next(t for (c, t), _ in entries if c == code)
        col.add(dict(hospital=hospital, code=code, code_type=ctype, description=desc, setting="outpatient",
                     payer=None, plan=None, rate_type=None, negotiated_rate=None, cash_price=cash,
                     gross_charge=gc, notes="gross charge / cash price"), 1)
    log(f"{hospital}: {len(entries)} matching entries")


# ------------------------------------------------------------------ ECMC chargemaster (gross only)
def read_ecmc(col):
    rows = list(csv.reader(io.StringIO(read_bytes(DATA / ECMC_FILE).decode("utf-8", "replace"))))
    start = next(i for i, r in enumerate(rows) if r and r[0].strip() == "CDM") + 1
    n = 0
    for r in rows[start:]:
        r = (r + [""] * 10)[:10]
        price = num(r[9])
        if price is None:
            continue
        for src in (r[6], r[7]):  # CPT4 / HCPC columns; value may carry modifiers e.g. 77066TC
            s = src.strip().upper()
            m = re.match(r"^(\d{5})((?:[A-Z0-9]{2}){0,3})$", s)
            if not m or m.group(1) not in TARGET:
                continue
            code, suffix = m.group(1), m.group(2)
            col.add(dict(hospital="ECMC", code=code, code_type="CPT", description=r[1].strip(),
                         setting="outpatient", payer=None, plan=None, rate_type=None, negotiated_rate=None,
                         cash_price=None, gross_charge=price,
                         notes=("chargemaster gross only; no insurer rates" +
                                (f"; source code {s}" if suffix else ""))), score(code, suffix, None))
            n += 1
            break
    log(f"ECMC: {n} matching rows")


# ------------------------------------------------------------------ ICD-10 codes
def icd10_rows():
    out = []
    path = DATA / "Code Descriptions" / "icd10cm_codes_2027.txt"
    for ln in open(path, encoding="utf-8", errors="replace"):
        parts = ln.rstrip("\n").split(None, 1)
        if len(parts) != 2:
            continue
        code, desc = parts
        if code == "R928" or code.startswith(("N63", "C50")):
            out.append((code[:3] + ("." + code[3:] if len(code) > 3 else ""), "ICD10", desc.strip(), "diagnosis"))
    return out


# ------------------------------------------------------------------ main
SCHEMA = """
DROP TABLE IF EXISTS prices; DROP TABLE IF EXISTS hospitals; DROP TABLE IF EXISTS codes;
CREATE TABLE prices (
  hospital TEXT, code TEXT, code_type TEXT, description TEXT, setting TEXT, payer TEXT, plan TEXT,
  rate_type TEXT, negotiated_rate REAL, cash_price REAL, gross_charge REAL, notes TEXT);
CREATE TABLE hospitals (hospital TEXT PRIMARY KEY, address TEXT, lat REAL, lng REAL);
CREATE TABLE codes (code TEXT PRIMARY KEY, code_type TEXT, description TEXT, phase TEXT);
"""
COLS = ["hospital", "code", "code_type", "description", "setting", "payer", "plan", "rate_type",
        "negotiated_rate", "cash_price", "gross_charge", "notes"]

def main():
    col = Collector()
    read_roswell(col)
    read_ecmc(col)
    hospitals = dict(HOSPITALS)
    if (DATA / KALEIDA_FILE).exists():
        read_cms_json(col, DATA / KALEIDA_FILE, list(KALEIDA_SITES))
        hospitals.update(KALEIDA_SITES)
    for hosp, fname in CMS_FILES.items():
        read_cms(hosp, fname, col)
    rows = [r for _, r in col.best.values()]
    log(f"{col.n:,} candidate rows -> {len(rows):,} after one-rate-per-plan selection")

    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    con.executemany(f"INSERT INTO prices VALUES ({','.join('?' * len(COLS))})",
                    [tuple(r[c] for c in COLS) for r in rows])
    con.executemany("INSERT INTO hospitals VALUES (?,?,?,?)", [(k, *v) for k, v in hospitals.items()])
    con.executemany("INSERT INTO codes VALUES (?,?,?,?)", CODES + icd10_rows())
    con.execute("CREATE INDEX idx_prices_code_payer ON prices(code, payer)")
    con.execute("CREATE INDEX idx_prices_hospital ON prices(hospital, code)")
    con.commit()
    for t in ("prices", "hospitals", "codes"):
        log(t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], "rows")
    con.close()
    log("DONE ->", DB)


if __name__ == "__main__":
    main()
