"""`POST /sync` merge logic.

Server-authoritative, last-push-received-wins. This module is the authoritative
description of the merge protocol; the client side is `crates/ck-core/src/sync.rs`
in the app repo.

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
from dataclasses import dataclass, field

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

    # "mirror": prune what the push omits before applying it (same transaction).
    pruned = (
        _mirror_prune(conn, pushed_campaign_ids, pushed_session_ids,
                      pushed_artifact_ids, pushed_codex_ids)
        if req.mode == "mirror"
        else _Pruned()
    )

    _apply_campaigns(conn, push.campaigns)
    _apply_sessions(conn, push.sessions)
    _apply_artifacts(conn, push.artifacts)
    _apply_artifact_deletions(conn, push.deleted_artifact_ids)
    _apply_codex_entries(conn, push.codex_entries)
    conn.commit()

    # Exclude what the client pushed and what it just pruned (no echo-back).
    pull = SyncPayload(
        campaigns=_pull_campaigns(conn, since, pushed_campaign_ids | pruned.campaigns),
        sessions=_pull_sessions(conn, since, pushed_session_ids | pruned.sessions),
        artifacts=_pull_artifacts(conn, since, pushed_artifact_ids),
        deleted_artifact_ids=_pull_deleted_artifacts(conn, since, pushed_deleted_ids | pruned.artifacts),
        codex_entries=_pull_codex_entries(conn, since, pushed_codex_ids | pruned.codex),
    )
    return SyncResponse(synced_at=str(current_seq(conn)), pull=pull)


# ── mirror: prune records the push omits ────────────────────────────────────


@dataclass
class _Pruned:
    """Ids the mirror prune just deleted, so the pull can exclude them."""

    campaigns: set[str] = field(default_factory=set)
    sessions: set[str] = field(default_factory=set)
    artifacts: set[str] = field(default_factory=set)
    codex: set[str] = field(default_factory=set)


def _mirror_prune(
    conn: sqlite3.Connection,
    keep_campaigns: set[str],
    keep_sessions: set[str],
    keep_artifacts: set[str],
    keep_codex: set[str],
) -> _Pruned:
    """Delete every server row whose id is absent from the push.

    Soft-delete for campaigns/sessions/codex, tombstone for artifacts; each bumps
    ``server_seq`` so the deletion propagates. Diffed in Python to dodge SQLite's
    bound-parameter limit on large pushes.
    """
    pruned = _Pruned()

    for r in conn.execute("SELECT campaign_id FROM campaigns WHERE deleted = 0").fetchall():
        cid = r["campaign_id"]
        if cid not in keep_campaigns:
            conn.execute(
                "UPDATE campaigns SET deleted = 1, server_seq = ? WHERE campaign_id = ?",
                (next_seq(conn), cid),
            )
            pruned.campaigns.add(cid)

    for r in conn.execute("SELECT session_id FROM sessions WHERE deleted = 0").fetchall():
        sid = r["session_id"]
        if sid not in keep_sessions:
            conn.execute(
                "UPDATE sessions SET deleted = 1, server_seq = ? WHERE session_id = ?",
                (next_seq(conn), sid),
            )
            pruned.sessions.add(sid)

    for r in conn.execute("SELECT entry_id FROM codex_entries WHERE deleted = 0").fetchall():
        eid = r["entry_id"]
        if eid not in keep_codex:
            conn.execute(
                "UPDATE codex_entries SET deleted = 1, server_seq = ? WHERE entry_id = ?",
                (next_seq(conn), eid),
            )
            pruned.codex.add(eid)

    for r in conn.execute("SELECT artifact_id FROM artifacts").fetchall():
        aid = r["artifact_id"]
        if aid not in keep_artifacts:
            seq = next_seq(conn)
            conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (aid,))
            conn.execute(
                """
                INSERT INTO deleted_artifacts (artifact_id, server_seq) VALUES (?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET server_seq = excluded.server_seq
                """,
                (aid, seq),
            )
            pruned.artifacts.add(aid)

    return pruned


# ── apply (push -> server) ──────────────────────────────────────────────────


def _apply_campaigns(conn: sqlite3.Connection, campaigns: list[Campaign]) -> None:
    for c in campaigns:
        seq = next_seq(conn)
        conn.execute(
            """
            INSERT INTO campaigns
              (campaign_id, name, next_session_number, system, gm, gm_pronouns, setting,
               default_language, players_json, extra_info, codex, codex_notes,
               recap, recap_updated_at, updated_at, deleted, server_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id) DO UPDATE SET
              name = excluded.name,
              next_session_number = excluded.next_session_number,
              system = excluded.system, gm = excluded.gm,
              gm_pronouns = excluded.gm_pronouns, setting = excluded.setting,
              default_language = excluded.default_language,
              players_json = excluded.players_json, extra_info = excluded.extra_info,
              codex = excluded.codex, codex_notes = excluded.codex_notes,
              recap = excluded.recap, recap_updated_at = excluded.recap_updated_at,
              updated_at = excluded.updated_at, deleted = excluded.deleted,
              server_seq = excluded.server_seq
            """,
            (
                c.campaign_id, c.name, c.next_session_number, c.system, c.gm, c.gm_pronouns, c.setting,
                c.default_language, json.dumps(c.players), c.extra_info, c.codex, c.codex_notes,
                c.recap, c.recap_updated_at, c.updated_at,
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
              (entry_id, campaign_id, name, kind, body, detail, source, updated_at, deleted, server_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              campaign_id = excluded.campaign_id, name = excluded.name, kind = excluded.kind,
              body = excluded.body, detail = excluded.detail, source = excluded.source,
              updated_at = excluded.updated_at, deleted = excluded.deleted,
              server_seq = excluded.server_seq
            """,
            (
                e.entry_id, e.campaign_id, e.name, e.kind, e.body, e.detail, e.source or "manual",
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
            gm_pronouns=r["gm_pronouns"] or "",
            setting=r["setting"] or "",
            default_language=r["default_language"] or "",
            players=json.loads(r["players_json"] or "[]"),
            extra_info=r["extra_info"] or "",
            codex=r["codex"] or "",
            codex_notes=r["codex_notes"] or "",
            recap=r["recap"] or "",
            recap_updated_at=r["recap_updated_at"] or "",
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
            detail=r["detail"] or "",
            source=r["source"] or "manual",
            updated_at=r["updated_at"] or "",
            deleted=bool(r["deleted"]),
        )
        for r in rows
    ]
