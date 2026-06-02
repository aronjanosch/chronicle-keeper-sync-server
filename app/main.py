"""Chronicle Keeper sync server.

Two endpoints only:
  - GET  /health  (public)         — liveness
  - POST /sync    (bearer token)   — offline-first batch sync

Transcription, summarization, LLM keys, and file processing all stay on the
client device. This server is a dumb authoritative data mirror — the wire
protocol is defined by ``app/models.py`` (shapes) and ``app/sync.py`` (merge).
"""

from __future__ import annotations

import os

import fastapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import TokenAuthMiddleware
from app.logging_config import get_logger, setup_logging
from app.models import SyncRequest, SyncResponse
from app.storage.db import get_connection
from app.sync import merge

setup_logging()
log = get_logger("api")

app = fastapi.FastAPI(
    title="Chronicle Keeper Sync Server",
    description="Offline-first multi-device sync. POST /sync only.",
    version="0.2.0",
)

app.add_middleware(TokenAuthMiddleware)

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


@app.exception_handler(Exception)
async def _unhandled(_request: fastapi.Request, exc: Exception):
    log.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok", "service": "ck-sync-server"}


@app.post("/sync", response_model=SyncResponse)
def sync(request: SyncRequest) -> SyncResponse:
    """One round-trip: apply the client's push, return changes since its cursor."""
    conn = get_connection()
    try:
        result = merge(conn, request)
    finally:
        conn.close()
    log.info(
        "sync client=%s mode=%s since=%s pushed(c=%d s=%d a=%d del=%d) -> synced_at=%s "
        "pulled(c=%d s=%d a=%d del=%d)",
        request.client_id, request.mode, request.since,
        len(request.push.campaigns), len(request.push.sessions),
        len(request.push.artifacts), len(request.push.deleted_artifact_ids),
        result.synced_at,
        len(result.pull.campaigns), len(result.pull.sessions),
        len(result.pull.artifacts), len(result.pull.deleted_artifact_ids),
    )
    return result
