"""Pydantic models for the CK sync server — the `POST /sync` wire protocol.

Field names mirror the Rust client's serde DTOs (`crates/ck-core/src/sync.rs`)
and the published spec in `docs/SYNC_PROTOCOL.md`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Campaign(BaseModel):
    campaign_id: str
    name: str = ""
    next_session_number: int = 1
    system: str = ""
    gm: str = ""
    setting: str = ""
    default_language: str = ""
    players: list[Any] = Field(default_factory=list)
    extra_info: str = ""
    updated_at: str = ""
    deleted: bool = False


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


class Artifact(BaseModel):
    artifact_id: str
    session_id: str
    kind: str
    provider: str = ""
    model: str = ""
    content: str = ""
    created_at: str = ""


class SyncPayload(BaseModel):
    campaigns: list[Campaign] = Field(default_factory=list)
    sessions: list[Session] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    deleted_artifact_ids: list[str] = Field(default_factory=list)


class SyncRequest(BaseModel):
    client_id: str
    since: str | None = None
    push: SyncPayload = Field(default_factory=SyncPayload)


class SyncResponse(BaseModel):
    synced_at: str
    pull: SyncPayload
