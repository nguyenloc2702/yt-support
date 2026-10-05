import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from config.settings import settings
from core.exceptions import InvalidMediaError, JobCancelledError, RenderError
from core.render_schemas import RenderConfiguration
from pipeline.ffmpeg import run_ffmpeg_with_progress, run_ffprobe
from services.asset_service import AssetService


@dataclass
class AudioPostProcessResult:
    output_path: Path
    duration_seconds: float
    has_audio: bool


def probe_joined_video(path: Path) -> Tuple[bool, float]:
    """Check if joined video has an audio stream and return (has_audio, duration_seconds)."""
    try:
        info = run_ffprobe(["-show_format", "-show_streams", str(path)])
    except Exception as exc:
        raise InvalidMediaError(f"Cannot probe joined video: {exc}") from exc

    streams = info.get("streams", [])
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    format_info = info.get("format", {})
    try:
        duration = float(format_info.get("duration", 0.0))
    except (ValueError, TypeError):
        duration = 0.0
    return has_audio, duration


def apply_global_audio_mix(
    joined_video_path: Path,
    output_path: Path,
    project_id: str,
    configuration: RenderConfiguration,
    output_duration_seconds: float,
    output_timeline_offset_seconds: float = 0.0,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancellation_token=None,
    base_percent: int = 80,
    span_percent: int = 15,
) -> AudioPostProcessResult:
    """
    Apply global audio post-processing (source voice gain, BGM mix, loudness normalization)
    to a joined video. Supports all 6 combinations of source audio & music presence/enablement.
    Writes output to a `.partial.mp4` file and renames upon success.
    """
    joined_video_path = Path(joined_video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    partial_path = output_path.with_name(output_path.stem + ".partial.mp4")
    if partial_path.exists():
        partial_path.unlink()

    source_has_audio, actual_video_dur = probe_joined_video(joined_video_path)
    if output_duration_seconds <= 0:
        output_duration_seconds = actual_video_dur if actual_video_dur > 0 else 1.0

    voice_opts = configuration.voice
    music_opts = configuration.music
    loud_opts = configuration.loudness

    # Determine effective flags
    effective_source_audio = source_has_audio and voice_opts.enabled
    effective_music = music_opts.enabled and bool(music_opts.asset_id)

    asset_service = AssetService()
    music_asset_path: Optional[Path] = None
    music_asset = None
    if effective_music:
        try:
            music_asset_path = asset_service.resolve_music_path(project_id, music_opts.asset_id)
            music_asset = asset_service.get_music_asset(project_id, music_opts.asset_id)
        except Exception as exc:
            raise RenderError(f"Cannot resolve music asset: {exc}") from exc

    # Loudnorm filter string
    loudnorm_filter = (
        f"loudnorm=I={loud_opts.target_lufs}:LRA={loud_opts.loudness_range}:TP={loud_opts.true_peak_db}"
        if loud_opts.enabled
        else None
    )

    args: List[str] = ["-i", str(joined_video_path)]

    # Case 5 & 6: Video-only (no source audio or source disabled, no music)
    if not effective_source_audio and not effective_music:
        args += [
            "-map", "0:v:0",
            "-an",
            "-c:v", "copy",
            "-movflags", "+faststart",
            "-f", "mp4",
            str(partial_path),
        ]
        has_final_audio = False

    # Case 1: Source audio only (music disabled or unavailable)
    elif effective_source_audio and not effective_music:
        voice_gain = voice_opts.gain_db
        af_chain = [f"volume={voice_gain:.2f}dB"]
        if loudnorm_filter:
            af_chain.append(loudnorm_filter)

        args += [
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-c:v", "copy",
            "-af", ",".join(af_chain),
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-movflags", "+faststart",
            "-f", "mp4",
            str(partial_path),
        ]
        has_final_audio = True

    # Case 2, 3, 4: Music is enabled
    else:
        # Prepare music inputs and playhead calculations
        # output_timeline_offset_seconds accounts for test preview playhead
        full_timeline_playhead = output_timeline_offset_seconds
        music_start = music_opts.timeline_start_seconds
        music_dur = music_asset.duration_seconds if music_asset else 60.0

        # Calculate offset in music track
        if full_timeline_playhead < music_start:
            # Music hasn't started yet at the beginning of preview/full
            effective_delay = music_start - full_timeline_playhead
            read_offset = music_opts.source_offset_seconds
        else:
            # Music is already playing at full_timeline_playhead
            effective_delay = 0.0
            elapsed = full_timeline_playhead - music_start
            if music_opts.loop:
                read_offset = (music_opts.source_offset_seconds + elapsed) % music_dur
            else:
                read_offset = music_opts.source_offset_seconds + elapsed

        # Build music input
        music_input_args: List[str] = []
        if music_opts.loop:
            music_input_args += ["-stream_loop", "-1"]
        if read_offset > 0:
            music_input_args += ["-ss", f"{read_offset:.3f}"]
        music_input_args += ["-i", str(music_asset_path)]

        args = ["-i", str(joined_video_path)] + music_input_args

        # Calculate music visible duration
        music_visible_dur = max(0.0, output_duration_seconds - effective_delay)
        if not music_opts.loop:
            remaining_music = max(0.0, music_dur - read_offset)
            music_visible_dur = min(music_visible_dur, remaining_music)

        fade_in = min(music_opts.fade_in_seconds, music_visible_dur * 0.5)
        fade_out = min(music_opts.fade_out_seconds, music_visible_dur * 0.5)
        fade_out_start = max(0.0, music_visible_dur - fade_out)

        # Build music audio filter
        music_filters = [
            f"atrim=duration={music_visible_dur:.3f}",
            "asetpts=PTS-STARTPTS",
            f"volume={music_opts.gain_db:.2f}dB",
        ]
        if fade_in > 0:
            music_filters.append(f"afade=t=in:st=0:d={fade_in:.2f}")
        if fade_out > 0:
            music_filters.append(f"afade=t=out:st={fade_out_start:.2f}:d={fade_out:.2f}")
        if effective_delay > 0:
            delay_ms = int(effective_delay * 1000)
            music_filters.append(f"adelay={delay_ms}:all=1")

        music_chain = ",".join(music_filters)

        # Case 2: Source audio + Music
        if effective_source_audio:
            voice_gain = voice_opts.gain_db
            filter_complex = (
                f"[0:a:0]volume={voice_gain:.2f}dB[voice];"
                f"[1:a:0]{music_chain}[music];"
                f"[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mixed]"
            )
            if loudnorm_filter:
                filter_complex += f";[mixed]{loudnorm_filter}[aout]"
            else:
                filter_complex += ";[mixed]anull[aout]"
        else:
            # Case 3 & 4: Music only (no source audio or source disabled)
            filter_complex = f"[1:a:0]{music_chain},apad=whole_dur={output_duration_seconds:.3f},atrim=duration={output_duration_seconds:.3f}[music_padded]"
            if loudnorm_filter:
                filter_complex += f";[music_padded]{loudnorm_filter}[aout]"
            else:
                filter_complex += ";[music_padded]anull[aout]"

        args += [
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-movflags", "+faststart",
            "-f", "mp4",
            str(partial_path),
        ]
        has_final_audio = True

    try:
        run_ffmpeg_with_progress(
            args,
            total_duration=output_duration_seconds,
            progress_callback=progress_callback,
            cancellation_token=cancellation_token,
            base_percent=base_percent,
            span_percent=span_percent,
            step_label="Audio mixing & normalization",
        )
    except subprocess.CalledProcessError as exc:
        if partial_path.exists():
            partial_path.unlink()
        raise RenderError(f"Audio post-processing failed: {exc.stderr or exc}") from exc
    except JobCancelledError:
        if partial_path.exists():
            partial_path.unlink()
        raise

    if not partial_path.exists():
        raise RenderError("Audio post-processing produced no output file")

    if output_path.exists():
        output_path.unlink()
    partial_path.replace(output_path)

    return AudioPostProcessResult(
        output_path=output_path,
        duration_seconds=output_duration_seconds,
        has_audio=has_final_audio,
    )


def slice_timeline_by_output_range(
    timeline: Dict[str, Any],
    output_start: float,
    duration: float,
) -> Tuple[Dict[str, Any], float]:
    """
    Derive a slice of a timeline intersecting [output_start, output_start + duration].
    Maps timestamps back to source clips so that the normal rendering pipeline can render
    a quick 10-30s test preview without re-rendering the whole project.
    Returns (derived_timeline, effective_output_start).
    """
    clips = [c for c in timeline.get("clips", []) if c.get("enabled", True)]
    if not clips:
        raise RenderError("Timeline contains no enabled clips to slice")

    output_end = output_start + duration
    accumulated = 0.0
    sliced_clips: List[Dict[str, Any]] = []

    for clip in clips:
        src_start = float(clip["source_start"])
        src_end = float(clip["source_end"])
        clip_dur = max(0.0, src_end - src_start)

        clip_out_start = accumulated
        clip_out_end = accumulated + clip_dur
        accumulated += clip_dur

        # Check intersection with [output_start, output_end]
        inter_start = max(output_start, clip_out_start)
        inter_end = min(output_end, clip_out_end)

        if inter_start < inter_end:
            # Overlap exists
            offset_in_clip = inter_start - clip_out_start
            overlap_dur = inter_end - inter_start

            new_clip = clip.copy()
            new_clip["id"] = f"slice_{len(sliced_clips) + 1}"
            new_clip["source_start"] = src_start + offset_in_clip
            new_clip["source_end"] = new_clip["source_start"] + overlap_dur
            new_clip["order"] = len(sliced_clips) + 1
            new_clip["enabled"] = True
            sliced_clips.append(new_clip)

    if not sliced_clips:
        raise RenderError(f"Test preview range [{output_start}s - {output_end}s] does not overlap any clips")

    total_sliced_dur = sum(float(c["source_end"]) - float(c["source_start"]) for c in sliced_clips)

    derived = timeline.copy()
    derived["clips"] = sliced_clips
    derived["actual_duration"] = total_sliced_dur
    derived["is_derived_preview"] = True

    return derived, output_start
