"""SQLite migration runner for the business-ontology store.

Migrations are plain SQL files applied in version order and tracked with
``PRAGMA user_version``. Re-applying is a no-op, so startup can call
``apply_migrations`` unconditionally.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
MIGRATIONS = {1: "0001_init.sql"}


def _connect(db):
    """Return ``(connection, owns_connection)`` for a path or connection."""
    if hasattr(db, "execute"):
        return db, False
    conn = sqlite3.connect(str(db))
    return conn, True


def current_version(db) -> int:
    """Return the schema version recorded in ``PRAGMA user_version``."""
    conn, close = _connect(db)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        if close:
            conn.close()


def apply_migrations(db=":memory:", migrations_dir=None) -> int:
    """Apply pending migrations to *db* and return the resulting version.

    *db* may be an open ``sqlite3.Connection`` or a database path. The call is
    idempotent: a database already at ``SCHEMA_VERSION`` is left untouched.
    """
    directory = Path(migrations_dir) if migrations_dir is not None else MIGRATIONS_DIR
    conn, close = _connect(db)
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        for target in sorted(MIGRATIONS):
            if target <= version:
                continue
            path = directory / MIGRATIONS[target]
            if not path.is_file():
                raise FileNotFoundError(f"migration file missing: {path}")
            conn.executescript(path.read_text(encoding="utf-8"))
            # The target is a module constant, never user input.
            conn.execute(f"PRAGMA user_version = {int(target)}")
            conn.commit()
            version = target
        return version
    finally:
        if close:
            conn.close()


# Conventional aliases used by callers/tests.
apply = apply_migrations
version = current_version
