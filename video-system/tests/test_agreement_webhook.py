"""Webhook `agreement.next_action_changed` — phát khi bước kế của brief đổi.

Nguyên tắc test: không bắn HTTP thật. `webhooks.dispatch_event` bị monkeypatch để
bắt event row; mọi assertion đọc payload JSON đúng như receiver sẽ thấy.
"""
from __future__ import annotations

import json

import pytest

from backend.app.agreement_notifier import AgreementNotifier
from script_helpers import agents, make_brief, make_proposal, make_shots  # noqa: F401


@pytest.fixture()
def captured(monkeypatch):
    """Bắt event thay vì gửi webhook (không có receiver trong test)."""
    from backend.app.main import webhooks as webhook_svc

    events: list[dict] = []

    def fake_dispatch(event_row, synchronous=False):
        events.append(dict(event_row))
        return []

    # Patch trên instance: AgreementNotifier giữ đúng object webhooks này.
    monkeypatch.setattr(webhook_svc, "dispatch_event", fake_dispatch)
    return events


def _payloads(events: list[dict]) -> list[dict]:
    return [json.loads(e["payload_json"]) for e in events]


def _sequence(events: list[dict]) -> list[str]:
    return [p["next_action"] for p in _payloads(events)]


# -- luồng đầy đủ -----------------------------------------------------------

def test_next_action_sequence_full_flow(client, agents, captured):
    """brief -> critic -> approve -> proposal -> finding -> 2 chữ ký."""
    b = make_brief(client, agents["author"])
    # Brief `hard` sinh ra ở draft -> chờ owner duyệt; critic không đổi bước kế.
    assert _sequence(captured) == ["await_brief_approval", "create_proposal"]

    p = make_proposal(client, agents["author"], b["id"])
    assert p.status_code == 201, p.text
    pid = p.json()["id"]

    f = client.post(f"/api/v1/script-proposals/{pid}/findings", headers=agents["b"],
                    json={"category": "fact", "severity": "low", "evidence": "nhẹ"})
    assert f.status_code == 200, f.text

    first = client.post(f"/api/v1/script-proposals/{pid}/sign", headers=agents["author"],
                        json={"verdict": "pass"})
    assert first.status_code == 200, first.text
    assert first.json()["agreed"] is False

    second = client.post(f"/api/v1/script-proposals/{pid}/sign", headers=agents["b"],
                         json={"verdict": "pass"})
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "agreed"

    # finding + chữ ký đầu không đổi bước kế -> không bắn; chữ ký thứ 2 -> build_job
    assert _sequence(captured) == [
        "await_brief_approval", "create_proposal", "review_or_sign", "build_job"]
    assert [p["reason"] for p in _payloads(captured)] == [
        "brief.created", "brief.approved", "proposal.created", "proposal.signed"]


def test_draft_brief_waits_for_approval(client, agents, captured):
    """Brief draft không được đẩy writer chạy; duyệt xong mới đổi bước kế."""
    r = client.post("/api/v1/briefs", headers=agents["author"], json={
        "title": "Draft only", "target_duration_sec": 120, "approval_mode": "hard"})
    assert r.status_code == 201, r.text
    brief = r.json()
    assert brief["status"] == "draft"

    agg = client.get(f"/api/v1/briefs/{brief['id']}/agreement", headers=agents["read"])
    assert agg.status_code == 200, agg.text
    assert agg.json()["next_action"] == "await_brief_approval"
    assert _sequence(captured) == ["await_brief_approval"]

    a = client.post(f"/api/v1/briefs/{brief['id']}/approve", headers=agents["owner"])
    assert a.status_code == 200, a.text
    # approve phải phát event vì next_action đổi thật (không phải dedupe nuốt)
    assert _sequence(captured) == ["await_brief_approval", "create_proposal"]


def test_cancelled_brief_stops_flow(client, agents, captured):
    """Brief bị huỷ thì bước kế là dừng hẳn, không mời author nộp proposal."""
    b = make_brief(client, agents["author"])
    c = client.post(f"/api/v1/briefs/{b['id']}/cancel", headers=agents["owner"])
    assert c.status_code == 200, c.text

    agg = client.get(f"/api/v1/briefs/{b['id']}/agreement", headers=agents["read"])
    assert agg.status_code == 200, agg.text
    assert agg.json()["next_action"] == "brief_cancelled"
    assert _sequence(captured)[-1] == "brief_cancelled"


def test_event_payload_and_type(client, agents, captured):
    b = make_brief(client, agents["author"])
    evt = captured[-1]
    assert evt["type"] == "agreement.next_action_changed"
    # item_id/shot_id NULL để hook global (item_id IS NULL) nhận được
    assert evt["item_id"] is None and evt["shot_id"] is None
    assert evt["actor"] == "system"
    data = json.loads(evt["payload_json"])
    assert data["brief_id"] == b["id"]
    assert data["next_action"] == "create_proposal"
    assert data["agreement"]["brief_id"] == b["id"]
    assert data["agreement"]["next_action"] == "create_proposal"
    assert data["reason"] == "brief.approved"


