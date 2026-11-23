"""Helper dùng chung cho brief / script-duel tests.

Tách ra để `test_brief_script_flow.py` và `test_brief_script_hardening.py` không
lệch nhau khi luật đổi (critic, calendar owner-only, approve qua owner).
"""
from __future__ import annotations

import pytest

OWNER = {"Authorization": "Bearer owner"}


@pytest.fixture()
def agents(client, monkeypatch):
    """3 agent + owner. Env phải set lại vì auth.identity_for đọc api_keys()."""
    from backend.app.main import auth
    monkeypatch.setenv("VIDEO_API_KEYS",
                       "test-admin:admin,test-ingest:ingest,test-read:read,"
                       "owner:admin,agent-b:ingest,agent-c:ingest")
    keys = dict(auth.keys or {})
    keys.update({"owner": "admin", "agent-b": "ingest", "agent-c": "ingest"})
    monkeypatch.setattr(auth, "keys", keys)
    return {
        "author": {"Authorization": "Bearer test-ingest"},
        "b": {"Authorization": "Bearer agent-b"},
        "c": {"Authorization": "Bearer agent-c"},
        "owner": OWNER,
        "read": {"Authorization": "Bearer test-read"},
    }


def make_shots(n: int = 8, total_words: int = 300) -> list[dict]:
    per = total_words // n
    out = []
    for i in range(n):
        words = ["filler"] * (per - 2) + [f"scene{i + 1}", "beat"]
        out.append({
            "index": i + 1,
            "narration": " ".join(words),
            "visual_prompt": f"cinematic wide shot number {i + 1}",
            "shot_type": "scene",
        })
    return out


def make_brief(client, hdr, approve: bool = True, **over) -> dict:
    """Brief `hard` do tác giả tạo rồi owner duyệt — đường đi thật trên prod."""
    body = {"title": "Test brief", "target_duration_sec": 120,
            "approval_mode": "hard", "critic": "agent-b", **over}
    r = client.post("/api/v1/briefs", headers=hdr, json=body)
    assert r.status_code == 201, r.text
    brief = r.json()
    if approve and brief["status"] == "draft":
        a = client.post(f"/api/v1/briefs/{brief['id']}/approve", headers=OWNER)
        assert a.status_code == 200, a.text
        brief = a.json()
    return brief


def make_proposal(client, hdr, brief_id, shots=None, parent_id=None, key=None):
    headers = dict(hdr)
    if key:
        headers["Idempotency-Key"] = key
    return client.post("/api/v1/script-proposals", headers=headers, json={
        "brief_id": brief_id, "parent_id": parent_id, "shots": shots or make_shots()})
