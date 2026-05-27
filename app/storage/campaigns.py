"""Campaign and basic session-metadata storage for the sync server."""

from __future__ import annotations

import json
from typing import Any

from app.storage.config import get_config, set_current_campaign_id
from app.storage.db import get_connection


CAMPAIGN_FIELDS = {
    "campaign_id", "name", "next_session_number", "system",
    "gm", "setting", "default_language", "players", "extra_info",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _normalize_players(players: Any) -> list[dict[str, str]]:
    if not players:
        return []
    if isinstance(players, list):
        raw = players
    elif isinstance(players, str):
        raw = [item.strip() for item in players.split(",")]
    else:
        return []
    result: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            player = str(item.get("player_name", "")).strip()
            character = str(item.get("character_name", "")).strip()
        else:
            player = str(item).strip()
            character = ""
        if not player and not character:
            continue
        result.append({"player_name": player, "character_name": character})
    return result


def _row_to_campaign(row: dict[str, Any]) -> dict[str, Any]:
    config = get_config()
    try:
        players = json.loads(row.get("players_json") or "[]")
    except json.JSONDecodeError:
        players = []
    return {
        "campaign_id": row.get("campaign_id", ""),
        "name": row.get("name", ""),
        "next_session_number": int(row.get("next_session_number", 1)),
        "system": row.get("system") or "",
        "gm": row.get("gm") or "",
        "setting": row.get("setting") or "",
        "default_language": row.get("default_language") or config.get("default_language", "en"),
        "players": _normalize_players(players),
        "extra_info": row.get("extra_info") or "",
    }


# ── campaigns ─────────────────────────────────────────────────────────────────

def get_campaigns() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM campaigns ORDER BY name").fetchall()
    return [_row_to_campaign(dict(row)) for row in rows]


def get_campaign(campaign_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
    return _row_to_campaign(dict(row)) if row else None


def create_campaign(
    campaign_id: str, name: str, start_session_number: int = 1
) -> dict[str, Any]:
    existing = get_campaign(campaign_id)
    if existing:
        return existing
    config = get_config()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO campaigns (
                campaign_id, name, next_session_number,
                system, gm, setting, default_language, players_json, extra_info
            ) VALUES (?, ?, ?, '', '', '', ?, '[]', '')
            """,
            (campaign_id, name, int(start_session_number), config.get("default_language", "en")),
        )
        conn.commit()
    if not config.get("current_campaign_id"):
        set_current_campaign_id(campaign_id)
    return get_campaign(campaign_id) or {
        "campaign_id": campaign_id, "name": name,
        "next_session_number": int(start_session_number),
        "system": "", "gm": "", "setting": "",
        "default_language": config.get("default_language", "en"),
        "players": [], "extra_info": "",
    }


def update_campaign(campaign_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    if not updates:
        c = get_campaign(campaign_id)
        if not c:
            raise KeyError(f"Campaign not found: {campaign_id}")
        return c

    fields: list[str] = []
    values: list[Any] = []
    for key, value in updates.items():
        if key not in CAMPAIGN_FIELDS or value is None:
            continue
        if key == "players":
            fields.append("players_json = ?")
            values.append(json.dumps(_normalize_players(value)))
        elif key == "next_session_number":
            fields.append("next_session_number = ?")
            values.append(int(value))
        else:
            fields.append(f"{key} = ?")
            values.append(value)

    if not fields:
        c = get_campaign(campaign_id)
        if not c:
            raise KeyError(f"Campaign not found: {campaign_id}")
        return c

    values.append(campaign_id)
    with get_connection() as conn:
        if not conn.execute(
            "SELECT campaign_id FROM campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone():
            raise KeyError(f"Campaign not found: {campaign_id}")
        conn.execute(
            f"UPDATE campaigns SET {', '.join(fields)} WHERE campaign_id = ?",
            tuple(values),
        )
        conn.commit()
    return get_campaign(campaign_id) or {}


def get_next_session_number(campaign_id: str | None = None) -> int:
    from app.storage.config import get_current_campaign_id
    target = campaign_id or get_current_campaign_id()
    if not target:
        return 1
    with get_connection() as conn:
        row = conn.execute(
            "SELECT next_session_number FROM campaigns WHERE campaign_id = ?", (target,)
        ).fetchone()
    return int(row["next_session_number"]) if row else 1


def increment_session_number(campaign_id: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT next_session_number FROM campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        if not row:
            return 1
        next_n = int(row["next_session_number"]) + 1
        conn.execute(
            "UPDATE campaigns SET next_session_number = ? WHERE campaign_id = ?",
            (next_n, campaign_id),
        )
        conn.commit()
    return next_n
