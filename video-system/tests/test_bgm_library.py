"""Tests for BGM & Audio Collection API (POST/GET/DELETE /api/v1/bgm)."""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_bgm_dir(tmp_path, monkeypatch):
    bdir = tmp_path / "bgm_lib"
    bdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VIDEO_SYSTEM_BGM_DIR", str(bdir))
    return bdir


def _make_sine_wav(path: Path, dur: float = 2.0) -> Path:
    ffmpeg = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"
    res = subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
            "-ar", "44100", "-ac", "2", str(path),
        ],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert res.returncode == 0, res.stderr
    return path


def test_bgm_multipart_upload_loudness_filter_and_delete(client, ingest, read_key, isolated_bgm_dir, tmp_path):
    wav = _make_sine_wav(tmp_path / "Pulse_Beat.wav", dur=2.0)
    with wav.open("rb") as f:
        r = client.post(
            "/api/v1/bgm",
            headers=ingest,
            files={"file": ("Pulse_Beat.wav", f, "audio/wav")},
            data={
                "title": "Pulse Beat",
                "mood": "energetic",
                "track_type": "bgm",
                "genre": "electronic",
                "bpm": "124",
                "energy": "high",
                "tags": '["tech","finance","fast"]',
                "artist": "Studio Test",
                "license": "CC-BY-4.0",
                "source_url": "https://example.com/pulse",
                "note": "Good for rising act",
            },
        )
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["filename"] == "energetic__pulse_beat.mp3"
    assert d["mood"] == "energetic"
    assert d["beat_role"] == "rising"
    assert d["track_type"] == "bgm"
    assert d["genre"] == "electronic"
    assert d["bpm"] == 124
    assert d["energy"] == "high"
    assert d["tags"] == ["tech", "finance", "fast"]
    assert d["duration_sec"] > 1.5
    assert d["lufs"] is not None

    mp3_path = isolated_bgm_dir / "energetic__pulse_beat.mp3"
    assert mp3_path.exists()
    loud_data = json.loads((isolated_bgm_dir / "LOUDNESS.json").read_text(encoding="utf-8"))
    assert "energetic__pulse_beat.mp3" in loud_data["tracks"]

    # Duplicate without overwrite -> 409
    with wav.open("rb") as f:
        r_dup = client.post(
            "/api/v1/bgm",
            headers=ingest,
            files={"file": ("Pulse_Beat.wav", f, "audio/wav")},
            data={"title": "Pulse Beat", "mood": "energetic"},
        )
    assert r_dup.status_code == 409

    # List & filter
    r_list = client.get("/api/v1/bgm?mood=energetic&genre=electronic&q=pulse", headers=read_key)
    assert r_list.status_code == 200
    assert len(r_list.json()["tracks"]) == 1

    # Stream file
    r_file = client.get(d["file_url"], headers=read_key)
    assert r_file.status_code == 200
    assert r_file.headers["content-type"] == "audio/mpeg"
    assert len(r_file.content) > 1000

    # Delete (admin)
    r_del = client.delete(f"/api/v1/bgm/{d['track_id']}", headers={"Authorization": "Bearer test-admin"})
    assert r_del.status_code == 200
    assert not mp3_path.exists()
    loud_after = json.loads((isolated_bgm_dir / "LOUDNESS.json").read_text(encoding="utf-8"))
    assert "energetic__pulse_beat.mp3" not in loud_after["tracks"]


def test_bgm_json_base64_and_custom_mood(client, ingest, isolated_bgm_dir, tmp_path):
    wav = _make_sine_wav(tmp_path / "Drone.wav", dur=1.5)
    b64 = base64.b64encode(wav.read_bytes()).decode("ascii")
    r = client.post(
        "/api/v1/bgm",
        headers=ingest,
        json={
            "title": "Deep Space Drone",
            "mood": "space_ambient",
            "track_type": "ambient",
            "beat_role": "hook",
            "genre": "ambient",
            "bpm": 70,
            "energy": "low",
            "tags": ["space", "dark"],
            "lufs": -22.5,
            "true_peak_dbtp": -2.0,
            "allow_custom_mood": True,
            "filename": "deep_space.wav",
            "base64_data": f"data:audio/wav;base64,{b64}",
        },
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["filename"] == "ambient__space_ambient__deep_space.mp3"
    assert d["lufs"] == -22.5
    assert d["true_peak_dbtp"] == -2.0
    assert (isolated_bgm_dir / d["filename"]).exists()
