"""Word-level narration timing: scene windows cut inside the pauses.

Given a per-token alignment (script token -> (start, end) in the final
narration audio) and the token slice each scene owns, build contiguous scene
windows:

- a boundary between two scenes lands in the middle of the silence between
  them, so the picture changes while nobody is speaking;
- when two scenes share no pause, the boundary sits on the next scene's first
  word onset — never after it;
- the windows tile the whole audio track, so the concat timeline cannot drift
  against the narration.

Every scene carries its words in absolute and scene-relative time. The relative
values are what animation keys, SFX hits and emphasis hang off.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# Gate 4 hard-fails on these in algorithm mode; a window outside them is a
# script problem the owner must see at Gate 2, before producing shots.
MIN_PHYSICAL_DUR = 3.0
MAX_PHYSICAL_DUR = 10.5
# Advisory only, and deliberately the same window the scorecard grades
# (services/algorithm_qa.py SCENE_DUR_RANGE), which itself comes from the
# reference corpus: median 4.13s, p10-p90 3.10-5.49s.
BAND_MIN_DUR = 3.5
BAND_MAX_DUR = 6.0
EPS = 1e-6

Span = Tuple[float, float]


def _speech_spans(
    scene_slices: Sequence[Tuple[int, int]],
    spans: Sequence[Optional[Span]],
) -> List[Optional[Span]]:
    """First/last spoken instant per scene; None when no word carries timing."""
    speech: List[Optional[Span]] = []
    for start_idx, end_idx in scene_slices:
        valid = [
            (float(s), float(e))
            for s, e in (span for span in spans[start_idx:end_idx] if span)
            if e - s > EPS
        ]
        if valid:
            speech.append((min(s for s, _ in valid), max(e for _, e in valid)))
        else:
            speech.append(None)
    return speech


def _fill_missing_speech(
    speech: List[Optional[Span]], total: float
) -> List[Span]:
    """Scenes without timed words own the silence between their neighbours."""
    filled: List[Span] = []
    count = len(speech)
    idx = 0
    while idx < count:
        current = speech[idx]
        if current is not None:
            filled.append(current)
            idx += 1
            continue
        end = idx
        while end < count and speech[end] is None:
            end += 1
        left = speech[idx - 1][1] if idx > 0 else 0.0
        right = speech[end][0] if end < count else max(total, left)
        run = end - idx
        step = (right - left) / run if right > left else 0.0
        for offset in range(run):
            filled.append((left + step * offset, left + step * (offset + 1)))
        idx = end
    return filled


def build_scene_windows(
    scene_slices: Sequence[Tuple[int, int]],
    tokens: Sequence[str],
    spans: Sequence[Optional[Span]],
    audio_duration: float,
    cut_policy: str = "mid_pause",
) -> Dict[str, Any]:
    """
    Build contiguous scene windows over the narration audio.

    Args:
        scene_slices: token index range (start, end) owned by each scene.
        tokens: script tokens in reading order (aligned 1:1 with spans).
        spans: aligned (start, end) seconds per token; missing spans fall back
            to the surrounding silence.
        audio_duration: total length of the narration track in seconds.
        cut_policy: "mid_pause" cuts only at the pause between two scenes;
            "mid_clause" additionally splits any scene whose window runs past
            the 6.0s band top at its own loudest in-sentence breathing points
            (comma-preferred), so one long sentence can own more than one
            picture. Parts keep the parent scene's index, so plan-stage
            consumers can merge them back 1:1.

    Returns:
        {"scenes": [...], "total": float, "cut_policy": cut_policy,
         "warnings": [...]}
    """
    count = len(scene_slices)
    if count == 0:
        return {"scenes": [], "total": 0.0, "cut_policy": cut_policy, "warnings": []}

    padded_spans: List[Optional[Span]] = list(spans)
    if len(padded_spans) < len(tokens):
        padded_spans.extend([None] * (len(tokens) - len(padded_spans)))
    raw_speech = _speech_spans(scene_slices, padded_spans)

    last_spoken = max((span[1] for span in raw_speech if span), default=0.0)
    total = max(float(audio_duration or 0.0), last_spoken)
    if total <= 0:
        total = 0.0
    speech = _fill_missing_speech(raw_speech, total)

    bounds: List[float] = [0.0]
    for idx in range(1, count):
        prev_end = speech[idx - 1][1]
        next_start = speech[idx][0]
        gap = next_start - prev_end
        cut = prev_end + gap / 2.0 if gap > EPS else next_start
        cut = min(max(cut, bounds[-1]), total)
        bounds.append(cut)
    bounds.append(max(total, bounds[-1]))

    windows: List[Dict[str, Any]] = []
    for idx, (start_idx, end_idx) in enumerate(scene_slices):
        start = round(bounds[idx], 3)
        end = round(bounds[idx + 1], 3)
        words: List[Dict[str, Any]] = []
        for token_idx in range(start_idx, end_idx):
            span = padded_spans[token_idx] if token_idx < len(padded_spans) else None
            if not span:
                continue
            word_start, word_end = float(span[0]), float(span[1])
            if word_end - word_start <= EPS:
                continue
            words.append(
                {
                    "w": str(tokens[token_idx]) if token_idx < len(tokens) else "",
                    "start": round(word_start, 3),
                    "end": round(word_end, 3),
                    "rel": round(max(0.0, word_start - start), 3),
                }
            )
        scene_speech = speech[idx]
        windows.append(
            {
                "index": idx + 1,
                "start": start,
                "end": end,
                "duration": round(max(0.0, end - start), 3),
                "speech_start": round(scene_speech[0], 3),
                "speech_end": round(scene_speech[1], 3),
                "words": words,
            }
        )

    if cut_policy == "mid_clause":
        windows = split_overband_windows(windows)

    return {
        "scenes": windows,
        "total": round(bounds[-1], 3),
        "cut_policy": cut_policy,
        "warnings": summarize_windows(windows),
    }


# Clause separators a narrator breathes after: cuts go here before anywhere else.
_CLAUSE_PUNCT = (",", ";", ":", "—", "–", "!", "?")


def _clause_cut_points(
    words: Sequence[Dict[str, Any]], lo: float, hi: float, min_dur: float,
    max_dur: float,
) -> List[float]:
    """Legal cut midpoints inside [lo, hi], best first.

    A cut must leave both sides at least `min_dur` seconds and sit in the gap
    between two words. A gap that follows a clause separator — the narrator's
    own breathing point — always wins. Without one, the cut goes to the gap
    closest to the band top so the first piece fills its allowance and the
    remainder stays as long as the law allows.
    """
    ideal = max(min(hi - min_dur, lo + max_dur), lo + min_dur)
    candidates: List[Tuple[int, float, float]] = []
    for a, b in zip(words, words[1:]):
        a_end, b_start = float(a["end"]), float(b["start"])
        mid = (a_end + b_start) / 2.0
        if not (lo + min_dur - EPS <= mid <= hi - min_dur + EPS):
            continue
        after_punct = str(a.get("w", "")).strip().endswith(_CLAUSE_PUNCT)
        gap = b_start - a_end
        # Sort key: comma breaths first (widest silence among them), then the
        # cut closest to the band top.
        rank = (-(gap), mid) if after_punct else (abs(mid - ideal), mid)
        candidates.append((0 if after_punct else 1, rank[0], rank[1]))
    candidates.sort()
    return [c[2] for c in candidates]


def _split_one_window(
    window: Dict[str, Any], max_dur: float, min_dur: float
) -> List[Dict[str, Any]]:
    """Split one over-band window at its best clause pauses.

    The pieces tile the original span exactly (no audio moves), each piece is
    at least `min_dur` long, and every piece keeps the parent's scene index so
    plan-stage code can merge them back 1:1. A window with no legal cut point
    (no breathing room inside) is returned untouched.
    """
    duration = float(window.get("duration") or 0.0)
    if duration <= max_dur + EPS:
        return [window]
    words = [
        w for w in (window.get("words") or [])
        if float(w.get("end", 0.0)) - float(w.get("start", 0.0)) > EPS
    ]
    if len(words) < 2:
        return [window]
    start = float(window["start"])
    end = float(window["end"])

    # Stack of (words, span) still too long to ship as one picture.
    stack: List[Tuple[List[Dict[str, Any]], float, float]] = [(words, start, end)]
    parts: List[Tuple[List[Dict[str, Any]], float, float]] = []
    while stack:
        group, lo, hi = stack.pop()
        if hi - lo <= max_dur + EPS:
            parts.append((group, lo, hi))
            continue
        cuts = _clause_cut_points(group, lo, hi, min_dur, max_dur)
        if not cuts:
            parts.append((group, lo, hi))
            continue
        mid = cuts[0]
        left = [w for w in group if float(w["end"]) <= mid + EPS]
        right = [w for w in group if float(w["start"]) >= mid - EPS]
        stack.append((right, mid, hi))
        stack.append((left, lo, mid))
    parts.sort(key=lambda item: item[1])

    total_parts = len(parts)
    if total_parts < 2:
        return [window]
    split: List[Dict[str, Any]] = []
    for part_no, (group, lo, hi) in enumerate(parts, start=1):
        spoken = [w for w in group if lo - EPS <= float(w["start"]) < hi - EPS]
        split.append(
            {
                "index": window.get("index"),
                "part": part_no,
                "of": total_parts,
                "start": round(lo, 3),
                "end": round(hi, 3),
                "duration": round(max(0.0, hi - lo), 3),
                "speech_start": round(float(spoken[0]["start"]), 3) if spoken else float(window.get("speech_start", lo)),
                "speech_end": round(float(spoken[-1]["end"]), 3) if spoken else float(window.get("speech_end", hi)),
                "words": [
                    {
                        **w,
                        "rel": round(max(0.0, float(w["start"]) - lo), 3),
                    }
                    for w in group
                ],
            }
        )
    return split


def split_overband_windows(
    windows: Sequence[Dict[str, Any]],
    max_dur: float = BAND_MAX_DUR,
    min_dur: float = MIN_PHYSICAL_DUR,
) -> List[Dict[str, Any]]:
    """Apply the mid_clause split to every window past the band top."""
    result: List[Dict[str, Any]] = []
    for window in windows:
        result.extend(_split_one_window(window, max_dur, min_dur))
    return result


def _split_overband_windows(
    windows: List[Dict[str, Any]],
    max_dur: float = BAND_MAX_DUR,
    min_dur: float = MIN_PHYSICAL_DUR,
) -> List[Dict[str, Any]]:
    """Backwards-compatible alias for :func:`split_overband_windows`."""
    return split_overband_windows(windows, max_dur, min_dur)


def summarize_windows(windows: Sequence[Dict[str, Any]]) -> List[str]:
    """Warnings a Gate 2 reviewer needs: which scenes break the shot laws."""
    too_short: List[str] = []
    too_long: List[str] = []
    off_band: List[str] = []
    for win in windows:
        duration = float(win.get("duration") or 0.0)
        label = f"{duration:.2f}s"
        if duration < MIN_PHYSICAL_DUR - EPS:
            too_short.append(f"Cảnh {win.get('index')} ({label})")
        elif duration > MAX_PHYSICAL_DUR + EPS:
            too_long.append(f"Cảnh {win.get('index')} ({label})")
        elif not (BAND_MIN_DUR - EPS <= duration <= BAND_MAX_DUR + EPS):
            off_band.append(f"Cảnh {win.get('index')} ({label})")

    warnings: List[str] = []
    if too_short:
        warnings.append(
            f"{len(too_short)} cảnh dưới ngưỡng vật lý {MIN_PHYSICAL_DUR:.0f}s "
            f"của Gate 4: {', '.join(too_short)}"
        )
    if too_long:
        warnings.append(
            f"{len(too_long)} cảnh trên ngưỡng vật lý {MAX_PHYSICAL_DUR:.1f}s "
            f"của Gate 4: {', '.join(too_long)}"
        )
    if off_band:
        warnings.append(
            f"{len(off_band)} cảnh ngoài band {BAND_MIN_DUR:.0f}-{BAND_MAX_DUR:.0f}s "
            f"(cắt theo audio thật): {', '.join(off_band)}"
        )
    return warnings
