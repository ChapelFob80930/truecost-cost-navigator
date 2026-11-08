# TrueCost – know before you owe: breast cancer cost navigator for Western New York

Builds `truecost.db` (SQLite: `prices`, `hospitals`, `codes`) from hospital price-transparency files.

```
data/                raw downloads (git-ignored): 3 Catholic Health CSVs, Roswell Park JSON, ECMC CSV, ICD-10 codes
scripts/build_db.py  builds truecost.db  ->  .venv/bin/python scripts/build_db.py
notebooks/           build_db.ipynb (build + checks), preview_data.ipynb (first 5 rows of each file)
truecost.db  output database
```

Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
