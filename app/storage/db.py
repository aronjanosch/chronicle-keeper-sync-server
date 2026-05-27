"""SQLite database helpers for the CK sync server.

Schema differences vs. the standalone app's DB:
- ``artifacts.content TEXT`` stores text directly (no ``file_path``).
- ``sessions.speakers_json`` stores speaker labels in the sessions table.
- No ``provider_keys`` table (LLM keys stay client-side, never sent here).

Future: swap ``get_connection`` for an async Postgres driver when scale demands it.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


DEFAULT_DB_FILENAME = "chronicle_keeper_sync.db"

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS config (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS campaigns (
        campaign_id          TEXT PRIMARY KEY,
        name                 TEXT NOT NULL,
        next_session_number  INTEGER NOT NULL DEFAULT 1,
        system               TEXT,
        gm                   TEXT,
        setting              TEXT,
        default_language     TEXT,
        players_json         TEXT NOT NULL DEFAULT '[]',
        extra_info           TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id      TEXT PRIMARY KEY,
        campaign_id     TEXT,
        session_number  INTEGER,
        title           TEXT,
        date            TEXT,
        metadata_json   TEXT NOT NULL DEFAULT '{}',
        notes           TEXT,
        speakers_json   TEXT NOT NULL DEFAULT '[]',
        FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_campaign_id
    ON sessions(campaign_id)
    """,
    # content stored inline — no file_path dependency
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL,
        kind        TEXT NOT NULL,
        provider    TEXT NOT NULL,
        model       TEXT NOT NULL,
        content     TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_artifacts_session
    ON artifacts(session_id, kind)
    """,
]


def get_db_path() -> Path:
    default = Path.cwd() / DEFAULT_DB_FILENAME
    return Path(os.getenv("CK_DB_PATH", str(default)))


def get_connection() -> sqlite3.Connection:
    """Open a SQLite connection, initialise schema, and return it."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    conn.commit()
