"""Build a real audio track from approved block audio deliveries (Phase D).

Takes the audio_overlays entries produced by pipeline.audio_timeline and
mixes them into a single output audio track with exact integer-millisecond
offsets using FFmpeg ``adelay`` + ``amix``. Produces a sync report with
per-block placement diagnostics.

Rules (AUDIT 2, Phase D):
- Input only includes approved deliveries (caller gates this).
- Sorted by expected_start_ms; placed at integer ms offsets.
- Overlap must be rejected *before* FFmpeg is invoked — never auto-mixed.
- Gaps are preserved as silence and recorded in the report.
- Output duration matches the video window within a defined tolerance.
- Empty deliveries produce a warning, never a silent fake render.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.exceptions import AnalysisError
from pipeline.ffmpeg import run_ffmpeg

logger = logging.getLogger(__name__)

# Tolerance between the built track duration and the video window (ms)
DURATION_TOLERANCE_MS = 250


class AudioTrackError(AnalysisError):
    """Raised when the audio track cannot be built from deliveries."""


def build_audio_track(
    audio_overlays: list[dict[str, Any]],
    total_duration_seconds: float,
    output_path: Path,
    sample_rate: int = 44100,
    sync_report_path: Path | None = None,
) -> dict[str, Any]:
    """Mix approved block audio into one track placed at exact ms offsets.

    Args:
        audio_overlays: Entries from build_audio_timeline()["audio_overlays"],
            each with block_id, file_path, output_start, output_end,
            fit_mode, actual_duration_ms, expected_duration_ms.
        total_duration_seconds: The video timeline duration the track must
            match (within DURATION_TOLERANCE_MS).
        output_path: Destination audio file (WAV recommended).
        sample_rate: Target sample rate for the mixed track.
        sync_report_path: Optional path to write sync_report.json.

    Returns:
        Sync report dict: items, gaps, total_duration_ms, warnings, errors,
        video_duration_ms, track_duration_ms.

    Raises:
        AudioTrackError: Overlap between overlays, missing file, empty input
            with no explicit acknowledgment, or output duration mismatch.
    """
    output_path = Path(output_path)
    video_duration_ms = int(round(total_duration_seconds * 1000))

    warnings: list[str] = []
    errors: list[str] = []

    if not audio_overlays:
        raise AudioTrackError(
            "Không có audio overlay nào để dựng track "
            "(empty delivery — không render giả im lặng)."
        )

    items = sorted(audio_overlays, key=lambda o: int(round(o["output_start"] * 1000)))

    # Overlap must fail before FFmpeg
    for a, b in zip(items, items[1:]):
        a_end = int(round(a["output_end"] * 1000))
        b_start = int(round(b["output_start"] * 1000))
        if b_start < a_end:
            raise AudioTrackError(
                f"Audio overlap detected: block {a['block_id']} ends {a_end}ms "
                f"but block {b['block_id']} starts {b_start}ms. "
                "Overlap phải được xử lý trước khi dựng track."
            )

    # Verify files exist
    for item in items:
        p = Path(item["file_path"])
        if not p.exists():
            raise AudioTrackError(f"Audio file missing for block {item['block_id']}: {p}")

    # Gaps = silence between consecutive placements (recorded, not filled)
    gaps: list[dict[str, Any]] = []
    cursor = 0
    for item in items:
        start = int(round(item["output_start"] * 1000))
        if start > cursor:
            gaps.append({"from_ms": cursor, "to_ms": start, "duration_ms": start - cursor})
        cursor = max(cursor, int(round(item["output_end"] * 1000)))
    if cursor < video_duration_ms:
        gaps.append({
            "from_ms": cursor, "to_ms": video_duration_ms,
            "duration_ms": video_duration_ms - cursor,
        })
        warnings.append(
            f"Có {video_duration_ms - cursor}ms silence ở cuối track (gap giữ nguyên, không lấp)."
        )

    # Check trailing audio beyond the video window
    last_end = int(round(items[-1]["output_end"] * 1000))
    if last_end > video_duration_ms + DURATION_TOLERANCE_MS:
        raise AudioTrackError(
            f"Audio cuối vượt video duration: {last_end}ms > {video_duration_ms}ms. "
            "Cần explicit trim permission trước khi render."
        )
    if last_end > video_duration_ms:
        warnings.append(
            f"Audio cuối vượt {last_end - video_duration_ms}ms (trong tolerance)."
        )

    # Build FFmpeg filter_complex: adelay each input, then amix
    inputs: list[str] = []
    filter_parts: list[str] = []
    for idx, item in enumerate(items):
        start_ms = int(round(item["output_start"] * 1000))
        inputs.extend(["-i", str(item["file_path"])])
        filter_parts.append(
            f"[{idx}:a]aresample={sample_rate},adelay={start_ms}|{start_ms}[a{idx}]"
        )
    mix_inputs = "".join(f"[a{i}]" for i in range(len(items)))
    final_duration_s = video_duration_ms / 1000.0
    filter_complex = (
        ";".join(filter_parts)
        + f";{mix_inputs}amix=inputs={len(items)}:normalize=0,"
        f"apad=whole_dur={final_duration_s},atrim=0:{final_duration_s}[out]"
    )

    cmd = ["-y", *inputs, "-filter_complex", filter_complex, "-map", "[out]"]
    if output_path.suffix.lower() == ".wav":
        cmd += ["-c:a", "pcm_s16le"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd.append(str(output_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_ffmpeg(cmd)
    if result.returncode != 0:
        raise AudioTrackError(f"FFmpeg audio track build failed: {result.stderr[-500:]}")

    # Validate output via FFprobe
    from pipeline.media_probe import probe_audio

    info = probe_audio(output_path)
    track_duration_ms = int(info["duration_ms"])
    if abs(track_duration_ms - video_duration_ms) > DURATION_TOLERANCE_MS:
        errors.append(
            f"Track duration {track_duration_ms}ms differs from video "
            f"{video_duration_ms}ms beyond tolerance {DURATION_TOLERANCE_MS}ms"
        )

    # Per-block sync report
    placed: list[dict[str, Any]] = []
    for item in items:
        start_ms = int(round(item["output_start"] * 1000))
        end_ms = int(round(item["output_end"] * 1000))
        entry: dict[str, Any] = {
            "block_id": item["block_id"],
            "expected_start_ms": start_ms,
            "expected_end_ms": end_ms,
            "actual_placement_ms": start_ms,
            "duration_ms": end_ms - start_ms,
            "source_duration_ms": item.get("actual_duration_ms"),
            "delta_ms": (item.get("actual_duration_ms") or 0) - (end_ms - start_ms),
            "fit_mode": item.get("fit_mode"),
            "warnings": [],
            "errors": [],
        }
        if entry["delta_ms"] > DURATION_TOLERANCE_MS:
            entry["warnings"].append(f"audio dài hơn khung {entry['delta_ms']}ms")
        elif entry["delta_ms"] < -DURATION_TOLERANCE_MS:
            entry["warnings"].append(f"audio ngắn hơn khung {-entry['delta_ms']}ms")
        placed.append(entry)
        for w in entry["warnings"]:
            warnings.append(f"Block {item['block_id']}: {w}")

    report: dict[str, Any] = {
        "items": placed,
        "gaps": gaps,
        "overlaps": [],  # overlaps are rejected before building, always empty here
        "total_duration_ms": video_duration_ms,
        "track_duration_ms": track_duration_ms,
        "video_duration_ms": video_duration_ms,
        "warnings": warnings,
        "errors": errors,
        "sample_rate": sample_rate,
    }

    if sync_report_path is not None:
        sync_report_path.parent.mkdir(parents=True, exist_ok=True)
        sync_report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info(
        "Audio track built: %d items, %d gaps, track=%dms video=%dms",
        len(placed), len(gaps), track_duration_ms, video_duration_ms,
    )
    return report
