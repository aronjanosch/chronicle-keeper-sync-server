"""Pydantic models for the CK sync server (data CRUD only)."""

from __future__ import annotations

from pydantic import BaseModel


# ── speakers ──────────────────────────────────────────────────────────────────

class SpeakerLabel(BaseModel):
    track_id: str
    player_name: str | None = None
    character_name: str | None = None
    pronouns: str | None = None


class LabelSpeakersRequest(BaseModel):
    session_id: str
    speakers: list[SpeakerLabel]


class LabelSpeakersResponse(BaseModel):
    session_id: str
    speakers: list[SpeakerLabel]


# ── sessions ──────────────────────────────────────────────────────────────────

class SessionMetadataRequest(BaseModel):
    session_id: str
    campaign_id: str | None = None
    session_number: int | None = None
    title: str | None = None
    date: str | None = None
    metadata: dict | None = None
    notes: str | None = None


class SessionInfo(BaseModel):
    """Session record as returned by list/get endpoints."""
    session_id: str
    campaign_id: str | None = None
    session_number: int | None = None
    title: str | None = None
    date: str | None = None
    metadata: dict | None = None
    notes: str = ""
    speakers: list[dict] = []
    has_transcription: bool = False
    has_summary: bool = False


# ── artifacts ─────────────────────────────────────────────────────────────────

class ArtifactInfo(BaseModel):
    """Artifact metadata; content is fetched via a separate /content endpoint."""
    id: int
    session_id: str
    kind: str
    provider: str
    model: str
    created_at: str
    content_size: int = 0


class PushArtifactRequest(BaseModel):
    """Client pushes a transcript or summary to the server after local processing."""
    provider: str
    model: str
    content: str


# ── config ────────────────────────────────────────────────────────────────────

class UpdateConfigRequest(BaseModel):
    default_language: str | None = None


class ConfigResponse(BaseModel):
    default_language: str
    current_campaign_id: str | None = None


# ── campaigns ─────────────────────────────────────────────────────────────────

class CampaignInfo(BaseModel):
    campaign_id: str
    name: str
    next_session_number: int


class CampaignDetail(BaseModel):
    campaign_id: str
    name: str
    next_session_number: int
    system: str | None = None
    gm: str | None = None
    setting: str | None = None
    default_language: str | None = None
    players: list[dict] = []
    extra_info: str | None = None


class CampaignsResponse(BaseModel):
    campaigns: list[CampaignInfo]
    current_campaign_id: str | None = None


class CreateCampaignRequest(BaseModel):
    campaign_id: str
    name: str
    start_session_number: int = 1


class CampaignUpdateRequest(BaseModel):
    name: str | None = None
    system: str | None = None
    gm: str | None = None
    setting: str | None = None
    default_language: str | None = None
    players: list[dict] | list[str] | str | None = None
    extra_info: str | None = None
    next_session_number: int | None = None


class CampaignSessionInfo(BaseModel):
    session_id: str
    session_number: int | None = None
    title: str | None = None
    date: str | None = None
    metadata: dict | None = None
    has_transcription: bool = False
    has_summary: bool = False


class CreateCampaignSessionRequest(BaseModel):
    session_number: int | None = None
    title: str | None = None
    date: str | None = None


class NextSessionNumberResponse(BaseModel):
    next_session_number: int
