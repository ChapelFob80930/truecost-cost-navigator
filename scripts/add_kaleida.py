"""Add Kaleida Health prices to an EXISTING truecost.db (no need to rebuild everything).

1. Download the file first (notebooks/download_kaleida.ipynb) into data/.
2. Run:  .venv/bin/python scripts/add_kaleida.py

Safe to re-run: it deletes the Kaleida rows it added before and inserts them again.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_db as b  # noqa: E402


def main():
    path = b.DATA / b.KALEIDA_FILE
    if not path.exists():
        sys.exit(f"Missing {path}\nRun notebooks/download_kaleida.ipynb first.")
    if not b.DB.exists():
        sys.exit(f"Missing {b.DB}\nRun scripts/build_db.py first.")

    col = b.Collector()
    b.read_cms_json(col, path, list(b.KALEIDA_SITES))
    rows = [r for _, r in col.best.values()]
    b.log(f"{col.n:,} candidate rows -> {len(rows):,} after one-rate-per-plan selection")

    if not rows:
        d = b.load_json_file(path)
        sys.exit("No target-code rows found. Top-level keys in the file: "
                 f"{list(d)[:15] if isinstance(d, dict) else type(d)}\n"
                 "The file may use a different schema - send me this message and I'll adapt the parser.")

    names = list(b.KALEIDA_SITES)
    con = sqlite3.connect(b.DB)
    con.execute(f"DELETE FROM prices WHERE hospital IN ({','.join('?' * len(names))})", names)
    con.executemany(f"INSERT INTO prices VALUES ({','.join('?' * len(b.COLS))})",
                    [tuple(r[c] for c in b.COLS) for r in rows])
    con.executemany("INSERT OR REPLACE INTO hospitals VALUES (?,?,?,?)",
                    [(k, *v) for k, v in b.KALEIDA_SITES.items()])
    con.commit()
    for name, n, codes in con.execute(
            f"SELECT hospital, COUNT(*), COUNT(DISTINCT code) FROM prices WHERE hospital IN ({','.join('?' * len(names))}) GROUP BY hospital", names):
        b.log(f"{name}: {n} price rows, {codes} codes")
    con.close()
    b.log("DONE - Kaleida added to", b.DB)


if __name__ == "__main__":
    main()
