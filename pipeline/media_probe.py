"""Media probing utilities for video analysis pipeline.

This module provides functions to probe media files and extract metadata
compatible with the analysis schemas.
"""

from pathlib import Path

from core.analysis_schemas import MediaInfo
from core.exceptions import InvalidMediaError
from pipeline.ffmpeg import run_ffprobe


def _parse_fps(fps_str: str) -> tuple:
    """Parse FPS string from ffprobe into numerator and denominator.
    
    Args:
        fps_str: FPS string like "30000/1001" or "30"
        
    Returns:
        Tuple of (numerator, denominator)
    """
    if '/' in fps_str:
        parts = fps_str.split('/')
        num = int(parts[0])
        den = int(parts[1])
    else:
        num = int(float(fps_str) * 1000)
        den = 1000
    return num, den


def probe_media(path: Path | str) -> MediaInfo:
    """Probe a media file and return schema-compatible MediaInfo.
    
    Args:
        path: Path to the media file
        
    Returns:
        MediaInfo object with all metadata extracted from the file
        
    Raises:
        InvalidMediaError: If the file cannot be probed or has no video stream
    """
    path = Path(path)
    if not path.exists():
        raise InvalidMediaError(f"File not found: {path}")
    
    try:
        data = run_ffprobe(["-show_format", "-show_streams", str(path)])
    except Exception as e:
        raise InvalidMediaError(f"FFprobe failed: {e}")
    
    format_data = data.get("format", {})
    streams = data.get("streams", [])
    
    # Find video and audio streams
    video_stream = None
    audio_stream = None
    
    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video_stream is None:
            video_stream = stream
        elif codec_type == "audio" and audio_stream is None:
            audio_stream = stream
    
    if video_stream is None:
        raise InvalidMediaError("No video stream found")
    
    # Extract duration (convert to milliseconds)
    duration_seconds = float(format_data.get("duration", 0))
    duration_ms = int(duration_seconds * 1000)
    
    # Extract video metadata
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    
    # Extract FPS
    fps_str = video_stream.get("r_frame_rate", video_stream.get("avg_frame_rate", "0"))
    fps_num, fps_den = _parse_fps(fps_str)
    
    video_codec = video_stream.get("codec_name", None)
    
    # Extract audio metadata
    if audio_stream:
        audio_sample_rate = int(audio_stream.get("sample_rate", 0))
        audio_channels = int(audio_stream.get("channels", 0))
        audio_codec = audio_stream.get("codec_name", None)
    else:
        audio_sample_rate = 0
        audio_channels = 0
        audio_codec = None
    
    return MediaInfo(
        duration_ms=duration_ms,
        fps_num=fps_num,
        fps_den=fps_den,
        width=width,
        height=height,
        audio_sample_rate=audio_sample_rate,
        audio_channels=audio_channels,
        audio_codec=audio_codec,
        video_codec=video_codec,
    )


def probe_audio(path: Path | str) -> dict:
    """Probe an *audio* file and return its metadata.

    Returns a dict with keys:
        duration_ms, sample_rate, channels, codec, file_size, sha256

    Raises:
        InvalidMediaError: If the file cannot be probed or has no audio stream.
    """
    import hashlib

    path = Path(path)
    if not path.exists():
        raise InvalidMediaError(f"File not found: {path}")

    try:
        data = run_ffprobe(["-show_format", "-show_streams", str(path)])
    except Exception as e:
        raise InvalidMediaError(f"FFprobe failed: {e}")

    streams = data.get("streams", [])
    audio_stream = next(
        (s for s in streams if s.get("codec_type") == "audio"), None
    )
    if audio_stream is None:
        raise InvalidMediaError("No audio stream found")

    duration_seconds = float(data.get("format", {}).get("duration", 0))
    return {
        "duration_ms": int(duration_seconds * 1000),
        "sample_rate": int(audio_stream.get("sample_rate", 0)),
        "channels": int(audio_stream.get("channels", 0)),
        "codec": audio_stream.get("codec_name"),
        "file_size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def get_media_duration_ms(path: Path | str) -> int:
    """Quick helper to get just the duration in milliseconds.
    
    Args:
        path: Path to the media file
        
    Returns:
        Duration in milliseconds
    """
    path = Path(path)
    if not path.exists():
        raise InvalidMediaError(f"File not found: {path}")
    
    try:
        data = run_ffprobe(["-show_entries", "format=duration", "-of", "json", str(path)])
    except Exception as e:
        raise InvalidMediaError(f"FFprobe failed: {e}")
    
    duration_seconds = float(data.get("format", {}).get("duration", 0))
    return int(duration_seconds * 1000)
