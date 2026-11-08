# Evaluation results

What we measured, how, and what we did **not** measure. Raw tables are in `eval/results/`.

## 1. Code extraction (note -> billing codes)

15 synthetic clinical notes (`eval/notes.json`), each with an answer key of codes that must appear and codes that must not.
Run it yourself: `python eval/run_eval.py --models gemini-3.1-flash-lite --prompts v1 v2` (needs `GEMINI_API_KEY`).
`--baseline-only` runs the keyword baseline with no API calls.

| Extractor | Precision | Recall | Invented codes | Forbidden-code hits | Perfect notes |
|---|---|---|---|---|---|
| Keyword rules (baseline) | 0.87 | 0.62 | 0.00 | 2 | 6 / 15 |
| Gemini 3.1 Flash-Lite, first prompt (v1) | 0.91 | 1.00 | 0.00 | 2 | 13 / 15 |
| **Gemini 3.1 Flash-Lite, final prompt (v2)** | **1.00** | **1.00** | **0.00** | **0** | **15 / 15** |

Gemini 3.5 Flash-Lite gave the same numbers (v1 13/15, v2 15/15).

**What the notes test:** clean cases, screening vs diagnostic, one-sided vs both sides, biopsy with no guidance stated,
"rule out cancer" vs confirmed cancer, shorthand, history that must not be coded, a service outside our list, and two
prompt-injection notes (one hidden mid-note).

**What the first prompt got wrong, and what fixed it**
- Note 14 (injection hidden mid-note): it followed the hidden instruction and returned a mastectomy code (19303) and a
  cancer diagnosis (C50.911) that the note never supported. The v2 prompt tells the model the note is data, and this
  note is now in the eval so a regression would show up. The code-side filters below would **not** have caught it,
  because the injected text is itself part of the note and could be quoted as "evidence". The fix is the prompt, checked by the eval.
- Note 12 (laterality unclear): it was more confident than it should have been (above the 0.5 cap). The v2 prompt caps
  confidence when laterality is unclear.

**Code-side filters that apply to every answer, whatever the prompt:**
a code must be on the allowed list; its evidence quote must appear word for word in the note and be 15 words or fewer;
confidence is clamped to 0 to 1. Invented-code rate stayed at 0.00 for every Gemini run.

## 2. Assistant agents

10 synthetic cases, 5 per agent, live Gemini, deterministic checks only (no AI judge): `python eval/run_agent_eval.py`.

| Result | Value |
|---|---|
| Cases passed | 10 / 10 |
| Checks passed | 94 / 94 |
| Cases where a guard replaced the AI's wording | 0 |

Cases cover income above and below a program's limit, no income given, an uninsured patient, a prompt injection through a
hospital name, a clean denial, a denied code the patient never confirmed, a letter with no deadline or claim number,
a prompt injection inside a letter, and an identifier-heavy letter.
The assistance cases use **fake program rows** (`eval/agent_fixtures/programs_TEST.json`), so they test the logic, not the real table.

## 3. Software tests

178 unit and API tests pass (`cd backend && python -m pytest -q`; 5 more run only against a live Postgres).
They cover the cost math, plan-type matching, labelled estimates, file upload and file-type checks, the PHI scrubber,
the tool loop and its limits, output guards, explanations, usage logging, and that the README still matches the code.

## 4. What we did not measure

- **No clinician has checked the answer keys.** The 15 notes and the v2 prompt rules were written by the same author in the
  same session, so 15/15 shows the guards work on these cases, not that it will hold on real notes.
- **Reading photos and scans (OCR) was not scored.** We tried a rendered photo and a scanned PDF of one note; both read correctly.
  Handwriting and blurry photos are untested, which is why the app shows the text it read for the patient to check.
- **Price accuracy was not audited** against the hospitals' original files beyond rebuilding the database from them (all 1,401
  price rows matched). Labelled estimates for unpublished rates have no ground truth to compare with.
- **Sample size is small.** 15 notes and 10 agent cases are enough to catch the failures above, not to claim a production error rate.
