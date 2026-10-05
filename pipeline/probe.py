from pathlib import Path
from typing import Optional
from pipeline.ffmpeg import run_ffprobe
from core.exceptions import InvalidMediaError

class MediaInfo:
    def __init__(self, data: dict):
        self.raw = data
        self.format = data.get("format", {})
        self.streams = data.get("streams", [])
        self.duration_seconds = float(self.format.get("duration", 0))
        self.video_stream = next((s for s in self.streams if s.get("codec_type") == "video"), None)
        self.audio_stream = next((s for s in self.streams if s.get("codec_type") == "audio"), None)
        if self.video_stream:
            self.width = int(self.video_stream.get("width", 0))
            self.height = int(self.video_stream.get("height", 0))
            self.fps = self._parse_fps(self.video_stream)
        else:
            self.width = 0
            self.height = 0
            self.fps = 0.0

    def _parse_fps(self, stream: dict) -> float:
        # Try r_frame_rate or avg_frame_rate
        r_frame_rate = stream.get("r_frame_rate", "")
        if r_frame_rate:
            parts = r_frame_rate.split('/')
            if len(parts) == 2:
                try:
                    return float(parts[0]) / float(parts[1])
                except ZeroDivisionError:
                    pass
        avg_frame_rate = stream.get("avg_frame_rate", "")
        if avg_frame_rate:
            parts = avg_frame_rate.split('/')
            if len(parts) == 2:
                try:
                    return float(parts[0]) / float(parts[1])
                except ZeroDivisionError:
                    pass
        return 0.0

def probe_media(path: Path) -> MediaInfo:
    if not Path(path).exists():
        raise InvalidMediaError(f"File not found: {path}")
    try:
        data = run_ffprobe(["-show_format", "-show_streams", str(path)])
    except Exception as e:
        raise InvalidMediaError(f"FFprobe failed: {e}")
    # Check for video stream
    media = MediaInfo(data)
    if not media.video_stream:
        raise InvalidMediaError("No video stream found")
    return media