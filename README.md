# chronicle-keeper-sync-server

**Proprietary.** This repository is not open source.  
The sync protocol is publicly documented in the app repo at [`docs/SYNC_PROTOCOL.md`](https://github.com/aronjanosch/chronicle-keeper/blob/native-rust-core/docs/SYNC_PROTOCOL.md).

---

## What this is

The official hosted sync backend for [Chronicle Keeper](https://github.com/aronjanosch/chronicle-keeper).

- Data CRUD + auth only — no transcription, no LLM, no file processing.
- Transcription and summarization always run on the client device.
- One endpoint does all the work: `POST /sync`.

## Status

> ⚠️ **Current code uses CRUD endpoints (15+ routes) — needs rebuild.**  
> Target: `GET /health` + `POST /sync` only. See protocol spec link above.

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

Conflict resolution: last `updated_at` wins for campaigns/sessions. Artifacts immutable.

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

- [ ] Rebuild around `POST /sync` (replace current CRUD endpoints)
- [ ] Add `updated_at` to campaigns + sessions schema
- [ ] Add `artifact_id` (client UUID) to artifacts
- [ ] Add `deleted_records` table for delete propagation
- [ ] Stripe webhook for subscription validation
- [ ] Per-user auth (replace shared token with user accounts)
- [ ] Postgres option for larger deployments
