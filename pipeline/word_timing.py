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
BAND_MIN_DUR = 4.0
BAND_MAX_DUR = 8.0
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
) -> Dict[str, Any]:
    """
    Build contiguous scene windows over the narration audio.

    Args:
        scene_slices: token index range (start, end) owned by each scene.
        tokens: script tokens in reading order (aligned 1:1 with spans).
        spans: aligned (start, end) seconds per token; missing spans fall back
            to the surrounding silence.
        audio_duration: total length of the narration track in seconds.

    Returns:
        {"scenes": [...], "total": float, "cut_policy": "mid_pause",
         "warnings": [...]}
    """
    count = len(scene_slices)
    if count == 0:
        return {"scenes": [], "total": 0.0, "cut_policy": "mid_pause", "warnings": []}

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

    return {
        "scenes": windows,
        "total": round(bounds[-1], 3),
        "cut_policy": "mid_pause",
        "warnings": summarize_windows(windows),
    }


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
