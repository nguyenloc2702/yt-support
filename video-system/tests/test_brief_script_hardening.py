# ruff: noqa: E501
"""Hardening theo verdict PO/Dev vòng 2 (R1–R11).

Mỗi test ở đây tương ứng một lỗi cụ thể đã bị bắt trong review:
- R1 race version → không bao giờ 500, không tạo 2 proposal trùng
- R2 Idempotency-Key scoped + request_hash
- R3 round cap 2 vòng
- R5 agreed là đóng băng
- R6 invalid không được "hồi sinh" thành under_review
- R8 CAS approve/cancel
- R9 owner_forced (deadlock resolution)
- R10 legacy row thiếu request_hash vẫn replay được
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

from backend.app import service_script_duel as duel_mod
from backend.app.store import SQLiteStore
from script_helpers import agents, make_brief, make_proposal, make_shots  # noqa: F401


def _approved_brief(title="race"):
    """Tạo brief ở tầng service (bỏ HTTP) để test concurrency cho nhanh."""
    from backend.app.main import briefs as briefs_svc
    b = briefs_svc.create(title=title, created_by="agent-a",
                          target_duration_sec=120, approval_mode="hard")
    return briefs_svc.approve(b["id"], approved_by="owner")


# -- R1: race khi 2 agent tạo version cùng lúc ---------------------------------

def _run_race(store_fixture, n_threads: int, iterations: int) -> None:
    from backend.app.main import duel as duel_svc

    orig_validate = duel_mod.validate
    barrier = threading.Barrier(n_threads)

    def slow_validate(*a, **k):
        res = orig_validate(*a, **k)
        try:
            barrier.wait(timeout=15)  # cả n thread cùng qua validator trước khi ghi
        except threading.BrokenBarrierError:
            pass
        return res

    duel_mod.validate = slow_validate
    try:
        for i in range(iterations):
            brief = _approved_brief(f"race-{n_threads}-{i}")
            results: list[tuple[str, object]] = []
            lock = threading.Lock()

            def worker():
                try:
                    p = duel_svc.create_proposal(brief_id=brief["id"], author="agent-a",
                                                 shots=make_shots())
                    out = ("ok", p["version"])
                except duel_mod.ScriptDuelError as exc:
                    out = ("err", exc.code)
                except Exception as exc:  # noqa: BLE001 — chính là thứ phải bắt
                    out = ("boom", repr(exc))
                with lock:
                    results.append(out)

            threads = [threading.Thread(target=worker) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
                assert not t.is_alive(), "thread treo — nghi deadlock transaction"

            boom = [r for r in results if r[0] == "boom"]
            assert not boom, f"500/exception trong race: {boom}"
            ok = [r for r in results if r[0] == "ok"]
            assert len(ok) == 1, f"phải đúng 1 người thắng, được {results}"
            for kind, code in (r for r in results if r[0] == "err"):
                assert code in {"duplicate_content", "version_conflict"}, code
            rows = duel_svc.list_proposals(brief["id"])
            assert len(rows) == 1, f"không được sinh 2 proposal: {[r['version'] for r in rows]}"
            assert rows[0]["version"] == 1
    finally:
        duel_mod.validate = orig_validate


def test_race_two_agents_one_winner(store_fixture):
    _run_race(store_fixture, n_threads=2, iterations=30)


def test_race_four_agents_one_winner(store_fixture):
    _run_race(store_fixture, n_threads=4, iterations=10)


def test_concurrent_versions_are_gapless(store_fixture):
    """Bằng chứng mạnh hơn cho R1: 4 nội dung KHÁC nhau phải ra version 1..4.

    Nếu `MAX(version)` bị đọc ngoài transaction, hai thread sẽ cùng nhận một số
    version → UNIQUE(brief_id, version) nổ → có thread nhận 409 thay vì 4 version.
    """
    from backend.app.main import duel as duel_svc

    orig_validate = duel_mod.validate
    n = 4
    barrier = threading.Barrier(n)

    def slow_validate(*a, **k):
        res = orig_validate(*a, **k)
        try:
            barrier.wait(timeout=15)
        except threading.BrokenBarrierError:
            pass
        return res

    duel_mod.validate = slow_validate
    try:
        for i in range(10):
            brief = _approved_brief(f"gap-{i}")
            results: list[tuple[str, object]] = []
            lock = threading.Lock()

            def worker(k: int):
                shots = make_shots()
                shots[0]["narration"] += f" thread{k}"
                try:
                    p = duel_svc.create_proposal(brief_id=brief["id"],
                                                 author="agent-a", shots=shots)
                    out = ("ok", p["version"])
                except duel_mod.ScriptDuelError as exc:
                    out = ("err", exc.code)
                except Exception as exc:  # noqa: BLE001
                    out = ("boom", repr(exc))
                with lock:
                    results.append(out)

            threads = [threading.Thread(target=worker, args=(k,)) for k in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            assert not [r for r in results if r[0] == "boom"], results
            versions = sorted(r[1] for r in results if r[0] == "ok")
            assert versions == [1, 2, 3, 4], f"version phải liền mạch 1..4, được {results}"
    finally:
        duel_mod.validate = orig_validate


# -- R2: idempotency scoped + request_hash ------------------------------------

def test_idempotency_key_reuse_with_different_body_is_422(client, agents):
    b = make_brief(client, agents["author"])
    first = make_proposal(client, agents["author"], b["id"], key="reuse-1")
    assert first.status_code == 201
    other = make_shots()
    other[0]["narration"] += " changed"
    second = make_proposal(client, agents["author"], b["id"], shots=other, key="reuse-1")
    assert second.status_code == 422
    assert "Idempotency-Key" in second.json()["detail"]


def test_idempotency_key_is_scoped_per_agent(client, agents):
    """Hai agent dùng cùng key không được nuốt request của nhau."""
    b = make_brief(client, agents["author"])
    a = make_proposal(client, agents["author"], b["id"], key="shared")
    assert a.status_code == 201
    shots2 = make_shots()
    shots2[0]["narration"] += " from c"
    c = make_proposal(client, agents["c"], b["id"], shots=shots2, key="shared")
    assert c.status_code == 201
    assert c.json()["id"] != a.json()["id"]


# -- R3: tối đa 2 vòng review -------------------------------------------------

def test_round_limit_after_two_review_rounds(client, agents):
    b = make_brief(client, agents["author"])
    v1 = make_proposal(client, agents["author"], b["id"]).json()
    f1 = client.post(f"/api/v1/script-proposals/{v1['id']}/findings", headers=agents["b"],
                     json={"category": "structure", "severity": "medium"})
    assert f1.status_code == 200

    shots2 = make_shots()
    shots2[0]["narration"] += " v2"
    v2 = make_proposal(client, agents["author"], b["id"], shots=shots2,
                       parent_id=v1["id"]).json()
    f2 = client.post(f"/api/v1/script-proposals/{v2['id']}/findings", headers=agents["b"],
                     json={"category": "structure", "severity": "medium"})
    assert f2.status_code == 200

    shots3 = make_shots()
    shots3[0]["narration"] += " v3"
    v3 = make_proposal(client, agents["author"], b["id"], shots=shots3, parent_id=v2["id"])
    assert v3.status_code == 409
    assert "vòng review" in v3.json()["detail"]

    agg = client.get(f"/api/v1/briefs/{b['id']}/agreement", headers=agents["read"]).json()
    assert agg["rounds_used"] == 2
    assert agg["max_rounds"] == 2


# -- R5: agreed đóng băng ------------------------------------------------------

def test_block_after_agreed_is_409(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                headers=agents["author"], json={"verdict": "pass"})
    agreed = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                         headers=agents["b"], json={"verdict": "pass"})
    assert agreed.json()["status"] == "agreed"
    late = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                       headers=agents["b"], json={"verdict": "block", "note": "quá muộn"})
    assert late.status_code == 409
    assert "agreed" in late.json()["detail"]


# -- R6: invalid không bị hồi sinh --------------------------------------------

def test_finding_on_invalid_proposal_keeps_invalid(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"],
                      shots=[{"index": 1, "narration": "tiny",
                              "visual_prompt": "x"}]).json()
    assert p["status"] == "invalid"
    f = client.post(f"/api/v1/script-proposals/{p['id']}/findings", headers=agents["b"],
                    json={"category": "duration", "severity": "high"})
    assert f.status_code == 200
    got = client.get(f"/api/v1/script-proposals/{p['id']}", headers=agents["read"]).json()
    assert got["status"] == "invalid"
    agg = client.get(f"/api/v1/briefs/{b['id']}/agreement", headers=agents["read"]).json()
    assert agg["next_action"] == "fix_proposal"


# -- R8: CAS approve / cancel --------------------------------------------------

def test_approve_after_cancel_is_409(client, agents):
    b = make_brief(client, agents["author"], approve=False)
    c = client.post(f"/api/v1/briefs/{b['id']}/cancel", headers=agents["owner"])
    assert c.status_code == 200
    assert c.json()["status"] == "cancelled"
    a = client.post(f"/api/v1/briefs/{b['id']}/approve", headers=agents["owner"])
    assert a.status_code == 409


def test_approve_is_idempotent_for_approved_brief(client, agents):
    b = make_brief(client, agents["author"])
    again = client.post(f"/api/v1/briefs/{b['id']}/approve", headers=agents["owner"])
    assert again.status_code == 200
    assert again.json()["status"] == "approved"


# -- R9: owner quyết khi deadlock ---------------------------------------------

def test_owner_force_agree_records_decision(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                headers=agents["author"], json={"verdict": "pass"})
    r = client.post(f"/api/v1/briefs/{b['id']}/decisions", headers=agents["owner"],
                    json={"decision": "force_agree", "proposal_id": p["id"],
                          "reason": "deadline sát, critic offline"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "agreed"

    agg = client.get(f"/api/v1/briefs/{b['id']}/agreement", headers=agents["read"]).json()
    assert agg["status"] == "agreed"
    assert agg["agreement"]["id"] == p["id"]
    assert agg["decision"]["decision"] == "force_agree"
    assert agg["decision"]["decided_by"] == "owner"
    assert agg["next_action"] == "build_job"


def test_owner_force_agree_requires_reason(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    r = client.post(f"/api/v1/briefs/{b['id']}/decisions", headers=agents["owner"],
                    json={"decision": "force_agree", "proposal_id": p["id"], "reason": "  "})
    assert r.status_code == 422


def test_decision_endpoint_is_owner_only(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    r = client.post(f"/api/v1/briefs/{b['id']}/decisions", headers=agents["author"],
                    json={"decision": "force_agree", "proposal_id": p["id"],
                          "reason": "tự quyết"})
    assert r.status_code == 403


# -- R10: legacy idempotency row ----------------------------------------------

def test_legacy_idempotency_row_without_hash_still_replays(client, agents, store_fixture):
    b = make_brief(client, agents["author"])
    first = make_proposal(client, agents["author"], b["id"], key="legacy")
    assert first.status_code == 201
    store_fixture.execute("UPDATE idempotency SET request_hash=NULL WHERE key=?",
                          ("test-ingest:legacy",))
    other = make_shots()
    other[0]["narration"] += " different body"
    second = make_proposal(client, agents["author"], b["id"], shots=other, key="legacy")
    assert second.status_code == 201
    assert second.headers.get("Idempotency-Replayed") == "true"
    assert second.json()["id"] == first.json()["id"]


def test_idempotency_request_hash_migration(tmp_path):
    """DB cũ chưa có request_hash: store phải ALTER, row cũ để NULL."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE idempotency (key TEXT PRIMARY KEY, method TEXT, path TEXT, "
                 "status_code INTEGER, response_json TEXT, created_at REAL)")
    conn.execute("INSERT INTO idempotency VALUES ('owner:k1','POST','/x',200,'{}',1.0)")
    conn.commit()
    conn.close()

    st = SQLiteStore(db)
    cols = {r["name"] for r in st.query("PRAGMA table_info(idempotency)")}
    assert "request_hash" in cols
    assert "claim_token" in cols
    row = st.query_one("SELECT * FROM idempotency WHERE key='owner:k1'")
    assert row["request_hash"] is None
    assert row["claim_token"] is None


