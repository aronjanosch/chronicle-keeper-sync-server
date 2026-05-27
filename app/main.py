"""Chronicle Keeper sync server — data CRUD + auth only.

Removed vs. the standalone backend:
  - /upload        (Craig ZIP extraction → client-side)
  - /transcribe    (speech-to-text → client-side)
  - /providers     (transcription providers → client-side)
  - /summarize     (LLM summarization → client-side)
  - /export        (Markdown export → client-side)
  - /prompts       (prompt presets → client-side)
  - /llm-providers (LLM key management → client-side)
  - /estimate-tokens

Added vs. the standalone backend:
  - POST /sessions/:id/transcripts   (push transcript text)
  - POST /sessions/:id/summaries     (push summary text)
  - Bearer-token auth middleware     (CK_SYNC_TOKEN env var)
  - Artifact content stored in DB    (no file_path references)
"""

from __future__ import annotations

import os

import fastapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from app.auth import TokenAuthMiddleware
from app.logging_config import get_logger, setup_logging
from app.models import (
    ArtifactInfo,
    CampaignDetail,
    CampaignInfo,
    CampaignSessionInfo,
    CampaignUpdateRequest,
    CampaignsResponse,
    ConfigResponse,
    CreateCampaignRequest,
    CreateCampaignSessionRequest,
    LabelSpeakersRequest,
    LabelSpeakersResponse,
    NextSessionNumberResponse,
    PushArtifactRequest,
    SessionInfo,
    SessionMetadataRequest,
    UpdateConfigRequest,
)
from app.storage.artifacts import (
    delete_artifact,
    get_artifact,
    get_artifact_content,
    insert_artifact,
    list_artifacts,
)
from app.storage.campaigns import (
    create_campaign,
    get_campaign,
    get_campaigns,
    get_next_session_number,
    increment_session_number,
    update_campaign,
)
from app.storage.config import get_config, get_current_campaign_id, update_config
from app.storage.sessions import (
    delete_session,
    get_session,
    list_all_sessions,
    list_sessions_for_campaign,
    update_speakers,
    upsert_session,
)

setup_logging()
log = get_logger("api")

# ── app setup ─────────────────────────────────────────────────────────────────

app = fastapi.FastAPI(
    title="Chronicle Keeper Sync Server",
    description="Multi-device sync: data CRUD + auth. No transcription/LLM.",
    version="0.1.0",
)

# Auth middleware (no-op when CK_SYNC_TOKEN is unset)
app.add_middleware(TokenAuthMiddleware)

