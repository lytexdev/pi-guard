"""Schema steps for the shared ``pi-guard.db``.

Version 1 is the original core tables. Version 2 folds every store table that
used to create itself with ``CREATE TABLE IF NOT EXISTS`` into one migration,
including columns that older installs added with ad-hoc ``ALTER TABLE``.
"""

from __future__ import annotations

import sqlite3

from pi_guard.storage.migrate import Migration, add_column

# Expected after the latest migration. Tests pin this list.
APP_TABLES = frozenset(
    {
        "kv",
        "users",
        "invites",
        "events",
        "audit",
        "web_sessions",
        "web_keyfiles",
        "web_account",
        "web_users",
        "web_recovery",
        "web_tokens",
        "web_factors",
        "web_credentials",
        "web_profiles",
        "wg_peers",
        "mobile_devices",
        "mobile_track",
        "push_subscriptions",
        "push_action_tokens",
        "emergency_contacts",
        "alerts",
        "agents",
        "jobs",
        "net_blocks",
        "kid_profiles",
        "kid_devices",
        "settings_history",
    }
)


def _v1(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE users (
            provider TEXT NOT NULL,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL,
            language TEXT,
            layout TEXT NOT NULL DEFAULT 'simple',
            notifications INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            PRIMARY KEY (provider, user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE invites (
            code TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            created_by TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,
            type TEXT NOT NULL,
            camera TEXT,
            data TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX events_ts ON events (ts)")
    conn.execute(
        """
        CREATE TABLE audit (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX audit_ts ON audit (ts)")


def _v2(conn: sqlite3.Connection) -> None:
    """Dashboard and add-on tables that used to be created in store constructors."""
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS web_sessions (
            id_hash TEXT PRIMARY KEY, username TEXT NOT NULL, csrf TEXT NOT NULL,
            created TEXT NOT NULL, last_seen TEXT NOT NULL, ip TEXT NOT NULL,
            agent TEXT NOT NULL, pending INTEGER NOT NULL DEFAULT 0)
        """,
        """
        CREATE TABLE IF NOT EXISTS web_keyfiles (
            name TEXT PRIMARY KEY, hash TEXT NOT NULL, created TEXT NOT NULL,
            last_used TEXT)
        """,
        """
        CREATE TABLE IF NOT EXISTS web_account (
            id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL,
            password_hash TEXT NOT NULL, must_change INTEGER NOT NULL, totp TEXT,
            totp_pending TEXT, updated TEXT NOT NULL)
        """,
        """
        CREATE TABLE IF NOT EXISTS web_users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL, totp TEXT, totp_pending TEXT,
            created TEXT NOT NULL, permissions TEXT NOT NULL DEFAULT '[]')
        """,
        """
        CREATE TABLE IF NOT EXISTS web_recovery (hash TEXT PRIMARY KEY)
        """,
        """
        CREATE TABLE IF NOT EXISTS web_tokens (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, hash TEXT NOT NULL UNIQUE,
            scope TEXT NOT NULL, created TEXT NOT NULL, last_used TEXT)
        """,
        """
        CREATE TABLE IF NOT EXISTS web_factors (name TEXT PRIMARY KEY, value TEXT NOT NULL)
        """,
        """
        CREATE TABLE IF NOT EXISTS web_credentials (
            id TEXT PRIMARY KEY, public_key TEXT NOT NULL, sign_count INTEGER NOT NULL,
            name TEXT NOT NULL, created TEXT NOT NULL, last_used TEXT)
        """,
        """
        CREATE TABLE IF NOT EXISTS web_profiles (
            username TEXT PRIMARY KEY COLLATE NOCASE, data TEXT NOT NULL)
        """,
        """
        CREATE TABLE IF NOT EXISTS wg_peers (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            address TEXT NOT NULL UNIQUE, public_key TEXT NOT NULL UNIQUE,
            private_key TEXT NOT NULL, preshared_key TEXT NOT NULL,
            full_tunnel INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT '')
        """,
        """
        CREATE TABLE IF NOT EXISTS mobile_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            platform TEXT NOT NULL, username TEXT NOT NULL, token TEXT NOT NULL UNIQUE,
            created TEXT NOT NULL, last_seen TEXT, info TEXT, vpn_peer INTEGER)
        """,
        """
        CREATE TABLE IF NOT EXISTS mobile_track (
            device_id INTEGER NOT NULL, ts TEXT NOT NULL, latitude REAL NOT NULL,
            longitude REAL NOT NULL, accuracy REAL)
        """,
        "CREATE INDEX IF NOT EXISTS mobile_track_device ON mobile_track (device_id, ts)",
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
            language TEXT NOT NULL, username TEXT NOT NULL, created TEXT NOT NULL)
        """,
        """
        CREATE TABLE IF NOT EXISTS push_action_tokens (
            hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires TEXT NOT NULL)
        """,
        """
        CREATE TABLE IF NOT EXISTS emergency_contacts (
            id INTEGER PRIMARY KEY, data TEXT NOT NULL, created TEXT NOT NULL)
        """,
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY, ts TEXT NOT NULL, type TEXT NOT NULL, camera TEXT,
            snapshot TEXT, acked_at TEXT, acked_by TEXT, escalated_at TEXT)
        """,
        "CREATE INDEX IF NOT EXISTS alerts_open ON alerts (acked_at)",
        """
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            token_hash TEXT NOT NULL, created TEXT NOT NULL, last_seen TEXT,
            info TEXT NOT NULL DEFAULT '{}')
        """,
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, run_at TEXT NOT NULL,
            args TEXT NOT NULL)
        """,
        """
        CREATE TABLE IF NOT EXISTS net_blocks (
            id INTEGER PRIMARY KEY, domain TEXT NOT NULL, device TEXT,
            label TEXT NOT NULL DEFAULT '', until TEXT, created TEXT NOT NULL,
            UNIQUE (domain, device))
        """,
        """
        CREATE TABLE IF NOT EXISTS kid_profiles (
            id INTEGER PRIMARY KEY, data TEXT NOT NULL, created TEXT NOT NULL)
        """,
        """
        CREATE TABLE IF NOT EXISTS kid_devices (
            mac TEXT PRIMARY KEY,
            profile INTEGER NOT NULL REFERENCES kid_profiles(id) ON DELETE CASCADE,
            name TEXT NOT NULL DEFAULT '')
        """,
        """
        CREATE TABLE IF NOT EXISTS settings_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
            source TEXT NOT NULL, changes TEXT NOT NULL)
        """,
    ):
        conn.execute(statement)
    # Pre-1.10 tables may already exist without these columns.
    add_column(conn, "web_users", "permissions", "TEXT NOT NULL DEFAULT '[]'")
    add_column(conn, "wg_peers", "kind", "TEXT NOT NULL DEFAULT ''")


MIGRATIONS: tuple[Migration, ...] = (_v1, _v2)
VERSION = len(MIGRATIONS)