# -- Error contract: agent cần `code` để quyết định retry hay không ------------

def test_error_body_carries_machine_code(client, agents):
    b = make_brief(client, agents["author"])
    assert make_proposal(client, agents["author"], b["id"]).status_code == 201
    dup = make_proposal(client, agents["author"], b["id"])
    assert dup.status_code == 409
    assert dup.json()["code"] == "duplicate_content"
    assert isinstance(dup.json()["detail"], str)  # client cũ vẫn đọc được

    p = client.get(f"/api/v1/briefs/{b['id']}/proposals",
                   headers=agents["read"]).json()["proposals"][0]
    not_critic = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                             headers=agents["c"], json={"verdict": "pass"})
    assert not_critic.json()["code"] == "not_critic"
    assert not_critic.status_code == 403


def test_owner_only_error_has_code(client, agents):
    b = make_brief(client, agents["author"], approve=False)
    # admin scope nhưng không phải owner -> 403 owner_only
    r = client.post(f"/api/v1/briefs/{b['id']}/approve",
                    headers={"Authorization": "Bearer test-admin"})
    assert r.status_code == 403
    assert r.json()["code"] == "owner_only"


# -- Vòng 3: lỗi Dev/PO bắt được khi verify -----------------------------------

def test_cancelled_brief_cannot_be_signed(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    assert client.post(f"/api/v1/briefs/{b['id']}/cancel",
                       headers=agents["owner"]).status_code == 200
    s = client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                    headers=agents["author"], json={"verdict": "pass"})
    assert s.status_code == 409
    assert s.json()["code"] == "brief_cancelled"
    f = client.post(f"/api/v1/script-proposals/{p['id']}/findings", headers=agents["b"],
                    json={"category": "fact", "severity": "low"})
    assert f.json()["code"] == "brief_cancelled"


