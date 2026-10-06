"""
Script-led output timeline planner.

Given an APPROVED script (with target_duration_seconds) and the transcript,
build an output timeline where:
- each beat gets an output window sized by its narration share;
- visuals are picked from the beat's referenced transcript segments WITHOUT
  stretching across unrelated source gaps (never min(start)..max(end));
- the sum of clip output durations ≈ script target duration.

Output contract: clips carry BOTH source time (source_start/source_end) and
output time (output_start/output_end). actual_duration == total output time.
"""

from typing import Any, Dict, List, Optional

from core.logging_config import get_logger
from pipeline.beat_mapper import _build_index, _tighten_with_words, MIN_CLIP_SECONDS

logger = get_logger(__name__)

# Segments closer than this are treated as one contiguous visual region.
GAP_TOLERANCE_SECONDS = 1.5


def _beat_word_count(beat: Dict[str, Any]) -> int:
    return max(1, len(str(beat.get("text") or "").split()))


def allocate_beat_windows(script: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Allocate contiguous output windows to beats proportional to word count,
    summing exactly to target_duration_seconds.
    """
    beats = [b for b in script.get("beats") or [] if str(b.get("text") or "").strip()]
    if not beats:
        return []

    target = float(script.get("target_duration_seconds") or 60.0)
    total_words = sum(_beat_word_count(b) for b in beats)

    windows: List[Dict[str, Any]] = []
    cursor = 0.0
    allocated = 0.0
    for i, beat in enumerate(beats):
        if i == len(beats) - 1:
            dur = target - allocated  # last beat absorbs rounding
        else:
            dur = target * (_beat_word_count(beat) / total_words)
        dur = max(MIN_CLIP_SECONDS, dur)
        windows.append(
            {
                "beat_id": int(beat.get("id", i)),
                "output_start": round(cursor, 3),
                "output_end": round(cursor + dur, 3),
                "duration": round(dur, 3),
            }
        )
        cursor += dur
        allocated += dur
    return windows


def _contiguous_ranges(
    segments: List[Dict[str, Any]], gap_tolerance: float = GAP_TOLERANCE_SECONDS
) -> List[tuple[float, float]]:
    """
    Merge overlapping/adjacent segment ranges into contiguous visual regions.
    NEVER bridge a gap larger than `gap_tolerance`.
    """
    ranges: List[tuple[float, float]] = []
    for seg in sorted(segments, key=lambda s: float(s["start"])):
        start, end = float(seg["start"]), float(seg["end"])
        if end <= start:
            continue
        if ranges and start - ranges[-1][1] <= gap_tolerance:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))
    return ranges


def _collect_visual_budget(
    ranges: List[tuple[float, float]], budget: float
) -> List[tuple[float, float]]:
    """
    Take visual material from ranges until `budget` seconds are filled.
    Returns chosen (source_start, source_end) sub-ranges.
    """
    chosen: List[tuple[float, float]] = []
    remaining = budget
    for start, end in ranges:
        if remaining <= MIN_CLIP_SECONDS:
            break
        avail = end - start
        take = min(avail, remaining)
        if take >= MIN_CLIP_SECONDS:
            chosen.append((start, start + take))
            remaining -= take
    return chosen


def _score_range(
    rng: tuple[float, float],
    used_ranges: List[tuple[float, float]],
    window_center: Optional[float] = None,
) -> float:
    """Score a candidate range: prefer unused material, duration fit, source proximity."""
    start, end = rng
    dur = end - start
    score = dur
    # Penalize material already used by earlier beats (reduce repetition).
    for u_start, u_end in used_ranges:
        overlap = max(0.0, min(end, u_end) - max(start, u_start))
        score -= overlap * 0.8
    return score


def plan_script_timeline(
    script: Dict[str, Any],
    transcript: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build the script-led output timeline.

    Returns timeline dict compatible with pipeline.renderer:
    clips have source_start/source_end/output_start/output_end, narration_text,
    emotion, beat_id. actual_duration == total output duration.
    """
    segments = transcript.get("segments") or []
    index = _build_index(segments)

    beats = [b for b in script.get("beats") or [] if str(b.get("text") or "").strip()]
    if not beats:
        raise ValueError("Script has no usable beats.")

    windows = allocate_beat_windows(script)
    used_ranges: List[tuple[float, float]] = []
    clips: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for win in windows:
        beat = next(
            (b for b in beats if int(b.get("id", -1)) == win["beat_id"]), beats[windows.index(win)]
        )
        budget = win["duration"]

        # Collect visual candidates from the beat's referenced segments only.
        ref_ids = [int(sid) for sid in beat.get("source_segment_ids") or [] if int(sid) in index]
        ref_segments = [index[sid] for sid in ref_ids]
        if not ref_segments:
            warnings.append(f"Beat {win['beat_id']}: không map được segment nguồn nào.")
            continue

        # Tighten first segment boundary to word timestamps when available.
        first_seg = min(ref_segments, key=lambda s: float(s["start"]))
        t_start, _ = _tighten_with_words(first_seg, float(first_seg["start"]), float(first_seg["end"]))
        ref_segments = [{**first_seg, "start": t_start}] + [
            s for s in ref_segments if s is not first_seg
        ]

        ranges = _contiguous_ranges(ref_segments)
        chosen = _collect_visual_budget(ranges, budget)

        filled = sum(e - s for s, e in chosen)
        if filled < budget - 0.5:
            warnings.append(
                f"Beat {win['beat_id']}: hình nguồn chỉ phủ {filled:.1f}s / cần {budget:.1f}s."
            )

        output_cursor = win["output_start"]
        for s_start, s_end in chosen:
            dur = s_end - s_start
            clips.append(
                {
                    "id": f"clip_{len(clips) + 1:03d}",
                    "beat_id": win["beat_id"],
                    "source_start": round(s_start, 3),
                    "source_end": round(s_end, 3),
                    "output_start": round(output_cursor, 3),
                    "output_end": round(output_cursor + dur, 3),
                    "duration": round(dur, 3),
                    "order": len(clips) + 1,
                    "enabled": True,
                    "label": "script_beat",
                    "reason": f"beat {win['beat_id']}: {str(beat.get('text', ''))[:60]}",
                    "narration_text": str(beat.get("text") or "").strip(),
                    "emotion": str(beat.get("emotion") or "neutral").strip(),
                    "score": 1.0,
                    "transition": "cut",
                }
            )
            output_cursor += dur
            used_ranges.append((s_start, s_end))

    actual = sum(c["duration"] for c in clips)
    target = float(script.get("target_duration_seconds") or 60.0)
    tol = float(script.get("duration_tolerance_seconds") or 2.0)

    timeline = {
        "mode": "script_review",
        "name": f"Script review: {script.get('title', '')}",
        "clips": clips,
        "actual_duration": round(actual, 3),
        "target_duration": target,
        "script_target_duration_seconds": target,
        "script_duration_tolerance_seconds": tol,
        "duration_delta_seconds": round(actual - target, 3),
        "duration_status": "ok" if abs(actual - target) <= tol else "out_of_tolerance",
        "visual_coverage": round(actual / max(target, 0.1), 3),
        "warnings": warnings,
        "script_status": script.get("status", "approved"),
        "script_title": script.get("title", ""),
    }
    logger.info(
        "Script-led timeline planned: %d clips, output %.1fs / target %.1fs (%d warnings)",
        len(clips), actual, target, len(warnings),
    )
    return timeline
