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
        # Stratified temporal window sampling:
        # Ensures coverage across the entire video arc (Intro -> Rising action -> Climax -> Ending).
        if not source_duration or source_duration <= 0:
            source_duration = max((c["source_end"] for c in candidates), default=target_duration)

        # Divide source video into balanced temporal windows
        num_windows = max(4, min(16, int(target_duration / 40.0)))
        win_size = source_duration / num_windows
        budget_per_win = target_duration / num_windows

        def _cand_key(c: Dict[str, Any]) -> Any:
            return c.get("id") or (round(float(c.get("source_start", 0.0)), 3), round(float(c.get("source_end", 0.0)), 3))

        selected = []
        total_duration = 0.0
        used_ids = set()

        # Step 1: Pick top highlight candidate(s) from each temporal window
        for w in range(num_windows):
            w_start = w * win_size
            w_end = (w + 1) * win_size

            win_cands = [
                c for c in candidates
                if _cand_key(c) not in used_ids and not (c["source_end"] <= w_start or c["source_start"] >= w_end)
            ]
            if not win_cands:
                continue

            win_cands.sort(key=lambda x: x["scores"].get("overall", 0), reverse=True)

            win_used = 0.0
            for cand in win_cands:
                if total_duration >= target_duration:
                    break

                rem_win_budget = budget_per_win - win_used
                rem_total_budget = target_duration - total_duration
                max_allowed = min(rem_win_budget, rem_total_budget)

                if max_allowed < 3.0:
                    break

                cand_dur = cand["duration"]
                if cand_dur <= max_allowed:
                    selected.append(cand)
                    used_ids.add(_cand_key(cand))
                    win_used += cand_dur
                    total_duration += cand_dur
                else:
                    trim_cand = cand.copy()
                    trim_cand["source_end"] = trim_cand["source_start"] + max_allowed
                    trim_cand["duration"] = max_allowed
                    selected.append(trim_cand)
                    used_ids.add(_cand_key(cand))
                    win_used += max_allowed
                    total_duration += max_allowed
                    break

        # Step 2: Fill remaining budget with highest overall candidates if any window had spare budget
        if total_duration < target_duration - 1.0:
            remaining_cands = sorted(
                [c for c in candidates if _cand_key(c) not in used_ids],
                key=lambda x: x["scores"].get("overall", 0),
                reverse=True,
            )
            for cand in remaining_cands:
                rem = target_duration - total_duration
                if rem < 1.0:
                    break
                cand_dur = cand["duration"]
                if cand_dur <= rem:
                    selected.append(cand)
                    used_ids.add(_cand_key(cand))
                    total_duration += cand_dur
                elif rem >= 3.0:
                    trim_cand = cand.copy()
                    trim_cand["source_end"] = trim_cand["source_start"] + rem
                    trim_cand["duration"] = rem
                    selected.append(trim_cand)
                    used_ids.add(_cand_key(cand))
                    total_duration += rem
                    break

        if not selected and candidates:
            selected = [candidates[0]]

        selected.sort(key=lambda x: x["source_start"])

        # Step 3: Assign story-arc labels based on temporal progress
        for idx, cand in enumerate(selected):
            progress = (cand["source_start"] + cand["source_end"]) / (2 * max(source_duration, 1.0))
            if idx == 0 or progress < 0.10:
                cand["label"] = "hook"
            elif idx == len(selected) - 1 or progress >= 0.85:
                cand["label"] = "conclusion"
            elif progress < 0.40:
                cand["label"] = "context"
            else:
                cand["label"] = "main_point"
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
            "label": cand.get("label", "other"),
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