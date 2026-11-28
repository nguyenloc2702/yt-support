# ruff: noqa: E501
"""Bảng chat chung cho agent — thread 1-1 (tối đa 2 participants), cursor polling,
rate limit, cap 30 tin, retention không rewind seq. Chốt 3 bên 2026-11-23."""
from __future__ import annotations

import pytest


@pytest.fixture()
def three_agents(client, monkeypatch):
    """Thêm keys: 2 agent + key 'owner' riêng của owner (mô hình 'mỗi agent 1 key')."""
    from backend.app.main import auth
    monkeypatch.setenv("VIDEO_API_KEYS",
                       "test-admin:admin,test-ingest:ingest,test-read:read,"
                       "agent-b:ingest,agent-c:ingest,owner:admin")
    keys = dict(auth.keys or {})
    keys.update({"agent-b": "ingest", "agent-c": "ingest", "owner": "admin"})
    monkeypatch.setattr(auth, "keys", keys)
    return {"a": {"Authorization": "Bearer test-ingest"},   # agent-a (key name = test-ingest)
            "b": {"Authorization": "Bearer agent-b"},
            "c": {"Authorization": "Bearer agent-c"},
            "admin_other": {"Authorization": "Bearer test-admin"},  # key admin KHÔNG tên owner
            "owner": {"Authorization": "Bearer owner"}}             # key owner riêng của owner


