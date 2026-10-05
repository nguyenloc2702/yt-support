"""
Beat mapper: convert script beats (text + source_segment_ids, NO timestamps)
into timeline clips using real transcript timestamps.

For each beat:
- start = start of the earliest referenced segment
- end   = end of the latest referenced segment
- if word-level timestamps are available, tighten boundaries to the first/
  last word actually covered by the referenced segment ids.
Clips may be merged when adjacent beats reference overlapping ranges.
"""

from typing import Any, Dict, List

from core.logging_config import get_logger

logger = get_logger(__name__)

MIN_CLIP_SECONDS = 0.5
GAP_MERGE_SECONDS = 0.3


def _build_index(segments: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    return {int(s["id"]): s for s in segments if s.get("id") is not None}


def _tighten_with_words(segment: Dict[str, Any], start: float, end: float) -> tuple[float, float]:
    """Narrow [start, end] using word timestamps if present."""
    words = segment.get("words") or []
    if not words:
        return start, end
    first = float(words[0]["start"])
    last = float(words[-1]["end"])
    if last > first:
        return max(start, first), min(end, max(last, first + MIN_CLIP_SECONDS))
    return start, end


def beats_to_clips(
    script: Dict[str, Any],
    transcript: Dict[str, Any],
    handle_leading_silence: bool = True,
) -> List[Dict[str, Any]]:
    """
    Map script beats to timeline clips.

    Returns a list of clip dicts compatible with pipeline.renderer:
        [{"source_start": float, "source_end": float, "beat_id": int, "text": str}]
    """
    segments = transcript.get("segments") or []
    index = _build_index(segments)
    clips: List[Dict[str, Any]] = []

    for beat in script.get("beats") or []:
        ref_ids = [int(sid) for sid in beat.get("source_segment_ids") or [] if int(sid) in index]
        if not ref_ids:
            logger.warning("Beat %s không map được segment nào, bỏ qua.", beat.get("id"))
            continue

        ref_segments = [index[sid] for sid in ref_ids]
        start = min(float(s["start"]) for s in ref_segments)
        end = max(float(s["end"]) for s in ref_segments)

        if handle_leading_silence:
            first_seg = min(ref_segments, key=lambda s: float(s["start"]))
            start, _ = _tighten_with_words(first_seg, start, end)

        if end - start < MIN_CLIP_SECONDS:
            end = start + MIN_CLIP_SECONDS

        clips.append(
            {
                "source_start": round(start, 3),
                "source_end": round(end, 3),
                "beat_id": beat.get("id"),
                "text": beat.get("text", ""),
            }
        )

    return _merge_adjacent(clips)


def _merge_adjacent(clips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge clips that overlap or are nearly adjacent (gap <= threshold)."""
    if not clips:
        return []
    clips = sorted(clips, key=lambda c: c["source_start"])
    merged = [dict(clips[0])]
    for clip in clips[1:]:
        prev = merged[-1]
        if clip["source_start"] <= prev["source_end"] + GAP_MERGE_SECONDS:
            prev["source_end"] = max(prev["source_end"], clip["source_end"])
            prev["text"] = f"{prev['text']} {clip['text']}".strip()
            prev["beat_id"] = [prev.get("beat_id"), clip.get("beat_id")]
        else:
            merged.append(dict(clip))
    return merged


def estimate_script_duration(script: Dict[str, Any], transcript: Dict[str, Any]) -> float:
    """Total duration (seconds) the mapped clips would produce."""
    return sum(
        c["source_end"] - c["source_start"]
        for c in beats_to_clips(script, transcript)
    )
