# TrueCost – backend (FastAPI)

```
cd backend
../.venv/bin/uvicorn app.main:create_app --factory --reload   # http://127.0.0.1:8000/docs
../.venv/bin/python -m pytest -q                              # unit + API tests (no DB needed)
```
Needs `truecost.db` in the project root (`scripts/build_db.py`), or set `DB_PATH`.

| Endpoint | Purpose |
|---|---|
| `POST /extract` | note -> `{codes:[{code,type,description,confidence,evidence}], extractor}` (Gemini if `GEMINI_API_KEY` set, else keyword fallback) |
| `POST /estimate` | `{codes, insurance{insurer,plan,...}, patient_zip}` -> `results[]` (cheapest first) + `caveats[]` |
| `POST /simulate` | same + optional `hospital`, `pathway[{code,month,step,repeat}]` -> `timeline[]`, `oop_max_reached_month` |
| `GET /api/v1/codes`, `/hospitals`, `/plans?insurer=` | reference data |

Keys live in the git-ignored `.env` at the repo root (see `.env.example`).

Layers: `api/` (HTTP) -> `services/` (use cases) -> `domain/` (entities, calculators, rate selector) and
`repositories/` (data access). `container.py` wires concrete classes together.
