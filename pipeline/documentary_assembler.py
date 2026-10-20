"""Documentary Assembler for Mode 2 (AI Genesis).

Stitches individual AI-generated shots into a unified cinematic documentary movie,
layers background audio / narration, validates the final render, and cleans up temp buffers.
"""

import json
import logging
import os
from pathlib import Path
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
    Concatenates all raw shot clips for job_id into a single documentary video.
    Optionally overlays master audio / narration soundtrack.
    Validates output with validate_rendered_output before returning.
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

    # 1. Prepare FFmpeg concat list
    concat_list_file = project_dir / "concat_shots.txt"
    lines = []
    for sf in shot_files:
        # Use forward slashes for FFmpeg concat file
        safe_path = str(sf.resolve()).replace("\\", "/")
        lines.append(f"file '{safe_path}'")
    concat_list_file.write_text("\n".join(lines), encoding="utf-8")

    logger.info("Assembling %d shots for Job %s into %s...", len(shot_files), job_id, output_final.name)

    # 2. Check for optional audio track
    target_audio = audio_path
    if not target_audio:
        candidates = list(project_dir.glob("narration.*")) + list(project_dir.glob("audio.*"))
        if candidates:
            target_audio = candidates[0]

    # 3. Build FFmpeg command
    temp_assembled = project_dir / f"assembled_temp_{job_id}.mp4"
    if temp_assembled.exists():
        temp_assembled.unlink()

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file),
    ]

    if target_audio and target_audio.exists():
        cmd += [
            "-i", str(target_audio),
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-pix_fmt", "yuv420p",
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
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(temp_assembled),
        ]

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        logger.error("FFmpeg concat failed: %s", res.stderr[-500:])
        raise RuntimeError(f"FFmpeg assembly failed: {res.stderr[-300:]}")

    if not temp_assembled.exists() or temp_assembled.stat().st_size == 0:
        raise RuntimeError("Assembled video file is missing or empty.")

    # 4. Atomic rename to final output
    if output_final.exists():
        output_final.unlink()
    temp_assembled.replace(output_final)

    # 5. Quality Control validation
    val_res = validate_rendered_output(output_final)
    if not val_res.is_valid:
        raise ValueError(f"Documentary QC validation failed: {val_res.error_message}")

    logger.info("✔ Documentary assembly successfully completed for %s: %s (%.1f MB)",
                job_id, output_final.name, output_final.stat().st_size / (1024 * 1024))
    return output_final
