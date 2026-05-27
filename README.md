# chronicle-keeper-sync-server

**Proprietary.** This repository is not open source.  
The sync protocol is publicly documented in the app repo at [`docs/SYNC_PROTOCOL.md`](https://github.com/aronjanosch/chronicle-keeper/blob/native-rust-core/docs/SYNC_PROTOCOL.md).

---

## What this is

The official hosted sync backend for [Chronicle Keeper](https://github.com/aronjanosch/chronicle-keeper).

- Data mirror + auth only — no transcription, no LLM, no file processing.
- Transcription and summarization always run on the client device.
- One endpoint does all the work: `POST /sync` (plus public `GET /health`).

## Status

> ✅ **Rebuilt around `POST /sync`** (replaces the old CRUD routes). Offline-first
> batch sync with server-authoritative merge. Round-trip tests in `tests/`.

---

## Architecture

```
Client (Tauri app)
  │
  │  POST /sync  { since, push: { campaigns, sessions, artifacts } }
  │  ←→
  │  200 OK      { synced_at, pull: { campaigns, sessions, artifacts } }
  ▼
chronicle-keeper-sync-server (this)
  │
  ▼
SQLite (WAL mode) on VPS
```

Conflict resolution: **server-authoritative, last push received wins.** Each accepted
record gets a monotonic `server_seq` (the sync cursor, opaque to the client). Artifacts
are immutable (push-once by `artifact_id`); deletions propagate via tombstones. Clock-skew
immune — client timestamps are never used for conflicts. See the protocol spec.

---

## Development

```bash
cp .env.example .env
# Set CK_SYNC_TOKEN to a random secret

uv run uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
```

`GET /health` is public. All other routes require `Authorization: Bearer <CK_SYNC_TOKEN>`.

---

## Deploy (VPS)

```bash
docker build -t ck-sync-server .

docker run -d \
  --name ck-sync \
  -p 8080:8080 \
  -v /opt/ck-data:/data \
  -e CK_SYNC_TOKEN=your-long-random-secret \
  ck-sync-server
```

Put Caddy in front for TLS. Back up `/data/chronicle_keeper_sync.db`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CK_SYNC_TOKEN` | *(unset = open)* | Shared bearer token |
| `CK_DB_PATH` | `./chronicle_keeper_sync.db` | SQLite path |
| `CK_HOST` | `0.0.0.0` | Bind host |
| `CK_PORT` | `8080` | Bind port |
| `CK_CORS_ORIGINS` | `*` | Allowed CORS origins |
| `CHRONICLE_DEBUG` | `0` | Verbose logs |

---

## Roadmap

- [x] Rebuild around `POST /sync` (replaced the CRUD endpoints)
- [x] `server_seq` cursor + `updated_at`/`deleted` on campaigns + sessions
- [x] `artifact_id` (client UUID) primary key on artifacts; push-once
- [x] `deleted_artifacts` tombstone table for delete propagation
- [ ] Stripe webhook for subscription validation
- [ ] Per-user auth (replace shared token with user accounts / Stripe customer ids)
- [ ] Postgres option for larger deployments
