"""Emit the database's foreign-key graph as a Mermaid ER diagram.

`docs/data_model.md` is hand-curated: which tables lead, how they are grouped, and what is worth
saying about each. That editorial part is the useful part and a generator cannot write it.

What a generator *can* do is keep the curated picture honest. This prints the complete FK graph
straight out of `PRAGMA foreign_key_list`, so a relationship that exists in the schema and not in
the document shows up as a diff rather than as something nobody noticed. Run it after any schema
change and check the curated diagram still covers what it claims to.

    python scripts/er_diagram.py                    # mermaid, all tables
    python scripts/er_diagram.py --check            # list what docs/data_model.md omits
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "data_model.md"


def foreign_keys(conn: sqlite3.Connection) -> list[tuple[str, str, str, str]]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    found = []
    for table in tables:
        for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
            found.append((table, fk[3], fk[2], fk[4]))  # child, from, parent, to
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=REPO / "data" / "recon_demo.sqlite")
    parser.add_argument("--check", action="store_true", help="report pairs missing from the doc")
    args = parser.parse_args()

    if not args.db.exists():
        sys.exit(f"no database at {args.db}")
    conn = sqlite3.connect(f"file:{args.db.as_posix()}?mode=ro", uri=True)
    try:
        keys = foreign_keys(conn)
    finally:
        conn.close()

    if not args.check:
        print("erDiagram")
        for child, _, parent, _ in keys:
            # Every FK here is "many children to one optional parent": the column is nullable on
            # the child side in each case, which is what lets a record exist before the thing it
            # will eventually point at has been resolved.
            print(f"    {parent} ||--o{{ {child} : \"\"")
        return

    # --check: which (parent, child) pairs does the curated document not draw?
    text = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    drawn = set()
    for line in text.splitlines():
        m = re.match(r"\s*(\w+)\s+\|\|--o[|{]\s+(\w+)\s*:", line)
        if m:
            drawn.add((m.group(1), m.group(2)))
        m = re.match(r"\s*(\w+)\s+\}o--o\|\s+(\w+)\s*:", line)
        if m:
            drawn.add((m.group(2), m.group(1)))  # reversed: child }o--o| parent

    missing = sorted({(p, c) for c, _, p, _ in keys} - drawn)
    if missing:
        print(f"{len(missing)} relationship(s) in the schema and not in {DOC.name}:")
        for parent, child in missing:
            print(f"  {parent} -> {child}")
    else:
        print(f"{DOC.name} covers every foreign key in {args.db.name}.")


if __name__ == "__main__":
    main()
