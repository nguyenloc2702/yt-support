"""Tests — chế độ `?mode=review` cho `shots/next` + `reviewable_counts` trong inbox.

Vấn đề: dispatch mặc định red_regen → empty_ready → waiting_review →
missing_info luôn đẩy việc gen trước, job chấm nằm dưới đáy — reviewer gọi
liên tục vẫn khó kiếm job cần chấm. mode=review cho reviewer một vòng lặp
"chỉ chấm": đủ điều kiện theo caller, hết việc trả no_review_work thay vì
quét tay từng shot rồi ăn 403/409.

Chạy:

    VIDEO_BOARD_ACCEPTANCE=1 pytest video-system/tests/test_board_review_mode.py -q

Nguyên tắc giống test_board_acceptance:
- Chỉ gọi HTTP qua `client` (black box), không import service nội bộ.
- Mỗi test tự dựng state của riêng nó, không phụ thuộc thứ tự chạy.
- Assert lên `code` trong envelope lỗi, không assert lên `message`.
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
    reason="Shot board API chưa tồn tại — bật bằng VIDEO_BOARD_ACCEPTANCE=1",
)


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
    """3 worker + owner — shot chỉ có thể được chấm bởi người không gen/upload."""
    from backend.app.main import auth

    extra = {"muse-1": "ingest", "muse-2": "ingest", "muse-3": "ingest", "owner": "admin"}
    saved_env = os.environ.get("VIDEO_API_KEYS")
    saved_keys = dict(auth.keys or {})
    os.environ["VIDEO_API_KEYS"] = (
        "test-admin:admin,test-ingest:ingest,test-read:read,"
        + ",".join(f"{k}:{v}" for k, v in extra.items())
    )
    auth.keys = dict(saved_keys, **extra)
    try:
        yield {k: {"Authorization": f"Bearer {k}"} for k in extra}
    finally:
        if saved_env is None:
            os.environ.pop("VIDEO_API_KEYS", None)
        else:
            os.environ["VIDEO_API_KEYS"] = saved_env
        auth.keys = saved_keys


@pytest.fixture()
def board_job(client, ingest, agents_hdrs, wav_file, make_item_fn, import_payload_factory):
    item_id = make_item_fn("review-mode")
    payload = import_payload_factory(count=3, duration=24.0, seg=8.0)
    payload["audio"]["file_path"] = str(wav_file(24.0))
    r = client.post(f"/api/v1/items/{item_id}/shots/import", headers=ingest, json=payload)
    assert r.status_code == 201, r.text
    return item_id


def _upload(client, hdrs, job_id, actor: str, shot_index: int, tmp_path, seq: int = 1):
    clip = _make_mp4(tmp_path / f"clip_r{shot_index}_{seq}.mp4",
                     color="navy" if seq % 2 else "darkgreen")
    with open(clip, "rb") as f:
        r = client.post(
            f"/api/v1/todos/{job_id}/videos",
            headers=hdrs[actor],
            files={"file": ("clip.mp4", f, "video/mp4")},
            data={"shot_index": str(shot_index), "seq_num": str(seq),
                  "covered_shot_indexes": str(shot_index)},
        )
    assert r.status_code == 201, r.text
    return r.json()["board_shot"]["video_hash"]


def _review(client, hdrs, job_id, index, video_hash, actor: str, codes: list[str]):
    r = client.post(
        f"/api/v1/todos/{job_id}/shots/{index}/reviews",
        headers=hdrs[actor],
        json={"defect_codes": codes, "pairing_ok": True,
              "reviewed_video_hash": video_hash, "frames_checked": [0, 12, 23]},
    )
    assert r.status_code == 201, r.text
    return r.json()["review"]


def _next(client, hdrs, job_id, mode: str | None = "review"):
    qs = f"?mode={mode}" if mode else ""
    r = client.get(f"/api/v1/todos/{job_id}/shots/next{qs}", headers=hdrs)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------
# mode=review — chỉ trả việc chấm mà caller đủ điều kiện
# --------------------------------------------------------------------------
def test_review_mode_returns_only_reviewable_work(
        client, board_job, agents_hdrs, tmp_path):
    vh = _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)

    # Người gen không được nhận việc chấm chính shot của mình — không phải
    # để nó quét rồi ăn 403 self_review.
    gen = _next(client, agents_hdrs["muse-2"], board_job)
    assert gen["shot"] is None
    assert gen["reason"] == "no_review_work"
    assert gen["retry_after_sec"] > 0, "vòng lặp reviewer cần biết nghỉ bao lâu"

    # Người ngoài nhận đúng shot đó, kèm video_hash để chấm mù.
    rv = _next(client, agents_hdrs["muse-1"], board_job)
    assert rv["reason"] == "waiting_review"
    assert rv["shot"]["index"] == 1
    assert rv["shot"]["video_hash"] == vh


def test_default_dispatch_still_buries_review_under_gen_work(
        client, board_job, agents_hdrs, tmp_path):
    """Không có mode → giữ nguyên C-3: việc gen đứng trước việc chấm.

    Đây chính là nỗi đau gốc của reviewer — test này khoá hành vi cũ để biết
    mode=review là thứ thay đổi cuộc chơi, không phải dispatch mặc định tự
    đổi theo ý muốn ai đó.
    """
    _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)

    no_mode = _next(client, agents_hdrs["muse-1"], board_job, mode=None)
    assert no_mode["reason"] in {"empty_ready", "missing_info"}, (
        f"dispatch mặc định phải ưu tiên việc gen trước; got {no_mode['reason']}")

    review = _next(client, agents_hdrs["muse-1"], board_job)
    assert review["reason"] == "waiting_review"
    assert review["shot"]["index"] == 1


def test_review_mode_skips_self_and_already_reviewed(
        client, board_job, agents_hdrs, tmp_path):
    vh = _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)
    _review(client, agents_hdrs, board_job, 1, vh, "muse-3", ["HELMET_BALL"])

    # muse-3 đã chấm version này → không được nhận lại (409 nếu để lọt).
    m3 = _next(client, agents_hdrs["muse-3"], board_job)
    assert m3["shot"] is None
    assert m3["reason"] == "no_review_work"

    # muse-1 chưa chấm, không phải người gen → vẫn nhận.
    m1 = _next(client, agents_hdrs["muse-1"], board_job)
    assert m1["shot"] is not None
    assert m1["shot"]["index"] == 1

    # muse-1 chấm pass lượt 2 → shot xanh, hết việc chấm.
    _review(client, agents_hdrs, board_job, 1, vh, "muse-1", ["HELMET_BALL"])
    after = _next(client, agents_hdrs["muse-1"], board_job)
    assert after["shot"] is None
    assert after["reason"] == "no_review_work"


def test_review_mode_prioritizes_one_pass_first(
        client, board_job, agents_hdrs, tmp_path):
    """Shot đã có 1 pass xếp trước: thêm đúng 1 lượt là xanh.

    Mỗi lượt review trên shot này chốt được một shot, trong khi chấm shot
    chưa ai xem thì còn phải chờ thêm một reviewer nữa. 2 lỗi nhẹ = 9.8 pass.
    """
    _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)
    vh2 = _upload(client, agents_hdrs, board_job, "muse-2", 2, tmp_path)
    _review(client, agents_hdrs, board_job, 2, vh2, "muse-3", ["HELMET_BALL"])

    m1 = _next(client, agents_hdrs["muse-1"], board_job)
    assert m1["reason"] == "waiting_review"
    assert m1["shot"]["index"] == 2, (
        "phải ưu tiên shot đã có 1 pass (chốt xanh ngay) trước shot chưa ai chấm")


def test_review_mode_excludes_grandfathered(
        client, board_job, agents_hdrs, tmp_path):
    """C-6: video trước ngày bật board không cần chấm — không được dispatch.

    Migration qua HTTP endpoint thật (admin), không đụng service nội bộ.
    """
    _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)
    _upload(client, agents_hdrs, board_job, "muse-2", 2, tmp_path)

    mig = client.post("/api/v1/board/migrate-grandfathered",
                      headers=agents_hdrs["owner"],
                      json={"board_enabled_at": time.time() + 1.0})
    assert mig.status_code == 200, mig.text
    assert mig.json()["migrated"] >= 2, mig.text

    # Shot 3 upload SAU migration → không grandfathered, là việc chấm thật.
    vh3 = _upload(client, agents_hdrs, board_job, "muse-2", 3, tmp_path)

    m1 = _next(client, agents_hdrs["muse-1"], board_job)
    assert m1["reason"] == "waiting_review"
    assert m1["shot"]["index"] == 3, (
        "shot grandfathered không phải việc chấm — dispatch nó là phí lượt review")
    assert m1["shot"]["video_hash"] == vh3

    m2 = _next(client, agents_hdrs["muse-2"], board_job)
    assert m2["shot"] is None, "người gen không được nhận việc chấm kể cả shot mới"


def test_review_mode_rejects_unknown_mode(client, board_job, agents_hdrs):
    r = client.get(f"/api/v1/todos/{board_job}/shots/next?mode=gen",
                   headers=agents_hdrs["muse-1"])
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_mode"


# --------------------------------------------------------------------------
# inbox — reviewable_counts giúp reviewer tìm job còn chỗ chấm
# --------------------------------------------------------------------------
def _scoped(body, job_id):
    """Lọc danh sách inbox về đúng item của test này.

    Store dùng chung trong cả session pytest — item của test trước vẫn nằm
    trong inbox. Mọi assert phải scope theo item, không assert toàn cục.
    """
    return [s for s in body["waiting_review_shots"] if s["item_id"] == job_id]


def test_inbox_reviewable_counts_are_per_caller(
        client, board_job, agents_hdrs, tmp_path):
    _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)
    vh2 = _upload(client, agents_hdrs, board_job, "muse-2", 2, tmp_path)
    _review(client, agents_hdrs, board_job, 2, vh2, "muse-3", ["HELMET_BALL"])

    # muse-1 đủ điều kiện chấm cả 2 shot.
    inbox1 = client.get("/api/v1/agent/inbox", headers=agents_hdrs["muse-1"]).json()
    assert inbox1["reviewable_counts"].get(board_job) == 2
    indexes = [s["index"] for s in _scoped(inbox1, board_job)]
    assert indexes == [2, 1], "inbox phải xếp shot đã có 1 pass lên trước"

    # muse-3 đã chấm shot 2 → còn đúng 1 job cho nó.
    inbox3 = client.get("/api/v1/agent/inbox", headers=agents_hdrs["muse-3"]).json()
    assert inbox3["reviewable_counts"].get(board_job) == 1
    assert [s["index"] for s in _scoped(inbox3, board_job)] == [1]

    # muse-2 là người gen của cả hai → item này không còn job chấm nào cho nó.
    inbox2 = client.get("/api/v1/agent/inbox", headers=agents_hdrs["muse-2"]).json()
    assert board_job not in inbox2["reviewable_counts"]
    assert _scoped(inbox2, board_job) == []


def test_inbox_reviewable_counts_drop_after_shot_turns_green(
        client, board_job, agents_hdrs, tmp_path):
    """Counter đếm việc THẬT, không đếm shot: xanh rồi thì biến khỏi mọi inbox."""
    vh = _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)
    _review(client, agents_hdrs, board_job, 1, vh, "muse-3", ["HELMET_BALL"])
    _review(client, agents_hdrs, board_job, 1, vh, "muse-1", ["HELMET_BALL"])

    inbox = client.get("/api/v1/agent/inbox", headers=agents_hdrs["muse-1"]).json()
    assert board_job not in inbox["reviewable_counts"]
    assert _scoped(inbox, board_job) == []
