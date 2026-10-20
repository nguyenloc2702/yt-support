"""Documentary Assembler for Mode 2 (AI Genesis).

Stitches individual AI-generated shots into a unified cinematic documentary movie:
1. Strictly normalizes all shot clips to standard 1080p 24fps yuv420p video streams (no silent fallback).
2. Synthesizes Vietnamese narration per-shot from script via Edge-TTS and aligns to timeline with silence padding/tempo.
3. Concat-assembles video and audio streams seamlessly without silent truncation.
4. Validates final output with validate_rendered_output and cleans up temporary buffers while retaining raw shots.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from core.logging_config import get_logger
from pipeline.output_validator import validate_rendered_output

logger = get_logger("documentary_assembler")


def probe_duration(media_path: Path) -> float:
    """Probes the exact duration of an audio or video file in seconds."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(media_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.strip())
    except Exception as e:
        logger.debug("Failed to probe duration for %s: %s", media_path, e)
    return 0.0


def probe_video_stream(media_path: Path) -> Dict[str, str]:
    """Probes video stream properties: width, height, pix_fmt, r_frame_rate."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,pix_fmt,r_frame_rate",
        "-of", "json",
        str(media_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            streams = data.get("streams", [])
            if streams:
                return streams[0]
    except Exception as e:
        logger.debug("Failed to probe video stream for %s: %s", media_path, e)
    return {}


def probe_media_streams(media_path: Path) -> Dict[str, Any]:
    """Probes all streams in media file to count audio/video and retrieve durations."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=index,codec_type,duration:format=duration",
        "-of", "json",
        str(media_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout)
    except Exception as e:
        logger.debug("Failed to probe media streams for %s: %s", media_path, e)
    return {}


