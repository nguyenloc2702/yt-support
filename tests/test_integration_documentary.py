"""Integration tests with real FFmpeg execution and heavy lock heartbeats."""

import json
from pathlib import Path
import subprocess
import time
import cv2
import numpy as np
import pytest

from pipeline.documentary_assembler import (
    assemble_documentary,
    probe_duration,
    probe_media_streams,
    probe_video_stream,
)
from pipeline.subtitles import build_ass_from_word_timeline
from services.job_queue import JobQueueManager


def create_synthetic_mp4(
    out_path: Path,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    duration: float = 1.0,
    with_audio: bool = False,
):
    """Generates a real, valid tiny MP4 video using FFmpeg testsrc."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
    ]
    if with_audio:
        cmd += [
            "-f", "lavfi",
            "-i", f"sine=frequency=1000:duration={duration}",
            "-c:a", "aac", "-b:a", "128k",
        ]
    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(out_path)
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def test_assemble_documentary_real_ffmpeg(tmp_path: Path):
    """
    P0.2 & P1.5: End-to-end documentary assembly with real FFmpeg:
    Feeds heterogeneous clips (different resolutions, fps, and audio presence)
    and verifies that assemble_documentary standardizes them into 1080p, 24fps, yuv420p.
    """
    proj_dir = tmp_path / "test_doc_proj"
    shots_dir = proj_dir / "shots"
    shots_dir.mkdir(parents=True)

    # Shot 1: 640x360, 30fps, no audio
    shot1 = shots_dir / "shot_001.mp4"
    create_synthetic_mp4(shot1, width=640, height=360, fps=30, duration=1.0, with_audio=False)

    # Shot 2: 1280x720, 25fps, with audio
    shot2 = shots_dir / "shot_002.mp4"
    create_synthetic_mp4(shot2, width=1280, height=720, fps=25, duration=1.0, with_audio=True)

    # Shots plan for per-shot timeline alignment
    shots_plan = [
        {"index": 1, "duration": 1.0, "text": ""},
        {"index": 2, "duration": 1.0, "text": ""},
    ]
    (proj_dir / "shots_plan.json").write_text(json.dumps(shots_plan), encoding="utf-8")

    out_final = proj_dir / "final_assembled.mp4"
    res_path = assemble_documentary(job_id="int_test", project_dir=proj_dir, output_path=out_final)

    assert res_path.exists()
    assert res_path.stat().st_size > 5000

    # Probe final output properties using ffprobe
    stream = probe_video_stream(res_path)
    assert stream.get("width") == 1920
    assert stream.get("height") == 1080
    assert stream.get("pix_fmt") == "yuv420p"

    total_dur = probe_duration(res_path)
    assert 1.8 <= total_dur <= 2.5

    # Verify raw shots are retained
    assert shot1.exists()
    assert shot2.exists()


def test_assemble_documentary_fails_on_corrupt_shot(tmp_path: Path):
    """
    P0.2: Verifies that if any shot normalization fails, assemble_documentary raises
    an exception and does NOT silently append corrupt/original clips.
    """
    proj_dir = tmp_path / "test_corrupt_proj"
    shots_dir = proj_dir / "shots"
    shots_dir.mkdir(parents=True)

    shot1 = shots_dir / "shot_001.mp4"
    create_synthetic_mp4(shot1, width=640, height=360, fps=30, duration=1.0)

    # Corrupt shot 2 (fake video bytes)
    shot2 = shots_dir / "shot_002.mp4"
    shot2.write_bytes(b"not a valid mp4 file at all")

    with pytest.raises(RuntimeError, match="Shot normalization failed for 'shot_002.mp4'"):
        assemble_documentary(job_id="corrupt_test", project_dir=proj_dir)

    # Raw shots must be preserved for retry
    assert shot1.exists()
    assert shot2.exists()


def _flat_clip(out_path: Path, duration: float = 3.0, size: str = "1280x720") -> Path:
    """A solid dark-grey clip: two renders of it differ only where libass drew."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=0x202020:s={size}:r=30:d={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(out_path),
    ], check=True, capture_output=True)
    return out_path


