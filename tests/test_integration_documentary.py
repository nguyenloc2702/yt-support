"""Integration tests with real FFmpeg execution and heavy lock heartbeats."""

import json
from pathlib import Path
import subprocess
import time
import pytest

from pipeline.documentary_assembler import assemble_documentary, probe_duration, probe_video_stream
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
