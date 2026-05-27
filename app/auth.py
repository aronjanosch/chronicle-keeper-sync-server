"""Bearer-token authentication middleware for the sync server.

Auth is controlled by the CK_SYNC_TOKEN environment variable:
- If set: every request (except /health) must include
  ``Authorization: Bearer <token>``.  Token comparison is constant-time.
- If unset: server runs in open mode (dev / local use only).
"""

from __future__ import annotations

import os
import secrets

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Paths that skip auth regardless of CK_SYNC_TOKEN
_EXEMPT = frozenset({"/health", "/", "/docs", "/openapi.json", "/redoc"})


def _get_configured_token() -> str | None:
    return os.environ.get("CK_SYNC_TOKEN") or None


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests without a valid Bearer token when CK_SYNC_TOKEN is set."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXEMPT:
            return await call_next(request)

        expected = _get_configured_token()
        if expected is None:
            # Open mode — no token configured
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return Response(
                content='{"detail":"Missing or invalid Authorization header"}',
                status_code=401,
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )

        provided = auth_header[len("Bearer "):]
        if not secrets.compare_digest(
            provided.encode("utf-8"), expected.encode("utf-8")
        ):
            return Response(
                content='{"detail":"Invalid token"}',
                status_code=401,
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)
