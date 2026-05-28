"""`POST /sync` merge logic.

Server-authoritative, last-push-received-wins (see docs/SYNC_PROTOCOL.md):

1. Apply the client's push. Each accepted campaign/session/artifact is stamped
   with a fresh monotonic ``server_seq``. Campaigns/sessions are upserted
   (overwrite). Artifacts are push-once (ignored if ``artifact_id`` exists).
   Artifact deletions are applied and tombstoned.
2. Return everything changed past the client's ``since`` cursor, excluding the
   records the client just pushed (it already has those).

The cursor is the integer ``server_seq`` serialised as a string; it is opaque
to the client, which simply echoes the last ``synced_at`` back as ``since``.
"""

from __future__ import annotations

import json
import sqlite3

from app.models import Artifact, Campaign, CodexEntry, Session, SyncPayload, SyncRequest, SyncResponse
from app.storage.db import current_seq, next_seq


def _parse_since(since: str | None) -> int:
    if not since:
        return 0
    try:
        return int(since)
    except (TypeError, ValueError):
        return 0


def merge(conn: sqlite3.Connection, req: SyncRequest) -> SyncResponse:
    push = req.push
    since = _parse_since(req.since)

    pushed_campaign_ids = {c.campaign_id for c in push.campaigns}
    pushed_session_ids = {s.session_id for s in push.sessions}
    pushed_artifact_ids = {a.artifact_id for a in push.artifacts}
    pushed_deleted_ids = set(push.deleted_artifact_ids)
    pushed_codex_ids = {e.entry_id for e in push.codex_entries}

    _apply_campaigns(conn, push.campaigns)
    _apply_sessions(conn, push.sessions)
    _apply_artifacts(conn, push.artifacts)
    _apply_artifact_deletions(conn, push.deleted_artifact_ids)
    _apply_codex_entries(conn, push.codex_entries)
    conn.commit()

    pull = SyncPayload(
        campaigns=_pull_campaigns(conn, since, pushed_campaign_ids),
        sessions=_pull_sessions(conn, since, pushed_session_ids),
        artifacts=_pull_artifacts(conn, since, pushed_artifact_ids),
        deleted_artifact_ids=_pull_deleted_artifacts(conn, since, pushed_deleted_ids),
        codex_entries=_pull_codex_entries(conn, since, pushed_codex_ids),
    )
    return SyncResponse(synced_at=str(current_seq(conn)), pull=pull)


# ── apply (push -> server) ──────────────────────────────────────────────────


def _apply_campaigns(conn: sqlite3.Connection, campaigns: list[Campaign]) -> None:
    for c in campaigns:
        seq = next_seq(conn)
        conn.execute(
            """
            INSERT INTO campaigns
              (campaign_id, name, next_session_number, system, gm, setting,
               default_language, players_json, extra_info, codex, updated_at, deleted, server_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id) DO UPDATE SET
              name = excluded.name,
              next_session_number = excluded.next_session_number,
              system = excluded.system, gm = excluded.gm, setting = excluded.setting,
              default_language = excluded.default_language,
              players_json = excluded.players_json, extra_info = excluded.extra_info,
              codex = excluded.codex,
              updated_at = excluded.updated_at, deleted = excluded.deleted,
              server_seq = excluded.server_seq
            """,
            (
                c.campaign_id, c.name, c.next_session_number, c.system, c.gm, c.setting,
                c.default_language, json.dumps(c.players), c.extra_info, c.codex, c.updated_at,
                int(c.deleted), seq,
            ),
        )


def _apply_sessions(conn: sqlite3.Connection, sessions: list[Session]) -> None:
    for s in sessions:
        seq = next_seq(conn)
        conn.execute(
            """
            INSERT INTO sessions
              (session_id, campaign_id, session_number, title, date, metadata_json,
               notes, speakers_json, updated_at, deleted, server_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
              campaign_id = excluded.campaign_id, session_number = excluded.session_number,
              title = excluded.title, date = excluded.date,
              metadata_json = excluded.metadata_json, notes = excluded.notes,
              speakers_json = excluded.speakers_json, updated_at = excluded.updated_at,
              deleted = excluded.deleted, server_seq = excluded.server_seq
            """,
            (
                s.session_id, s.campaign_id, s.session_number, s.title, s.date,
                json.dumps(s.metadata), s.notes, json.dumps(s.speakers), s.updated_at,
                int(s.deleted), seq,
            ),
        )


