"""Shared and private SQLite schema migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pi_guard.addons.knowledge import Knowledge
from pi_guard.addons.medical import MedicalRecords
from pi_guard.detection.faces import BiometricStore
from pi_guard.network.wgpeers import PeerStore
from pi_guard.storage.db import Database
from pi_guard.storage.migrate import (
    SchemaTooNewError,
    add_column,
    apply_migrations,
    check_ident,
    schema_version,
    table_columns,
)
from pi_guard.storage.schema import APP_TABLES, MIGRATIONS, VERSION, _v1


def test_fresh_database_has_every_app_table(tmp_path: Path) -> None:
    db = Database(tmp_path / "fresh.db")
    assert db.schema_version == VERSION == db.schema_target
    names = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert names == APP_TABLES
    assert "permissions" in table_columns(db._conn, "web_users")
    assert "kind" in table_columns(db._conn, "wg_peers")
    db.close()


def test_reopening_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first = Database(path)
    first.set("mode", "away")
    first.close()
    second = Database(path)
    assert second.schema_version == VERSION
    assert second.get("mode") == "away"
    second.close()


def test_v1_database_gains_store_tables_and_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("BEGIN")
    _v1(conn)
    conn.execute(
        """
        CREATE TABLE wg_peers (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            address TEXT NOT NULL UNIQUE, public_key TEXT NOT NULL UNIQUE,
            private_key TEXT NOT NULL, preshared_key TEXT NOT NULL,
            full_tunnel INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL)
        """
    )
    conn.execute(
        """
        CREATE TABLE web_users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL, totp TEXT, totp_pending TEXT,
            created TEXT NOT NULL)
        """
    )
    conn.execute("PRAGMA user_version = 1")
    conn.execute("COMMIT")
    conn.close()

    db = Database(path)
    assert db.schema_version == VERSION
    assert "kind" in table_columns(db._conn, "wg_peers")
    assert "permissions" in table_columns(db._conn, "web_users")
    assert "settings_history" in {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    store = PeerStore(db, tmp_path, b"k" * 32)
    store.add("Anna", kind="android")
    assert store.list()[0]["kind"] == "android"
    db.close()


def test_failed_migration_does_not_bump_version(tmp_path: Path) -> None:
    path = tmp_path / "broken.db"
    conn = sqlite3.connect(path, isolation_level=None)

    def bad(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        apply_migrations(conn, (bad,))
    assert schema_version(conn) == 0
    conn.close()


def test_schema_newer_than_code_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(f"PRAGMA user_version = {VERSION + 5}")
    conn.close()
    with pytest.raises(SchemaTooNewError):
        Database(path)


def test_add_column_rejects_bad_identifiers() -> None:
    with pytest.raises(ValueError):
        check_ident("web_users; DROP TABLE users")
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE t (id INTEGER)")
    assert add_column(conn, "t", "name", "TEXT")
    assert not add_column(conn, "t", "name", "TEXT")
    with pytest.raises(ValueError):
        add_column(conn, "t", "bad-name", "TEXT")
    conn.close()


def test_migration_list_is_the_published_version() -> None:
    assert len(MIGRATIONS) == VERSION >= 2


def test_biometric_store_migrates_old_file(tmp_path: Path) -> None:
    path = tmp_path / "bio.db"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(
        "CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
        "enrolled_at TEXT NOT NULL, enrolled_by TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE samples (id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL, "
        "embedding BLOB NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    store = BiometricStore(path)
    cols = table_columns(store._conn, "samples")
    assert {"pose", "light", "source"} <= cols
    assert "rejections" in {
        row[0] for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    store._conn.close()


def test_medical_drops_legacy_links(tmp_path: Path) -> None:
    path = tmp_path / "medical.db"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(
        "CREATE TABLE records (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
        "data BLOB NOT NULL, updated TEXT NOT NULL)"
    )
    conn.execute("CREATE TABLE links (hash TEXT PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    (tmp_path / "medical.key").write_bytes(b"\0" * 32)
    records = MedicalRecords(tmp_path)
    names = {
        row[0]
        for row in records._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert names == {"records"}
    assert schema_version(records._conn) == 2
    records._conn.close()


def test_knowledge_gets_a_version(tmp_path: Path) -> None:
    store = Knowledge(tmp_path / "k.db")
    assert schema_version(store._db) == 1
    store._db.close()