def test_owner_cannot_reject_an_agreed_proposal(client, agents):
    b = make_brief(client, agents["author"])
    p = make_proposal(client, agents["author"], b["id"]).json()
    client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                headers=agents["author"], json={"verdict": "pass"})
    client.post(f"/api/v1/script-proposals/{p['id']}/sign",
                headers=agents["b"], json={"verdict": "pass"})
    r = client.post(f"/api/v1/briefs/{b['id']}/decisions", headers=agents["owner"],
                    json={"decision": "reject", "proposal_id": p["id"],
                          "reason": "đổi ý"})
    assert r.status_code == 409
    assert r.json()["code"] == "already_agreed"
    got = client.get(f"/api/v1/script-proposals/{p['id']}", headers=agents["read"]).json()
    assert got["status"] == "agreed"


def test_invalid_proposals_do_not_consume_review_rounds(client, agents):
    """Agent sửa lỗi validator nhiều lần vẫn còn quota review."""
    b = make_brief(client, agents["author"])
    for i in range(4):
        bad = make_proposal(client, agents["author"], b["id"],
                            shots=[{"index": 1, "narration": f"tiny {i}",
                                    "visual_prompt": "x"}])
        assert bad.status_code == 201
        assert bad.json()["status"] == "invalid"
    agg = client.get(f"/api/v1/briefs/{b['id']}/agreement", headers=agents["read"]).json()
    assert agg["rounds_used"] == 0
    good = make_proposal(client, agents["author"], b["id"])
    assert good.status_code == 201
    assert good.json()["validation"]["ok"] is True


