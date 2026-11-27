"""Tests — backend phục vụ web Review Board: defect-codes + comment per-shot.

Chạy:

    VIDEO_BOARD_ACCEPTANCE=1 pytest video-system/tests/test_board_web_support.py -q

Nguyên tắc giống test_board_acceptance: chỉ gọi HTTP qua `client`, mỗi test
tự dựng state, assert lên `code`. Bảng §4.2 được copy độc lập — endpoint
defect-codes phải khớp bản copy, lệch số nào thì đỏ số đó.
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

# Bản copy độc lập bảng §4.2 (như test_board_acceptance) — endpoint
# /board/defect-codes phải trả đúng các số này cho UI.
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

AGENTS = {"muse-1": "ingest", "muse-2": "ingest", "muse-3": "ingest",
          "muse-boss": "manager", "owner": "admin"}


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


@pytest.fixture()
def board_job(client, ingest, agents_hdrs, wav_file, make_item_fn, import_payload_factory):
    item_id = make_item_fn("web-support")
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


def _upload(client, hdrs, job_id, actor: str, shot_index: int, tmp_path,
            seq: int = 1, **board_fields):
    clip = _make_mp4(tmp_path / f"ws_clip{shot_index}_{seq}.mp4",
                     color="navy" if seq % 2 else "darkgreen")
    data = {"shot_index": str(shot_index), "seq_num": str(seq),
            "covered_shot_indexes": str(shot_index)}
    data.update({k: str(v) for k, v in board_fields.items()})
    with open(clip, "rb") as f:
        r = client.post(
            f"/api/v1/todos/{job_id}/videos",
            headers=hdrs[actor],
            files={"file": ("clip.mp4", f, "video/mp4")},
            data=data,
        )
    assert r.status_code == 201, r.text
    return r.json()["board_shot"]["video_hash"]


def _review(client, hdrs, job_id, index, video_hash, actor: str, codes: list[str],
            regen_brief: dict | None = None):
    body = {"defect_codes": codes, "pairing_ok": True,
            "reviewed_video_hash": video_hash, "frames_checked": [0, 12, 23]}
    if regen_brief is not None:
        body["regen_brief"] = regen_brief
    r = client.post(
        f"/api/v1/todos/{job_id}/shots/{index}/reviews",
        headers=hdrs[actor],
        json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()["review"]


def _comment(client, hdrs, job_id, index, body: str, key: str | None = None):
    headers = dict(hdrs)
    if key:
        headers["Idempotency-Key"] = key
    return client.post(f"/api/v1/todos/{job_id}/shots/{index}/comments",
                       headers=headers, json={"body": body})


# --------------------------------------------------------------------------
# defect-codes — UI không hardcode bảng §4.2
# --------------------------------------------------------------------------
def test_defect_codes_match_independent_table(client, agents_hdrs):
    r = client.get("/api/v1/board/defect-codes", headers=agents_hdrs["muse-1"])
    assert r.status_code == 200, r.text
    codes = {c["code"]: c for c in r.json()["codes"]}
    assert set(codes) == set(DEDUCTION), "endpoint thiếu/thừa code so với bảng §4.2"
    for code, (ded, fail_level) in DEDUCTION.items():
        assert codes[code]["deduction"] == pytest.approx(ded), code
        assert codes[code]["fail_level"] is fail_level, code
        assert codes[code]["description"], code


# --------------------------------------------------------------------------
# comment — CRUD + idempotency + validation
# --------------------------------------------------------------------------
def test_comment_post_list_roundtrip(client, board_job, agents_hdrs, tmp_path):
    vh = _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)
    _review(client, agents_hdrs, board_job, 1, vh, "muse-3", ["HELMET_BALL"])
    # muse-3 đã chấm → thấy thread; tự post được comment
    r = _comment(client, agents_hdrs["muse-3"], board_job, 1,
                 "Chậm 0.3s đầu câu — kéo prompt sang trái 3 frame")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["comment"]["author"] == "muse-3"
    assert body["comment"]["video_version"] == 1

    listing = client.get(f"/api/v1/todos/{board_job}/shots/1/comments",
                         headers=agents_hdrs["muse-3"]).json()
    assert listing["comments_visible"] is True
    assert listing["comment_count"] == 1
    assert listing["comments"][0]["body"].startswith("Chậm 0.3s")


def test_comment_replay_is_idempotent(client, board_job, agents_hdrs, tmp_path):
    _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)
    first = _comment(client, agents_hdrs["muse-1"], board_job, 1, "góp ý 1", key="ws-cmt-1")
    assert first.status_code == 201, first.text
    replay = _comment(client, agents_hdrs["muse-1"], board_job, 1, "góp ý 1", key="ws-cmt-1")
    assert replay.status_code == 200, replay.text
    assert replay.json().get("idempotent_replay") is True
    assert replay.json()["comment"]["id"] == first.json()["comment"]["id"]
    count = client.get(f"/api/v1/todos/{board_job}/shots/1/comments",
                       headers=agents_hdrs["muse-1"]).json()["comment_count"]
    assert count == 1, "replay không được tạo comment thứ hai"


def test_comment_validation_and_scopes(client, board_job, agents_hdrs, tmp_path):
    _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)
    empty = _comment(client, agents_hdrs["muse-1"], board_job, 1, "   ")
    assert empty.status_code == 422, empty.text
    too_long = _comment(client, agents_hdrs["muse-1"], board_job, 1, "x" * 2001)
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["code"] == "comment_too_long"

    missing = _comment(client, agents_hdrs["muse-1"], board_job, 99, "shot 99 không có")
    assert missing.status_code == 404, missing.text
    assert missing.json()["code"] == "shot_not_found"

    # scope read không được post (mutation cần ingest)
    read_hdrs = {"Authorization": "Bearer test-read"}
    forbidden = _comment(client, read_hdrs, board_job, 1, "đọc thôi")
    assert forbidden.status_code == 403, forbidden.text


# --------------------------------------------------------------------------
# visibility — đúng luật chấm mù C-4
# --------------------------------------------------------------------------
def test_comment_visibility_three_layers(client, board_job, agents_hdrs, tmp_path):
    """Manager + người gen + đã chấm thấy thread; worker chưa chấm chỉ thấy đếm."""
    vh = _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)
    _comment(client, agents_hdrs["muse-3"], board_job, 1, "mặt phản chiếu ở giây 2")

    # muse-1: worker chưa chấm, không phải người gen → ẩn nội dung
    w = client.get(f"/api/v1/todos/{board_job}/shots/1/comments",
                   headers=agents_hdrs["muse-1"]).json()
    assert w["comments_visible"] is False
    assert w["comment_count"] == 1
    assert w["comments"] == []

    # muse-2: người gen → thấy ngay (đây là lúc cần comment để sửa)
    g = client.get(f"/api/v1/todos/{board_job}/shots/1/comments",
                   headers=agents_hdrs["muse-2"]).json()
    assert g["comments_visible"] is True
    assert g["comments"][0]["author"] == "muse-3"

    # muse-boss: manager → thấy hết
    m = client.get(f"/api/v1/todos/{board_job}/shots/1/comments",
                   headers=agents_hdrs["muse-boss"]).json()
    assert m["comments_visible"] is True

    # muse-1 chấm xong → được thấy (không còn nguy cơ bias)
    _review(client, agents_hdrs, board_job, 1, vh, "muse-1", ["HELMET_BALL"])
    w2 = client.get(f"/api/v1/todos/{board_job}/shots/1/comments",
                    headers=agents_hdrs["muse-1"]).json()
    assert w2["comments_visible"] is True
    assert len(w2["comments"]) == 1


def test_comments_embedded_in_shot_detail_and_red_regen(
        client, board_job, agents_hdrs, tmp_path):
    """Shot đỏ nhận việc gen lại phải mang theo comment (mục đích của comment)."""
    vh = _upload(client, agents_hdrs, board_job, "muse-2", 1, tmp_path)
    _review(client, agents_hdrs, board_job, 1, vh, "muse-3",
            ["CAMERA_WRONG_DIRECTION"],  # fail-level → shot đỏ
            regen_brief={"what_wrong": "camera push-in thay vì pull-out",
                         "do_instead": "camera lùi ra, subject nhỏ dần",
                         "staging_change": "camera_move: PUSH_IN -> PULL_OUT"})
    _comment(client, agents_hdrs["muse-3"], board_job, 1,
             "camera đang push-in, brief yêu cầu pull-out")

    detail = client.get(f"/api/v1/todos/{board_job}/shots/1",
                        headers=agents_hdrs["muse-2"]).json()
    assert detail["status"] == "red"
    assert detail["comments_visible"] is True
    assert detail["comment_count"] == 1

    nxt = client.get(f"/api/v1/todos/{board_job}/shots/next",
                     headers=agents_hdrs["muse-2"]).json()
    assert nxt["reason"] == "red_regen"
    assert nxt["shot"]["index"] == 1
    assert nxt["shot"]["comment_count"] == 1
    assert nxt["shot"]["comments"][0]["body"].startswith("camera đang push-in")
    assert nxt["shot"]["regen_brief"], "brief vẫn phải đi cùng comment"
