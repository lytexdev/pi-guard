"""Numbered SQLite migrations with PRAGMA user_version.

Each database file owns its migration list. Steps are callables that use
``execute`` only (never ``executescript``), so one failed step rolls back and
leaves ``user_version`` unchanged. Table and column names are fixed in code.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Sequence

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

Migration = Callable[[sqlite3.Connection], None]


class SchemaTooNewError(RuntimeError):
    """This pi-guard build is older than the database file."""


def check_ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"bad SQL identifier: {name!r}")
    return name


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    check_ident(table)
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column(conn: sqlite3.Connection, table: str, name: str, declaration: str) -> bool:
    """Add a column when missing. ``declaration`` is a fixed string from code."""
    check_ident(table)
    check_ident(name)
    if name in table_columns(conn, table):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    return True


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(conn: sqlite3.Connection, migrations: Sequence[Migration]) -> int:
    """Apply migrations after the current ``user_version``. Returns the new version."""
    version = schema_version(conn)
    target = len(migrations)
    if version > target:
        raise SchemaTooNewError(
            f"database schema version {version} is newer than this pi-guard ({target})"
        )
    for number, step in enumerate(migrations[version:], start=version + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            step(conn)
            conn.execute(f"PRAGMA user_version = {number}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return schema_version(conn)
