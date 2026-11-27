"""Acceptance tests — Shot Board v1.0, tranche 1 (C-1, C-2, C-3, C-5, C-13).

Chủ sở hữu: WorkBuddy (Reviewer C). Người tiêu thụ: Antigravity (implementer).
Nguồn contract: `plans/team/review_c_shot_board_v1.md` + spec §8 / §13.1.

Chạy:

    VIDEO_BOARD_ACCEPTANCE=1 pytest video-system/tests/test_board_acceptance.py -q

Vì sao gate bằng env: bộ này viết TRƯỚC khi API board tồn tại (§13.1 yêu cầu
"agent code phải demo được"), nên mặc định skip để CI repo không đỏ vì tính
năng chưa có. Khi C-3 xong thì bật gate cho riêng file này trong CI.

Nguyên tắc của bộ test này:
- Chỉ gọi HTTP qua `client` (black box). Không import service nội bộ, không
  assert lên bảng SQL — để refactor bên trong không làm đỏ test.
- Mỗi test tự dựng state của riêng nó, không phụ thuộc thứ tự chạy.
- Assert lên `code` trong envelope lỗi (§8.0), không assert lên `message`.

Nếu setup media khác với giả định ở đây (keyframe/audio/upload path) thì sửa
fixture `job`/`upload_clip`, KHÔNG nới assertion — assertion là contract.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("VIDEO_BOARD_ACCEPTANCE") != "1",
    reason="Shot board API chưa tồn tại — bật bằng VIDEO_BOARD_ACCEPTANCE=1",
)

# Mã lỗi mới do tranche 1 sinh ra (bổ sung vào bảng §8.3 — xem Turn 3 của
# thread 2026-11-27-001, mục "3 mã lỗi mới"):
#   422 client_score_rejected | 422 regen_brief_required | 422 brief_required
#   422 stale_brief | 409 must_change_staging | 409 idempotency_key_conflict

BOARD_AGENTS = {"muse-1": "ingest", "muse-2": "ingest", "muse-3": "ingest",
                "owner": "admin"}

# Bảng trừ điểm §4.2 — bản sao độc lập, cố ý KHÔNG import từ backend.
# Nếu backend đổi số ở đây thì test phải đỏ: đó là điểm của nó.
DEDUCTION = {
    "FACE_REFLECTION": (0.4, True),
    "SUBJECT_OUT_OF_FRAME": (0.4, True),
    "CAMERA_WRONG_DIRECTION": (1.0, True),
    "INVENTED_ACTION": (10.0, True),
    "DUPLICATE_OBJECT": (0.2, False),
    "HELMET_BALL": (0.2, False),
    "COSTUME_MORPH": (0.2, False),
    "PANEL_COUNT_MORPH": (0.2, False),
    "TEXT_EMERGES": (0.2, False),
    "DISCLOSE_ONLY": (0.0, False),
}


def expected_score(codes: list[str]) -> float:
    return round(max(0.0, 10.0 - sum(DEDUCTION[c][0] for c in codes)), 2)


def expected_verdict(codes: list[str], pairing_ok: bool = True) -> str:
    if not pairing_ok:
        return "fail"
    if any(DEDUCTION[c][1] for c in codes):
        return "fail"
    return "fail" if expected_score(codes) <= 9.5 else "pass"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture()
def board_agents(client):
    """4 identity cho board: 3 worker + owner (spec C-7 cần ≥ 4 agent active).

    Env phải set TRỰC TIẾP trên os.environ, không qua monkeypatch: middleware
    PIN gate đọc api_keys() từ env ở mỗi request, và thứ tự fixture làm
    monkeypatch bị trả về trước khi request chạy.
    """
    import os as _os

    from backend.app.main import auth

    base = "test-admin:admin,test-ingest:ingest,test-read:read"
    extra = ",".join(f"{name}:{scope}" for name, scope in BOARD_AGENTS.items())
    saved_env = _os.environ.get("VIDEO_API_KEYS")
    saved_keys = dict(auth.keys or {})
    _os.environ["VIDEO_API_KEYS"] = f"{base},{extra}"
    auth.keys = dict(saved_keys, **BOARD_AGENTS)
    try:
        yield {name: {"Authorization": f"Bearer {name}"} for name in BOARD_AGENTS}
    finally:
        if saved_env is None:
            _os.environ.pop("VIDEO_API_KEYS", None)
        else:
            _os.environ["VIDEO_API_KEYS"] = saved_env
        auth.keys = saved_keys


@pytest.fixture()
def job(client, ingest, board_agents, wav_file, make_item_fn,
        import_payload_factory):
    """Job 4 shot × 8s, đủ info, chưa có video.

    KHÔNG `from tests.conftest import ...`: import đó chạy lại module conftest
    và ghi đè VIDEO_API_KEYS về giá trị gốc, làm mất key của board_agents.
    """
    item_id = make_item_fn("board acceptance")
    payload = import_payload_factory(count=4, duration=32.0, seg=8.0)
    payload["audio"]["file_path"] = str(wav_file(32.0))
    r = client.post(f"/api/v1/items/{item_id}/shots/import", headers=ingest,
                    json=payload)
    assert r.status_code == 201, r.text
    return item_id


def _make_test_mp4(path: Path, duration_sec: float, color: str = "steelblue") -> Path:
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
def upload_clip(client, job, board_agents, adapters, tmp_path):
    """Upload 1 clip cho 1 shot theo đường board (job-level /videos + shot_index).

    Contract C-2/C-5: `POST /api/v1/todos/{id}/videos` nhận thêm 4 field tuỳ
    chọn — `shot_index`, `addresses_review_id`, `staging_changed`,
    `declared_generator` — và CHỈ khi có `shot_index` thì luật board mới áp.
    Không có `shot_index` → giữ nguyên hành vi cũ (test #9 §13.1).
    """
    counter = {"n": 0}

    def _upload(actor: str = "muse-2", shot_index: int = 1, key: str | None = None,
                clip: Path | None = None, seq_num: int | None = None, **board_fields):
        counter["n"] += 1
        if clip is None:
            clip = _make_test_mp4(tmp_path / f"clip{counter['n']}.mp4", 8.0,
                                  color="navy" if counter["n"] % 2 else "darkgreen")
        data = {"shot_index": str(shot_index),
                "seq_num": str(seq_num if seq_num is not None else counter["n"]),
                "covered_shot_indexes": str(shot_index)}
        data.update({k: str(v) for k, v in board_fields.items()})
        headers = dict(board_agents[actor])
        if key:
            headers["Idempotency-Key"] = key
        with open(clip, "rb") as f:
            return client.post(f"/api/v1/todos/{job}/videos",
                               headers=headers,
                               files={"file": ("clip.mp4", f, "video/mp4")},
                               data=data)

    return _upload


def _shot(client, hdrs, job_id, index=1):
    r = client.get(f"/api/v1/todos/{job_id}/shots/{index}", headers=hdrs)
    assert r.status_code == 200, r.text
    return r.json()


def _review(client, hdrs, job_id, index, video_hash, codes, *, pairing_ok=True,
            regen_brief=None, key=None):
    body = {"defect_codes": codes, "pairing_ok": pairing_ok,
            "reviewed_video_hash": video_hash, "frames_checked": [0, 12, 23]}
    if regen_brief is not None:
        body["regen_brief"] = regen_brief
    headers = dict(hdrs)
    if key:
        headers["Idempotency-Key"] = key
    return client.post(f"/api/v1/todos/{job_id}/shots/{index}/reviews",
                       headers=headers, json=body)


# --------------------------------------------------------------------------
# C-3 — dispatch không được đẩy agent đi chấm shot đỏ
# --------------------------------------------------------------------------
def test_c3_next_never_returns_red_unreviewed(client, job, board_agents):
    """`shots/next` chỉ được trả 4 loại việc; `red_unreviewed` phải biến mất."""
    r = client.get(f"/api/v1/todos/{job}/shots/next", headers=board_agents["muse-1"])
    assert r.status_code == 200, r.text
    body = r.json()
    if body.get("shot") is None:
        assert body["reason"] == "no_work"
        return
    assert body["reason"] in {"red_regen", "empty_ready", "waiting_review",
                              "missing_info"}, body["reason"]
    assert body["reason"] != "red_unreviewed"


def test_c3_inbox_exposes_regen_and_unclaimed_split(client, job, board_agents):
    """Inbox tách `red_regen_shots` (nghĩa vụ) khỏi `red_unclaimed` (cơ hội)."""
    r = client.get(f"/api/v1/agent/inbox", headers=board_agents["muse-1"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert "red_regen_shots" in body, "inbox thiếu red_regen_shots[]"
    assert "red_unclaimed" in body, "inbox thiếu red_unclaimed[]"
    assert "red_shots" not in body, "nhóm cũ red_shots[] phải bị bỏ, không để song song"


def test_c3_red_regen_is_scoped_to_generator(client, upload_clip, board_agents, job):
    """`red_regen` chỉ trả shot đỏ do CHÍNH caller gen — không phải shot đỏ của người khác.

    Đây là điểm khác biệt duy nhất giữa `red_regen` (nghĩa vụ, của tôi) và
    `red_unclaimed` (cơ hội, ai cũng claim). Nếu scope sai thì C-3 biến thành
    `red_unreviewed` mặc áo mới: agent vẫn bị đẩy đi làm việc của người khác.
    """
    up = upload_clip(actor="muse-2", shot_index=1)
    assert up.status_code == 201, up.text
    video_hash = _shot(client, board_agents["muse-2"], job, 1)["video_hash"]
    rv = _review(client, board_agents["muse-3"], job, 1, video_hash,
                 ["CAMERA_WRONG_DIRECTION"], regen_brief=SAMPLE_BRIEF)
    assert rv.status_code in (200, 201), rv.text
    assert _shot(client, board_agents["muse-2"], job, 1)["status"] == "red"

    # Người gen (muse-2) phải nhận shot đỏ này ở dạng red_regen.
    mine = client.get(f"/api/v1/todos/{job}/shots/next", headers=board_agents["muse-2"])
    assert mine.status_code == 200, mine.text
    assert mine.json().get("shot") is not None, "người gen không nhận được việc gen lại"
    assert mine.json()["reason"] == "red_regen"
    assert mine.json()["shot"]["index"] == 1

    # Người khác (muse-1) không được nhận nó như nghĩa vụ red_regen.
    other = client.get(f"/api/v1/todos/{job}/shots/next", headers=board_agents["muse-1"])
    assert other.status_code == 200, other.text
    body = other.json()
    if body.get("shot") is not None and body.get("reason") == "red_regen":
        assert body["shot"]["index"] != 1, \
            "muse-1 nhận shot đỏ của muse-2 như việc của mình — scope red_regen sai"


# --------------------------------------------------------------------------
# C-1 — score/verdict do server tính
# --------------------------------------------------------------------------
def test_c1_client_score_is_rejected(client, upload_clip, board_agents, job):
    """Review body không còn field `score`; gửi lên phải bị từ chối rõ ràng."""
    up = upload_clip(actor="muse-2", shot_index=1)
    assert up.status_code == 201, up.text
    video_hash = _shot(client, board_agents["muse-2"], job)["video_hash"]
    r = client.post(f"/api/v1/todos/{job}/shots/1/reviews",
                    headers=board_agents["muse-3"],
                    json={"score": 10.0, "defect_codes": [], "pairing_ok": True,
                          "reviewed_video_hash": video_hash, "frames_checked": [0, 1, 2]})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "client_score_rejected"


SAMPLE_BRIEF = {
    "what_wrong": "pull-out bị render thành push-in, subject to dần ra",
    "do_instead": "camera lùi ra, subject nhỏ dần; ghi 'camera pulls back slowly' ở đầu prompt",
    "staging_change": "camera_move: PULL_OUT -> STATIC",
}


def test_c1_one_fail_level_code_is_fail_despite_score_9_6(
        client, upload_clip, board_agents, job):
    """FACE_REFLECTION = −0.4 → 9.6 nhưng phải FAIL (đây là bug số học gốc).

    `regen_brief` đi kèm vì C-2 bắt buộc nó trên MỌI review fail — test này kiểm
    số học, không kiểm sự vắng mặt của brief.
    """
    up = upload_clip(actor="muse-2", shot_index=1)
    assert up.status_code == 201, up.text
    video_hash = _shot(client, board_agents["muse-2"], job)["video_hash"]
    codes = ["FACE_REFLECTION"]
    r = _review(client, board_agents["muse-3"], job, 1, video_hash, codes,
                regen_brief=SAMPLE_BRIEF)
    assert r.status_code in (200, 201), r.text
    rv = r.json()["review"]
    assert rv["score"] == pytest.approx(expected_score(codes)), rv
    assert rv["verdict"] == "fail", "9.6 mà PASS thì bảng 4.2 vẫn là trang trí"
    assert _shot(client, board_agents["muse-2"], job)["status"] == "red"


def test_c1_light_codes_arithmetic(client, upload_clip, board_agents, job):
    """2 lỗi nhẹ = 9.6 pass; 3 lỗi nhẹ = 9.4 fail — ngưỡng 9.5 phải có nghĩa."""
    up = upload_clip(actor="muse-2", shot_index=2)
    assert up.status_code == 201, up.text
    video_hash = _shot(client, board_agents["muse-2"], job, 2)["video_hash"]

    two = ["DUPLICATE_OBJECT", "HELMET_BALL"]
    r2 = _review(client, board_agents["muse-3"], job, 2, video_hash, two)
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["review"]["score"] == pytest.approx(9.6)
    assert r2.json()["review"]["verdict"] == "pass"

    three = two + ["COSTUME_MORPH"]
    r3 = _review(client, board_agents["muse-1"], job, 2, video_hash, three,
                 regen_brief=SAMPLE_BRIEF)
    assert r3.status_code in (200, 201), r3.text
    assert r3.json()["review"]["score"] == pytest.approx(9.4)
    assert r3.json()["review"]["verdict"] == "fail"


# --------------------------------------------------------------------------
# C-2 — vòng đỏ → regen_brief → gen lại
# --------------------------------------------------------------------------
def test_c2_fail_review_requires_regen_brief(client, upload_clip, board_agents, job):
    up = upload_clip(actor="muse-2", shot_index=3)
    assert up.status_code == 201, up.text
    video_hash = _shot(client, board_agents["muse-2"], job, 3)["video_hash"]
    r = _review(client, board_agents["muse-3"], job, 3, video_hash,
                ["CAMERA_WRONG_DIRECTION"], regen_brief=None)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "regen_brief_required"


def test_c2_upload_on_red_requires_addresses_review_id(
        client, upload_clip, board_agents, job):
    """Shot đỏ mà upload không khai `addresses_review_id` → 422 brief_required."""
    up = upload_clip(actor="muse-2", shot_index=3)
    assert up.status_code == 201, up.text
    video_hash = _shot(client, board_agents["muse-2"], job, 3)["video_hash"]
    r = _review(client, board_agents["muse-3"], job, 3, video_hash,
                ["CAMERA_WRONG_DIRECTION"],
                regen_brief={"what_wrong": "pull-out ra push-in",
                             "do_instead": "ghi 'camera pulls back slowly' đầu prompt",
                             "staging_change": "camera_move: PULL_OUT -> STATIC"})
    assert r.status_code in (200, 201), r.text
    assert _shot(client, board_agents["muse-2"], job, 3)["status"] == "red"

    up2 = upload_clip(actor="muse-2", shot_index=3)
    assert up2.status_code == 422, up2.text
    assert up2.json()["code"] == "brief_required"


def test_c2_rejected_upload_must_not_persist_video(
        client, upload_clip, board_agents, job):
    """Upload bị board từ chối không được để lại video version.

    Đây là hệ quả trực tiếp của thứ tự validate (Turn 3: board fields → media).
    Nếu `submit_video` chạy trước rồi board mới từ chối thì:
    - job tăng `uploaded_count` và shot bị đánh dấu "có video" dù request 404/422;
    - `must_change_staging` đếm cả upload bị từ chối → luật §5.4 tự vô hiệu.
    """
    def uploaded_count() -> int:
        body = client.get(f"/api/v1/todos/{job}", headers=board_agents["muse-1"]).json()
        return int((body.get("video_progress") or {}).get("uploaded_count") or 0)

    before = uploaded_count()
    r = upload_clip(actor="muse-2", shot_index=99)
    assert r.status_code == 404, r.text
    assert r.json().get("code") == "shot_not_found", (
        f"KeyError của board đang bị map thành 'todo item not found'; body={r.text}")
    assert uploaded_count() == before, \
        "upload bị từ chối vẫn ghi video version — board check phải chạy TRƯỚC submit_video"


def test_c2_third_upload_without_staging_change_is_409(
        client, upload_clip, board_agents, job):
    """Luật §5.4 phải được máy thi hành, không phải quy trình miệng."""
    up = upload_clip(actor="muse-2", shot_index=4)
    assert up.status_code == 201, up.text
    vh = _shot(client, board_agents["muse-2"], job, 4)["video_hash"]
    r1 = _review(client, board_agents["muse-3"], job, 4, vh,
                 ["DUPLICATE_OBJECT"],
                 regen_brief={"what_wrong": "thừa object mép phải",
                              "do_instead": "bỏ object thứ hai",
                              "staging_change": "none"})
    assert r1.status_code in (200, 201), r1.text
    review_id = r1.json()["review"]["id"]

    up2 = upload_clip(actor="muse-2", shot_index=4,
                      addresses_review_id=review_id, staging_changed="false")
    assert up2.status_code == 201, up2.text

    up3 = upload_clip(actor="muse-2", shot_index=4,
                      addresses_review_id=review_id, staging_changed="false")
    assert up3.status_code == 409, up3.text
    assert up3.json()["code"] == "must_change_staging"


def test_c2_recurring_defect_flags_same_code_on_new_version(
        client, upload_clip, board_agents, job):
    """Tick lại đúng lỗi cũ ở version mới thì review mới phải mang `recurring_defect`."""
    brief = {"what_wrong": "thừa object mép phải", "do_instead": "bỏ object thứ hai",
             "staging_change": "none"}

    up1 = upload_clip(actor="muse-2", shot_index=3)
    assert up1.status_code == 201, up1.text
    vh1 = _shot(client, board_agents["muse-2"], job, 3)["video_hash"]
    r1 = _review(client, board_agents["muse-3"], job, 3, vh1, ["FACE_REFLECTION"],
                 regen_brief=brief)
    assert r1.status_code in (200, 201), r1.text
    assert r1.json()["review"]["recurring_defect"] is False, \
        "lỗi ở version đầu không thể là lỗi tái phát"

    up2 = upload_clip(actor="muse-2", shot_index=3,
                      addresses_review_id=r1.json()["review"]["id"],
                      staging_changed="true")
    assert up2.status_code == 201, up2.text
    vh2 = _shot(client, board_agents["muse-2"], job, 3)["video_hash"]
    r2 = _review(client, board_agents["muse-3"], job, 3, vh2, ["FACE_REFLECTION"],
                 regen_brief=brief)
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["review"]["recurring_defect"] is True, \
        "cùng FACE_REFLECTION ở version 2 phải bị đánh dấu tái phát"


# --------------------------------------------------------------------------
# C-5 — chống lách "upload hộ"
# --------------------------------------------------------------------------
def test_c5_upload_records_both_identities(client, upload_clip, board_agents, job):
    up = upload_clip(actor="muse-2", shot_index=1, declared_generator="muse-1")
    assert up.status_code == 201, up.text
    shot = _shot(client, board_agents["muse-2"], job, 1)
    assert shot["uploaded_by"] == "muse-2"
    assert shot["declared_generator"] == "muse-1", \
        "không có declared_generator thì 'ai gen' bị ghi đè thành người upload"


def test_c5_declared_generator_cannot_review_own_shot(
        client, upload_clip, board_agents, job):
    up = upload_clip(actor="muse-2", shot_index=1, declared_generator="muse-1")
    assert up.status_code == 201, up.text
    video_hash = _shot(client, board_agents["muse-2"], job, 1)["video_hash"]
    r = _review(client, board_agents["muse-1"], job, 1, video_hash, ["HELMET_BALL"])
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "self_review"


# --------------------------------------------------------------------------
# C-13 — idempotency
# --------------------------------------------------------------------------
def test_c13_replay_returns_200_not_409(client, upload_clip, board_agents, job):
    up = upload_clip(actor="muse-2", shot_index=2)
    assert up.status_code == 201, up.text
    video_hash = _shot(client, board_agents["muse-2"], job, 2)["video_hash"]
    key = "board-acc-c13-1"

    first = _review(client, board_agents["muse-3"], job, 2, video_hash,
                    ["HELMET_BALL"], key=key)
    assert first.status_code in (200, 201), first.text
    second = _review(client, board_agents["muse-3"], job, 2, video_hash,
                     ["HELMET_BALL"], key=key)

    assert second.status_code == 200, second.text
    assert second.json()["idempotent_replay"] is True
    assert second.json()["review"]["id"] == first.json()["review"]["id"]


def test_c13_replay_is_scoped_by_caller(client, upload_clip, board_agents, job):
    """Key của agent khác không được trả response của người đi trước."""
    up = upload_clip(actor="muse-2", shot_index=2)
    assert up.status_code == 201, up.text
    video_hash = _shot(client, board_agents["muse-2"], job, 2)["video_hash"]
    key = "board-acc-c13-2"

    first = _review(client, board_agents["muse-3"], job, 2, video_hash,
                    ["HELMET_BALL"], key=key)
    assert first.status_code in (200, 201), first.text
    stolen = _review(client, board_agents["muse-1"], job, 2, video_hash,
                     ["HELMET_BALL"], key=key)

    assert stolen.status_code == 409, stolen.text
    assert stolen.json()["code"] == "idempotency_key_conflict"


def test_c13_replay_of_successful_upload_returns_200_not_409(
        client, upload_clip, board_agents, job, tmp_path):
    """Replay một upload ĐÃ THÀNH CÔNG phải trả 200 + response gốc, không 409.

    Đây là lỗ hổng của C-13 trên đường upload: `preflight_upload` chạy TRƯỚC
    `_idempotent_call`, nên khi retry cùng key nó đánh giá lại trên state mà
    chính lần đầu vừa thay đổi (video_version đã +1, review đã bị set stale).
    Kết quả: agent timeout, retry an toàn theo đúng luật idempotency, và nhận
    409 `must_change_staging` cho một request đã ghi thành công.

    Thứ tự đúng: tra cache idempotency TRƯỚC (rẻ, không cần đọc body), chỉ khi
    miss mới preflight → đọc media → submit. Hai yêu cầu không xung đột.
    """
    clip = _make_test_mp4(tmp_path / "replay.mp4", 8.0, color="navy")
    key = "board-acc-c13-upload"

    up1 = upload_clip(actor="muse-2", shot_index=4, clip=clip)
    assert up1.status_code == 201, up1.text
    vh = _shot(client, board_agents["muse-2"], job, 4)["video_hash"]

    rv = _review(client, board_agents["muse-3"], job, 4, vh, ["DUPLICATE_OBJECT"],
                 regen_brief={"what_wrong": "thừa object mép phải",
                              "do_instead": "bỏ object thứ hai",
                              "staging_change": "none"})
    assert rv.status_code in (200, 201), rv.text
    review_id = rv.json()["review"]["id"]

    first = upload_clip(actor="muse-2", shot_index=4, key=key, clip=clip, seq_num=1,
                        addresses_review_id=review_id, staging_changed="false")
    assert first.status_code == 201, first.text
    video_id = first.json()["video"]["id"]

    replay = upload_clip(actor="muse-2", shot_index=4, key=key, clip=clip, seq_num=1,
                         addresses_review_id=review_id, staging_changed="false")
    assert replay.status_code == 200, (
        f"replay của upload thành công bị trả {replay.status_code} — "
        f"preflight đang chạy ngoài lớp idempotency; body={replay.text}")
    assert replay.json().get("idempotent_replay") is True
    assert replay.json()["video"]["id"] == video_id

    shot = _shot(client, board_agents["muse-2"], job, 4)
    assert shot["video_version"] == 2, "replay không được tăng version lần hai"



def test_c13_same_key_different_shot_must_not_replay(
        client, upload_clip, board_agents, job, tmp_path):
    """Cùng key nhưng khác shot phải bị 422 `idempotency_key_reused`, không replay.

    `_idempotent_hit` tra cache TRƯỚC khi so body hash, nên nếu agent dùng một key
    cố định cho mọi upload thì shot thứ hai nhận response của shot thứ nhất và
    **không có gì được upload** — im lặng, tệ hơn cả 409.
    """
    clip = _make_test_mp4(tmp_path / "samekey.mp4", 8.0, color="navy")
    key = "board-acc-c13-samekey"

    a = upload_clip(actor="muse-2", shot_index=2, key=key, clip=clip)
    assert a.status_code == 201, a.text

    b = upload_clip(actor="muse-2", shot_index=3, key=key, clip=clip)
    assert b.status_code == 422, (
        f"cùng key khác shot bị trả {b.status_code} — replay im lặng, "
        f"shot 3 không được upload; body={b.text}")
    assert b.json().get("code") == "idempotency_key_reused"