# CORS — configurable for production; default open for dev
_cors_origins_raw = os.environ.get("CK_CORS_ORIGINS", "*")
_cors_origins = (
    ["*"]
    if _cors_origins_raw.strip() == "*"
    else [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── error handling ────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def _unhandled(_request: fastapi.Request, exc: Exception):
    log.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# ── health (no auth) ──────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "ck-sync-server"}


# ── config ────────────────────────────────────────────────────────────────────

@app.get("/config", response_model=ConfigResponse)
def read_config():
    cfg = get_config()
    return ConfigResponse(
        default_language=cfg.get("default_language", "en"),
        current_campaign_id=cfg.get("current_campaign_id") or None,
    )


@app.put("/config", response_model=ConfigResponse)
def write_config(request: UpdateConfigRequest):
    updated = update_config(request.model_dump(exclude_none=True))
    return ConfigResponse(
        default_language=updated.get("default_language", "en"),
        current_campaign_id=updated.get("current_campaign_id") or None,
    )


# ── campaigns ─────────────────────────────────────────────────────────────────

@app.get("/campaigns", response_model=CampaignsResponse)
def read_campaigns():
    return CampaignsResponse(
        campaigns=[CampaignInfo(**c) for c in get_campaigns()],
        current_campaign_id=get_current_campaign_id(),
    )


@app.post("/campaigns")
def create_campaign_route(request: CreateCampaignRequest):
    campaign = create_campaign(
        request.campaign_id, request.name, request.start_session_number
    )
    return {"status": "success", "campaign": campaign}


@app.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
def read_campaign(campaign_id: str):
    c = get_campaign(campaign_id)
    if not c:
        raise fastapi.HTTPException(status_code=404, detail=f"Campaign not found: {campaign_id}")
    return CampaignDetail(**c)


@app.put("/campaigns/{campaign_id}", response_model=CampaignDetail)
def update_campaign_route(campaign_id: str, request: CampaignUpdateRequest):
    try:
        c = update_campaign(campaign_id, request.model_dump(exclude_none=True))
        return CampaignDetail(**c)
    except KeyError as exc:
        raise fastapi.HTTPException(status_code=404, detail=str(exc))


@app.get("/campaigns/{campaign_id}/sessions", response_model=list[CampaignSessionInfo])
def read_campaign_sessions(campaign_id: str):
    sessions = list_sessions_for_campaign(campaign_id)
    result: list[CampaignSessionInfo] = []
    for s in sessions:
        sid = s["session_id"]
        result.append(CampaignSessionInfo(
            session_id=sid,
            session_number=s.get("session_number"),
            title=s.get("title"),
            date=s.get("date"),
            metadata=s.get("metadata") or {},
            has_transcription=bool(list_artifacts(sid, "transcript")),
            has_summary=bool(list_artifacts(sid, "summary")),
        ))
    return result


@app.post("/campaigns/{campaign_id}/sessions", response_model=CampaignSessionInfo)
def create_campaign_session_route(campaign_id: str, request: CreateCampaignSessionRequest):
    """Create a session record under a campaign (no file system)."""
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise fastapi.HTTPException(status_code=404, detail=f"Campaign not found: {campaign_id}")

    session_number = request.session_number
    if session_number is None:
        session_number = get_next_session_number(campaign_id)

    # Check for duplicates
    existing = [
        s for s in list_sessions_for_campaign(campaign_id)
        if s.get("session_number") == session_number
    ]
    if existing:
        raise fastapi.HTTPException(
            status_code=409,
            detail=f"Session number {session_number} already exists for campaign {campaign_id}",
        )

    import uuid
    session_id = str(uuid.uuid4())
    upsert_session(
        session_id=session_id,
        campaign_id=campaign_id,
        session_number=session_number,
        title=request.title,
        date=request.date,
    )
    if session_number >= campaign.get("next_session_number", 1):
        increment_session_number(campaign_id)

    return CampaignSessionInfo(
        session_id=session_id,
        session_number=session_number,
        title=request.title,
        date=request.date,
        metadata={},
        has_transcription=False,
        has_summary=False,
    )


@app.get("/next-session-number", response_model=NextSessionNumberResponse)
def read_next_session_number(campaign_id: str | None = None):
    return NextSessionNumberResponse(
        next_session_number=get_next_session_number(campaign_id)
    )


# ── sessions ──────────────────────────────────────────────────────────────────

def _session_to_info(s: dict) -> SessionInfo:
    sid = s["session_id"]
    return SessionInfo(
        session_id=sid,
        campaign_id=s.get("campaign_id"),
        session_number=s.get("session_number"),
        title=s.get("title"),
        date=s.get("date"),
        metadata=s.get("metadata") or {},
        notes=s.get("notes") or "",
        speakers=s.get("speakers") or [],
        has_transcription=bool(list_artifacts(sid, "transcript")),
        has_summary=bool(list_artifacts(sid, "summary")),
    )


@app.get("/sessions", response_model=list[SessionInfo])
def read_sessions():
    return [_session_to_info(s) for s in list_all_sessions()]


@app.get("/session/{session_id}", response_model=SessionInfo)
def read_session(session_id: str):
    s = get_session(session_id)
    if not s:
        raise fastapi.HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return _session_to_info(s)


@app.get("/session/{session_id}/metadata")
def read_session_metadata(session_id: str):
    s = get_session(session_id)
    if not s:
        raise fastapi.HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    campaign_name = None
    if s.get("campaign_id"):
        c = get_campaign(s["campaign_id"])
        if c:
            campaign_name = c.get("name")
    return {
        "campaign_id": s.get("campaign_id"),
        "campaign_name": campaign_name,
        "session_number": s.get("session_number"),
        "title": s.get("title"),
        "date": s.get("date"),
        "metadata": s.get("metadata") or {},
        "notes": s.get("notes") or "",
    }


@app.post("/session-metadata")
def update_session_metadata(request: SessionMetadataRequest):
    """Create or update session metadata (called by the client when labelling a session)."""
    session_number = request.session_number
    should_increment = False
    if request.campaign_id and session_number is None:
        session_number = get_next_session_number(request.campaign_id)
        should_increment = True

    session = upsert_session(
        session_id=request.session_id,
        campaign_id=request.campaign_id,
        session_number=session_number,
        title=request.title,
        date=request.date,
        metadata=request.metadata,
        notes=request.notes,
    )
    if should_increment and request.campaign_id:
        increment_session_number(request.campaign_id)

    campaign_name = None
    if request.campaign_id:
        c = get_campaign(request.campaign_id)
        if c:
            campaign_name = c.get("name")

    return {
        "status": "success",
        "campaign": {
            "campaign_id": request.campaign_id,
            "campaign_name": campaign_name,
            "session_number": session_number,
            "title": request.title,
            "date": request.date,
            "notes": request.notes or "",
            "metadata": session.get("metadata") or {},
        },
    }


@app.post("/label-speakers", response_model=LabelSpeakersResponse)
def label_speakers(request: LabelSpeakersRequest):
    s = get_session(request.session_id)
    if not s:
        # Create a minimal session record if it doesn't exist yet
        upsert_session(session_id=request.session_id)
    update_speakers(
        request.session_id,
        [sp.model_dump() for sp in request.speakers],
    )
    return LabelSpeakersResponse(
        session_id=request.session_id,
        speakers=request.speakers,
    )


@app.delete("/sessions/{session_id}")
def remove_session(session_id: str):
    s = get_session(session_id)
    if not s:
        raise fastapi.HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}


# ── artifacts: transcripts ────────────────────────────────────────────────────

@app.get("/sessions/{session_id}/transcripts", response_model=list[ArtifactInfo])
def read_transcripts(session_id: str):
    return [ArtifactInfo(**a) for a in list_artifacts(session_id, "transcript")]


@app.post("/sessions/{session_id}/transcripts", response_model=ArtifactInfo)
def push_transcript(session_id: str, request: PushArtifactRequest):
    """Client pushes a transcript after on-device transcription."""
    # Ensure session record exists
    if not get_session(session_id):
        upsert_session(session_id=session_id)
    artifact = insert_artifact(
        session_id=session_id,
        kind="transcript",
        provider=request.provider,
        model=request.model,
        content=request.content,
    )
    return ArtifactInfo(**artifact)


@app.get("/sessions/{session_id}/transcripts/{artifact_id}/content")
def read_transcript_content(session_id: str, artifact_id: int):
    content = get_artifact_content(artifact_id, session_id)
    if content is None:
        raise fastapi.HTTPException(status_code=404, detail="Transcript not found")
    return PlainTextResponse(content)


@app.delete("/sessions/{session_id}/transcripts/{artifact_id}")
def remove_transcript(session_id: str, artifact_id: int):
    a = get_artifact(artifact_id)
    if not a or a["session_id"] != session_id:
        raise fastapi.HTTPException(status_code=404, detail="Transcript not found")
    delete_artifact(artifact_id)
    return {"status": "deleted", "artifact_id": artifact_id}


# ── artifacts: summaries ──────────────────────────────────────────────────────

@app.get("/sessions/{session_id}/summaries", response_model=list[ArtifactInfo])
def read_summaries(session_id: str):
    return [ArtifactInfo(**a) for a in list_artifacts(session_id, "summary")]


@app.post("/sessions/{session_id}/summaries", response_model=ArtifactInfo)
def push_summary(session_id: str, request: PushArtifactRequest):
    """Client pushes a summary after on-device summarization."""
    if not get_session(session_id):
        upsert_session(session_id=session_id)
    artifact = insert_artifact(
        session_id=session_id,
        kind="summary",
        provider=request.provider,
        model=request.model,
        content=request.content,
    )
    return ArtifactInfo(**artifact)


@app.get("/sessions/{session_id}/summaries/{artifact_id}/content")
def read_summary_content(session_id: str, artifact_id: int):
    content = get_artifact_content(artifact_id, session_id)
    if content is None:
        raise fastapi.HTTPException(status_code=404, detail="Summary not found")
    return PlainTextResponse(content)


@app.delete("/sessions/{session_id}/summaries/{artifact_id}")
def remove_summary(session_id: str, artifact_id: int):
    a = get_artifact(artifact_id)
    if not a or a["session_id"] != session_id:
        raise fastapi.HTTPException(status_code=404, detail="Summary not found")
    delete_artifact(artifact_id)
    return {"status": "deleted", "artifact_id": artifact_id}
