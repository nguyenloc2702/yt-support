"""Tests for pipeline.audio_track_builder (Phase D)."""

import json
import struct
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.audio_track_builder import (
    AudioTrackError,
    build_audio_track,
)


def _make_wav(path: Path, duration_ms: int, sample_rate: int = 8000) -> Path:
    n_samples = int(sample_rate * duration_ms / 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(
            b"".join(struct.pack("<h", int(12000 * (i % 32) / 32)) for i in range(n_samples))
        )
    return path


def _overlay(block_id, start_s, end_s, path="fake.wav", dur_ms=5000):
    return {
        "block_id": block_id,
        "file_path": str(path),
        "output_start": start_s,
        "output_end": end_s,
        "fit_mode": "preserve_speed",
        "actual_duration_ms": dur_ms,
        "expected_duration_ms": int((end_s - start_s) * 1000),
    }


class TestBuildAudioTrackGates:
    def test_empty_overlays_rejected(self, tmp_path):
        with pytest.raises(AudioTrackError, match="empty delivery"):
            build_audio_track([], 5.0, tmp_path / "out.wav")

    def test_overlap_rejected_before_ffmpeg(self, tmp_path):
        overlays = [
            _overlay("b1", 0.0, 5.0),
            _overlay("b2", 4.0, 9.0),  # starts before b1 ends
        ]
        with patch("pipeline.audio_track_builder.run_ffmpeg") as rf:
            with pytest.raises(AudioTrackError, match="overlap"):
                build_audio_track(overlays, 9.0, tmp_path / "out.wav")
            rf.assert_not_called()  # fail BEFORE FFmpeg

    def test_missing_file_rejected(self, tmp_path):
        overlays = [_overlay("b1", 0.0, 5.0, path=tmp_path / "nope.wav")]
        with pytest.raises(AudioTrackError, match="missing"):
            build_audio_track(overlays, 5.0, tmp_path / "out.wav")

    def test_trailing_audio_beyond_video_fails(self, tmp_path):
        wav = _make_wav(tmp_path / "a.wav", 5000)
        overlays = [_overlay("b1", 0.0, 8.0, path=wav, dur_ms=5000)]
        with pytest.raises(AudioTrackError, match="vượt video duration"):
            build_audio_track(overlays, 5.0, tmp_path / "out.wav")


class TestBuildAudioTrackMockedFFmpeg:
    """Verify the FFmpeg command construction without running FFmpeg."""

    def test_command_structure(self, tmp_path):
        w1 = _make_wav(tmp_path / "a.wav", 5000)
        w2 = _make_wav(tmp_path / "b.wav", 5000)
        overlays = [
            _overlay("b2", 5.0, 10.0, path=w2),
            _overlay("b1", 0.0, 5.0, path=w1),  # out of order on purpose
        ]
        with patch("pipeline.audio_track_builder.run_ffmpeg") as rf, \
             patch("pipeline.media_probe.probe_audio", return_value={"duration_ms": 10000}):
            rf.return_value.returncode = 0
            report = build_audio_track(overlays, 10.0, tmp_path / "out.wav")

        args = rf.call_args[0][0]
        # Two inputs, adelay at 0 and 5000 ms, amix with 2 inputs
        assert args.count("-i") == 2
        fc = args[args.index("-filter_complex") + 1]
        assert "adelay=0|0" in fc
        assert "adelay=5000|5000" in fc
        assert "amix=inputs=2" in fc
        assert "apad=whole_dur=10.0" in fc

        # Sorted placement
        assert [i["block_id"] for i in report["items"]] == ["b1", "b2"]
        assert report["overlaps"] == []
        assert report["gaps"] == []
        assert report["video_duration_ms"] == 10000
        assert report["errors"] == []

    def test_gap_preserved_and_reported(self, tmp_path):
        w1 = _make_wav(tmp_path / "g.wav", 5000)
        overlays = [_overlay("b1", 0.0, 5.0, path=w1)]
        with patch("pipeline.audio_track_builder.run_ffmpeg") as rf, \
             patch("pipeline.media_probe.probe_audio", return_value={"duration_ms": 10000}):
            rf.return_value.returncode = 0
            report = build_audio_track(overlays, 10.0, tmp_path / "out.wav")

        assert len(report["gaps"]) == 1
        gap = report["gaps"][0]
        assert gap["from_ms"] == 5000
        assert gap["duration_ms"] == 5000
        assert any("silence" in w for w in report["warnings"])

    def test_sync_report_written(self, tmp_path):
        w1 = _make_wav(tmp_path / "s.wav", 5000)
        overlays = [_overlay("b1", 0.0, 5.0, path=w1)]
        report_path = tmp_path / "sync_report.json"
        with patch("pipeline.audio_track_builder.run_ffmpeg") as rf, \
             patch("pipeline.media_probe.probe_audio", return_value={"duration_ms": 5000}):
            rf.return_value.returncode = 0
            build_audio_track(overlays, 5.0, tmp_path / "out.wav", sync_report_path=report_path)
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["items"][0]["block_id"] == "b1"
        assert data["items"][0]["expected_start_ms"] == 0
        assert data["video_duration_ms"] == 5000

    def test_ffmpeg_failure_raises(self, tmp_path):
        w1 = _make_wav(tmp_path / "f.wav", 5000)
        overlays = [_overlay("b1", 0.0, 5.0, path=w1)]
        with patch("pipeline.audio_track_builder.run_ffmpeg") as rf:
            rf.return_value.returncode = 1
            rf.return_value.stderr = "boom"
            with pytest.raises(AudioTrackError, match="FFmpeg audio track build failed"):
                build_audio_track(overlays, 5.0, tmp_path / "out.wav")


class TestBuildAudioTrackIntegration:
    """Real FFmpeg integration: build a track from two WAVs."""

    def test_real_track_build(self, tmp_path):
        pytest.importorskip("shutil", reason="ffmpeg availability checked below")
        import shutil
        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not available")

        w1 = _make_wav(tmp_path / "i1.wav", 5000)
        w2 = _make_wav(tmp_path / "i2.wav", 5000)
        overlays = [
            _overlay("b1", 0.0, 5.0, path=w1, dur_ms=5000),
            _overlay("b2", 5.0, 10.0, path=w2, dur_ms=5000),
        ]
        out = tmp_path / "track.wav"
        report = build_audio_track(overlays, 10.0, out, sync_report_path=tmp_path / "sync.json")

        assert out.exists()
        from pipeline.media_probe import probe_audio
        info = probe_audio(out)
        assert abs(int(info["duration_ms"]) - 10000) <= 250
        assert report["track_duration_ms"] > 0
        assert report["errors"] == []
        assert report["overlaps"] == []
