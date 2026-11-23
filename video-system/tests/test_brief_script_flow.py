from __future__ import annotations

import pytest

from backend.app import service_briefs as briefs_mod
from backend.app.service_script_duel import content_hash, normalize_shots, validate
from script_helpers import OWNER, agents, make_brief, make_proposal, make_shots  # noqa: F401


# -- validator ---------------------------------------------------------------

def test_validator_rejects_bad_duration_and_shot_count():
    brief = {"target_duration_sec": 120, "style_lock_version": "v3"}
    shots = normalize_shots([{"index": 1, "narration": "too short",
                              "visual_prompt": "one frame"}])
    res = validate(brief, shots)
    assert res["ok"] is False
    codes = {e["code"] for e in res["errors"]}
    assert "duration_out_of_range" in codes
    assert "shot_count_out_of_range" in codes


def test_validator_requires_source_for_numbers():
    brief = {"target_duration_sec": 120, "style_lock_version": "v3"}
    shots = make_shots()
    shots[2]["narration"] = "In 1999 the company shipped its first product to customers"
    res = validate(brief, normalize_shots(shots))
    assert any(e["code"] == "missing_source" for e in res["errors"])


def test_validator_accepts_balanced_script():
    brief = {"target_duration_sec": 120, "style_lock_version": "v3"}
    res = validate(brief, normalize_shots(make_shots()))
    assert res["ok"] is True, res
    assert res["stats"]["shots"] == 8


def test_content_hash_is_stable_and_sensitive():
    shots = normalize_shots(make_shots())
    assert content_hash(shots) == content_hash(normalize_shots(make_shots()))
    changed = [dict(s) for s in shots]
    changed[0]["narration"] += " extra"
    assert content_hash(changed) != content_hash(shots)


# -- brief gate --------------------------------------------------------------

def test_objection_window_auto_approves(client, agents, monkeypatch):
    monkeypatch.setenv("VIDEO_BRIEF_SWEEP_ALWAYS", "1")
    b = make_brief(client, agents["author"], approve=False, approval_mode="objection",
                   objection_window_sec=1800)
    assert b["status"] == "draft"
    from backend.app.main import briefs as briefs_svc
    # Trong giờ làm việc: quá cửa sổ -> duyệt
    res = briefs_svc.sweep_objection_windows(now_ts=b["created_at"] + 1900)
    # Store dùng chung giữa các test -> sweep có thể duyệt cả draft còn sót; chỉ
    # khẳng định brief này được duyệt.
    assert res["approved"] >= 1
    got = client.get(f"/api/v1/briefs/{b['id']}", headers=agents["read"]).json()
    assert got["status"] == "approved"
    assert got["approved_by"] == "auto:objection-window"


def test_objection_sweep_does_not_approve_outside_working_hours(client, agents, monkeypatch):
    monkeypatch.delenv("VIDEO_BRIEF_SWEEP_ALWAYS", raising=False)
    b = make_brief(client, agents["author"], approve=False, approval_mode="objection",
                   objection_window_sec=1800)
    from backend.app.main import briefs as briefs_svc
    # 03:00 sáng VN -> scheduler không duyệt
    ts = b["created_at"] + 1900
    local = briefs_mod.datetime.fromtimestamp(ts, briefs_mod._VN_TZ)
    at_3am = ts - local.hour * 3600 - local.minute * 60 - local.second + 3 * 3600
    res = briefs_svc.sweep_objection_windows(now_ts=at_3am)
    assert res.get("skipped") == "outside_working_hours"
    assert res["approved"] == 0
    got = client.get(f"/api/v1/briefs/{b['id']}", headers=agents["read"]).json()
    assert got["status"] == "draft"


def test_objection_window_not_auto_approved_on_read(client, agents, monkeypatch):
    """PO R4: read-path không được tự duyệt hàng loạt."""
    monkeypatch.setenv("VIDEO_BRIEF_SWEEP_ALWAYS", "0")
    b = make_brief(client, agents["author"], approve=False, approval_mode="objection",
                   objection_window_sec=1800)
    monkeypatch.setattr(briefs_mod, "now", lambda: b["created_at"] + 600)
    for _ in range(3):
        r = client.get(f"/api/v1/briefs/{b['id']}", headers=agents["read"])
        assert r.status_code == 200
        assert r.json()["status"] == "draft"


def test_calendar_brief_is_owner_only(client, agents):
    body = {"title": "Cal", "target_duration_sec": 120, "approval_mode": "calendar"}
    denied = client.post("/api/v1/briefs", headers=agents["author"], json=body)
    assert denied.status_code == 403
    ok = client.post("/api/v1/briefs", headers=agents["owner"], json=body)
    assert ok.status_code == 201
    assert ok.json()["status"] == "approved"


def test_sensitive_brief_forces_hard_gate(client, agents):
    b = make_brief(client, agents["author"], approve=False, approval_mode="objection",
                   sensitive=True)
    assert b["approval_mode"] == "hard"
    assert b["status"] == "draft"
    denied = client.post(f"/api/v1/briefs/{b['id']}/approve",
                         headers={"Authorization": "Bearer test-admin"})
    assert denied.status_code == 403
    ok = client.post(f"/api/v1/briefs/{b['id']}/approve", headers=agents["owner"])
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"


def test_proposal_requires_approved_brief(client, agents):
    b = make_brief(client, agents["author"], approve=False)
    r = make_proposal(client, agents["author"], b["id"])
    assert r.status_code == 409
    assert "approved" in r.json()["detail"]


