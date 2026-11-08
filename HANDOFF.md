# TrueCost – handoff (paste this into a new chat)

## Project
**TrueCost** ("Know before you owe"): hackathon app for breast cancer patients in Buffalo / Western NY.
Patient uploads a doctor's note -> LLM suggests billing codes -> patient confirms -> enters insurance
(insurer, plan, deductible left, coinsurance %, out-of-pocket max left) -> app ranks local hospitals by
estimated out-of-pocket cost and shows savings. 24-hour hackathon, 4-person team.
Scope: breast cancer only; insurers Independent Health, Highmark BCBS, Univera; outpatient diagnostic
phase is the focus. Inpatient (MS-DRG 582/583) shows "estimate unavailable, contact hospital".
Caveats to state in the pitch: facility fees only, estimates, synthetic data only, coinsurance plans assumed.
AI angle: LLM reads/explains (note -> codes, explain-my-bill chat, insurance-card photo); plain code does all money math. No model training.

## Folder: <project root>  (renamed from hackathon-101)
```
data/        raw files (git-ignored): 3 Catholic Health CSVs (4-8 GB each), Roswell Park JSON, ECMC CSV,
             Kaleida JSON, Code Descriptions (ICD-10)
scripts/build_db.py      builds truecost.db from data/ (~10 min, scans ~17 GB). Reads files through `tail`
                         because Python gets "Operation timed out" on files under ~/Desktop.
scripts/add_kaleida.py   adds Kaleida prices to an existing truecost.db
notebooks/               build_db.ipynb, download_kaleida.ipynb, preview_data.ipynb  (kernel: "Python (TrueCost)")
backend/                 FastAPI app (layers: api -> services -> domain + repositories), 25 passing tests
truecost.db              SQLite: prices, hospitals, codes
.venv/                   rebuilt after the rename
```
Run API:  `cd backend && ../.venv/bin/uvicorn app.main:create_app --factory --reload`  (docs at /docs)
Run tests: `cd backend && ../.venv/bin/python -m pytest -q`

## Database (truecost.db) - current state
- prices: hospital, code, code_type, description, setting, payer, plan, rate_type, negotiated_rate, cash_price, gross_charge, notes
- Hospitals (7): Roswell Park (115 rows, 18 codes), Buffalo General (165, 18), Millard Fillmore Suburban (165, 18),
  Mercy Hospital of Buffalo / Kenmore Mercy / Mount St. Mary's (316 rows, only 10 codes each), ECMC (8 rows, gross charge only).
- Rules: target CPT/HCPCS codes + MS-DRG 582/583 only; facility fees only; payers mapped to the 3 insurers (Medicare/Medicaid/Essential dropped);
  one rate per hospital+code+setting+payer+plan (prefer Radiology note for imaging, then no modifier, then TC).
- Kaleida's two sites share one system-wide price list (identical prices). The 3 Catholic hospitals also share identical prices.
- Untrusted 10minutemail XLSX and Medicare inpatient data were deleted.

## Backend (FastAPI)
Endpoints: GET /api/v1/codes, /hospitals, /plans?payer=; POST /api/v1/extract (keyword fallback extractor;
swap in an LLM `CodeExtractor` in container.py); POST /api/v1/estimate (insured or self_pay; ranks hospitals;
order of codes matters because earlier services use up the deductible; returns low-high rate range, per-line
breakdown, disclaimers, optional distance).
Classes: InsuredCostCalculator / SelfPayCostCalculator (Strategy), PlanAwareMedianSelector, EstimateService,
SqlitePriceRepository (abstract PriceRepository), KeywordCodeExtractor, Container.

## Open issues / decisions
1. Independent Health imaging rule: Radiology-tagged rate ($216.56 for 77066 at Roswell) vs untagged ($307.90). Team example said $307.90. Undecided.
2. Catholic hospitals only have 10 of 18 codes (no MRI 77048, 19085, lumpectomy, mastectomy, lymph node, chemo 96413, radiation 77385). Not yet investigated - may be under other code types.
3. Sanity-check prices vs hospital sites: Roswell came out ~$1,000 cheaper than Catholic hospitals on 4 diagnostic codes; Roswell cash price for 77066 is $545 vs $58 at Catholic hospitals.
4. Hospital lat/lng and Kaleida addresses were typed from memory - verify.
5. Project name TrueCost not checked for trademark/domain conflicts.

## Next steps
1. Verify Kaleida rows (python/sqlite check), fix issues 2-3 above.
2. LLM extraction: Claude-based CodeExtractor + 5-10 synthetic notes with answer keys + accuracy script.
3. Frontend (Streamlit): upload -> confirm codes -> insurance -> results (+ map).
4. Extra LLM features: explain-my-bill chat, insurance card photo, financial assistance matcher.
5. Pitch: "Meet Sarah" story, caveats slide, backup demo video.