def assemble_documentary(
    job_id: str,
    project_dir: Path,
    output_path: Optional[Path] = None,
    audio_path: Optional[Path] = None,
) -> Path:
    """
    Strictly normalizes and concatenates all raw shot clips for job_id into a single documentary video.
    Overlays per-shot aligned narration/soundtrack, validates output, and cleans up temp files.
    """
    project_dir = Path(project_dir)
    shots_dir = project_dir / "shots"
    if not shots_dir.exists():
        raise FileNotFoundError(f"Shots directory not found at {shots_dir}")

    # P1.3: Match strictly shot_XXX.mp4 pattern and extract numeric index
    indexed_files = []
    for p in shots_dir.glob("shot_*.mp4"):
        m = re.match(r"^shot_(\d{3})\.mp4$", p.name)
        if m:
            indexed_files.append((int(m.group(1)), p))

    if not indexed_files:
        raise FileNotFoundError(f"No shot clips found matching pattern 'shot_XXX.mp4' in {shots_dir}")

    indexed_files.sort(key=lambda x: x[0])
    shot_files = [x[1] for x in indexed_files]
    file_indices = {x[0] for x in indexed_files}

    # Verify shots_plan.json matches exactly
    shots_plan_file = project_dir / "shots_plan.json"
    shots_data: List[Dict[str, Any]] = []
    if shots_plan_file.exists():
        try:
            shots_data = json.loads(shots_plan_file.read_text(encoding="utf-8"))
            plan_indices = {s["index"] for s in shots_data if "index" in s}
            if plan_indices and plan_indices != file_indices:
                missing = plan_indices - file_indices
                extra = file_indices - plan_indices
                raise RuntimeError(
                    f"Shot files mismatch plan for Job {job_id}: missing indices {missing}, extra indices {extra}"
                )
        except json.JSONDecodeError as err:
            logger.warning("Could not parse shots_plan.json: %s", err)

    output_final = Path(output_path or (project_dir / f"documentary_final_{job_id}.mp4"))
    output_final.parent.mkdir(parents=True, exist_ok=True)

    norm_dir = project_dir / "temp_norm_shots"
    norm_dir.mkdir(parents=True, exist_ok=True)
    temp_tts_dir = project_dir / "temp_tts_shots"
    temp_assembled = project_dir / f"assembled_temp_{job_id}.mp4"

    try:
        # 1. Strictly normalize each shot to standard 1080p (1920x1080), 24fps, yuv420p (strip internal audio)
        logger.info("Normalizing %d shots for Job %s to 1080p 24fps yuv420p...", len(shot_files), job_id)
        norm_shot_files = []
        shot_durations = []

        for idx_num, sf in indexed_files:
            norm_sf = norm_dir / f"norm_{sf.name}"
            cmd_norm = [
                "ffmpeg", "-y",
                "-i", str(sf),
                "-map", "0:v:0",
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                "-an",
                str(norm_sf)
            ]
            try:
                res_norm = subprocess.run(cmd_norm, capture_output=True, text=True, timeout=300)
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"Normalization timed out after 300s for shot '{sf.name}': {e}")

            if res_norm.returncode != 0 or not norm_sf.exists() or norm_sf.stat().st_size <= 1000:
                err_snippet = (res_norm.stderr or "").strip()[-300:]
                raise RuntimeError(
                    f"Shot normalization failed for '{sf.name}' (exit code {res_norm.returncode}): {err_snippet}"
                )

            # Probe and validate normalized output
            stream_info = probe_video_stream(norm_sf)
            if stream_info:
                w = int(stream_info.get("width", 0))
                h = int(stream_info.get("height", 0))
                if w != 1920 or h != 1080:
                    raise RuntimeError(f"Normalized shot '{sf.name}' resolution mismatch: {w}x{h} (expected 1920x1080)")

            dur = probe_duration(norm_sf)
            dur = max(0.5, dur) if dur > 0 else 10.0
            shot_durations.append(dur)
            norm_shot_files.append(norm_sf)

        total_video_duration = sum(shot_durations)
        logger.info("Normalized %d shots successfully. Total video duration: %.2fs", len(norm_shot_files), total_video_duration)

        # 2. Prepare FFmpeg concat list for video
        concat_list_file = project_dir / "concat_shots.txt"
        lines = [f"file '{sf.resolve().as_posix()}'" for sf in norm_shot_files]
        concat_list_file.write_text("\n".join(lines), encoding="utf-8")

        # 3. Prepare or synthesize Master Narration Timeline (Per-Shot Aligned)
        target_audio = audio_path
        if target_audio and Path(target_audio).exists():
            # P1.5: Pad or trim external audio to exact total_video_duration
            padded_ext = project_dir / "temp_ext_aligned.wav"
            cmd_pad_ext = [
                "ffmpeg", "-y",
                "-i", str(target_audio),
                "-af", f"apad=whole_dur={total_video_duration:.3f}",
                "-t", f"{total_video_duration:.3f}",
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                str(padded_ext)
            ]
            subprocess.run(cmd_pad_ext, check=True, capture_output=True, timeout=120)
            target_audio = padded_ext

        # Synthesize per-shot narration from shots_plan.json
        if not target_audio and shots_data:
            temp_tts_dir.mkdir(parents=True, exist_ok=True)
            try:
                aligned_audio_segments = []
                narration_report = []

                import edge_tts

                async def _synth_shot_with_retry(text: str, out_p: Path):
                    for attempt in range(2):
                        try:
                            comm = edge_tts.Communicate(text, "vi-VN-HoaiMyNeural")
                            await asyncio.wait_for(comm.save(str(out_p)), timeout=60.0)
                            return
                        except Exception as e:
                            if attempt == 1:
                                raise e
                            await asyncio.sleep(1.0)

                shots_by_idx = {s.get("index"): s for s in shots_data}

                for idx, (idx_num, _) in enumerate(indexed_files):
                    shot_dur = shot_durations[idx]
                    s_meta = shots_by_idx.get(idx_num, {})
                    shot_text = str(s_meta.get("text", "")).strip()
                    seg_out = temp_tts_dir / f"seg_aligned_{idx_num:03d}.wav"
                    truncated = False

                    if shot_text:
                        raw_tts_file = temp_tts_dir / f"raw_tts_{idx_num:03d}.mp3"
                        asyncio.run(_synth_shot_with_retry(shot_text, raw_tts_file))

                        tts_dur = probe_duration(raw_tts_file)
                        if tts_dur <= 0:
                            tts_dur = 1.0

                        ratio = tts_dur / shot_dur

                        if ratio <= 1.0:
                            # Pad with silence to fit exact shot duration
                            cmd_pad = [
                                "ffmpeg", "-y",
                                "-i", str(raw_tts_file),
                                "-af", "apad",
                                "-t", f"{shot_dur:.3f}",
                                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                                str(seg_out)
                            ]
                            subprocess.run(cmd_pad, check=True, capture_output=True, timeout=120)
                        elif ratio <= 1.25:
                            # Speed up slightly within intelligible range
                            cmd_speed = [
                                "ffmpeg", "-y",
                                "-i", str(raw_tts_file),
                                "-filter:a", f"atempo={ratio:.2f}",
                                "-t", f"{shot_dur:.3f}",
                                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                                str(seg_out)
                            ]
                            subprocess.run(cmd_speed, check=True, capture_output=True, timeout=120)
                        else:
                            # P1.2: Cap atempo at 1.35 and apply fade-out over last 0.3s to prevent abrupt clip
                            truncated = True
                            logger.warning(
                                "Shot %d narration longer than shot duration (tts: %.2fs > shot: %.2fs). Applying tempo+fade.",
                                idx_num, tts_dur, shot_dur
                            )
                            fade_start = max(0.1, shot_dur - 0.3)
                            filt = f"atempo=1.35,afade=t=out:st={fade_start:.3f}:d=0.3"
                            cmd_speed_fade = [
                                "ffmpeg", "-y",
                                "-i", str(raw_tts_file),
                                "-filter:a", filt,
                                "-t", f"{shot_dur:.3f}",
                                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                                str(seg_out)
                            ]
                            subprocess.run(cmd_speed_fade, check=True, capture_output=True, timeout=120)

                        narration_report.append({
                            "shot_index": idx_num,
                            "text_length": len(shot_text),
                            "tts_dur": round(tts_dur, 2),
                            "shot_dur": round(shot_dur, 2),
                            "ratio": round(ratio, 2),
                            "truncated": truncated,
                        })
                    else:
                        # Generate exact silence segment
                        cmd_silence = [
                            "ffmpeg", "-y",
                            "-f", "lavfi",
                            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                            "-t", f"{shot_dur:.3f}",
                            "-c:a", "pcm_s16le",
                            str(seg_out)
                        ]
                        subprocess.run(cmd_silence, check=True, capture_output=True, timeout=120)
                        narration_report.append({
                            "shot_index": idx_num,
                            "text_length": 0,
                            "tts_dur": 0.0,
                            "shot_dur": round(shot_dur, 2),
                            "ratio": 0.0,
                            "truncated": False,
                        })

                    aligned_audio_segments.append(seg_out)

                # Write QC report
                (project_dir / "narration_report.json").write_text(
                    json.dumps(narration_report, indent=2), encoding="utf-8"
                )

                # Concatenate aligned audio segments into master narration track
                audio_concat_txt = temp_tts_dir / "concat_audio.txt"
                audio_lines = [f"file '{p.resolve().as_posix()}'" for p in aligned_audio_segments]
                audio_concat_txt.write_text("\n".join(audio_lines), encoding="utf-8")

                master_narration = project_dir / "master_narration.wav"
                cmd_audio_join = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", str(audio_concat_txt),
                    "-c:a", "pcm_s16le",
                    str(master_narration)
                ]
                subprocess.run(cmd_audio_join, check=True, capture_output=True, timeout=180)
                if master_narration.exists() and master_narration.stat().st_size > 1000:
                    target_audio = master_narration
                    logger.info("✔ Master narration generated & aligned to timeline (%.2fs)", probe_duration(master_narration))
            except Exception as e:
                logger.error("Failed to generate per-shot aligned narration: %s", e, exc_info=True)
                raise RuntimeError(f"Edge-TTS documentary narration synthesis failed: {e}")

        # 4. Mix with BGM if configured and asset available (P1.1: Never use original download.* speech!)
        mixed_audio = target_audio
        bgm_source_policy = os.environ.get("MODE2_BGM_SOURCE", "asset").lower()

        # Check for licensed BGM music asset if requested
        music_asset_path: Optional[Path] = None
        if bgm_source_policy == "asset":
            for music_dir in [project_dir / "assets", Path("assets/music"), Path("data/music")]:
                if music_dir.exists():
                    found = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav"))
                    if found:
                        music_asset_path = found[0]
                        break

        if target_audio and music_asset_path and music_asset_path.exists():
            mixed_out = project_dir / "temp_mixed_soundtrack.aac"
            try:
                cmd_mix = [
                    "ffmpeg", "-y",
                    "-i", str(target_audio),
                    "-stream_loop", "-1", "-i", str(music_asset_path),
                    "-filter_complex",
                    f"[0:a]volume=1.0[vocal];[1:a]volume=0.12[bgm];[vocal][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                    "-map", "[aout]",
                    "-t", f"{total_video_duration:.3f}",
                    "-c:a", "aac", "-b:a", "192k",
                    str(mixed_out)
                ]
                res_mix = subprocess.run(cmd_mix, capture_output=True, text=True, timeout=180)
                if res_mix.returncode == 0 and mixed_out.exists() and mixed_out.stat().st_size > 1000:
                    mixed_audio = mixed_out
                    logger.info("✔ Licensed BGM mixed under narration at volume=0.12")
            except Exception as e:
                logger.debug("BGM mixing skipped: %s", e)
        else:
            logger.info("Mode 2 soundtrack: narration-only (clean AI documentary, no YouTube speech mixed).")

        # 5. Build Final FFmpeg concat & mux command
        if temp_assembled.exists():
            temp_assembled.unlink()

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
        ]

        if mixed_audio and mixed_audio.exists():
            cmd += [
                "-i", str(mixed_audio),
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-r", "24",
                "-c:a", "aac", "-b:a", "192k",
                "-t", f"{total_video_duration:.3f}",
                str(temp_assembled),
            ]
        else:
            cmd += [
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-r", "24",
                "-c:a", "aac", "-b:a", "192k",
                "-t", f"{total_video_duration:.3f}",
                str(temp_assembled),
            ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"FFmpeg assembly timed out after 900s: {e}")

        if res.returncode != 0:
            logger.error("FFmpeg concat failed: %s", res.stderr[-500:])
            raise RuntimeError(f"FFmpeg assembly failed: {res.stderr[-300:]}")

        if not temp_assembled.exists() or temp_assembled.stat().st_size == 0:
            raise RuntimeError("Assembled video file is missing or empty.")

        # 6. Post-mux stream & duration assertions (P1.4)
        streams_meta = probe_media_streams(temp_assembled)
        stream_list = streams_meta.get("streams", [])
        v_streams = [s for s in stream_list if s.get("codec_type") == "video"]
        a_streams = [s for s in stream_list if s.get("codec_type") == "audio"]

        if len(v_streams) != 1:
            raise RuntimeError(f"Post-mux assertion failed: expected 1 video stream, found {len(v_streams)}")
        if len(a_streams) != 1:
            raise RuntimeError(f"Post-mux assertion failed: expected 1 audio stream, found {len(a_streams)}")

        actual_video_dur = probe_duration(temp_assembled)
        if abs(actual_video_dur - total_video_duration) > 0.25:
            raise RuntimeError(
                f"Post-mux duration assertion failed: video duration {actual_video_dur:.2f}s "
                f"differs from expected {total_video_duration:.2f}s by >0.25s"
            )

        # 7. Atomic replace to final output
        if output_final.exists():
            output_final.unlink()
        temp_assembled.replace(output_final)

        # 8. Quality Control validation
        val_res = validate_rendered_output(output_final)
        if not val_res.is_valid:
            raise ValueError(f"Documentary QC validation failed: {val_res.error_message}")

        logger.info("✔ Documentary assembly successfully completed for %s: %s (%.1f MB, %.2fs)",
                    job_id, output_final.name, output_final.stat().st_size / (1024 * 1024), actual_video_dur)
        return output_final

    finally:
        # Clean up temporary normalized shot files and intermediate audio buffers
        shutil.rmtree(norm_dir, ignore_errors=True)
        shutil.rmtree(temp_tts_dir, ignore_errors=True)
        if (project_dir / "temp_bgm.aac").exists():
            (project_dir / "temp_bgm.aac").unlink(missing_ok=True)
        if (project_dir / "temp_mixed_soundtrack.aac").exists():
            (project_dir / "temp_mixed_soundtrack.aac").unlink(missing_ok=True)
        if (project_dir / "temp_ext_aligned.wav").exists():
            (project_dir / "temp_ext_aligned.wav").unlink(missing_ok=True)