# -- proposals ---------------------------------------------------------------

def test_invalid_proposal_cannot_be_signed(client, agents):
    b = make_brief(client, agents["author"])
    r = make_proposal(client, agents["author"], b["id"],
                      shots=[{"index": 1, "narration": "tiny", "visual_prompt": "x"}])
    assert r.status_code == 201
    p = r.json()
    assert p["validation"]["ok"] is False
    assert p["status"] == "invalid"
    s = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                    headers=agents["author"], json={"verdict": "pass"})
    assert s.status_code == 409
    assert "validator" in s.json()["detail"]


def test_duplicate_content_rejected(client, agents):
    b = make_brief(client, agents["author"])
    assert make_proposal(client, agents["author"], b["id"]).status_code == 201
    dup = make_proposal(client, agents["author"], b["id"])
    assert dup.status_code == 409
    assert "trùng" in dup.json()["detail"]


def test_new_version_supersedes_parent(client, agents):
    b = make_brief(client, agents["author"])
    v1 = make_proposal(client, agents["author"], b["id"]).json()
    shots2 = make_shots()
    shots2[0]["narration"] += " revised"
    v2 = make_proposal(client, agents["author"], b["id"], shots=shots2,
                       parent_id=v1["id"]).json()
    assert v2["version"] == 2
    again = client.get(f"/api/v1/script-proposals/{v1['id']}", headers=agents["read"]).json()
    assert again["status"] == "superseded"


def test_idempotent_create_replays_same_proposal(client, agents):
    b = make_brief(client, agents["author"])
    first = make_proposal(client, agents["author"], b["id"], key="same-key")
    second = make_proposal(client, agents["author"], b["id"], key="same-key")
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers.get("Idempotency-Replayed") == "true"


# -- review / agreement ------------------------------------------------------

def test_self_review_rejected(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    r = client.post(f"/api/v1/script-proposals/{p['id']}/findings",
                    headers=agents["author"],
                    json={"category": "fact", "severity": "high", "blocking": True})
    assert r.status_code == 422
    assert "tự review" in r.json()["detail"]


def test_blocking_finding_blocks_pass(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    f = client.post(f"/api/v1/script-proposals/{p['id']}/findings",
                    headers=agents["b"],
                    json={"category": "fact", "severity": "high",
                          "evidence": "số liệu sai", "blocking": True})
    assert f.status_code == 200
    s = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                    headers=agents["author"], json={"verdict": "pass"})
    assert s.status_code == 409
    assert "blocking" in s.json()["detail"]


def test_two_signatures_make_agreement(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    first = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                        headers=agents["author"], json={"verdict": "pass"})
    assert first.status_code == 200
    assert first.json()["agreed"] is False
    second = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                         headers=agents["b"], json={"verdict": "pass"})
    assert second.status_code == 200
    assert second.json()["status"] == "agreed"
    assert second.json()["agreed"] is True
    agg = client.get(f"/api/v1/briefs/{b['id']}/agreement", headers=agents["read"]).json()
    assert agg["status"] == "agreed"
    assert agg["next_action"] == "build_job"
    assert agg["agreement"]["content_hash"] == p["content_hash"]


def test_author_must_sign_for_agreement(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    first = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                        headers=agents["b"], json={"verdict": "pass"})
    assert first.status_code == 200
    assert first.json()["status"] != "agreed"
    agg = client.get(f"/api/v1/briefs/{b['id']}/agreement", headers=agents["read"]).json()
    assert agg["status"] != "agreed"
    assert agg["next_action"] == "review_or_sign"
    second = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                         headers=agents["author"], json={"verdict": "pass"})
    assert second.status_code == 200
    assert second.json()["status"] == "agreed"


def test_non_critic_cannot_sign_or_find(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    s = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                    headers=agents["c"], json={"verdict": "pass"})
    assert s.status_code == 403
    assert "critic" in s.json()["detail"]
    f = client.post(f"/api/v1/script-proposals/{p['id']}/findings",
                    headers=agents["c"],
                    json={"category": "fact", "severity": "low"})
    assert f.status_code == 403


def test_brief_without_critic_blocks_signing(client, agents):
    b = make_brief(client, agents["author"], critic=None)
    p = make_proposal(client, agents["author"], b["id"]).json()
    r = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                    headers=agents["b"], json={"verdict": "pass"})
    assert r.status_code == 409
    assert "critic" in r.json()["detail"]
    # owner set critic sau đó -> ký được
    c = client.post(f"/api/v1/briefs/{b['id']}/critic", headers=agents["owner"],
                    json={"critic": "agent-b"})
    assert c.status_code == 200
    ok = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                     headers=agents["b"], json={"verdict": "pass"})
    assert ok.status_code == 200


def test_block_verdict_rejects_proposal(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    r = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                    headers=agents["b"], json={"verdict": "block", "note": "sai số liệu"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    agg = client.get(f"/api/v1/briefs/{b['id']}/agreement", headers=agents["read"]).json()
    assert agg["next_action"] == "create_new_version"


def test_agreed_brief_is_frozen(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                headers=agents["author"], json={"verdict": "pass"})
    client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                headers=agents["b"], json={"verdict": "pass"})
    shots2 = make_shots()
    shots2[1]["narration"] += " after agreement"
    r = make_proposal(client, agents["author"], b["id"], shots=shots2)
    assert r.status_code == 409
    assert "chốt" in r.json()["detail"]