def test_agreement_read_path_never_emits(client, agents, captured):
    b = make_brief(client, agents["author"])
    before = len(captured)
    assert before >= 1
    for _ in range(3):
        r = client.get(f"/api/v1/briefs/{b['id']}/agreement", headers=agents["read"])
        assert r.status_code == 200
        assert r.json()["next_action"] == "create_proposal"
    assert len(captured) == before


def test_dispatch_failure_does_not_break_business(client, agents, monkeypatch):
    """Webhook chết không được biến 201 thành 500."""
    from backend.app.main import webhooks as webhook_svc

    def boom(event_row, synchronous=False):
        raise RuntimeError("receiver down")

    monkeypatch.setattr(webhook_svc, "dispatch_event", boom)
    b = make_brief(client, agents["author"])          # POST /briefs + /critic + /approve
    assert b["status"] == "approved"
    p = make_proposal(client, agents["author"], b["id"])
    assert p.status_code == 201, p.text
    s = client.post(f"/api/v1/script-proposals/{p.json()['id']}/sign",
                    headers=agents["author"], json={"verdict": "pass"})
    assert s.status_code == 200, s.text


# -- notifier trực tiếp (không qua route) ------------------------------------

def test_sync_dedupes_and_emits_on_change(tmp_path, captured):
    from backend.app.main import webhooks as webhook_svc
    from backend.app.store import SQLiteStore

    st = SQLiteStore(tmp_path / "sync.db")
    notifier = AgreementNotifier(st, webhook_svc)     # không cần agreement_fn
    fake = {"brief_id": "brf_fake", "status": "none", "next_action": "owner_required"}

    assert notifier.sync("brf_fake", fake, "manual") is True
    assert notifier.sync("brf_fake", dict(fake), "manual") is False   # y hệt -> dedupe
    assert notifier.sync("brf_fake", {**fake, "next_action": "build_job"}, "manual") is True
    # next_action rỗng -> bỏ qua, không ghi state
    assert notifier.sync("brf_fake", {"brief_id": "brf_fake"}, "manual") is False

    assert _sequence(captured) == ["owner_required", "build_job"]
    row = st.query_one("SELECT next_action FROM brief_agreement_state WHERE brief_id=?",
                       ("brf_fake",))
    assert row["next_action"] == "build_job"


def test_sweep_catches_transition_outside_routes(tmp_path, captured):
    """Scheduler auto-approve / process khác đổi state -> sweep phải phát."""
    from backend.app.main import webhooks as webhook_svc
    from backend.app.service_briefs import BriefService
    from backend.app.service_script_duel import ScriptDuelService
    from backend.app.store import SQLiteStore

    st = SQLiteStore(tmp_path / "sweep.db")
    briefs_svc = BriefService(st)
    duel_svc = ScriptDuelService(st, briefs_svc)
    b = briefs_svc.create(title="Sweep brief", created_by="owner",
                          approval_mode="calendar", target_duration_sec=120)
    assert b["status"] == "approved"
    assert captured == []                             # chưa route nào chạy

    notifier = AgreementNotifier(st, webhook_svc, agreement_fn=duel_svc.agreement)
    assert notifier.sweep() == 1
    assert _sequence(captured) == ["create_proposal"]

    # Đổi trạng thái bằng service (như scheduler/CLI), không qua HTTP route
    duel_svc.create_proposal(brief_id=b["id"], author="agent-a", shots=make_shots())
    assert notifier.sweep() == 1
    assert _sequence(captured) == ["create_proposal", "review_or_sign"]

    assert notifier.sweep() == 0                      # đã đồng bộ -> im lặng
    assert len(captured) == 2


def test_sync_rolls_back_state_when_event_insert_fails(tmp_path, captured):
    """State + event là một transaction: event fail thì state không được ghi."""
    from backend.app.main import webhooks as webhook_svc
    from backend.app.store import SQLiteStore

    st = SQLiteStore(tmp_path / "atomic.db")
    notifier = AgreementNotifier(st, webhook_svc)
    fake = {"brief_id": "brf_atomic", "next_action": "owner_required"}

    # Trigger tạm chặn INSERT vào events (không đụng bảng khác).
    st.execute("CREATE TRIGGER block_events BEFORE INSERT ON events "
               "BEGIN SELECT RAISE(ABORT, 'events blocked'); END")

    assert notifier.sync("brf_atomic", fake, "manual") is False
    assert st.query_one(
        "SELECT next_action FROM brief_agreement_state WHERE brief_id=?",
        ("brf_atomic",)) is None                       # state không bị đánh dấu
    assert captured == []

    # Bỏ chặn: lần sync sau vẫn phát được (event không bị mất vĩnh viễn).
    st.execute("DROP TRIGGER block_events")
    assert notifier.sync("brf_atomic", dict(fake), "manual") is True
    assert _sequence(captured) == ["owner_required"]
    row = st.query_one("SELECT next_action FROM brief_agreement_state WHERE brief_id=?",
                       ("brf_atomic",))
    assert row["next_action"] == "owner_required"


