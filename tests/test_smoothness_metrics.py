"""Tests for the smoothness measurement yardstick (cut detection, motion, audio)."""

import shutil
import subprocess

import numpy as np
import pytest

from core.smoothness import metrics

FFMPEG = shutil.which("ffmpeg")

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)


@pytest.fixture(scope="module")
def cuts_video(tmp_path_factory):
    """Six seconds: red -> green -> blue, with a 440 Hz tone."""
    tmp = tmp_path_factory.mktemp("cuts")
    silent = tmp / "silent.mp4"
    _run([
        FFMPEG, "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2:r=30",
        "-f", "lavfi", "-i", "color=c=green:s=320x240:d=2:r=30",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2:r=30",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(silent),
    ])
    out = tmp / "cuts.mp4"
    _run([
        FFMPEG, "-y", "-v", "error", "-i", str(silent),
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "copy", "-c:a", "aac", "-shortest", str(out),
    ])
    return out


def test_probe_and_cuts(cuts_video):
    info = metrics.ffprobe_streams(cuts_video)
    assert info["width"] == 320 and info["height"] == 240
    assert info["duration_s"] == pytest.approx(6.0, abs=0.2)
    assert info["fps"] == pytest.approx(30.0, abs=0.1)

    cuts = metrics.detect_cuts(cuts_video, fps_hint=info["fps"])
    assert len(cuts) == 2
    assert cuts[0] == pytest.approx(2.0, abs=0.2)
    assert cuts[1] == pytest.approx(4.0, abs=0.2)


def test_cut_detection_is_deterministic(cuts_video):
    first = metrics.detect_cuts(cuts_video, fps_hint=30.0)
    second = metrics.detect_cuts(cuts_video, fps_hint=30.0)
    assert first == second


def test_music_bed_stats_separates_stereo_bed_from_mono_voice():
    mid = np.concatenate([np.full(50, -10.0), np.full(50, -40.0)])
    with_bed = metrics.music_bed_stats(mid, np.full(100, -30.0))
    assert with_bed["looks_like_music_bed"] is True

    # A very quiet bed still shows up relative to the voice, above the floor.
    quiet_bed = metrics.music_bed_stats(mid, np.full(100, -50.0))
    assert quiet_bed["looks_like_music_bed"] is True

    no_bed = metrics.music_bed_stats(mid, np.full(100, -100.0))
    assert no_bed["looks_like_music_bed"] is False
    assert no_bed["side_active_share"] == 0.0


@pytest.fixture(scope="module")
def pan_and_local_videos(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("motion")
    still = tmp / "still.png"
    box = tmp / "box.png"
    pan = tmp / "pan.mp4"
    local = tmp / "local.mp4"
    _run([
        FFMPEG, "-y", "-v", "error", "-f", "lavfi",
        "-i", "testsrc2=s=960x540:r=30:d=1", "-frames:v", "1", str(still),
    ])
    _run([
        FFMPEG, "-y", "-v", "error", "-f", "lavfi",
        "-i", "color=c=red:s=90x90", "-frames:v", "1", str(box),
    ])
    _run([
        FFMPEG, "-y", "-v", "error", "-loop", "1", "-i", str(still), "-t", "3", "-r", "30",
        "-vf", "crop=w=640:h=360:x='(in_w-out_w)*t/3':y='(in_h-out_h)/2'",
        "-pix_fmt", "yuv420p", str(pan),
    ])
    _run([
        FFMPEG, "-y", "-v", "error", "-loop", "1", "-i", str(still),
        "-loop", "1", "-i", str(box), "-t", "3", "-r", "30",
        "-filter_complex",
        "[0:v]scale=640:360[bg];[bg][1:v]overlay=x='20+t*180':y=140:shortest=1",
        "-pix_fmt", "yuv420p", str(local),
    ])
    return pan, local


def test_motion_separates_camera_move_from_local_animation(pan_and_local_videos):
    pan, local = pan_and_local_videos
    pan_m = metrics.measure_motion(pan, [], 3.0)
    loc_m = metrics.measure_motion(local, [], 3.0)

    assert pan_m["available"] and loc_m["available"]
    assert pan_m["camera"]["trans_px_s_median"] > 2.0
    assert loc_m["camera"]["trans_px_s_median"] < 2.0
    # A moving object leaves far more residual than a pure camera pan over a still.
    assert loc_m["local_motion"]["residual_median"] > 2 * pan_m["local_motion"]["residual_median"]
    assert loc_m["local_motion"]["residual_median"] > 1.0