def _mk_thread(client, hdr, title="Hỏi cách vẽ keyframe chuẩn v3"):
    r = client.post("/api/v1/chat/threads", headers=hdr, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _post(client, hdr, thread_id, body):
    return client.post(f"/api/v1/chat/threads/{thread_id}/messages", headers=hdr,
                       json={"body": body})


def test_create_thread_identity_is_key_name(client, three_agents):
    t = _mk_thread(client, three_agents["a"], "Onboarding: workflow 7 bước chạy thế nào?")
    assert t["created_by"] == "test-ingest"  # key-name làm identity
    assert t["participant_b"] is None
    assert t["status"] == "open"
    # list threads public cho read key — agent mới tìm được agent cũ
    r = client.get("/api/v1/chat/threads", headers={"Authorization": "Bearer test-read"})
    assert r.status_code == 200
    assert any(x["id"] == t["id"] for x in r.json()["threads"])


def test_second_agent_claims_slot_and_cursor_reads(client, three_agents):
    t = _mk_thread(client, three_agents["a"])
    r1 = _post(client, three_agents["a"], t["id"], "Shot 12 đã submit chưa bro?")
    assert r1.status_code == 200
    r2 = _post(client, three_agents["b"], t["id"], "Chưa — đang chờ keyframe duyệt, shortfall 3s.")
    assert r2.status_code == 200, r2.text  # agent-b claim slot trống thành công

    th = client.get(f"/api/v1/chat/threads/{t['id']}", headers=three_agents["a"]).json()
    assert th["participant_b"] == "agent-b"

    # cursor: đọc từ đầu → 2 tin; since_id=seq tin 1 → chỉ tin 2
    full = client.get(f"/api/v1/chat/threads/{t['id']}/messages", headers=three_agents["a"]).json()
    assert [m["sender"] for m in full["messages"]] == ["test-ingest", "agent-b"]
    assert full["last_seq"] == full["messages"][-1]["seq"]
    inc = client.get(f"/api/v1/chat/threads/{t['id']}/messages?since_id={full['messages'][0]['seq']}",
                     headers=three_agents["b"]).json()
    assert [m["body"] for m in inc["messages"]] == ["Chưa — đang chờ keyframe duyệt, shortfall 3s."]


def test_max_two_agents_enforced(client, three_agents):
    t = _mk_thread(client, three_agents["a"])
    assert _post(client, three_agents["b"], t["id"], "hi").status_code == 200
    # agent-c là người thứ 3: post → 403, đọc nội dung → 403
    r3 = _post(client, three_agents["c"], t["id"], "cho tôi vào với")
    assert r3.status_code == 403
    assert "2 agent" in r3.json()["detail"]
    r_read = client.get(f"/api/v1/chat/threads/{t['id']}/messages", headers=three_agents["c"])
    assert r_read.status_code == 403
    # nhưng metadata list vẫn public cho agent-c tìm thread khác
    assert client.get("/api/v1/chat/threads", headers=three_agents["c"]).status_code == 200


def test_rate_limit_429(client, three_agents, store_fixture):
    store_fixture.execute("DELETE FROM chat_messages")  # reset counter 5 phút của các test trước
    t = _mk_thread(client, three_agents["a"])
    for i in range(20):  # trần 20 tin / 5 phút / sender
        assert _post(client, three_agents["b"], t["id"], f"tin {i}").status_code == 200
    r = _post(client, three_agents["b"], t["id"], "tin 21")
    assert r.status_code == 429


def test_thread_auto_locks_at_30(client, three_agents, store_fixture):
    store_fixture.execute("DELETE FROM chat_messages")  # né rate limit đếm từ test trước
    t = _mk_thread(client, three_agents["a"])
    # 30 tin chia 2 sender (né rate limit 20/sender)
    for i in range(15):
        assert _post(client, three_agents["a"], t["id"], f"a{i}").status_code == 200
    for i in range(15):
        assert _post(client, three_agents["b"], t["id"], f"b{i}").status_code == 200
    r = _post(client, three_agents["a"], t["id"], "tin thứ 31")
    assert r.status_code == 409
    th = client.get(f"/api/v1/chat/threads/{t['id']}", headers=three_agents["a"]).json()
    assert th["status"] == "locked"


def test_owner_key_and_pin_session_are_owner_other_admin_key_is_not(client, three_agents, store_fixture):
    """Mô hình owner: CHỈ key tên 'owner' + session PIN là owner. Key admin khác = participant thường,
    NHƯNG scope admin vẫn ĐỌC được mọi thread (moderator read) — post vào thread người khác thì 403."""
    t = _mk_thread(client, three_agents["a"])
    assert _post(client, three_agents["b"], t["id"], "giữa 2 agent").status_code == 200

    # Key admin khác (test-admin): moderator read → 200; post vào thread người khác → 403
    r_foreign_read = client.get(f"/api/v1/chat/threads/{t['id']}/messages", headers=three_agents["admin_other"])
    assert r_foreign_read.status_code == 200
    assert any(m["body"] == "giữa 2 agent" for m in r_foreign_read.json()["messages"])
    assert _post(client, three_agents["admin_other"], t["id"], "tôi admin mà").status_code == 403

    # Key 'owner' riêng của owner: đọc + post mọi thread, sender = 'owner'
    r_read = client.get(f"/api/v1/chat/threads/{t['id']}/messages", headers=three_agents["owner"])
    assert r_read.status_code == 200
    r_post = _post(client, three_agents["owner"], t["id"], "owner vào việc: ưu tiên shot 12")
    assert r_post.status_code == 200
    assert r_post.json()["sender"] == "owner"

    # message_count có trong metadata (seq global không phải số tin trong thread)
    th = client.get(f"/api/v1/chat/threads/{t['id']}", headers=three_agents["a"]).json()
    assert th["message_count"] == 2  # b post 1 + owner post 1 (a chỉ tạo thread)


def test_pin_session_is_owner(client, store_fixture, monkeypatch):
    """Đăng nhập PIN (session cookie) → identity 'owner' — đọc + post mọi thread."""
    from backend.app.auth import SESSION_COOKIE, webauth
    monkeypatch.setenv("VIDEO_API_KEYS", "test-admin:admin,test-ingest:ingest,test-read:read")
    store_fixture.execute("DELETE FROM chat_messages")
    # tạo thread bởi agent thường
    hdr_a = {"Authorization": "Bearer test-ingest"}
    t = _mk_thread(client, hdr_a, "thread cho PIN session test")
    assert _post(client, hdr_a, t["id"], "tin agent").status_code == 200

    # client với session cookie PIN hợp lệ (không bearer)
    client.cookies.set(SESSION_COOKIE, webauth.make_token())
    try:
        r_read = client.get(f"/api/v1/chat/threads/{t['id']}/messages")
        assert r_read.status_code == 200
        r_post = client.post(f"/api/v1/chat/threads/{t['id']}/messages", json={"body": "owner qua PIN"})
        assert r_post.status_code == 200
        assert r_post.json()["sender"] == "owner"
    finally:
        client.cookies.delete(SESSION_COOKIE)


def test_cursor_no_rewind_after_retention_cleanup(store_fixture):
    """Retention DELETE xong seq AUTOINCREMENT phải KHÔNG đếm lại — cursor an toàn."""
    from backend.app.main import chat
    t = chat.create_thread("agent-a", "retention test")
    tid = t["id"]
    for i in range(5):
        chat.post_message(tid, "agent-a", f"msg {i}", is_owner=True)
    res = chat.cleanup_old_messages(retention_days=0)  # xoá sạch tin cũ
    assert res["deleted_messages"] >= 5
    m6 = chat.post_message(tid, "agent-a", "msg sau retention", is_owner=True)
    assert m6["seq"] > 5  # AUTOINCREMENT — không đếm lại từ 1
    got = chat.get_messages(tid, "agent-a", since_id=2, is_owner=True)
    assert [m["seq"] for m in got["messages"]] == [m6["seq"]]
    assert got["messages"][0]["body"] == "msg sau retention"


def test_body_limits(client, three_agents):
    t = _mk_thread(client, three_agents["a"])
    r_empty = _post(client, three_agents["a"], t["id"], "   ")
    assert r_empty.status_code == 422
    r_long = _post(client, three_agents["a"], t["id"], "x" * 2001)
    assert r_long.status_code == 422


def test_spa_has_chat_nav(client):
    """MPA: trang /chat có nav chat + pageChat; goChatPage/esc nằm trong module
    chat.js/core.js (tách khỏi monolith index.html)."""
    page = client.get("/chat")
    assert page.status_code == 200
    assert "/chat#/chat" in page.text
    assert "pageChat" in page.text
    chat_js = client.get("/static/js/chat.js").text
    core_js = client.get("/static/js/core.js").text
    # Regression: chat JS từng gọi escapeHtml() không tồn tại (helper thật là esc())
    # → lỗi runtime "escapeHtml is not defined" khi mở tab chat. Không được tái phạm.
    assert "escapeHtml" not in chat_js + core_js
    assert "function goChatPage(" in chat_js
    assert "function esc(" in core_js
