"""Artifact storage — content stored inline in the DB (no file system).

Transcripts and summaries are pushed by the client as text; the server stores
them verbatim.  ``content_size`` (byte length) is exposed in list responses so
clients can decide whether to fetch full content.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.storage.db import get_connection


def insert_artifact(
    session_id: str,
    kind: str,
    provider: str,
    model: str,
    content: str,
) -> dict[str, Any]:
    """Insert an artifact and return its info (without content)."""
    created_at = datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO artifacts (session_id, kind, provider, model, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, kind, provider, model, content, created_at),
        )
        artifact_id = cursor.lastrowid
        conn.commit()
    return {
        "id": artifact_id,
        "session_id": session_id,
        "kind": kind,
        "provider": provider,
        "model": model,
        "created_at": created_at,
        "content_size": len(content.encode("utf-8")),
    }


def list_artifacts(session_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    """List artifacts (metadata only, no content) for a session."""
    with get_connection() as conn:
        if kind:
            rows = conn.execute(
                "SELECT id, session_id, kind, provider, model, created_at, "
                "LENGTH(content) AS content_size "
                "FROM artifacts WHERE session_id = ? AND kind = ? ORDER BY created_at DESC",
                (session_id, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, session_id, kind, provider, model, created_at, "
                "LENGTH(content) AS content_size "
                "FROM artifacts WHERE session_id = ? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_artifact_content(artifact_id: int, session_id: str) -> str | None:
    """Return the text content of an artifact, or None if not found."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT content FROM artifacts WHERE id = ? AND session_id = ?",
            (artifact_id, session_id),
        ).fetchone()
    return row["content"] if row else None


def get_artifact(artifact_id: int) -> dict[str, Any] | None:
    """Return artifact metadata (no content)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, session_id, kind, provider, model, created_at, "
            "LENGTH(content) AS content_size "
            "FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_artifact(artifact_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
        conn.commit()