def test_round_cap_points_to_owner(client, agents):
    b = make_brief(client, agents["author"])
    v1 = make_proposal(client, agents["author"], b["id"]).json()
    client.post(f"/api/v1/script-proposals/{v1['id']}/findings", headers=agents["b"],
                json={"category": "structure", "severity": "medium"})
    shots2 = make_shots()
    shots2[0]["narration"] += " v2"
    v2 = make_proposal(client, agents["author"], b["id"], shots=shots2,
                       parent_id=v1["id"]).json()
    client.post(f"/api/v1/script-proposals/{v2['id']}/findings", headers=agents["b"],
                json={"category": "structure", "severity": "medium"})
    agg = client.get(f"/api/v1/briefs/{b['id']}/agreement", headers=agents["read"]).json()
    assert agg["rounds_used"] == 2
    assert agg["next_action"] == "owner_required"
    assert agg["phase2_open"] is False


def test_critic_cannot_be_the_author(client, agents):
    r = client.post("/api/v1/briefs", headers=agents["author"], json={
        "title": "same", "target_duration_sec": 120, "approval_mode": "hard",
        "critic": "test-ingest"})
    assert r.status_code == 422
    assert r.json()["code"] == "critic_is_author"

    b = make_brief(client, agents["author"])
    r2 = client.post(f"/api/v1/briefs/{b['id']}/critic", headers=agents["owner"],
                     json={"critic": "test-ingest"})
    assert r2.status_code == 422


