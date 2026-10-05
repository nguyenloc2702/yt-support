from typing import List, Dict, Any, Optional
import math

def build_candidate_segments(
    transcript: List[Dict[str, Any]],
    scenes: List[Dict[str, Any]],
    silences: List[Dict[str, Any]],
    source_duration: float,
    min_duration: float = 3.0,
    preferred_duration: float = 12.0,
    max_duration: float = 30.0,
    boundary_tolerance: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Build candidate segments from transcript, scenes, and silences.
    Returns a list of segments with start, end, text, etc.
    """
    if not transcript:
        # If no transcript, create a single segment covering whole video
        return [{
            "id": "seg_000",
            "source_start": 0.0,
            "source_end": source_duration,
            "duration": source_duration,
            "text": "",
            "scene_ids": [],
            "silence_ratio": 0.0,
            "word_count": 0,
            "scores": {},
            "flags": [],
            "reason": "No transcript"
        }]

    # Merge transcript segments into larger candidates
    candidates = []
    current_start = transcript[0]["start"]
    current_text = ""
    current_end = transcript[0]["end"]
    current_segment_ids = []

    for seg in transcript:
        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_text = seg["text"]

        # If adding this segment exceeds max_duration, close current candidate
        if (seg_end - current_start) > max_duration and current_end - current_start >= min_duration:
            # Close candidate
            candidate = _finalize_candidate(
                start=current_start,
                end=current_end,
                text=current_text.strip(),
                scenes=scenes,
                silences=silences,
                source_duration=source_duration,
            )
            if candidate:
                candidates.append(candidate)
            # Start new candidate
            current_start = seg_start
            current_text = seg_text
            current_end = seg_end
            current_segment_ids = []
        else:
            # Extend current candidate
            if current_text:
                current_text += " " + seg_text
            else:
                current_text = seg_text
            current_end = seg_end

    # Final candidate
    if current_end - current_start >= min_duration:
        candidate = _finalize_candidate(
            start=current_start,
            end=current_end,
            text=current_text.strip(),
            scenes=scenes,
            silences=silences,
            source_duration=source_duration,
        )
        if candidate:
            candidates.append(candidate)

    # Assign IDs
    for i, cand in enumerate(candidates):
        cand["id"] = f"seg_{i:03d}"

    return candidates


def _finalize_candidate(
    start: float,
    end: float,
    text: str,
    scenes: List[Dict[str, Any]],
    silences: List[Dict[str, Any]],
    source_duration: float,
) -> Optional[Dict[str, Any]]:
    """Create a candidate segment with metadata."""
    if end <= start:
        return None
    duration = end - start
    if duration < 0.5:
        return None

    # Find scene IDs that overlap
    scene_ids = []
    for scene in scenes:
        scene_start = scene["start"]
        scene_end = scene["end"]
        if scene_start < end and scene_end > start:
            scene_ids.append(scene.get("scene_id", len(scene_ids)))

    # Calculate silence ratio within this segment
    silence_ratio = 0.0
    total_silence = 0.0
    for sil in silences:
        sil_start = sil["start"]
        sil_end = sil["end"]
        overlap_start = max(start, sil_start)
        overlap_end = min(end, sil_end)
        if overlap_end > overlap_start:
            total_silence += (overlap_end - overlap_start)
    if duration > 0:
        silence_ratio = total_silence / duration

    word_count = len(text.split())

    return {
        "source_start": start,
        "source_end": end,
        "duration": duration,
        "text": text,
        "scene_ids": scene_ids,
        "silence_ratio": silence_ratio,
        "word_count": word_count,
        "scores": {},
        "flags": [],
        "reason": "",
    }