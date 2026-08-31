"""Applies every db/migrations/NNN_*.sql file, in filename order, against a
given database. Each file is expected to be idempotent on its own (CREATE
TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS), so re-running this against
an already-migrated database is safe.

Used by:
  - tests/integration/test_customer_repository.py, to bring the isolated
    test database up to the V2 schema before running Postgres-backed tests.
  - manual/CI application against a real dev/staging database:
        python db/migrations/apply.py "$DATABASE_URL"
"""

import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def apply_all(database_url: str) -> None:
    import psycopg

    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        for path in migration_files():
            cur.execute(path.read_text(encoding="utf-8"))
        conn.commit()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python db/migrations/apply.py <DATABASE_URL>", file=sys.stderr)
        raise SystemExit(1)
    apply_all(sys.argv[1])
    print(f"applied {len(migration_files())} migration file(s)")
