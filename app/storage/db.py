"""SQLite database for the CK sync server.

The server is a dumb authoritative mirror for `POST /sync`:

- Every accepted record carries a monotonic ``server_seq`` (allocated from a
  single counter). The sync cursor (`since`/`synced_at`) is that integer,
  serialised as a string — opaque to the client. This makes the pull cursor
  immune to client clock skew (see ``docs/SYNC_PROTOCOL.md``).
- Referential integrity is the client's job; we deliberately omit foreign keys
  so an out-of-order push can never abort a sync.
- Artifact content is stored inline (no file paths). ``artifact_id`` is the
  client-generated UUID and is unique (push-once / immutable).
- Hard-deleted artifacts leave a tombstone in ``deleted_artifacts`` so the
  deletion propagates to other devices. Campaigns/sessions use a soft-delete
  flag instead.

Future: swap ``get_connection`` for async Postgres when scale demands it.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_FILENAME = "chronicle_keeper_sync.db"

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS sync_counter (
        id  INTEGER PRIMARY KEY CHECK (id = 0),
        seq INTEGER NOT NULL
    )
    """,
    "INSERT OR IGNORE INTO sync_counter (id, seq) VALUES (0, 0)",
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
        extra_info           TEXT,
        codex                TEXT NOT NULL DEFAULT '',
        updated_at           TEXT NOT NULL DEFAULT '',
        deleted              INTEGER NOT NULL DEFAULT 0,
        server_seq           INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_campaigns_seq ON campaigns(server_seq)",
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
        updated_at      TEXT NOT NULL DEFAULT '',
        deleted         INTEGER NOT NULL DEFAULT 0,
        server_seq      INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_seq ON sessions(server_seq)",
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id  TEXT PRIMARY KEY,
        session_id   TEXT NOT NULL,
        kind         TEXT NOT NULL,
        provider     TEXT NOT NULL,
        model        TEXT NOT NULL,
        content      TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL,
        server_seq   INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_artifacts_seq ON artifacts(server_seq)",
    """
    CREATE TABLE IF NOT EXISTS deleted_artifacts (
        artifact_id TEXT PRIMARY KEY,
        server_seq  INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_deleted_artifacts_seq ON deleted_artifacts(server_seq)",
    """
    CREATE TABLE IF NOT EXISTS codex_entries (
        entry_id     TEXT PRIMARY KEY,
        campaign_id  TEXT NOT NULL,
        name         TEXT NOT NULL,
        kind         TEXT NOT NULL,
        body         TEXT NOT NULL DEFAULT '',
        source       TEXT NOT NULL DEFAULT 'manual',
        updated_at   TEXT NOT NULL DEFAULT '',
        deleted      INTEGER NOT NULL DEFAULT 0,
        server_seq   INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_codex_entries_seq ON codex_entries(server_seq)",
    "CREATE INDEX IF NOT EXISTS idx_codex_entries_campaign ON codex_entries(campaign_id)",
]

# Best-effort ALTERs for already-deployed DBs (the CREATE TABLEs above only apply
# to fresh installs). SQLite has no ADD COLUMN IF NOT EXISTS, so a "duplicate
# column" error means it's already there — ignore it; surface anything else.
MIGRATION_STATEMENTS = [
    "ALTER TABLE campaigns ADD COLUMN codex TEXT NOT NULL DEFAULT ''",
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
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    for stmt in MIGRATION_STATEMENTS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise
    conn.commit()


def next_seq(conn: sqlite3.Connection) -> int:
    """Allocate and return the next monotonic server sequence number."""
    conn.execute("UPDATE sync_counter SET seq = seq + 1 WHERE id = 0")
    row = conn.execute("SELECT seq FROM sync_counter WHERE id = 0").fetchone()
    return int(row["seq"])


def current_seq(conn: sqlite3.Connection) -> int:
    """Current high-water sequence number (returned to the client as `synced_at`)."""
    row = conn.execute("SELECT seq FROM sync_counter WHERE id = 0").fetchone()
    return int(row["seq"]) if row else 0
