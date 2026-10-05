from typing import List, Dict, Any, Optional
import uuid


def build_timeline(
    candidates: List[Dict[str, Any]],
    mode: str,
    target_duration: float,
    source_duration: float,
    preserve_source_order: bool = True,
    output_aspect_ratio: str = "16:9",
) -> Dict[str, Any]:
    """
    Build a timeline from ranked candidates.
    Supports modes: 'remove_silence', 'shorten', 'summary'.
    Returns a timeline dict.
    """
    if not candidates:
        # Fallback: use whole video
        return {
            "id": f"timeline_{uuid.uuid4().hex[:8]}",
            "project_id": "",
            "version": 1,
            "name": "Full video",
            "mode": mode,
            "target_duration": target_duration,
            "actual_duration": source_duration,
            "output_aspect_ratio": output_aspect_ratio,
            "clips": [{
                "id": "clip_001",
                "source_start": 0.0,
                "source_end": source_duration,
                "order": 1,
                "enabled": True,
                "label": "other",
                "reason": "No candidates available",
                "score": 0.5,
                "transition": "cut"
            }]
        }

    selected = []

    if mode == "remove_silence":
        # Keep all candidates but remove silence gaps; actually we build clips from non-silence regions
        # For simplicity, we use candidates as they are (they already avoid silence)
        selected = candidates.copy()
        # Sort by source_start to preserve order
        selected.sort(key=lambda x: x["source_start"])
    elif mode == "shorten":
        # Select top candidates to meet target duration
        sorted_candidates = sorted(candidates, key=lambda x: x["scores"].get("overall", 0), reverse=True)
        selected = []
        total_duration = 0.0
        for cand in sorted_candidates:
            if total_duration + cand["duration"] <= target_duration:
                selected.append(cand)
                total_duration += cand["duration"]
            else:
                # Try to trim candidate to fit
                remaining = target_duration - total_duration
                if remaining >= 1.0 and cand["duration"] > remaining:
                    # Shorten this candidate
                    trim_cand = cand.copy()
                    trim_cand["source_end"] = trim_cand["source_start"] + remaining
                    trim_cand["duration"] = remaining
                    selected.append(trim_cand)
                    total_duration += remaining
                break
        if not selected and candidates:
            # At least one clip
            selected = [candidates[0]]
        # Sort by source_start to preserve order if requested
        if preserve_source_order:
            selected.sort(key=lambda x: x["source_start"])
    elif mode == "summary":
        # For summary, we need to assign labels: hook, context, main_point, conclusion
        # Use heuristics: first candidate as hook, last as conclusion, others as main_point
        # For simplicity, select top candidates sorted by overall, then reorder by source_start
        sorted_candidates = sorted(candidates, key=lambda x: x["scores"].get("overall", 0), reverse=True)
        # Select up to target duration
        selected = []
        total_duration = 0.0
        for cand in sorted_candidates:
            if total_duration + cand["duration"] <= target_duration:
                selected.append(cand)
                total_duration += cand["duration"]
            else:
                remaining = target_duration - total_duration
                if remaining >= 1.0 and cand["duration"] > remaining:
                    trim_cand = cand.copy()
                    trim_cand["source_end"] = trim_cand["source_start"] + remaining
                    trim_cand["duration"] = remaining
                    selected.append(trim_cand)
                    total_duration += remaining
                break
        if not selected and candidates:
            selected = [candidates[0]]
        # Sort by source_start to preserve order (or we could reorder for summary)
        # For summary, we might reorder to hook->context->main->conclusion, but preserve order for now
        selected.sort(key=lambda x: x["source_start"])
    else:
        # Default: use all candidates sorted by source_start
        selected = sorted(candidates, key=lambda x: x["source_start"])

    # Create clips from selected candidates
    clips = []
    for i, cand in enumerate(selected, start=1):
        clips.append({
            "id": f"clip_{i:03d}",
            "source_start": cand["source_start"],
            "source_end": cand["source_end"],
            "order": i,
            "enabled": True,
            "label": "other",
            "reason": cand.get("reason", ""),
            "score": cand["scores"].get("overall", 0.5),
            "transition": "cut"
        })

    actual_duration = sum(c["source_end"] - c["source_start"] for c in clips)
    timeline = {
        "id": f"timeline_{uuid.uuid4().hex[:8]}",
        "project_id": "",  # will be filled later
        "version": 1,
        "name": f"{mode.capitalize()} timeline",
        "mode": mode,
        "target_duration": target_duration,
        "actual_duration": actual_duration,
        "output_aspect_ratio": output_aspect_ratio,
        "clips": clips,
        "render_options": {
            "resolution": "1080x1920" if output_aspect_ratio == "9:16" else "1920x1080",
            "video_codec": "h264",
            "audio_codec": "aac",
            "burn_subtitles": False,
            "normalize_audio": True
        }
    }
    return timeline