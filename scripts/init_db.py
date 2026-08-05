"""Initialize the PlusMinus SQLite store from the schema in SPEC.md §3.

Usage:
    python scripts/init_db.py
"""

import sys
from pathlib import Path

# Allow running as `python scripts/init_db.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


def main() -> None:
    db.init_db()
    with db.get_conn() as conn:
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
    target = db.get_active_target()
    print(f"Initialized {db.DB_PATH}")
    print("Tables:", ", ".join(tables))
    print("Active default target:", target)


if __name__ == "__main__":
    main()
