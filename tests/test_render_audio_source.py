"""Tests for Phase D: renderer audio_source + render service gates."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import RenderError


class TestRendererAudioSourceValidation:
    """audio_source validation happens before any heavy work."""

    def _timeline(self, with_overlays=True):
        tl = {
            "clips": [
                {"source_start": 0.0, "source_end": 2.0, "output_start": 0.0,
                 "output_end": 2.0, "duration": 2.0, "enabled": True},
            ],
            "audio_overlays": [
                {"block_id": "b1", "file_path": "x.wav", "output_start": 0.0,
                 "output_end": 2.0, "fit_mode": "preserve_speed",
                 "actual_duration_ms": 2000, "expected_duration_ms": 2000},
            ] if with_overlays else [],
        }
        return tl

    def test_invalid_audio_source_rejected(self, tmp_path):
        from pipeline.renderer import render_video

        with pytest.raises(RenderError, match="Invalid audio_source"):
            render_video(
                source_path=tmp_path / "src.mp4",
                timeline=self._timeline(),
                output_path=tmp_path / "out.mp4",
                preset={"width": 640, "height": 360},
                audio_source="auto_mix",
            )

    def test_uploaded_blocks_without_overlays_rejected(self, tmp_path):
        from pipeline.renderer import render_video

        with pytest.raises(RenderError, match="audio_overlays"):
            render_video(
                source_path=tmp_path / "src.mp4",
                timeline=self._timeline(with_overlays=False),
                output_path=tmp_path / "out.mp4",
                preset={"width": 640, "height": 360},
                audio_source="uploaded_blocks",
            )

    def test_uploaded_blocks_never_calls_tts(self, tmp_path):
        """When audio_source=uploaded_blocks, TTS overlay must not run."""
        from pipeline import renderer as renderer_mod

        calls = {}

        def fake_build_track(audio_overlays, total_duration_seconds, output_path, **kw):
            calls["overlays"] = audio_overlays
            Path(output_path).write_bytes(b"RIFF")
            return {"items": [], "gaps": [], "overlaps": [], "warnings": [],
                    "errors": [], "total_duration_ms": 2000, "track_duration_ms": 2000}

        def fake_mix(joined_video_path, track_path, output_path, **kw):
            Path(output_path).write_bytes(b"fake mp4")

        from pipeline import audio_track_builder as atb_mod

        def fake_ffmpeg(args, **kwargs):
            # The concat step (-f concat) must produce the joined file
            if "-f" in args and args[args.index("-f") + 1] == "concat":
                Path(args[-1]).write_bytes(b"joined")

        # Point the overlay at a real file so track-build gates pass
        real_wav = tmp_path / "overlay.wav"
        real_wav.write_bytes(b"RIFF....")

        with patch.object(atb_mod, "build_audio_track", fake_build_track), \
             patch.object(renderer_mod, "_mix_with_block_track", fake_mix), \
             patch.object(renderer_mod, "pick_video_encoder", return_value="libx264"), \
             patch.object(renderer_mod, "run_ffmpeg_with_progress", side_effect=fake_ffmpeg), \
             patch.object(renderer_mod, "validate_rendered_output") as v, \
             patch.object(renderer_mod, "generate_qc_report"):
            v.return_value.valid = True
            v.return_value.errors = []
            v.return_value.warnings = []
            v.return_value.duration_seconds = 2.0
            v.return_value.width = 640
            v.return_value.height = 360
            v.return_value.has_video = True
            v.return_value.has_audio = True

            cfg = MagicMock()
            cfg.tts = MagicMock(enabled=True)  # TTS on in config — must be ignored
            cfg.voice.enabled = False
            cfg.music.enabled = False
            cfg.music.asset_id = None
            cfg.version = 1

            out = tmp_path / "out.mp4"
            (tmp_path / "src.mp4").write_bytes(b"fake")

            tl = self._timeline()
            tl["audio_overlays"][0]["file_path"] = str(real_wav)
            renderer_mod.render_video(
                source_path=tmp_path / "src.mp4",
                timeline=tl,
                output_path=out,
                preset={"width": 640, "height": 360, "fps": 30, "video_codec": "libx264"},
                render_configuration=cfg,
                audio_source="uploaded_blocks",
            )
            assert calls["overlays"][0]["block_id"] == "b1"


class TestRenderServiceGates:
    def test_uploaded_blocks_without_overlays_blocked(self):
        from services.render_service import RenderService

        with pytest.raises(RenderError, match="Audio gate failed"):
            RenderService.enforce_render_gates(
                "proj_x", {"analysis_id": "a", "audio_overlays": []},
                audio_source="uploaded_blocks",
            )

    def test_uploaded_blocks_with_overlap_blocked(self):
        from services.render_service import RenderService

        tl = {
            "analysis_id": "a",
            "audio_overlays": [
                {"block_id": "b1", "output_start": 0.0, "output_end": 5.0},
                {"block_id": "b2", "output_start": 4.0, "output_end": 9.0},
            ],
        }
        with pytest.raises(RenderError, match="overlap"):
            RenderService.enforce_render_gates("proj_x", tl, audio_source="uploaded_blocks")

    def test_uploaded_blocks_clean_timeline_passes(self):
        from services.render_service import RenderService

        tl = {
            "analysis_id": "a",
            "audio_overlays": [
                {"block_id": "b1", "output_start": 0.0, "output_end": 5.0},
                {"block_id": "b2", "output_start": 5.0, "output_end": 9.0},
            ],
        }
        result = RenderService.enforce_render_gates("proj_x", tl, audio_source="uploaded_blocks")
        assert result is tl

    def test_original_source_skips_audio_gate(self):
        from services.render_service import RenderService

        tl = {"analysis_id": "a", "audio_overlays": []}
        result = RenderService.enforce_render_gates("proj_x", tl, audio_source="original")
        assert result is tl
