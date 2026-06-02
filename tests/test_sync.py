"""Round-trip tests for POST /sync against a temp SQLite DB."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Fresh DB per test; open auth mode (no CK_SYNC_TOKEN).
    monkeypatch.setenv("CK_DB_PATH", str(tmp_path / "sync.db"))
    monkeypatch.delenv("CK_SYNC_TOKEN", raising=False)
    from app.main import app

    return TestClient(app)


def _sync(client, client_id, since=None, push=None):
    body = {
        "client_id": client_id,
        "since": since,
        "push": push or {},
    }
    resp = client.post("/sync", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_push_then_other_device_pulls(client):
    # Device A pushes a campaign + session + artifact.
    push = {
        "campaigns": [{"campaign_id": "c1", "name": "Camp", "codex": "Neverwinter — frozen city.", "updated_at": "t1"}],
        "sessions": [{"session_id": "s1", "campaign_id": "c1", "updated_at": "t1"}],
        "artifacts": [{
            "artifact_id": "a1", "session_id": "s1", "kind": "summary",
            "provider": "ollama", "model": "llama", "content": "notes", "created_at": "t1",
        }],
    }
    a = _sync(client, "deviceA", since=None, push=push)
    # A does not pull back its own pushes.
    assert a["pull"]["campaigns"] == []
    assert a["pull"]["artifacts"] == []
    assert int(a["synced_at"]) > 0

    # Device B (fresh cursor) pulls everything A pushed.
    b = _sync(client, "deviceB", since=None, push={})
    assert {c["campaign_id"] for c in b["pull"]["campaigns"]} == {"c1"}
    assert b["pull"]["campaigns"][0]["codex"] == "Neverwinter — frozen city."
    assert {s["session_id"] for s in b["pull"]["sessions"]} == {"s1"}
    arts = b["pull"]["artifacts"]
    assert len(arts) == 1 and arts[0]["content"] == "notes"

    # B re-syncs with its new cursor: nothing new.
    b2 = _sync(client, "deviceB", since=b["synced_at"], push={})
    assert b2["pull"]["campaigns"] == []
    assert b2["pull"]["artifacts"] == []


def test_artifact_push_once(client):
    art = {
        "artifact_id": "a1", "session_id": "s1", "kind": "transcript",
        "provider": "sherpa", "model": "m", "content": "first", "created_at": "t1",
    }
    _sync(client, "A", push={"artifacts": [art]})
    # Re-push same artifact_id with different content — must be ignored.
    art2 = {**art, "content": "SHOULD BE IGNORED"}
    _sync(client, "A", push={"artifacts": [art2]})

    b = _sync(client, "B", since=None, push={})
    arts = b["pull"]["artifacts"]
    assert len(arts) == 1
    assert arts[0]["content"] == "first"


def test_last_push_wins(client):
    _sync(client, "A", push={"campaigns": [{"campaign_id": "c1", "name": "First", "updated_at": "t1"}]})
    _sync(client, "B", push={"campaigns": [{"campaign_id": "c1", "name": "Second", "updated_at": "t2"}]})
    # A fresh device sees the last write.
    c = _sync(client, "C", since=None, push={})
    camps = {x["campaign_id"]: x for x in c["pull"]["campaigns"]}
    assert camps["c1"]["name"] == "Second"


def test_null_json_fields_accepted(client):
    # The Rust client serializes an unset players/metadata/speakers as JSON null.
    push = {
        "campaigns": [{"campaign_id": "c1", "name": "C", "players": None, "updated_at": "t"}],
        "sessions": [{"session_id": "s1", "metadata": None, "speakers": None, "updated_at": "t"}],
    }
    r = _sync(client, "A", push=push)
    assert r["synced_at"] is not None
    b = _sync(client, "B", since=None, push={})
    camp = next(c for c in b["pull"]["campaigns"] if c["campaign_id"] == "c1")
    assert camp["players"] == []
    sess = next(s for s in b["pull"]["sessions"] if s["session_id"] == "s1")
    assert sess["metadata"] == {} and sess["speakers"] == []


def test_codex_entries_round_trip(client):
    entry = {
        "entry_id": "e1",
        "campaign_id": "c1",
        "name": "Aragorn",
        "kind": "npc",
        "body": "Ranger",
        "source": "manual",
        "updated_at": "t1",
        "deleted": False,
    }
    _sync(client, "A", push={"codex_entries": [entry]})
    b = _sync(client, "B", since=None, push={})
    pulled = b["pull"]["codex_entries"]
    assert len(pulled) == 1 and pulled[0]["body"] == "Ranger"

    # Soft-delete propagates as a row with deleted=true.
    _sync(client, "A", push={"codex_entries": [{**entry, "deleted": True, "updated_at": "t2"}]})
    c = _sync(client, "C", since=None, push={})
    cdx = c["pull"]["codex_entries"]
    assert len(cdx) == 1 and cdx[0]["deleted"] is True


def test_campaign_recap_and_codex_notes_round_trip(client):
    # Recap, codex_notes, and codex_entries.detail must survive the server
    # round-trip (regression: these were silently dropped before the columns
    # existed, clobbering the client's local copy on the next pull).
    push = {
        "campaigns": [{
            "campaign_id": "c1",
            "name": "Camp",
            "codex_notes": '[{"title":"Bree","body":"A village."}]',
            "recap": "The party rose from nothing.",
            "recap_updated_at": "2026-05-29T00:00:00Z",
            "updated_at": "t1",
        }],
        "codex_entries": [{
            "entry_id": "e1", "campaign_id": "c1", "name": "Aragorn", "kind": "npc",
            "body": "Ranger", "detail": "A weathered ranger of the North.",
            "source": "manual", "updated_at": "t1",
        }],
    }
    _sync(client, "A", push=push)
    b = _sync(client, "B", since=None, push={})

    camp = next(c for c in b["pull"]["campaigns"] if c["campaign_id"] == "c1")
    assert camp["recap"] == "The party rose from nothing."
    assert camp["recap_updated_at"] == "2026-05-29T00:00:00Z"
    assert camp["codex_notes"] == '[{"title":"Bree","body":"A village."}]'

    entry = next(e for e in b["pull"]["codex_entries"] if e["entry_id"] == "e1")
    assert entry["detail"] == "A weathered ranger of the North."


def test_artifact_deletion_propagates(client):
    art = {
        "artifact_id": "a1", "session_id": "s1", "kind": "summary",
        "provider": "p", "model": "m", "content": "x", "created_at": "t1",
    }
    first = _sync(client, "A", push={"artifacts": [art]})
    # B syncs to learn about a1.
    b = _sync(client, "B", since=None, push={})
    assert len(b["pull"]["artifacts"]) == 1
    # A deletes a1.
    _sync(client, "A", since=first["synced_at"], push={"deleted_artifact_ids": ["a1"]})
    # B pulls the deletion.
    b2 = _sync(client, "B", since=b["synced_at"], push={})
    assert b2["pull"]["deleted_artifact_ids"] == ["a1"]
