"""Export a recon sqlite database to CSV, or to a single replayable .sql dump.

    python scripts/export_db.py                        # demo db -> exports/demo/*.csv
    python scripts/export_db.py --db data/recon_full.sqlite --out exports/full
    python scripts/export_db.py --sql                  # one exports/demo.sql instead
    python scripts/export_db.py --table verdict        # just one table, to stdout

There is no sqlite3 CLI on this machine, which is why this exists.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/recon_demo.sqlite")
    ap.add_argument("--out", default=None, help="output directory (CSV mode)")
    ap.add_argument("--sql", action="store_true", help="one .sql dump instead of CSVs")
    ap.add_argument("--table", default=None, help="one table, written to stdout as CSV")
    args = ap.parse_args()

    db = (ROOT / args.db) if not Path(args.db).is_absolute() else Path(args.db)
    if not db.exists():
        print(f"no such database: {db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    if args.table:
        cur = conn.execute(f'SELECT * FROM "{args.table}"')
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow([c[0] for c in cur.description])
        w.writerows(cur)
        return 0

    if args.sql:
        dest = ROOT / (args.out or f"exports/{db.stem}.sql")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as fh:
            for line in conn.iterdump():
                fh.write(line + "\n")
        print(f"{dest}  ({dest.stat().st_size / 1e6:.1f} MB)")
        return 0

    outdir = ROOT / (args.out or f"exports/{db.stem}")
    outdir.mkdir(parents=True, exist_ok=True)
    for name in tables(conn):
        cur = conn.execute(f'SELECT * FROM "{name}"')
        cols = [c[0] for c in cur.description]
        path = outdir / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            n = 0
            for row in cur:
                w.writerow(row)
                n += 1
        print(f"{name:28} {n:>7} rows  ->  {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
