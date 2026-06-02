"""Pydantic models for the CK sync server — the `POST /sync` wire protocol.

Field names mirror the Rust client's serde DTOs (`crates/ck-core/src/sync.rs`
in the app repo). These models are the wire-format reference for the protocol.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


def _none_to_list(v: Any) -> Any:
    return [] if v is None else v


def _none_to_dict(v: Any) -> Any:
    return {} if v is None else v


class Campaign(BaseModel):
    campaign_id: str
    name: str = ""
    next_session_number: int = 1
    system: str = ""
    gm: str = ""
    gm_pronouns: str = ""
    setting: str = ""
    default_language: str = ""
    players: list[Any] = Field(default_factory=list)
    extra_info: str = ""
    codex: str = ""
    # JSON-encoded array [{title, body}] of freeform glossary notes (opaque string).
    codex_notes: str = ""
    # LLM-generated "story so far" recap (markdown) + when it was last generated.
    recap: str = ""
    recap_updated_at: str = ""
    updated_at: str = ""
    deleted: bool = False

    # The Rust client may serialize an unset JSON value as null; treat as empty.
    _players_none = field_validator("players", mode="before")(_none_to_list)


class Session(BaseModel):
    session_id: str
    campaign_id: str | None = None
    session_number: int | None = None
    title: str | None = None
    date: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    speakers: list[Any] = Field(default_factory=list)
    updated_at: str = ""
    deleted: bool = False

    _metadata_none = field_validator("metadata", mode="before")(_none_to_dict)
    _speakers_none = field_validator("speakers", mode="before")(_none_to_list)


class Artifact(BaseModel):
    artifact_id: str
    session_id: str
    kind: str
    provider: str = ""
    model: str = ""
    content: str = ""
    created_at: str = ""


class CodexEntry(BaseModel):
    entry_id: str
    campaign_id: str
    name: str
    kind: str
    body: str = ""
    # Longer write-up shown in the entry inspector (not fed into summaries).
    detail: str = ""
    source: str = "manual"
    updated_at: str = ""
    deleted: bool = False


class SyncPayload(BaseModel):
    campaigns: list[Campaign] = Field(default_factory=list)
    sessions: list[Session] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    deleted_artifact_ids: list[str] = Field(default_factory=list)
    codex_entries: list[CodexEntry] = Field(default_factory=list)


class SyncRequest(BaseModel):
    client_id: str
    since: str | None = None
    # "merge" (default) or "mirror" — see `_mirror_prune` in sync.py.
    mode: str = "merge"
    push: SyncPayload = Field(default_factory=SyncPayload)


class SyncResponse(BaseModel):
    synced_at: str
    pull: SyncPayload
