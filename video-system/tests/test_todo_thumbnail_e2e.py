"""End-to-end tests for mandatory 1-thumbnail requirement on TODO jobs."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def fast_offline_tts(monkeypatch):
    """Use fast deterministic offline WAV synthesis."""
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")


def _make_test_mp4(path: Path, duration_sec: float, color: str = "steelblue") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s=640x360:r=24:d={duration_sec:.3f}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "ultrafast",
        "-movflags", "+faststart",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert res.returncode == 0, f"ffmpeg test mp4 failed: {res.stderr}"
    return path


def _make_test_png(path: Path, color: str = "crimson") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s=1280x720:d=0.1",
        "-frames:v", "1",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    assert res.returncode == 0, f"ffmpeg test png failed: {res.stderr}"
    return path


def test_todo_requires_thumbnail_before_confirm_assemble_e2e(
    client, ingest, read_key, fast_offline_tts, tmp_path
):
    """Verify:
    1. New todo starts with has_thumbnail=False and ready_to_complete=False.
    2. Uploading enough video duration sets ready_to_assemble=True, but ready_to_complete remains False.
    3. confirm-assemble without thumbnail returns HTTP 409 error='thumbnail_missing'.
    4. Uploading 1 image with kind='thumbnail' sets has_thumbnail=True and ready_to_complete=True.
    5. GET /images/1/file?kind=thumbnail serves the PNG and confirm-assemble succeeds (200 completed).
    """
    r_create = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Mandatory Thumbnail E2E Test",
            "script_text": "One clear thumbnail helps viewers click immediately.",
            "bgm_enabled": False,
            "collect_references": False,
        },
    )
    assert r_create.status_code == 201, r_create.text
    pkg = r_create.json()
    item_id = pkg["item_id"]
    audio_dur = pkg["audio_duration_sec"]

    # 1. Initial GET todo shows thumbnail missing
    r_get0 = client.get(f"/api/v1/todos/{item_id}", headers=read_key)
    assert r_get0.status_code == 200, r_get0.text
    d0 = r_get0.json()
    assert d0["has_thumbnail"] is False
    assert d0["video_progress"]["has_thumbnail"] is False
    assert d0["video_progress"]["ready_to_complete"] is False
    assert d0["thumbnail_prompt_hint"]

    # 2. Upload full video clip
    clip_path = _make_test_mp4(tmp_path / "full.mp4", round(audio_dur + 0.5, 2), color="navy")
    with open(clip_path, "rb") as f:
        r_vid = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("full.mp4", f, "video/mp4")},
            data={"seq_num": "1", "covered_shot_indexes": "[1]"},
        )
    assert r_vid.status_code == 201, r_vid.text
    vp_after_vid = r_vid.json()["video_progress"]
    assert vp_after_vid["ready_to_assemble"] is True
    assert vp_after_vid["has_thumbnail"] is False
    assert vp_after_vid["ready_to_complete"] is False

    # 3. confirm-assemble without thumbnail -> HTTP 409 thumbnail_missing
    r_blocked = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": False, "allow_shortfall": False},
    )
    assert r_blocked.status_code == 409, r_blocked.text
    err = r_blocked.json()
    assert err["error"] == "thumbnail_missing"
    assert "thumbnail" in err["message"].lower()

    # 4. Upload 1 thumbnail via POST /images (kind='thumbnail', omitting seq_num -> defaults to 1)
    thumb_path = _make_test_png(tmp_path / "thumb.png")
    with open(thumb_path, "rb") as f:
        r_thumb = client.post(
            f"/api/v1/todos/{item_id}/images",
            headers=ingest,
            files={"file": ("thumb.png", f, "image/png")},
            data={"kind": "thumbnail", "prompt_used": "Bold red chart hook thumbnail"},
        )
    assert r_thumb.status_code == 201, r_thumb.text
    thumb_res = r_thumb.json()
    assert thumb_res["image"]["kind"] == "thumbnail"
    assert thumb_res["image"]["seq_num"] == 1
    assert thumb_res["image_progress"]["has_thumbnail"] is True
    assert thumb_res["image_progress"]["thumbnail_count"] == 1
    assert thumb_res["video_progress"]["has_thumbnail"] is True
    assert thumb_res["video_progress"]["ready_to_complete"] is True

    # 5. Thumbnail file endpoint serves the uploaded PNG
    thumb_url = thumb_res["video_progress"]["thumbnail_url"]
    assert thumb_url == f"/api/v1/todos/{item_id}/images/1/file?kind=thumbnail"
    r_thumb_file = client.get(thumb_url, headers=read_key)
    assert r_thumb_file.status_code == 200
    assert r_thumb_file.headers["content-type"].startswith("image/")
    assert len(r_thumb_file.content) > 500

    # 6. confirm-assemble now succeeds -> completed
    r_ok = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": False, "allow_shortfall": False, "watermark_enabled": True},
    )
    assert r_ok.status_code == 200, r_ok.text
    asm = r_ok.json()
    assert asm["status"] == "completed"

    # 7. GET todo reflects completed + thumbnail + final video
    r_get1 = client.get(f"/api/v1/todos/{item_id}", headers=read_key)
    assert r_get1.status_code == 200
    d1 = r_get1.json()
    assert d1["has_thumbnail"] is True
    assert d1["thumbnail_url"] == thumb_url
    assert d1["status"] == "completed"
