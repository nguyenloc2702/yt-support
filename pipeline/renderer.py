import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from config.settings import settings
from core.exceptions import JobCancelledError, RenderError
from core.render_schemas import RenderConfiguration
from pipeline.audio_mix import apply_global_audio_mix
from pipeline.ffmpeg import pick_video_encoder, run_ffmpeg_with_progress
from pipeline.output_validator import generate_qc_report, validate_rendered_output


def _build_scale_filter(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    )


def render_video(
    source_path: Path,
    timeline: Dict[str, Any],
    output_path: Path,
    preset: Dict[str, Any],
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancellation_token=None,
    render_configuration: Optional[RenderConfiguration] = None,
    project_id: str = "",
    render_id: str = "",
    output_timeline_offset_seconds: float = 0.0,
) -> Path:
    """
    Render a video from a timeline.
    1. Normalizes and re-encodes each clip.
    2. Concatenates into a joined video stream.
    3. Performs global audio post-processing (source voice gain, BGM mixing, loudness normalization).
    4. Validates output and generates Quality Control (.qc.json) report.
    5. Supports cooperative cancellation and writes safely to .partial.mp4 before atomic replace.
    """
    clips = [c for c in timeline.get("clips", []) if c.get("enabled", True)]
    if not clips:
        raise RenderError("Timeline has no enabled clips")

    source_path = Path(source_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = output_path.parent / f"temp_render_{output_path.stem}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    width = int(preset.get("width", 1920))
    height = int(preset.get("height", 1080))
    vf = _build_scale_filter(width, height)
    fps = preset.get("fps")

    video_encoder = pick_video_encoder(preset.get("video_codec", "libx264"))
    partial_path = output_path.with_name(output_path.stem + ".partial.mp4")
    joined_video_path = temp_dir / "joined.mp4"

    clip_files = []
    total_clips = len(clips)
    total_timeline_dur = sum(max(0.0, float(c["source_end"]) - float(c["source_start"])) for c in clips)

    try:
        # Phase 1: Render each clip
        for i, clip in enumerate(clips):
            start = float(clip["source_start"])
            end = float(clip["source_end"])
            duration = max(end - start, 0.0)
            clip_output = temp_dir / f"clip_{i:03d}.mp4"

            args = [
                "-ss", f"{start:.3f}",
                "-to", f"{end:.3f}",
                "-i", str(source_path),
                "-vf", vf,
            ]
            if fps:
                args += ["-r", str(fps)]

            args += [
                "-c:v", video_encoder,
                "-preset", preset.get("preset", "veryfast"),
                "-crf", str(preset.get("crf", 23)),
                "-pix_fmt", preset.get("pixel_format", "yuv420p"),
                "-c:a", preset.get("audio_codec", "aac"),
                "-b:a", preset.get("audio_bitrate", "192k"),
                "-ar", "48000",
                "-ac", "2",
                str(clip_output),
            ]

            base = int(i / total_clips * 65)
            span = max(int(1 / total_clips * 65), 1)
            try:
                run_ffmpeg_with_progress(
                    args,
                    total_duration=duration,
                    progress_callback=progress_callback,
                    cancellation_token=cancellation_token,
                    base_percent=base,
                    span_percent=span,
                    step_label=f"Rendering clip {i + 1}/{total_clips}",
                )
            except subprocess.CalledProcessError as exc:
                raise RenderError(f"Failed to encode clip {i + 1}: {exc.stderr or exc}") from exc
            clip_files.append(clip_output)

        # Phase 2: Concatenate clips
        concat_file = temp_dir / "concat.txt"
        with open(concat_file, "w", encoding="utf-8") as fh:
            for cf in clip_files:
                fh.write(f"file '{cf.absolute().as_posix()}'\n")

        if progress_callback:
            progress_callback(66, "Concatenating clips")

        concat_args = [
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            "-f", "mp4",
            str(joined_video_path),
        ]
        try:
            run_ffmpeg_with_progress(
                concat_args,
                total_duration=0.0,
                progress_callback=None,
                cancellation_token=cancellation_token,
                base_percent=66,
                span_percent=10,
                step_label="Concatenating clips",
            )
        except subprocess.CalledProcessError as exc:
            raise RenderError(f"Failed to concatenate clips: {exc.stderr or exc}") from exc

        if not joined_video_path.exists():
            raise RenderError("Timeline concatenation produced no video file")

        # Phase 3: Global audio post-processing
        tts_opts = getattr(render_configuration, "tts", None) if render_configuration else None
        tts_enabled = bool(tts_opts and tts_opts.enabled)

        if render_configuration is not None:
            if progress_callback:
                progress_callback(78, "Applying audio post-processing & BGM mixing")
            mix_output = temp_dir / "mixed.mp4" if tts_enabled else output_path
            apply_global_audio_mix(
                joined_video_path=joined_video_path,
                output_path=mix_output,
                project_id=project_id,
                configuration=render_configuration,
                output_duration_seconds=total_timeline_dur,
                output_timeline_offset_seconds=output_timeline_offset_seconds,
                progress_callback=progress_callback,
                cancellation_token=cancellation_token,
                base_percent=78,
                span_percent=18 if tts_enabled else 18,
            )

            # Phase 3b: TTS narration overlay (script beats -> speech)
            if tts_enabled:
                from pipeline.tts import generate_and_overlay_narration

                result = generate_and_overlay_narration(
                    video_path=mix_output,
                    timeline=timeline,
                    output_path=output_path,
                    voice=tts_opts.voice,
                    rate=tts_opts.rate,
                    gain_db=tts_opts.gain_db,
                    duck_gain_db=tts_opts.duck_gain_db,
                    replace_original=bool(getattr(tts_opts, "replace_original", False)),
                    temp_dir=temp_dir / "tts",
                    progress_callback=progress_callback,
                )
                if result is None:
                    # No narration text in timeline — fall back to mixed output
                    if output_path.exists():
                        output_path.unlink()
                    shutil.copy2(mix_output, output_path)
        else:
            # Fallback legacy mode: move joined directly to output
            if output_path.exists():
                output_path.unlink()
            shutil.copy2(joined_video_path, output_path)

        # Phase 4: Output validation & Automated QC
        if progress_callback:
            progress_callback(97, "Running media Quality Control (QC)")

        expect_audio = True
        if render_configuration:
            expect_audio = render_configuration.voice.enabled or (
                render_configuration.music.enabled and bool(render_configuration.music.asset_id)
            )

        qc_result = validate_rendered_output(
            output_path=output_path,
            expected_duration=total_timeline_dur,
            expected_width=width,
            expected_height=height,
            expect_audio=expect_audio,
        )

        generate_qc_report(
            output_path=output_path,
            validation_result=qc_result,
            render_id=render_id or output_path.stem,
            project_id=project_id,
            timeline_version=timeline.get("version", 1),
            render_config_version=render_configuration.version if render_configuration else 1,
        )

        if not qc_result.valid:
            error_msg = "; ".join(qc_result.errors)
            raise RenderError(f"Rendered media failed quality control: {error_msg}")

        if progress_callback:
            progress_callback(100, "Render completed successfully")

    except JobCancelledError:
        if partial_path.exists():
            partial_path.unlink()
        if output_path.exists():
            output_path.unlink()
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return output_path