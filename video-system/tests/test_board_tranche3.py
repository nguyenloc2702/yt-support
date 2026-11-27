"""Tests for Shot Board Tranche 3: C-4 (blind review redaction) & C-6 (grandfathered)."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


def _make_mp4(path: Path, duration_sec: float = 8.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"
    cmd = [
        ffmpeg, "-y", "-f", "lavfi",
        "-i", f"color=c=navy:s=640x360:r=24:d={duration_sec:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if res.returncode != 0:
        pytest.skip(f"ffmpeg unavailable: {res.stderr[:120]}")
    return path


@pytest.fixture()
def agents_hdrs(client):
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
    item_id = make_item_fn("tranche3")
    payload = import_payload_factory(count=3, duration=24.0, seg=8.0)
    payload["audio"]["file_path"] = str(wav_file(24.0))
    r = client.post(f"/api/v1/items/{item_id}/shots/import", headers=ingest, json=payload)
    assert r.status_code == 201, r.text
    return item_id


def test_c4_blind_redaction_and_reveal(client, board_job, agents_hdrs, tmp_path):
    clip = _make_mp4(tmp_path / "c4.mp4")
    with open(clip, "rb") as f:
        up = client.post(
            f"/api/v1/todos/{board_job}/videos",
            headers=agents_hdrs["muse-2"],
            files={"file": ("clip.mp4", f, "video/mp4")},
            data={"shot_index": "1", "seq_num": "1", "covered_shot_indexes": "1"},
        )
    assert up.status_code == 201, up.text
    vh = up.json()["board_shot"]["video_hash"]

    # muse-3 submits a fail review
    rv = client.post(
        f"/api/v1/todos/{board_job}/shots/1/reviews",
        headers=agents_hdrs["muse-3"],
        json={
            "defect_codes": ["CAMERA_WRONG_DIRECTION"],
            "pairing_ok": True,
            "reviewed_video_hash": vh,
            "regen_brief": {
                "what_wrong": "wrong dir",
                "do_instead": "pull back",
                "staging_change": "STATIC",
            },
        },
    )
    assert rv.status_code == 201, rv.text

    # muse-1 has NOT reviewed -> score & defect_codes redacted, verdict & regen_brief kept
    d1 = client.get(f"/api/v1/todos/{board_job}/shots/1", headers=agents_hdrs["muse-1"])
    assert d1.status_code == 200
    body1 = d1.json()
    assert body1["scores_summary"] is None
    assert body1["reviews"][0]["score"] is None
    assert body1["reviews"][0]["defect_codes"] is None
    assert body1["reviews"][0]["verdict"] == "fail"
    assert body1["reviews"][0]["regen_brief"]["staging_change"] == "STATIC"

    # ?reveal=1 for non-privileged muse-1 is silently ignored (200, still redacted)
    d1_rev = client.get(
        f"/api/v1/todos/{board_job}/shots/1?reveal=1", headers=agents_hdrs["muse-1"]
    )
    assert d1_rev.status_code == 200
    assert d1_rev.json()["reviews"][0]["score"] is None

    # owner sees real score & summary
    d_owner = client.get(
        f"/api/v1/todos/{board_job}/shots/1", headers=agents_hdrs["owner"]
    )
    assert d_owner.status_code == 200
    assert d_owner.json()["reviews"][0]["score"] == pytest.approx(9.0)
    assert d_owner.json()["reviews"][0]["defect_codes"] == ["CAMERA_WRONG_DIRECTION"]
    assert d_owner.json()["scores_summary"]["count"] == 1

    # muse-1 submits review -> now muse-1 sees real scores
    rv2 = client.post(
        f"/api/v1/todos/{board_job}/shots/1/reviews",
        headers=agents_hdrs["muse-1"],
        json={
            "defect_codes": ["HELMET_BALL"],
            "pairing_ok": True,
            "reviewed_video_hash": vh,
        },
    )
    assert rv2.status_code == 201, rv2.text
    d1_after = client.get(
        f"/api/v1/todos/{board_job}/shots/1", headers=agents_hdrs["muse-1"]
    )
    assert d1_after.status_code == 200
    assert d1_after.json()["scores_summary"]["count"] == 2
    assert d1_after.json()["reviews"][0]["score"] == pytest.approx(9.0)


def test_c6_grandfathered_idempotent_and_assemble_gate(
    client, board_job, agents_hdrs, tmp_path
):
    from backend.app.main import board, store

    clip = _make_mp4(tmp_path / "c6.mp4")
    # Upload shot 1 & 2 before cutoff
    for idx in (1, 2):
        with open(clip, "rb") as f:
            r = client.post(
                f"/api/v1/todos/{board_job}/videos",
                headers=agents_hdrs["muse-2"],
                files={"file": ("clip.mp4", f, "video/mp4")},
                data={"shot_index": str(idx), "seq_num": str(idx), "covered_shot_indexes": str(idx)},
            )
        assert r.status_code == 201, r.text

    cutoff = time.time() + 1.0
    # Force shot 1 & 2 created_at before cutoff, and shot 3 created_at after cutoff
    store.execute(
        "UPDATE shots SET created_at=? WHERE item_id=? AND index_num IN (1,2)",
        (cutoff - 10.0, board_job),
    )

    m1 = board.migrate_grandfathered(board_enabled_at=cutoff)
    assert m1["migrated"] >= 2

    # Idempotent 2nd run
    m2 = board.migrate_grandfathered(board_enabled_at=cutoff)
    assert m2["migrated"] == 0
    assert m2["already_grandfathered"] >= 2

    summary = client.get(
        f"/api/v1/todos/{board_job}/board/summary", headers=agents_hdrs["muse-1"]
    ).json()
    assert summary["green"] == 0
    assert summary["grandfathered"] == 2
    assert summary["header_text"] == "Xanh 0 · Cũ (chưa chấm) 2 · Đỏ 0"

    # Grandfathered shots do NOT block assemble gate
    board.check_assemble_gate(board_job)

    # Now upload shot 3 AFTER cutoff -> new video must be reviewed before assemble
    with open(clip, "rb") as f:
        r3 = client.post(
            f"/api/v1/todos/{board_job}/videos",
            headers=agents_hdrs["muse-2"],
            files={"file": ("clip.mp4", f, "video/mp4")},
            data={"shot_index": "3", "seq_num": "3", "covered_shot_indexes": "3"},
        )
    assert r3.status_code == 201, r3.text
    assert r3.json()["board_shot"]["grandfathered"] is False

    # Assemble gate now blocks because shot 3 is new & unreviewed
    asm = client.post(
        f"/api/v1/todos/{board_job}/confirm-assemble",
        headers=agents_hdrs["muse-2"],
        json={"allow_shortfall": True, "allow_missing_thumbnail": True},
    )
    assert asm.status_code == 409, asm.text
    assert asm.json()["code"] == "assemble_blocked_unreviewed"
