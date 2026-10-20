"""Documentary Assembler for Mode 2 (AI Genesis).

Stitches individual AI-generated shots into a unified cinematic documentary movie:
1. Normalizes all shot clips to standard 1080p 24fps yuv420p video streams.
2. Synthesizes Vietnamese narration from script via Edge-TTS and mixes with background audio.
3. Concat-assembles video and audio streams seamlessly.
4. Validates final output with validate_rendered_output and cleans up temporary buffers.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import List, Optional

from core.logging_config import get_logger
from pipeline.output_validator import validate_rendered_output

logger = get_logger("documentary_assembler")


def assemble_documentary(
    job_id: str,
    project_dir: Path,
    output_path: Optional[Path] = None,
    audio_path: Optional[Path] = None,
) -> Path:
    """
    Normalizes and concatenates all raw shot clips for job_id into a single documentary video.
    Overlays narration/soundtrack, validates output, and cleans up temp normalization files.
    """
    project_dir = Path(project_dir)
    shots_dir = project_dir / "shots"
    if not shots_dir.exists():
        raise FileNotFoundError(f"Shots directory not found at {shots_dir}")

    shot_files = sorted(shots_dir.glob("shot_*.mp4"), key=lambda p: p.name)
    if not shot_files:
        raise FileNotFoundError(f"No shot clips found in {shots_dir}")

    output_final = Path(output_path or (project_dir / f"documentary_final_{job_id}.mp4"))
    output_final.parent.mkdir(parents=True, exist_ok=True)

    norm_dir = project_dir / "temp_norm_shots"
    norm_dir.mkdir(parents=True, exist_ok=True)
    temp_assembled = project_dir / f"assembled_temp_{job_id}.mp4"

    try:
        # 1. Normalize each shot to standard 1080p, 24fps, yuv420p (strip internal shot audio)
        logger.info("Normalizing %d shots for Job %s to 1080p 24fps...", len(shot_files), job_id)
        norm_shot_files = []
        for sf in shot_files:
            norm_sf = norm_dir / f"norm_{sf.name}"
            cmd_norm = [
                "ffmpeg", "-y",
                "-i", str(sf),
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                "-an",
                str(norm_sf)
            ]
            res_norm = subprocess.run(cmd_norm, capture_output=True, text=True)
            if res_norm.returncode == 0 and norm_sf.exists() and norm_sf.stat().st_size > 1000:
                norm_shot_files.append(norm_sf)
            else:
                logger.warning("Shot normalization failed for %s (%s). Using original.", sf.name, res_norm.stderr[-200:])
                norm_shot_files.append(sf)

        # 2. Prepare FFmpeg concat list
        concat_list_file = project_dir / "concat_shots.txt"
        lines = [f"file '{sf.resolve().as_posix()}'" for sf in norm_shot_files]
        concat_list_file.write_text("\n".join(lines), encoding="utf-8")

        # 3. Prepare or synthesize Master Narration / Soundtrack
        target_audio = audio_path
        if not target_audio:
            candidates = list(project_dir.glob("narration.*")) + list(project_dir.glob("audio.*"))
            if candidates:
                target_audio = candidates[0]

        # If no pre-existing narration audio, attempt to synthesize from shots_plan.json
        if not target_audio:
            shots_plan_file = project_dir / "shots_plan.json"
            if shots_plan_file.exists():
                try:
                    shots_data = json.loads(shots_plan_file.read_text(encoding="utf-8"))
                    narration_texts = [s.get("text", "").strip() for s in shots_data if s.get("text")]
                    full_narration = " ... ".join(narration_texts)
                    if full_narration:
                        synth_out = project_dir / "narration.mp3"
                        logger.info("Synthesizing documentary voiceover narration via Edge-TTS...")
                        import edge_tts
                        async def _synth():
                            comm = edge_tts.Communicate(full_narration, "vi-VN-HoaiMyNeural")
                            await comm.save(str(synth_out))
                        asyncio.run(_synth())
                        if synth_out.exists() and synth_out.stat().st_size > 1000:
                            target_audio = synth_out
                except Exception as e:
                    logger.warning("Could not synthesize automated narration for Mode 2: %s", e)

        # 4. Check for original video BGM to mix with narration
        mixed_audio = target_audio
        raw_video = next(project_dir.glob("download.*"), None)
        if target_audio and raw_video and raw_video.exists():
            bgm_extracted = project_dir / "temp_bgm.aac"
            mixed_out = project_dir / "temp_mixed_soundtrack.aac"
            try:
                # Extract original audio as background music
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(raw_video), "-vn", "-c:a", "aac", "-b:a", "128k", str(bgm_extracted)],
                    capture_output=True, check=True
                )
                if bgm_extracted.exists() and bgm_extracted.stat().st_size > 1000:
                    # Mix: narration at 100%, BGM ducked at -15dB
                    cmd_mix = [
                        "ffmpeg", "-y",
                        "-i", str(target_audio),
                        "-i", str(bgm_extracted),
                        "-filter_complex", "[0:a]volume=1.2[a0];[1:a]volume=0.18[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                        "-map", "[aout]",
                        "-c:a", "aac", "-b:a", "192k",
                        str(mixed_out)
                    ]
                    res_mix = subprocess.run(cmd_mix, capture_output=True, text=True)
                    if res_mix.returncode == 0 and mixed_out.exists():
                        mixed_audio = mixed_out
            except Exception as e:
                logger.debug("BGM extraction/mixing skipped: %s", e)

        # 5. Build FFmpeg concat command
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
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-r", "24",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                str(temp_assembled),
            ]
        else:
            # Generate clean silent audio track if raw shots lack audio
            cmd += [
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-r", "24",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                str(temp_assembled),
            ]

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if res.returncode != 0:
            logger.error("FFmpeg concat failed: %s", res.stderr[-500:])
            raise RuntimeError(f"FFmpeg assembly failed: {res.stderr[-300:]}")

        if not temp_assembled.exists() or temp_assembled.stat().st_size == 0:
            raise RuntimeError("Assembled video file is missing or empty.")

        # 6. Atomic replace to final output
        if output_final.exists():
            output_final.unlink()
        temp_assembled.replace(output_final)

        # 7. Quality Control validation
        val_res = validate_rendered_output(output_final)
        if not val_res.is_valid:
            raise ValueError(f"Documentary QC validation failed: {val_res.error_message}")

        logger.info("✔ Documentary assembly successfully completed for %s: %s (%.1f MB)",
                    job_id, output_final.name, output_final.stat().st_size / (1024 * 1024))
        return output_final

    finally:
        # Clean up temporary normalized shot files and intermediate audio
        shutil.rmtree(norm_dir, ignore_errors=True)
        if (project_dir / "temp_bgm.aac").exists():
            (project_dir / "temp_bgm.aac").unlink(missing_ok=True)
        if (project_dir / "temp_mixed_soundtrack.aac").exists():
            (project_dir / "temp_mixed_soundtrack.aac").unlink(missing_ok=True)
