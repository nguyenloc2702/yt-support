from pathlib import Path
from typing import Optional

from config.settings import settings
from core.render_schemas import OutputValidationResult
from pipeline.ffmpeg import run_ffprobe
from utils.json_io import atomic_write_json


def validate_rendered_output(
    output_path: Path,
    expected_duration: float,
    expected_width: int,
    expected_height: int,
    expect_audio: bool,
    duration_tolerance_seconds: Optional[float] = None,
) -> OutputValidationResult:
    """
    Automated post-render Quality Control (QC).
    Validates file existence, container readability, video resolution, duration tolerance,
    and audio presence.
    """
    output_path = Path(output_path)
    tolerance = (
        duration_tolerance_seconds
        if duration_tolerance_seconds is not None
        else getattr(settings, "render_duration_tolerance_seconds", 0.75)
    )

    result = OutputValidationResult(valid=True)

    if not output_path.exists():
        result.valid = False
        result.errors.append(f"Output file does not exist: {output_path}")
        return result

    if output_path.stat().st_size == 0:
        result.valid = False
        result.errors.append("Output file is empty (0 bytes)")
        return result

    try:
        data = run_ffprobe(["-show_format", "-show_streams", str(output_path)])
    except Exception as exc:
        result.valid = False
        result.errors.append(f"FFprobe failed to inspect output: {exc}")
        return result

    streams = data.get("streams", [])
    format_info = data.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not video_stream:
        result.valid = False
        result.errors.append("Output media contains no video stream")
    else:
        result.has_video = True
        result.video_codec = video_stream.get("codec_name")
        result.width = int(video_stream.get("width", 0))
        result.height = int(video_stream.get("height", 0))

        if expected_width > 0 and expected_height > 0:
            if result.width != expected_width or result.height != expected_height:
                result.warnings.append(
                    f"Resolution mismatch: got {result.width}x{result.height}, expected {expected_width}x{expected_height}"
                )

    if audio_stream:
        result.has_audio = True
        result.audio_codec = audio_stream.get("codec_name")

    if expect_audio and not result.has_audio:
        result.valid = False
        result.errors.append("Expected audio stream but none was found in output")
    elif not expect_audio and result.has_audio:
        result.warnings.append("Video was expected to be video-only but audio stream is present")

    try:
        dur = float(format_info.get("duration", 0.0))
        result.duration_seconds = dur
        if expected_duration > 0:
            diff = abs(dur - expected_duration)
            if diff > tolerance:
                result.warnings.append(
                    f"Duration deviation ({diff:.2f}s) exceeded tolerance ({tolerance}s). Got {dur:.2f}s, expected {expected_duration:.2f}s"
                )
    except (ValueError, TypeError):
        result.warnings.append("Could not accurately determine media duration")

    return result


def generate_qc_report(
    output_path: Path,
    validation_result: OutputValidationResult,
    render_id: str,
    project_id: str,
    timeline_version: int,
    render_config_version: int,
) -> Path:
    """Generate and persist a standardized .qc.json report alongside the rendered video."""
    output_path = Path(output_path)
    qc_path = output_path.with_name(f"{output_path.name}.qc.json")

    report = {
        "render_id": render_id,
        "project_id": project_id,
        "timeline_version": timeline_version,
        "render_config_version": render_config_version,
        "valid": validation_result.valid,
        "errors": validation_result.errors,
        "warnings": validation_result.warnings,
        "media": {
            "duration_seconds": validation_result.duration_seconds,
            "width": validation_result.width,
            "height": validation_result.height,
            "has_video": validation_result.has_video,
            "has_audio": validation_result.has_audio,
            "video_codec": validation_result.video_codec,
            "audio_codec": validation_result.audio_codec,
        },
    }

    atomic_write_json(qc_path, report)
    return qc_path