def test_pending_idempotency_key_is_409_not_silent(client, agents, store_fixture):
    """Row status_code=0 = request khác đang chạy cùng key -> 409 để backoff."""
    from backend.app.store import now as _now
    store_fixture.execute(
        "INSERT INTO idempotency (key,method,path,status_code,response_json,created_at,"
        "request_hash) VALUES (?,?,?,0,'{}',?,?)",
        ("test-ingest:busy", "POST", "/api/v1/briefs", _now(), "h" * 64))
    r = client.post("/api/v1/briefs", headers={**agents["author"],
                                               "Idempotency-Key": "busy"},
                    json={"title": "x", "target_duration_sec": 120})
    assert r.status_code == 409
    assert r.json()["code"] == "idempotency_in_progress"


def test_stale_pending_idempotency_key_is_reclaimed(client, agents, store_fixture):
    """Row status_code=0 quá 60s = process bị kill -> cho chiếm lại, không kẹt 409."""
    from backend.app.main import IDEMPOTENCY_STALE_SEC
    old = 1.0  # rất cũ
    store_fixture.execute(
        "INSERT INTO idempotency (key,method,path,status_code,response_json,created_at,"
        "request_hash) VALUES (?,?,?,0,'{}',?,?)",
        ("test-ingest:stale", "POST", "/api/v1/briefs", old, "h" * 64))
    assert IDEMPOTENCY_STALE_SEC >= 60  # đủ dài để không cướp request đang chạy
    r = client.post("/api/v1/briefs",
                    headers={**agents["author"], "Idempotency-Key": "stale"},
                    json={"title": "reclaimed", "target_duration_sec": 120})
    assert r.status_code == 201, r.text
    row = store_fixture.query_one("SELECT * FROM idempotency WHERE key=?",
                                  ("test-ingest:stale",))
    assert row["status_code"] == 201


