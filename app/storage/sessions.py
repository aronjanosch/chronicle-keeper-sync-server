"""Session storage — pure DB, no file system.

Sessions on the sync server are DB records only.  The client pushes metadata +
speaker labels; artifacts (transcripts/summaries) are pushed separately.
"""

from __future__ import annotations

import json
from typing import Any

from app.storage.db import get_connection


_METADATA_CATEGORIES = ("characters", "locations", "events", "items", "tags")


def _normalize_metadata(metadata: Any) -> dict[str, list[str]]:
    if not metadata:
        return {cat: [] for cat in _METADATA_CATEGORIES}
    if isinstance(metadata, dict):
        result: dict[str, list[str]] = {}
        for cat in _METADATA_CATEGORIES:
            raw = metadata.get(cat)
            if isinstance(raw, list):
                result[cat] = [str(v).strip() for v in raw if v and str(v).strip()]
            elif isinstance(raw, str):
                result[cat] = [v.strip() for v in raw.split(",") if v.strip()]
            else:
                result[cat] = []
        return result
    if isinstance(metadata, list):
        base = {cat: [] for cat in _METADATA_CATEGORIES}
        base["tags"] = [str(v).strip() for v in metadata if v and str(v).strip()]
        return base
    return {cat: [] for cat in _METADATA_CATEGORIES}


def _row_to_session(row: dict[str, Any]) -> dict[str, Any]:
    try:
        metadata = json.loads(row.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        metadata = {}
    try:
        speakers = json.loads(row.get("speakers_json") or "[]")
    except json.JSONDecodeError:
        speakers = []
    return {
        "session_id": row["session_id"],
        "campaign_id": row.get("campaign_id"),
        "session_number": row.get("session_number"),
        "title": row.get("title"),
        "date": row.get("date"),
        "metadata": _normalize_metadata(metadata),
        "notes": row.get("notes") or "",
        "speakers": speakers,
    }


# ── read ──────────────────────────────────────────────────────────────────────

def get_session(session_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return _row_to_session(dict(row)) if row else None


def list_all_sessions() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY session_id DESC").fetchall()
    return [_row_to_session(dict(row)) for row in rows]


def list_sessions_for_campaign(campaign_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE campaign_id = ? ORDER BY session_number DESC",
            (campaign_id,),
        ).fetchall()
    return [_row_to_session(dict(row)) for row in rows]


# ── write ─────────────────────────────────────────────────────────────────────

def upsert_session(
    session_id: str,
    campaign_id: str | None = None,
    session_number: int | None = None,
    title: str | None = None,
    date: str | None = None,
    metadata: dict | list | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create or update a session record (does not touch speakers)."""
    metadata_json = json.dumps(_normalize_metadata(metadata))
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, campaign_id, session_number, title,
                date, metadata_json, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                campaign_id    = COALESCE(excluded.campaign_id, campaign_id),
                session_number = COALESCE(excluded.session_number, session_number),
                title          = COALESCE(excluded.title, title),
                date           = COALESCE(excluded.date, date),
                metadata_json  = excluded.metadata_json,
                notes          = COALESCE(excluded.notes, notes)
            """,
            (session_id, campaign_id, session_number, title, date, metadata_json, notes),
        )
        conn.commit()
    return get_session(session_id) or {"session_id": session_id}


def update_speakers(session_id: str, speakers: list) -> None:
    """Persist speaker labels for a session."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, speakers_json) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET speakers_json = excluded.speakers_json",
            (session_id, json.dumps(speakers)),
        )
        conn.commit()


def delete_session(session_id: str) -> None:
    """Delete a session and its artifacts."""
    with get_connection() as conn:
        conn.execute("DELETE FROM artifacts WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()
