"""Tests for the media_probe module."""

import pytest
from pathlib import Path

from pipeline.media_probe import probe_media, get_media_duration_ms, _parse_fps
from core.analysis_schemas import MediaInfo
from core.exceptions import InvalidMediaError


class TestParseFps:
    """Tests for FPS parsing."""

    def test_parse_fractional_fps(self):
        num, den = _parse_fps("30000/1001")
        assert num == 30000
        assert den == 1001

    def test_parse_integer_fps(self):
        num, den = _parse_fps("30")
        assert num == 30000
        assert den == 1000

    def test_parse_float_fps(self):
        num, den = _parse_fps("29.97")
        assert num == 29970
        assert den == 1000


class TestProbeMedia:
    """Tests for media probing."""

    def test_probe_nonexistent_file(self):
        """Test that probing a non-existent file raises InvalidMediaError."""
        with pytest.raises(InvalidMediaError, match="File not found"):
            probe_media("/nonexistent/file.mp4")

    def test_probe_returns_media_info(self, tmp_path):
        """Test that probing returns a valid MediaInfo object."""
        # Create a minimal test video file using ffmpeg
        # This test requires ffmpeg to be available
        test_file = tmp_path / "test.mp4"
        
        # Try to create a test video file
        try:
            import subprocess
            from config.settings import settings
            
            ffmpeg_path = settings.ffmpeg_path
            cmd = [
                ffmpeg_path,
                "-f", "lavfi",
                "-i", "color=size=100x100:rate=1:duration=1",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(test_file),
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=30)
            
            # Now probe it
            media_info = probe_media(test_file)
            
            assert isinstance(media_info, MediaInfo)
            assert media_info.duration_ms > 0
            assert media_info.width == 100
            assert media_info.height == 100
            assert media_info.fps_num > 0
            assert media_info.fps_den > 0
            
        except (subprocess.SubprocessError, FileNotFoundError):
            pytest.skip("FFmpeg not available for test")


class TestGetMediaDuration:
    """Tests for duration extraction."""

    def test_duration_nonexistent_file(self):
        """Test that getting duration of non-existent file raises error."""
        with pytest.raises(InvalidMediaError, match="File not found"):
            get_media_duration_ms("/nonexistent/file.mp4")

    def test_duration_returns_int(self, tmp_path):
        """Test that duration is returned as integer milliseconds."""
        try:
            import subprocess
            from config.settings import settings
            
            test_file = tmp_path / "test.mp4"
            ffmpeg_path = settings.ffmpeg_path
            
            cmd = [
                ffmpeg_path,
                "-f", "lavfi",
                "-i", "color=size=100x100:rate=1:duration=2",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(test_file),
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=30)
            
            duration_ms = get_media_duration_ms(test_file)
            
            assert isinstance(duration_ms, int)
            assert duration_ms > 0
            # Should be approximately 2000ms (2 seconds)
            assert 1500 <= duration_ms <= 2500
            
        except (subprocess.SubprocessError, FileNotFoundError):
            pytest.skip("FFmpeg not available for test")