def _frame(clip: Path, at_s: float) -> np.ndarray:
    raw = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", str(at_s), "-i", str(clip),
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "bmp", "-",
    ], capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def test_burned_captions_add_no_stream_and_no_time(tmp_path: Path):
    """P1: the caption burn must cost neither an extra muxed stream nor timeline drift."""
    proj_dir = tmp_path / "cap_proj"
    _flat_clip(proj_dir / "shots" / "shot_001.mp4")
    _flat_clip(proj_dir / "shots" / "shot_002.mp4")
    plan = [{"index": 1, "duration": 3.0, "text": ""},
            {"index": 2, "duration": 3.0, "text": ""}]
    (proj_dir / "shots_plan.json").write_text(json.dumps(plan), encoding="utf-8")

    timeline = {"scenes": [{"index": 1, "words": [
        {"w": "captions", "start": 1.0, "end": 1.5},
        {"w": "are", "start": 1.6, "end": 1.8},
        {"w": "burned", "start": 1.9, "end": 2.4},
        {"w": "in", "start": 2.5, "end": 2.7},
        {"w": "place", "start": 2.8, "end": 3.2},
    ]}]}
    ass = build_ass_from_word_timeline(timeline, proj_dir / "subtitles_en.ass")

    plain = assemble_documentary(job_id="cap_plain", project_dir=proj_dir,
                                 output_path=proj_dir / "plain.mp4")
    burned = assemble_documentary(job_id="cap_burn", project_dir=proj_dir,
                                  output_path=proj_dir / "burned.mp4", subtitle_path=ass)

    kinds = sorted(s.get("codec_type") for s in probe_media_streams(burned).get("streams", []))
    assert kinds == ["audio", "video"]
    assert abs(probe_duration(burned) - probe_duration(plain)) <= 0.1
    marker = json.loads(burned.with_suffix(".captions.json").read_text(encoding="utf-8"))
    assert marker == {"burned": True, "cue_file": "subtitles_en.ass"}
    plain_marker = json.loads(plain.with_suffix(".captions.json").read_text(encoding="utf-8"))
    assert plain_marker["burned"] is False

    before, after = _frame(plain, 2.0), _frame(burned, 2.0)
    height = before.shape[0]
    diff = np.abs(after.astype(int) - before.astype(int))
    ink = diff > 40
    caption_band = slice(int(height * 0.70), height)
    upper_band = slice(0, int(height * 0.50))
    assert ink[caption_band].sum() > 5000, (
        f"caption burned only {int(ink[caption_band].sum())} pixels")
    assert diff[caption_band].max() > 150
    assert ink[upper_band].sum() == 0, "burn changed pixels outside the caption band"


def test_assembly_survives_an_unburnable_caption_file(tmp_path: Path):
    """A cue file libass refuses must not kill the render: fall back to no captions."""
    proj_dir = tmp_path / "cap_bad_proj"
    _flat_clip(proj_dir / "shots" / "shot_001.mp4", duration=2.0)
    (proj_dir / "shots_plan.json").write_text(
        json.dumps([{"index": 1, "duration": 2.0, "text": ""}]), encoding="utf-8")
    bad = proj_dir / "subtitles_en.ass"
    bad.write_text("not an ass file at all\n", encoding="utf-8")

    res = assemble_documentary(job_id="cap_bad", project_dir=proj_dir,
                               output_path=proj_dir / "bad.mp4", subtitle_path=bad)
    assert res.exists()
    assert probe_video_stream(res).get("width") == 1920
    marker = json.loads(res.with_suffix(".captions.json").read_text(encoding="utf-8"))
    assert marker["burned"] is False


def test_heavy_lock_lease_heartbeat(tmp_path: Path):
    """
    P0.1: Verifies that HeavyLockLease holds the lock, pulses heartbeat,
    blocks competing jobs, and releases cleanly upon exit.
    """
    db_file = tmp_path / "lock_test.sqlite3"
    jq = JobQueueManager(db_path=db_file)

    job_a = "job_alpha"
    job_b = "job_beta"

    with jq.heavy_lock_lease(job_a, interval=0.2) as lease:
        assert lease.is_valid
        # Job B cannot acquire while Job A's lease is active
        assert jq.acquire_heavy_lock(job_b) is False
        time.sleep(0.5)
        # Lease should still be actively held by Job A
        assert lease.is_valid

    # After exit, Job A's lock is released
    # Job B can now acquire
    assert jq.acquire_heavy_lock(job_b) is True
    jq.release_heavy_lock(job_b)