def _apply_artifacts(conn: sqlite3.Connection, artifacts: list[Artifact]) -> None:
    for a in artifacts:
        seq = next_seq(conn)
        # Push-once: ignore if this artifact_id already exists (immutable).
        conn.execute(
            """
            INSERT OR IGNORE INTO artifacts
              (artifact_id, session_id, kind, provider, model, content, created_at, server_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (a.artifact_id, a.session_id, a.kind, a.provider, a.model, a.content, a.created_at, seq),
        )


def _apply_artifact_deletions(conn: sqlite3.Connection, ids: list[str]) -> None:
    for aid in ids:
        seq = next_seq(conn)
        conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (aid,))
        conn.execute(
            """
            INSERT INTO deleted_artifacts (artifact_id, server_seq) VALUES (?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET server_seq = excluded.server_seq
            """,
            (aid, seq),
        )


def _apply_codex_entries(conn: sqlite3.Connection, entries: list[CodexEntry]) -> None:
    for e in entries:
        seq = next_seq(conn)
        conn.execute(
            """
            INSERT INTO codex_entries
              (entry_id, campaign_id, name, kind, body, source, updated_at, deleted, server_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              campaign_id = excluded.campaign_id, name = excluded.name, kind = excluded.kind,
              body = excluded.body, source = excluded.source,
              updated_at = excluded.updated_at, deleted = excluded.deleted,
              server_seq = excluded.server_seq
            """,
            (
                e.entry_id, e.campaign_id, e.name, e.kind, e.body, e.source or "manual",
                e.updated_at, int(e.deleted), seq,
            ),
        )


# ── pull (server -> client) ─────────────────────────────────────────────────


def _exclude_clause(column: str, ids: set[str]) -> tuple[str, list[str]]:
    """Build a `AND column NOT IN (?, ?, ...)` clause, or empty if no ids."""
    if not ids:
        return "", []
    placeholders = ", ".join("?" for _ in ids)
    return f" AND {column} NOT IN ({placeholders})", list(ids)


def _pull_campaigns(conn: sqlite3.Connection, since: int, exclude: set[str]) -> list[Campaign]:
    clause, extra = _exclude_clause("campaign_id", exclude)
    rows = conn.execute(
        f"SELECT * FROM campaigns WHERE server_seq > ?{clause}", [since, *extra]
    ).fetchall()
    return [
        Campaign(
            campaign_id=r["campaign_id"],
            name=r["name"],
            next_session_number=r["next_session_number"],
            system=r["system"] or "",
            gm=r["gm"] or "",
            setting=r["setting"] or "",
            default_language=r["default_language"] or "",
            players=json.loads(r["players_json"] or "[]"),
            extra_info=r["extra_info"] or "",
            codex=r["codex"] or "",
            updated_at=r["updated_at"] or "",
            deleted=bool(r["deleted"]),
        )
        for r in rows
    ]


def _pull_sessions(conn: sqlite3.Connection, since: int, exclude: set[str]) -> list[Session]:
    clause, extra = _exclude_clause("session_id", exclude)
    rows = conn.execute(
        f"SELECT * FROM sessions WHERE server_seq > ?{clause}", [since, *extra]
    ).fetchall()
    return [
        Session(
            session_id=r["session_id"],
            campaign_id=r["campaign_id"],
            session_number=r["session_number"],
            title=r["title"],
            date=r["date"],
            metadata=json.loads(r["metadata_json"] or "{}"),
            notes=r["notes"] or "",
            speakers=json.loads(r["speakers_json"] or "[]"),
            updated_at=r["updated_at"] or "",
            deleted=bool(r["deleted"]),
        )
        for r in rows
    ]


def _pull_artifacts(conn: sqlite3.Connection, since: int, exclude: set[str]) -> list[Artifact]:
    clause, extra = _exclude_clause("artifact_id", exclude)
    rows = conn.execute(
        f"SELECT * FROM artifacts WHERE server_seq > ?{clause}", [since, *extra]
    ).fetchall()
    return [
        Artifact(
            artifact_id=r["artifact_id"],
            session_id=r["session_id"],
            kind=r["kind"],
            provider=r["provider"] or "",
            model=r["model"] or "",
            content=r["content"] or "",
            created_at=r["created_at"] or "",
        )
        for r in rows
    ]


def _pull_deleted_artifacts(conn: sqlite3.Connection, since: int, exclude: set[str]) -> list[str]:
    clause, extra = _exclude_clause("artifact_id", exclude)
    rows = conn.execute(
        f"SELECT artifact_id FROM deleted_artifacts WHERE server_seq > ?{clause}", [since, *extra]
    ).fetchall()
    return [r["artifact_id"] for r in rows]


def _pull_codex_entries(conn: sqlite3.Connection, since: int, exclude: set[str]) -> list[CodexEntry]:
    clause, extra = _exclude_clause("entry_id", exclude)
    rows = conn.execute(
        f"SELECT * FROM codex_entries WHERE server_seq > ?{clause}", [since, *extra]
    ).fetchall()
    return [
        CodexEntry(
            entry_id=r["entry_id"],
            campaign_id=r["campaign_id"],
            name=r["name"],
            kind=r["kind"],
            body=r["body"] or "",
            source=r["source"] or "manual",
            updated_at=r["updated_at"] or "",
            deleted=bool(r["deleted"]),
        )
        for r in rows
    ]
