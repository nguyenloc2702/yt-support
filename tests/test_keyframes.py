"""Tests for pipeline.keyframes (Phase 2) - logic without real video files."""

from pathlib import Path
from unittest.mock import patch

from pipeline.keyframes import KeyframeExtractor


def _extractor():
    return KeyframeExtractor(blur_threshold=10.0, brightness_min=5.0)


def test_short_shot_gets_one_frame():
    ex = _extractor()
    shots = [{"shot_id": 0, "start": 0.0, "end": 5.0}]
    with patch.object(ex, "_grab_frame", return_value=True), \
         patch.object(ex, "_score_frame", return_value={"blur": 100.0, "mean_luma": 128.0}):
        out = ex.extract_for_shots(Path("fake.mp4"), shots, Path("out"))
    assert len(out) == 1
    assert len(out[0]["keyframes"]) == 1
    assert out[0]["keyframes"][0]["time"] > 0.0
    assert out[0]["keyframes"][0]["time"] < 5.0


def test_long_shot_gets_two_frames():
    ex = _extractor()
    shots = [{"shot_id": 0, "start": 0.0, "end": 20.0}]
    with patch.object(ex, "_grab_frame", return_value=True), \
         patch.object(ex, "_score_frame", return_value={"blur": 100.0, "mean_luma": 128.0}):
        out = ex.extract_for_shots(Path("fake.mp4"), shots, Path("out"))
    assert len(out[0]["keyframes"]) == 2


def test_blurry_frames_filtered():
    ex = _extractor()
    shots = [{"shot_id": 0, "start": 0.0, "end": 5.0}]
    with patch.object(ex, "_grab_frame", return_value=True), \
         patch.object(ex, "_score_frame", return_value={"blur": 1.0, "mean_luma": 128.0}):
        out = ex.extract_for_shots(Path("fake.mp4"), shots, Path("out"))
    assert out == []  # all frames blurry -> shot dropped


def test_tiny_shot_skipped():
    ex = _extractor()
    shots = [{"shot_id": 0, "start": 1.0, "end": 1.05}]
    out = ex.extract_for_shots(Path("fake.mp4"), shots, Path("out"))
    assert out == []
