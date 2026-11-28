"""Tests — pipeline muse proposals: đề xuất → review chéo 3 agent → Telegram → duyệt.

Chạy:

    VIDEO_BOARD_ACCEPTANCE=1 pytest video-system/tests/test_muse_proposals.py -q

Nguyên tắc giống test_board_acceptance: chỉ gọi HTTP qua `client`, mỗi test
tự dựng state, assert lên `code`. Telegram chưa cấu hình (env unset trong
test) → notify phải im lặng, không lỗi.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("VIDEO_BOARD_ACCEPTANCE") != "1",
    reason="Muse proposal API — bật bằng VIDEO_BOARD_ACCEPTANCE=1",
)

AGENTS = {"muse-1": "ingest", "muse-2": "ingest", "muse-3": "ingest",
          "muse-4": "ingest", "muse-boss": "manager", "owner": "admin"}


@pytest.fixture()
def board_job(client, ingest, agents_hdrs, wav_file, make_item_fn, import_payload_factory):
    """Job 3 shot × 8s cho các test B2 (gate upload + migrate legacy)."""
    item_id = make_item_fn("muse-proposals-b2")
    payload = import_payload_factory(count=3, duration=24.0, seg=8.0)
    payload["audio"]["file_path"] = str(wav_file(24.0))
    r = client.post(f"/api/v1/items/{item_id}/shots/import", headers=ingest, json=payload)
    assert r.status_code == 201, r.text
    return item_id


def _make_mp4(path: Path, duration_sec: float = 8.0, color: str = "navy") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"
    cmd = [ffmpeg, "-y", "-f", "lavfi",
           "-i", f"color=c={color}:s=640x360:r=24:d={duration_sec:.3f}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
           "-movflags", "+faststart", str(path)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if res.returncode != 0:
        pytest.skip(f"ffmpeg không dựng được clip test: {res.stderr[:200]}")
    return path


@pytest.fixture()
def agents_hdrs(client):
    from backend.app.main import auth

    saved_env = os.environ.get("VIDEO_API_KEYS")
    saved_keys = dict(auth.keys or {})
    os.environ["VIDEO_API_KEYS"] = (
        "test-admin:admin,test-ingest:ingest,test-read:read,"
        + ",".join(f"{k}:{v}" for k, v in AGENTS.items())
    )
    auth.keys = dict(saved_keys, **AGENTS)
    try:
        yield {k: {"Authorization": f"Bearer {k}"} for k in AGENTS}
    finally:
        if saved_env is None:
            os.environ.pop("VIDEO_API_KEYS", None)
        else:
            os.environ["VIDEO_API_KEYS"] = saved_env
        auth.keys = saved_keys


_PROP_SEQ = {"n": 0}


def dims_for(score: float) -> dict:
    """Mảng trục tương ứng điểm mong muốn (server tự tính tổng = score).

    Trừ dần từ trục giới hạn thấp nhất: feasibility (2) → freshness (2) →
    topicality (3) → hook (3). Score ≤ 10 và ≥ 0."""
    deficit = round(10.0 - float(score), 2)
    d = {"hook": 3.0, "topicality": 3.0, "freshness": 2.0, "feasibility": 2.0}
    for key, cap in (("feasibility", 2.0), ("freshness", 2.0),
                     ("topicality", 3.0), ("hook", 3.0)):
        take = min(cap, max(0.0, deficit))
        d[key] = round(cap - take, 2)
        deficit = round(deficit - take, 2)
    return d


def _propose(client, hdrs, title=None, hook=None, **over):
    """Mỗi lời gọi mặc định có title/source_url RIÊNG — store dùng chung cả
    session pytest, cùng title sẽ bị dedupe gộp (hành vi đúng của hệ thống)."""
    _PROP_SEQ["n"] += 1
    n = _PROP_SEQ["n"]
    body = {
        "title": title or f"ISS sắp chết — vì sao NASA im lặng? (case {n})",
        "hook": hook or f"Trạm không gian cũ nhất hành tinh sẽ rơi xuống Trái Đất trong 6 tháng (case {n}).",
        "mode": "news", "lang": "en",
        "target_seconds": 60,
        "source_url": f"https://example.com/iss-case-{n}",
        "source_note": "NASA press release",
    }
    body.update(over)
    return client.post("/api/v1/muse-proposals", headers=hdrs, json=body)


def _review(client, hdrs, prop_id, score, comment=None):
    """Gửi dimensions ứng với điểm mong muốn — server tự tính score (Q3)."""
    body = {"dimensions": dims_for(score)}
    if comment:
        body["comment"] = comment
    return client.post(f"/api/v1/muse-proposals/{prop_id}/reviews",
                       headers=hdrs, json=body)


# --------------------------------------------------------------------------
# create + dedupe
# --------------------------------------------------------------------------
def test_create_and_dedupe_merge(client, agents_hdrs):
    r1 = _propose(client, agents_hdrs["muse-1"])
    assert r1.status_code == 201, r1.text
    body = r1.json()
    assert body["deduped"] is False
    pid = body["proposal"]["id"]
    assert body["proposal"]["status"] == "proposed"
    assert body["proposal"]["signals"] == 1

    # muse-2 đề xuất cùng title (khác viết hoa/dấu câu) → gộp, không tạo mới
    r2 = _propose(client, agents_hdrs["muse-2"],
                  title="iss SẮP CHẾT!  Vì sao NASA im lặng? (case 1)",
                  source_url="https://example.com/other-url")
    assert r2.json()["deduped"] is True, r2.text
    assert r2.json()["proposal_id"] == pid
    assert r2.json()["signals"] == 2

    # cùng source_url cũng trùng dù title khác
    r3 = _propose(client, agents_hdrs["muse-3"],
                  title="Trạm vũ trụ sẽ rơi", source_url="https://example.com/iss-case-1")
    assert r3.json()["deduped"] is True
    assert r3.json()["proposal_id"] == pid


def test_create_validation(client, agents_hdrs):
    no_hook = client.post("/api/v1/muse-proposals", headers=agents_hdrs["muse-1"],
                          json={"title": "x"})
    assert no_hook.status_code == 422, no_hook.text
    bad_mode = _propose(client, agents_hdrs["muse-1"], mode="vlog")
    assert bad_mode.status_code == 422, bad_mode.text
    long_title = _propose(client, agents_hdrs["muse-1"], title="x" * 301)
    assert long_title.status_code == 422, long_title.text


# --------------------------------------------------------------------------
# dispatch review — pool bất kỳ, loại proposer/đã chấm
# --------------------------------------------------------------------------
def test_dispatch_excludes_proposer_and_self_review(
        client, agents_hdrs):
    r = _propose(client, agents_hdrs["muse-1"])
    pid = r.json()["proposal"]["id"]

    # proposer không được nhận việc chấm chính đề xuất của mình
    mine = client.get("/api/v1/muse-proposals/next", headers=agents_hdrs["muse-1"])
    assert mine.status_code == 200
    assert mine.json()["proposal"] is None
    assert mine.json()["reason"] == "no_review_proposal"

    # người khác nhận được 1 proposal đủ điều kiện — store dùng chung cả
    # session pytest nên dispatch có thể trả proposal CŨ hơn của test khác;
    # assert theo tính chất (khác proposer), không assert id cụ thể.
    other = client.get("/api/v1/muse-proposals/next", headers=agents_hdrs["muse-2"])
    assert other.json()["proposal"] is not None
    assert other.json()["proposal"]["proposer"] != "muse-2"

    # self_review 403
    self_r = _review(client, agents_hdrs["muse-1"], pid, 9.8)
    assert self_r.status_code == 403, self_r.text
    assert self_r.json()["code"] == "self_review"


def test_review_progress_and_settle_awaiting_owner(client, agents_hdrs):
    r = _propose(client, agents_hdrs["muse-1"])
    pid = r.json()["proposal"]["id"]

    r1 = _review(client, agents_hdrs["muse-2"], pid, 9.7, "Hook mạnh, tin nóng")
    assert r1.status_code == 201, r1.text
    assert r1.json()["status"] == "under_review"
    assert r1.json()["review_count"] == 1

    # lượt 2 pass → đủ 2/3 pass → chốt awaiting_owner + priority từ avg
    r2 = _review(client, agents_hdrs["muse-3"], pid, 9.9)
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert body["status"] == "awaiting_owner"
    assert body["pass_count"] == 2
    assert body["priority"] == 1  # avg 9.8 → priority 1 (map ≥9.8 → 1)

    # chấm tiếp khi đã chốt → 409
    late = _review(client, agents_hdrs["muse-4"], pid, 9.5)
    assert late.status_code == 409, late.text
    assert late.json()["code"] == "proposal_settled"

    # đã chấm rồi → 409 — service kiểm state (đã chốt) TRƯỚC eligibility
    # (đã chấm), nên người đã chấm trên proposal chốt cũng nhận proposal_settled.
    dup = _review(client, agents_hdrs["muse-2"], pid, 9.5)
    assert dup.status_code == 409, dup.text
    assert dup.json()["code"] == "proposal_settled"


def test_early_reject_when_two_fails(client, agents_hdrs):
    """2 fail là mất khả năng đạt 2/3 pass → chốt rejected ngay, đừng phí lượt 3."""
    r = _propose(client, agents_hdrs["muse-1"],
                 title="Ý tưởng nhạt", hook="Không có gì thú vị.",
                 source_url="https://example.com/bland")
    pid = r.json()["proposal"]["id"]
    _review(client, agents_hdrs["muse-2"], pid, 8.0, "Hook yếu")
    r2 = _review(client, agents_hdrs["muse-3"], pid, 9.0, "Tin đã cũ")  # 9.0 < 9.5 → fail
    assert r2.json()["status"] == "rejected"
    assert r2.json()["pass_count"] == 0

    # rejected không vào hàng chờ needs_script
    q = client.get("/api/v1/muse-proposals/queue/needs-script",
                   headers=agents_hdrs["owner"]).json()
    assert all(p["id"] != pid for p in q["queue"])


def test_reject_when_all_reviewed_without_enough_pass(client, agents_hdrs):
    """Chấm đủ 3 lượt mà chỉ 1 pass → rejected (1 pass 2 fail đã chốt sớm ở
    nhánh trên; case này phủ nhánh chấm đủ số lượt)."""
    r = _propose(client, agents_hdrs["muse-1"],
                 title="Suy nghĩ lại hook", hook="Cần tốt hơn.",
                 source_url="https://example.com/rethink")
    pid = r.json()["proposal"]["id"]
    _review(client, agents_hdrs["muse-2"], pid, 9.6)
    r2 = _review(client, agents_hdrs["muse-3"], pid, 8.5)
    assert r2.json()["status"] == "under_review"  # 1 pass 1 fail — còn hy vọng
    r3 = _review(client, agents_hdrs["muse-4"], pid, 9.4)  # fail (dưới 9.5)
    assert r3.json()["status"] == "rejected"


# --------------------------------------------------------------------------
# approve/reject — chỉ admin, hàng chờ ưu tiên
# --------------------------------------------------------------------------
def test_approve_only_admin_and_priority_queue(client, agents_hdrs):
    pids = []
    for i, (title, scores) in enumerate([
        ("Ý A đủ ưu tiên cao", (9.9, 9.9)),
        ("Ý B ưu tiên vừa", (9.6, 9.7)),
        ("Ý C thường", (9.5, 9.5)),
    ]):
        r = _propose(client, agents_hdrs["muse-1"], title=title,
                     source_url=f"https://example.com/p{i}")
        pid = r.json()["proposal"]["id"]
        pids.append(pid)
        _review(client, agents_hdrs["muse-2"], pid, scores[0])
        _review(client, agents_hdrs["muse-3"], pid, scores[1])

    # ingest scope không duyệt được
    forbidden = client.post(f"/api/v1/muse-proposals/{pids[0]}/approve",
                            headers=agents_hdrs["muse-2"], json={})
    assert forbidden.status_code == 403, forbidden.text

    # owner duyệt ý C với priority override 1 — nhưng hàng chờ xếp theo
    # (priority, created_at): A tự được priority 1 và tạo TRƯỚC C → A đứng trước C.
    ok = client.post(f"/api/v1/muse-proposals/{pids[2]}/approve",
                     headers=agents_hdrs["owner"], json={"priority": 1})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "needs_script"
    assert ok.json()["priority"] == 1

    # duyệt lại/đề xuất chưa chốt → 409
    again = client.post(f"/api/v1/muse-proposals/{pids[2]}/approve",
                        headers=agents_hdrs["owner"], json={})
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "proposal_not_awaiting"

    # hai ý còn lại duyệt thường → auto priority (A: 9.9→1, B: 9.65→2)
    for pid in (pids[0], pids[1]):
        rr = client.post(f"/api/v1/muse-proposals/{pid}/approve",
                         headers=agents_hdrs["owner"], json={})
        assert rr.status_code == 200, rr.text

    q = client.get("/api/v1/muse-proposals/queue/needs-script",
                   headers=agents_hdrs["owner"]).json()
    order = [(p["id"], p["priority"]) for p in q["queue"]]
    assert q["total"] == 3
    # A và C đều priority 1 — A tạo trước nên đứng trước; B priority 2 cuối
    assert order[0][0] == pids[0] and order[0][1] == 1
    assert order[1][0] == pids[2] and order[1][1] == 1
    assert order[2][0] == pids[1] and order[2][1] == 2


def test_owner_reject(client, agents_hdrs):
    r = _propose(client, agents_hdrs["muse-1"],
                 title="Đề xuất bị owner chối", source_url="https://example.com/nope")
    pid = r.json()["proposal"]["id"]
    _review(client, agents_hdrs["muse-2"], pid, 9.6)
    _review(client, agents_hdrs["muse-3"], pid, 9.8)
    rej = client.post(f"/api/v1/muse-proposals/{pid}/reject",
                      headers=agents_hdrs["owner"], json={"reason": "đã làm đề tài này"})
    assert rej.status_code == 200, rej.text
    assert rej.json()["status"] == "rejected"


# --------------------------------------------------------------------------
# blind redact — đúng luật chấm mù
# --------------------------------------------------------------------------
def test_blind_redaction_until_self_review(client, agents_hdrs):
    r = _propose(client, agents_hdrs["muse-1"], source_url="https://example.com/blind")
    pid = r.json()["proposal"]["id"]
    _review(client, agents_hdrs["muse-2"], pid, 9.7, "góp ý nội bộ")

    # muse-3 chưa chấm → ẩn score/comment, vẫn thấy count
    d = client.get(f"/api/v1/muse-proposals/{pid}", headers=agents_hdrs["muse-3"]).json()
    assert d["reviews_visible"] is False
    assert d["review_count"] == 1
    assert d["pass_count"] is None
    assert d["reviews"][0]["score"] is None
    assert d["reviews"][0]["comment"] is None

    # manager thấy hết
    m = client.get(f"/api/v1/muse-proposals/{pid}", headers=agents_hdrs["muse-boss"]).json()
    assert m["reviews_visible"] is True
    assert m["reviews"][0]["score"] == 9.7

    # muse-3 chấm xong → thấy được
    _review(client, agents_hdrs["muse-3"], pid, 9.8)
    d2 = client.get(f"/api/v1/muse-proposals/{pid}", headers=agents_hdrs["muse-3"]).json()
    assert d2["reviews_visible"] is True
    assert d2["reviews"][0]["score"] == 9.7

    # proposer (muse-1) thấy khi đã chốt
    p = client.get(f"/api/v1/muse-proposals/{pid}", headers=agents_hdrs["muse-1"]).json()
    assert p["reviews_visible"] is True


# --------------------------------------------------------------------------
# idempotency + validation + scheduler expire
# --------------------------------------------------------------------------
def test_replay_idempotent_and_comment_too_long(client, agents_hdrs):
    r = _propose(client, agents_hdrs["muse-1"], source_url="https://example.com/idem")
    pid = r.json()["proposal"]["id"]
    headers = dict(agents_hdrs["muse-2"])
    headers["Idempotency-Key"] = "prop-rev-1"
    first = client.post(f"/api/v1/muse-proposals/{pid}/reviews", headers=headers,
                        json={"dimensions": dims_for(9.6)})
    assert first.status_code == 201, first.text
    replay = client.post(f"/api/v1/muse-proposals/{pid}/reviews", headers=headers,
                         json={"dimensions": dims_for(9.6)})
    assert replay.status_code == 200, replay.text
    assert replay.json().get("idempotent_replay") is True

    long = client.post(f"/api/v1/muse-proposals/{pid}/reviews",
                       headers=agents_hdrs["muse-3"],
                       json={"dimensions": dims_for(9.6), "comment": "x" * 2001})
    assert long.status_code == 422, long.text
    assert long.json()["code"] == "comment_too_long"


def test_expire_stale_awaiting_owner(client, agents_hdrs, monkeypatch):
    from backend.app.main import proposals as proposals_svc, store as app_store

    r = _propose(client, agents_hdrs["muse-1"], source_url="https://example.com/stale")
    pid = r.json()["proposal"]["id"]
    _review(client, agents_hdrs["muse-2"], pid, 9.6)
    _review(client, agents_hdrs["muse-3"], pid, 9.7)
    assert client.get(f"/api/v1/muse-proposals/{pid}",
                      headers=agents_hdrs["owner"]).json()["status"] == "awaiting_owner"

    # Q4: hết hạn phải báo Telegram (capture thay vì gọi thật — test env
    # chưa cấu hình token nên phải ép _telegram_configured trả True)
    sent = []
    monkeypatch.setattr(proposals_svc, "_telegram_configured", lambda: True)
    monkeypatch.setattr(proposals_svc, "_send_telegram", lambda text: sent.append(text))

    # giả lập chờ quá hạn
    app_store.execute(
        "UPDATE muse_proposals SET decided_at=? WHERE id=?",
        (time.time() - 13 * 3600.0, pid),
    )
    res = proposals_svc.expire_stale()
    assert res["expired"] >= 1
    assert client.get(f"/api/v1/muse-proposals/{pid}",
                      headers=agents_hdrs["owner"]).json()["status"] == "expired"
    assert sent, "expired phải gửi tin Telegram cho owner"
    assert "HẾT HẠN" in sent[0] and pid in sent[0]


def test_telegram_unconfigured_does_not_break(client, agents_hdrs):
    """Telegram chưa cấu hình trong test env → chấm vẫn chốt bình thường."""
    assert os.environ.get("VIDEO_TELEGRAM_BOT_TOKEN", "") == ""
    r = _propose(client, agents_hdrs["muse-1"], source_url="https://example.com/ntg")
    pid = r.json()["proposal"]["id"]
    r2 = _review(client, agents_hdrs["muse-2"], pid, 9.5)
    r3 = _review(client, agents_hdrs["muse-3"], pid, 9.6)
    assert r3.json()["status"] == "awaiting_owner"


# --------------------------------------------------------------------------
# B-T1 — Telegram inline buttons: nút Duyệt/Từ chối thay câu lệnh
# --------------------------------------------------------------------------
def test_notify_owner_has_inline_buttons(client, agents_hdrs, monkeypatch):
    """Tin chờ duyệt phải mang 2 nút callback pp:ok/pp:no đúng proposal id."""
    from backend.app.main import proposals as proposals_svc

    r = _propose(client, agents_hdrs["muse-1"], source_url="https://example.com/btn")
    pid = r.json()["proposal"]["id"]
    _review(client, agents_hdrs["muse-2"], pid, 9.7)
    _review(client, agents_hdrs["muse-3"], pid, 9.8)

    sent = []
    monkeypatch.setattr(proposals_svc, "_telegram_configured", lambda: True)
    monkeypatch.setattr(proposals_svc, "_send_telegram",
                        lambda text, reply_markup=None: sent.append(
                            {"text": text, "markup": reply_markup}))
    # lượt chấm thứ 3 không diễn ra (đã chốt 2 pass) — bắn lại notify qua
    # internal path bằng cách gọi _notify_owner trực tiếp (idempotent-ish)
    proposals_svc._notify_owner(pid)
    assert sent, "notify phải được gọi"
    markup = sent[-1]["markup"]
    assert markup, "tin chờ duyệt phải có inline keyboard"
    buttons = markup["inline_keyboard"][0]
    datas = [b["callback_data"] for b in buttons]
    assert f"pp:ok:{pid}" in datas and f"pp:no:{pid}" in datas


def test_telegram_callback_approve_and_reject_flows(client, agents_hdrs, monkeypatch):
    """Bấm nút qua callback_query: approve → needs_script; bấm lại/lệch trạng
    thái → alert lý do, state không đổi; data rác → không đụng state."""
    from backend.app.main import proposals as proposals_svc
    from backend.app import service_telegram_bot as tb

    r = _propose(client, agents_hdrs["muse-1"], source_url="https://example.com/cb")
    pid = r.json()["proposal"]["id"]
    _review(client, agents_hdrs["muse-2"], pid, 9.7)
    _review(client, agents_hdrs["muse-3"], pid, 9.8)

    posts: list[dict] = []

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            posts.append({"url": url, "json": json or {}})
            return FakeResp()

    monkeypatch.setattr(tb.httpx, "Client", FakeClient)

    def _cb(data: str) -> None:
        posts.clear()
        cb = {"id": f"cbq-{len(posts)}", "data": data,
              "message": {"chat": {"id": "123"}, "message_id": 42,
                          "text": "TIN GỐC"}}
        tb._handle_proposal_callback("https://api.telegram.org/botX", cb,
                                     proposals_svc, lambda t: posts.append(
                                         {"url": "fallback", "json": {"text": t}}))

    # bấm Duyệt → answerCallbackQuery + editMessageText + state đổi
    _cb(f"pp:ok:{pid}")
    assert any("answerCallbackQuery" in p["url"] for p in posts)
    edits = [p for p in posts if "editMessageText" in p["url"]]
    assert edits and "ĐÃ DUYỆT" in edits[0]["json"]["text"]
    assert "reply_markup" not in edits[0]["json"], "nút phải biến mất sau xử lý"
    now_status = client.get(f"/api/v1/muse-proposals/{pid}",
                            headers=agents_hdrs["owner"]).json()["status"]
    assert now_status == "needs_script"

    # bấm lại → alert lý do (đã cần kịch bản), state không đổi
    _cb(f"pp:ok:{pid}")
    answers = [p for p in posts if "answerCallbackQuery" in p["url"]]
    assert answers and answers[0]["json"].get("show_alert") is True
    assert client.get(f"/api/v1/muse-proposals/{pid}",
                      headers=agents_hdrs["owner"]).json()["status"] == "needs_script"

    # data rác → chỉ answer, không đụng state, không exception
    _cb("pp:weird:xxx")
    answers = [p for p in posts if "answerCallbackQuery" in p["url"]]
    assert answers and "không rõ" in answers[0]["json"].get("text", "")


# --------------------------------------------------------------------------
# B2 — gate shot_binding_required (đường upload không gắn shot)
# --------------------------------------------------------------------------
def test_video_upload_without_shot_binding_rejected(client, board_job,
                                                    agents_hdrs, tmp_path):
    clip = _make_mp4(tmp_path / "nobind.mp4")
    with open(clip, "rb") as f:
        r = client.post(
            f"/api/v1/todos/{board_job}/videos",
            headers=agents_hdrs["muse-2"],
            files={"file": ("clip.mp4", f, "video/mp4")},
            data={"seq_num": "1", "note": "no binding"},
        )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "shot_binding_required"


def test_video_upload_covered_applies_board_to_each_shot(
        client, board_job, agents_hdrs, tmp_path):
    """Upload gộp (covered_shot_indexes, không shot_index) → board áp cho TỪNG
    shot được phủ — merge flow sống mà board khép kín."""
    clip = _make_mp4(tmp_path / "covered.mp4")
    with open(clip, "rb") as f:
        r = client.post(
            f"/api/v1/todos/{board_job}/videos",
            headers=agents_hdrs["muse-2"],
            files={"file": ("clip.mp4", f, "video/mp4")},
            data={"seq_num": "1", "covered_shot_indexes": "1,2"},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "board_shots" in body, body
    hashes = {s["video_hash"] for s in body["board_shots"]}
    assert len(hashes) == 1, "cùng 1 clip → cùng video_hash"
    s1 = client.get(f"/api/v1/todos/{board_job}/shots/1",
                    headers=agents_hdrs["muse-2"]).json()
    s2 = client.get(f"/api/v1/todos/{board_job}/shots/2",
                    headers=agents_hdrs["muse-2"]).json()
    assert s1["video_hash"] == s2["video_hash"]
    assert s1["status"] == "generated" and s2["status"] == "generated"


# --------------------------------------------------------------------------
# B2 — migrate-legacy-videos: map kho cũ + grandfathered + outside_board
# --------------------------------------------------------------------------
def test_migrate_legacy_videos_maps_and_grandfathers(
        client, board_job, agents_hdrs, tmp_path):
    from backend.app.main import board, store as app_store

    # Upload qua board (shot 1 có video_hash) rồi xoá gắn kết — mô phỏng đúng
    # trạng thái legacy trên prod: todo_videos tồn tại nhưng shot không tham chiếu
    clip = _make_mp4(tmp_path / "legacy.mp4")
    with open(clip, "rb") as f:
        r = client.post(
            f"/api/v1/todos/{board_job}/videos",
            headers=agents_hdrs["muse-2"],
            files={"file": ("legacy.mp4", f, "video/mp4")},
            data={"shot_index": "1", "seq_num": "1", "covered_shot_indexes": "1"},
        )
    assert r.status_code == 201, r.text
    app_store.execute(
        "UPDATE shots SET video_hash=NULL, video_version=0, board_status=NULL, "
        "uploaded_by=NULL, declared_generator=NULL WHERE item_id=? AND index_num=1",
        (board_job,),
    )
    summary_before = client.get(f"/api/v1/todos/{board_job}/board/summary",
                                headers=agents_hdrs["muse-1"]).json()
    assert summary_before["outside_board"] >= 1, "video legacy phải được đếm ngoài board"

    res = board.migrate_legacy_videos()
    assert res["mapped_shots"] >= 1

    s1 = client.get(f"/api/v1/todos/{board_job}/shots/1",
                    headers=agents_hdrs["muse-1"]).json()
    assert s1["grandfathered"] is True, "video mapped từ kho cũ phải grandfathered"
    assert s1["uploaded_by"] == "legacy-mapping"
    assert s1["video_hash"], "legacy video phải được gắn lại"

    # grandfathered KHÔNG được dispatch cho reviewer (C-6 mở rộng)
    nxt = board.next_shot(board_job, "muse-1", mode="review")
    if nxt["shot"] is not None:
        assert nxt["shot"]["grandfathered"] is False

    # idempotent: chạy lại mapped=0
    res2 = board.migrate_legacy_videos()
    assert res2["mapped_shots"] == 0

    summary_after = client.get(f"/api/v1/todos/{board_job}/board/summary",
                               headers=agents_hdrs["muse-1"]).json()
    assert summary_after["outside_board"] == 0
    assert summary_after["grandfathered"] >= 1


# --------------------------------------------------------------------------
# Q3 — server-computed rubric: client KHÔNG được gửi score
# --------------------------------------------------------------------------
def test_client_score_is_rejected(client, agents_hdrs):
    r = _propose(client, agents_hdrs["muse-1"], source_url="https://example.com/q3")
    pid = r.json()["proposal"]["id"]
    cheat = client.post(f"/api/v1/muse-proposals/{pid}/reviews",
                        headers=agents_hdrs["muse-2"], json={"score": 9.9})
    assert cheat.status_code == 422, cheat.text
    assert cheat.json()["code"] == "client_score_rejected"


def test_dimensions_validation_and_server_sum(client, agents_hdrs):
    r = _propose(client, agents_hdrs["muse-1"], source_url="https://example.com/q3b")
    pid = r.json()["proposal"]["id"]

    # thiếu trục → 422
    missing = client.post(f"/api/v1/muse-proposals/{pid}/reviews",
                          headers=agents_hdrs["muse-2"],
                          json={"dimensions": {"hook": 3, "topicality": 3, "freshness": 2}})
    assert missing.status_code == 422, missing.text

    # giá trị vượt giới hạn → 422 (hook tối đa 3)
    over = client.post(f"/api/v1/muse-proposals/{pid}/reviews",
                       headers=agents_hdrs["muse-2"],
                       json={"dimensions": {"hook": 3.5, "topicality": 3, "freshness": 2,
                                            "feasibility": 2}})
    assert over.status_code == 422, over.text

    # server TỰ tính: trục chỉ đạt 9.0 → dù client "muốn" cao cũng fail
    nine = client.post(f"/api/v1/muse-proposals/{pid}/reviews",
                       headers=agents_hdrs["muse-2"],
                       json={"dimensions": dims_for(9.0)})
    assert nine.status_code == 201, nine.text
    assert nine.json()["reviews"][0]["score"] == pytest.approx(9.0)
    assert nine.json()["reviews"][0]["verdict"] == "fail"


# --------------------------------------------------------------------------
# Q6 — script-writer tiêu thụ hàng chờ: convert-to-brief
# --------------------------------------------------------------------------
def test_convert_to_brief_consumes_queue(client, agents_hdrs):
    r = _propose(client, agents_hdrs["muse-1"], source_url="https://example.com/q6")
    pid = r.json()["proposal"]["id"]
    _review(client, agents_hdrs["muse-2"], pid, 9.7)
    _review(client, agents_hdrs["muse-3"], pid, 9.9)

    # sai trạng thái (đang awaiting_owner) → 409
    early = client.post(f"/api/v1/muse-proposals/{pid}/convert-to-brief",
                        headers=agents_hdrs["muse-2"])
    assert early.status_code == 409, early.text

    ok = client.post(f"/api/v1/muse-proposals/{pid}/approve",
                     headers=agents_hdrs["owner"], json={})
    assert ok.status_code == 200, ok.text

    # script-writer (ingest) chuyển đề xuất → brief
    conv = client.post(f"/api/v1/muse-proposals/{pid}/convert-to-brief",
                       headers=agents_hdrs["muse-2"])
    assert conv.status_code == 201, conv.text
    body = conv.json()
    assert body["brief_id"], body
    assert body["brief"]["title"] == "ISS sắp chết — vì sao NASA im lặng? (case %d)" % _PROP_SEQ["n"]
    assert body["proposal"]["status"] == "in_production"
    assert body["proposal"]["brief_id"] == body["brief_id"]

    # chuyển lần 2 → 409 already_converted kèm brief_id cũ
    again = client.post(f"/api/v1/muse-proposals/{pid}/convert-to-brief",
                        headers=agents_hdrs["muse-3"])
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "already_converted"

    # hàng chờ needs_script không còn chứa nó
    q = client.get("/api/v1/muse-proposals/queue/needs-script",
                   headers=agents_hdrs["owner"]).json()
    assert all(p["id"] != pid for p in q["queue"])
