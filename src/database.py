"""
Unified database backend for the Fantasy Football Draft Commander.

- **SQLite** (local dev) — uses built-in ``sqlite3``, stored at ``data/app.db``
- **PostgreSQL** (Vercel / production) — uses ``psycopg2`` when ``DATABASE_URL`` is set

Both backends share the same schema and identical parameterized SQL so
application code never has to think about which one is active.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Prefer the explicit DATABASE_URL env var; fall back to building one from
# individual PG* env vars (set automatically by Vercel's Neon integration).
_RAW_DATABASE_URL: str | None = os.environ.get("DATABASE_URL")


def _build_database_url() -> str | None:
    """Construct a Postgres connection string from individual PG* env vars."""
    pg_host = os.environ.get("PGHOST") or os.environ.get("POSTGRES_HOST")
    pg_port = os.environ.get("PGPORT") or os.environ.get("POSTGRES_PORT", "5432")
    pg_user = os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER")
    pg_pass = os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD")
    pg_db = os.environ.get("PGDATABASE") or os.environ.get("POSTGRES_DATABASE")
    if pg_host and pg_user and pg_pass and pg_db:
        return f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}?sslmode=require"
    return None


DATABASE_URL: str | None = _RAW_DATABASE_URL or _build_database_url()

# SQLite fallback path (relative to this file's parent)
SQLITE_PATH = str(Path(__file__).resolve().parent.parent / "data" / "app.db")


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _is_postgres() -> bool:
    return bool(DATABASE_URL)


def _get_conn() -> sqlite3.Connection | Any:
    """Return a raw DB-API 2.0 connection.

    - Postgres: uses ``psycopg2`` with ``RealDictCursor`` so that
      ``row["column_name"]`` works everywhere.
    - SQLite: uses the built-in ``sqlite3`` module with ``Row`` factory.
    """
    if _is_postgres():
        import psycopg2
        from psycopg2.extras import RealDictCursor

        # Vercel's Neon integration includes ?sslmode=require in the URL.
        # Stripping it first avoids any double-parameter parsing edge cases
        # in psycopg2, then we apply it explicitly via keyword arg.
        clean_url = DATABASE_URL
        if "?sslmode=" in clean_url:
            clean_url = clean_url.split("?")[0]

        conn = psycopg2.connect(
            clean_url,
            sslmode="require",
            connect_timeout=10,
        )
        conn.autocommit = False
        conn.cursor_factory = RealDictCursor
        return conn
    else:
        os.makedirs(Path(SQLITE_PATH).parent, exist_ok=True)
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT    NOT NULL UNIQUE,
    password_hash TEXT  NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS leagues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    data        TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_leagues_user_id ON leagues(user_id);
CREATE INDEX IF NOT EXISTS idx_leagues_name    ON leagues(name);
"""


def init_db() -> None:
    """Create tables and indexes if they don't already exist."""
    conn = _get_conn()
    try:
        if _is_postgres():
            # Postgres-compatible DDL (SERIAL instead of AUTOINCREMENT)
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id          SERIAL PRIMARY KEY,
                        email       TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        created_at  TIMESTAMP NOT NULL DEFAULT NOW()
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS leagues (
                        id          SERIAL PRIMARY KEY,
                        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        name        TEXT NOT NULL,
                        data        TEXT NOT NULL DEFAULT '{}',
                        created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                        updated_at  TIMESTAMP NOT NULL DEFAULT NOW(),
                        UNIQUE(user_id, name)
                    );
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_leagues_user_id ON leagues(user_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_leagues_name ON leagues(name);")
            conn.commit()
        else:
            conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------

def db_create_user(email: str, password_hash: str) -> int:
    """Insert a new user. Returns the new user id."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        if _is_postgres():
            cur.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                (email, password_hash),
            )
            user_id = cur.fetchone()["id"]
        else:
            cur.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, password_hash),
            )
            user_id = cur.lastrowid
        conn.commit()
        return user_id
    finally:
        conn.close()


def db_get_user_by_email(email: str) -> Optional[dict]:
    """Return ``{id, email, password_hash, created_at}`` or ``None``."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        sql = (
            "SELECT id, email, password_hash, created_at FROM users WHERE email = ?"
            if not _is_postgres()
            else "SELECT id, email, password_hash, created_at FROM users WHERE email = %s"
        )
        cur.execute(sql, (email,))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()


def db_user_exists(email: str) -> bool:
    return db_get_user_by_email(email) is not None


# ---------------------------------------------------------------------------
# League operations
# ---------------------------------------------------------------------------

def db_save_league(user_id: int, league_name: str, league_dict: dict) -> None:
    """Insert or replace a league for a user."""
    data_json = json.dumps(league_dict, default=str)
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    try:
        cur = conn.cursor()
        if _is_postgres():
            cur.execute(
                """INSERT INTO leagues (user_id, name, data, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, name) DO UPDATE SET
                       data = EXCLUDED.data,
                       updated_at = EXCLUDED.updated_at""",
                (user_id, league_name, data_json, now, now),
            )
        else:
            cur.execute(
                "SELECT id FROM leagues WHERE user_id = ? AND name = ?",
                (user_id, league_name),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    "UPDATE leagues SET data = ?, updated_at = ? WHERE id = ?",
                    (data_json, now, existing["id"]),
                )
            else:
                cur.execute(
                    "INSERT INTO leagues (user_id, name, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, league_name, data_json, now, now),
                )
        conn.commit()
    finally:
        conn.close()


def db_load_league(user_id: int, league_name: str) -> Optional[dict]:
    """Load a league dict for a user by name. Returns ``None`` if not found."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        sql = (
            "SELECT data FROM leagues WHERE user_id = ? AND name = ?"
            if not _is_postgres()
            else "SELECT data FROM leagues WHERE user_id = %s AND name = %s"
        )
        cur.execute(sql, (user_id, league_name))
        row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row["data"])
    finally:
        conn.close()


def db_list_leagues(user_id: int) -> list[dict]:
    """Return metadata about all leagues for a user."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        sql = (
            "SELECT name, data, created_at, updated_at FROM leagues WHERE user_id = ? ORDER BY updated_at DESC"
            if not _is_postgres()
            else "SELECT name, data, created_at, updated_at FROM leagues WHERE user_id = %s ORDER BY updated_at DESC"
        )
        cur.execute(sql, (user_id,))
        rows = cur.fetchall()
        results = []
        for row in rows:
            data = json.loads(row["data"])
            results.append({
                "name": row["name"],
                "num_teams": data.get("num_teams", 0),
                "scoring_format": data.get("scoring_format", "PPR"),
                "is_active": data.get("is_active", True),
                "completed": data.get("completed", False),
                "overall_pick": data.get("overall_pick", 0),
                "current_round": data.get("current_round", 0),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return results
    finally:
        conn.close()


def db_delete_league(user_id: int, league_name: str) -> bool:
    """Delete a league. Returns ``True`` if a row was deleted."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        sql = (
            "DELETE FROM leagues WHERE user_id = ? AND name = ?"
            if not _is_postgres()
            else "DELETE FROM leagues WHERE user_id = %s AND name = %s"
        )
        cur.execute(sql, (user_id, league_name))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def db_get_all_users() -> list[dict]:
    """Return all users (for migration / admin use)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, email, created_at FROM users ORDER BY id")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