def test_sweep_picks_most_recently_updated_brief(tmp_path, captured):
    """limit < số brief approved -> phải lấy brief updated_at mới nhất."""
    from backend.app.main import webhooks as webhook_svc
    from backend.app.service_briefs import BriefService
    from backend.app.service_script_duel import ScriptDuelService
    from backend.app.store import SQLiteStore

    st = SQLiteStore(tmp_path / "order.db")
    briefs_svc = BriefService(st)
    duel_svc = ScriptDuelService(st, briefs_svc)

    ids = []
    for i in range(3):
        b = briefs_svc.create(title=f"Brief {i}", created_by="owner",
                              approval_mode="calendar", target_duration_sec=120)
        assert b["status"] == "approved"
        ids.append(b["id"])
        st.execute("UPDATE job_briefs SET updated_at=? WHERE id=?",
                   (1000.0 + i, b["id"]))
    assert captured == []

    notifier = AgreementNotifier(st, webhook_svc, agreement_fn=duel_svc.agreement)
    assert notifier.sweep(limit=1) == 1
    assert _payloads(captured)[-1]["brief_id"] == ids[-1]   # brief mới nhất


def test_sweep_without_limit_covers_all_briefs(tmp_path, captured, monkeypatch):
    """sweep() mặc định không LIMIT — brief cũ không bị bỏ đói."""
    from backend.app.main import webhooks as webhook_svc
    from backend.app.service_briefs import BriefService
    from backend.app.service_script_duel import ScriptDuelService
    from backend.app.store import SQLiteStore

    st = SQLiteStore(tmp_path / "nolimit.db")
    briefs_svc = BriefService(st)
    duel_svc = ScriptDuelService(st, briefs_svc)

    ids = []
    for i in range(3):
        b = briefs_svc.create(title=f"Brief {i}", created_by="owner",
                              approval_mode="calendar", target_duration_sec=120)
        ids.append(b["id"])
        # brief cũ nhất bị đẩy xuống cuối ORDER BY updated_at DESC
        st.execute("UPDATE job_briefs SET updated_at=? WHERE id=?", (1000.0 + i, b["id"]))

    sql_seen: list[str] = []
    real_query = st.query

    def spy(sql, params=()):
        sql_seen.append(sql)
        return real_query(sql, params)

    monkeypatch.setattr(st, "query", spy)

    notifier = AgreementNotifier(st, webhook_svc, agreement_fn=duel_svc.agreement)
    assert notifier.sweep() == 3
    assert {p["brief_id"] for p in _payloads(captured)} == set(ids)
    # Đường mặc định phải không LIMIT (nếu LIMIT thì brief cũ bị bỏ đói)
    assert sql_seen and "LIMIT" not in sql_seen[0].upper()


def test_start_sweeper_reuses_live_thread(tmp_path, captured):
    """Gọi start_sweeper nhiều lần không được spawn thêm thread."""
    from backend.app.main import webhooks as webhook_svc
    from backend.app.store import SQLiteStore

    st = SQLiteStore(tmp_path / "sweeper.db")
    notifier = AgreementNotifier(st, webhook_svc, agreement_fn=lambda _bid: {})
    th = notifier.start_sweeper(3600)
    try:
        assert th is not None and th.is_alive()
        assert notifier.start_sweeper(3600) is th
    finally:
        notifier._sweeper_thread = None


# -- dedupe dưới contention ---------------------------------------------------

def test_sync_dedupes_when_two_threads_race(tmp_path, captured):
    """2 thread cùng sync một next_action -> đúng 1 event, 1 row state.

    `prev` phải được đọc TRONG transaction: đọc ngoài thì cả hai cùng thấy
    "chưa có row" và bắn 2 event.
    """
    import threading

    from backend.app.main import webhooks as webhook_svc
    from backend.app.store import SQLiteStore

    st = SQLiteStore(tmp_path / "race.db")
    notifier = AgreementNotifier(st, webhook_svc)
    agreement = {"brief_id": "brf_race", "next_action": "build_job"}

    barrier = threading.Barrier(2)
    results: list[bool] = []

    def worker() -> None:
        barrier.wait(timeout=10)
        results.append(notifier.sync("brf_race", dict(agreement), "race"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results) == [False, True]
    assert _sequence(captured) == ["build_job"]
    rows = st.query("SELECT * FROM brief_agreement_state WHERE brief_id='brf_race'")
    assert len(rows) == 1