def test_only_one_thread_can_reclaim_a_stale_key(client, agents, store_fixture):
    """Reclaim phải là CAS thật: 2 reclaimer đọc cùng token cũ, chỉ 1 thắng."""
    from backend.app.main import IDEMPOTENCY_STALE_SEC
    store_fixture.execute(
        "INSERT INTO idempotency (key,method,path,status_code,response_json,created_at,"
        "request_hash,claim_token) VALUES (?,?,?,0,'{}',?,?,?)",
        ("test-ingest:contended", "POST", "/api/v1/briefs", 1.0, "h" * 64, "old-token"))

    # Mô phỏng đúng câu CAS mà handler dùng, chạy song song.
    barrier = threading.Barrier(2)
    wins: list[int] = []
    lock = threading.Lock()

    def claim(token: str):
        barrier.wait(timeout=10)
        n = store_fixture.execute(
            "UPDATE idempotency SET claim_token=?, created_at=?, request_hash=? "
            "WHERE key=? AND status_code=0 AND COALESCE(claim_token,'')=?",
            (token, 9e9, "h" * 64, "test-ingest:contended", "old-token"))
        with lock:
            wins.append(n)

    threads = [threading.Thread(target=claim, args=(f"tok-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert sorted(wins) == [0, 1], f"phải đúng 1 người reclaim thắng, được {wins}"
    assert IDEMPOTENCY_STALE_SEC >= 60


def test_reclaimed_key_is_not_clobbered_by_original_owner(client, agents, store_fixture):
    """Finalize của owner cũ (token cũ) không được ghi đè kết quả người reclaim."""
    from backend.app.store import now as _now
    store_fixture.execute(
        "INSERT INTO idempotency (key,method,path,status_code,response_json,created_at,"
        "request_hash,claim_token) VALUES (?,?,?,0,'{}',?,?,?)",
        ("test-ingest:clobber", "POST", "/api/v1/briefs", 1.0, "h" * 64, "winner-token"))
    n = store_fixture.execute(
        "UPDATE idempotency SET status_code=201, response_json='{}', claim_token=NULL "
        "WHERE key=? AND claim_token=?", ("test-ingest:clobber", "loser-token"))
    assert n == 0
    row = store_fixture.query_one("SELECT * FROM idempotency WHERE key=?",
                                  ("test-ingest:clobber",))
    assert row["status_code"] == 0
    assert row["claim_token"] == "winner-token"
    assert _now() > 0


def test_failed_request_releases_idempotency_key(client, agents, store_fixture):
    """Lỗi nghiệp vụ không được chiếm key vĩnh viễn."""
    bad = {"title": "  ", "target_duration_sec": 120}
    r1 = client.post("/api/v1/briefs", headers={**agents["author"],
                                                "Idempotency-Key": "retry-1"}, json=bad)
    assert r1.status_code == 422
    assert store_fixture.query_one(
        "SELECT 1 AS x FROM idempotency WHERE key=?", ("test-ingest:retry-1",)) is None
    r2 = client.post("/api/v1/briefs", headers={**agents["author"],
                                                "Idempotency-Key": "retry-1"},
                     json={"title": "ok now", "target_duration_sec": 120})
    assert r2.status_code == 201


def test_approve_and_cancel_race_has_one_winner(store_fixture):
    """CAS: approve và cancel chạy song song không được cả hai cùng thắng."""
    from backend.app.main import briefs as briefs_svc
    from backend.app.service_briefs import BriefError

    for i in range(30):
        b = briefs_svc.create(title=f"cas-{i}", created_by="agent-a",
                              target_duration_sec=120, approval_mode="hard")
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def worker(fn, tag):
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
            try:
                fn()
                out = f"{tag}:ok"
            except BriefError as exc:
                out = f"{tag}:{exc.code}"
            except Exception as exc:  # noqa: BLE001
                out = f"{tag}:boom:{exc!r}"
            with lock:
                outcomes.append(out)

        t1 = threading.Thread(target=worker,
                              args=(lambda: briefs_svc.approve(b["id"], approved_by="owner"),
                                    "approve"))
        t2 = threading.Thread(target=worker,
                              args=(lambda: briefs_svc.cancel(b["id"], actor="owner"),
                                    "cancel"))
        t1.start(); t2.start(); t1.join(timeout=20); t2.join(timeout=20)

        assert not [o for o in outcomes if "boom" in o], outcomes
        final = briefs_svc.get(b["id"])["status"]
        assert final in {"approved", "cancelled"}, final
        # không thể vừa approved vừa cancelled; ít nhất một bên phải thành công
        assert any(o.endswith(":ok") for o in outcomes), outcomes
