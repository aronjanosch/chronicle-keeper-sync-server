"""Server-side configuration storage (minimal — no transcription or LLM keys)."""

from __future__ import annotations

import os
from typing import Any

from app.storage.db import get_connection


_DEFAULT_CONFIG: dict[str, str] = {
    "default_language": os.getenv("CK_DEFAULT_LANGUAGE", "en"),
    "current_campaign_id": "",
}

_CONFIG_KEYS = frozenset(_DEFAULT_CONFIG)


def get_config() -> dict[str, Any]:
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    config = dict(_DEFAULT_CONFIG)
    config.update({k: v for k, v in stored.items() if k in _CONFIG_KEYS})
    return config


def update_config(updates: dict[str, Any]) -> dict[str, Any]:
    filtered = {k: str(v) for k, v in updates.items() if k in _CONFIG_KEYS and v is not None}
    if not filtered:
        return get_config()
    with get_connection() as conn:
        for key, value in filtered.items():
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        conn.commit()
    return get_config()


def get_current_campaign_id() -> str | None:
    return get_config().get("current_campaign_id") or None


def set_current_campaign_id(campaign_id: str) -> None:
    update_config({"current_campaign_id": campaign_id})
