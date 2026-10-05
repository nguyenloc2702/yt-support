import math
from pathlib import Path
from typing import Dict, Any
from pipeline.ffmpeg import run_ffprobe
from core.exceptions import InvalidMediaError


def percent_to_db(percent: float, floor_db: float = -60.0) -> float:
    """
    Convert volume percentage (0 to 200+) to decibels (dB).
    0% -> floor_db (-60 dB)
    100% -> 0.0 dB
    200% -> +6.02 dB
    """
    if percent <= 0:
        return floor_db
    return max(floor_db, 20.0 * math.log10(percent / 100.0))


def db_to_percent(db: float, floor_db: float = -60.0) -> float:
    """
    Convert decibels (dB) back to volume percentage.
    floor_db -> 0%
    0.0 dB -> 100%
    """
    if db <= floor_db:
        return 0.0
    return 100.0 * (10.0 ** (db / 20.0))


def probe_audio(path: Path) -> Dict[str, Any]:
    """
    Probe an audio asset using FFprobe.
    Ensures that an audio stream exists, duration > 0 and finite.
    Returns metadata dictionary.
    """
    path = Path(path)
    if not path.exists():
        raise InvalidMediaError(f"Audio file not found: {path}")

    try:
        data = run_ffprobe(["-show_format", "-show_streams", str(path)])
    except Exception as exc:
        raise InvalidMediaError(f"FFprobe failed to analyze audio file: {exc}") from exc

    streams = data.get("streams", [])
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not audio_stream:
        raise InvalidMediaError("No audio stream found in media file")

    format_info = data.get("format", {})
    try:
        duration = float(audio_stream.get("duration") or format_info.get("duration") or 0.0)
    except (ValueError, TypeError):
        duration = 0.0

    if duration <= 0 or not math.isfinite(duration):
        raise InvalidMediaError(f"Invalid or non-finite audio duration: {duration}")

    sample_rate = None
    try:
        if "sample_rate" in audio_stream:
            sample_rate = int(audio_stream["sample_rate"])
    except (ValueError, TypeError):
        pass

    channels = audio_stream.get("channels")
    codec_name = audio_stream.get("codec_name")

    return {
        "duration_seconds": duration,
        "codec_name": codec_name,
        "sample_rate": sample_rate,
        "channels": channels,
    }
