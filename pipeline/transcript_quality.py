"""Transcript quality scoring and hallucination filtering (Phase 1).

Whisper can produce hallucinated segments (repeated tokens, garbled text,
non-speech noise transcribed as words). This module computes per-segment and
global quality metrics so downstream ranking can down-weight or drop bad
segments instead of silently building a broken summary.
"""

import re
from typing import Any, Dict, List, Tuple

# Segments matching these patterns are very likely Whisper hallucinations
# (they are common Whisper artifacts on silence/noise, even with VAD on).
_HALLUCINATION_PATTERNS = [
    re.compile(r"^(.)\1{2,}$"),  # single char repeated: "aaa"
    re.compile(r"^(\b\w+\b)(\s+\1){3,}$", re.IGNORECASE),  # same word x4+: "của của của của"
    re.compile(r"^(thank you[\s.,!]*|thanks for watching[\s.!]*)+$", re.IGNORECASE),
    re.compile(r"^\[?(âm nhạc|nhạc|music|nước ngoài|người nước ngoài|cảm ơn)\]?$", re.IGNORECASE),
    re.compile(r"^[\W_]+$"),  # punctuation only
    re.compile(r"^(am减退|amv|hey|bye|bye-bye)[\s.!]*$", re.IGNORECASE),
]

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _clean_text(text: str) -> str:
    return (text or "").strip()


def _repetition_ratio(text: str) -> float:
    """Ratio of the most repeated word over total words (0..1)."""
    words = _WORD_RE.findall(text.lower())
    if not words:
        return 0.0
    counts: Dict[str, int] = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    return max(counts.values()) / len(words)


def score_segment(seg: Dict[str, Any]) -> Dict[str, Any]:
    """Attach quality metadata to one transcript segment (mutates and returns it)."""
    text = _clean_text(seg.get("text", ""))
    duration = float(seg.get("end", 0.0)) - float(seg.get("start", 0.0))
    words = _WORD_RE.findall(text)

    is_hallucination = any(p.match(text) for p in _HALLUCINATION_PATTERNS) if text else True
    rep_ratio = _repetition_ratio(text)
    # Word rate far from natural speech (too few or nonsense bursts) is suspect.
    wps = len(words) / duration if duration > 0 else 0.0
    rate_suspect = duration >= 2.0 and (wps < 0.5 or wps > 8.0)

    quality = 1.0
    if is_hallucination:
        quality = 0.0
    else:
        if rep_ratio > 0.5:
            quality -= 0.4 * min((rep_ratio - 0.5) / 0.5, 1.0)
        if rate_suspect:
            quality -= 0.2
        if words and not text[-1] in ".!?…":
            quality -= 0.05  # mid-sentence cut
        quality = max(0.0, min(1.0, quality))

    seg["quality"] = {
        "score": round(quality, 3),
        "repetition_ratio": round(rep_ratio, 3),
        "words_per_second": round(wps, 3),
        "is_hallucination": is_hallucination,
    }
    return seg


def filter_transcript(
    segments: List[Dict[str, Any]],
    min_quality: float = 0.3,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Score all segments, drop hallucinated ones, return (kept, meta).

    meta contains global stats for the analysis manifest / UI warnings.
    """
    scored = [score_segment(dict(s)) for s in segments]
    kept = [s for s in scored if s["quality"]["score"] >= min_quality]
    dropped = len(scored) - len(kept)

    total_words = sum(len(_WORD_RE.findall(s.get("text", ""))) for s in kept)
    total_duration = sum(
        float(s.get("end", 0)) - float(s.get("start", 0)) for s in kept
    )
    avg_quality = (
        sum(s["quality"]["score"] for s in kept) / len(kept) if kept else 0.0
    )

    # Global quality label used by the UI.
    if not kept or total_words < 20:
        label = "poor"
    elif avg_quality >= 0.8 and dropped <= max(1, len(scored) // 10):
        label = "good"
    else:
        label = "warning"

    meta = {
        "total_segments": len(scored),
        "dropped_segments": dropped,
        "kept_segments": len(kept),
        "average_quality": round(avg_quality, 3),
        "total_words": total_words,
        "speech_seconds": round(total_duration, 2),
        "label": label,
        "min_quality_threshold": min_quality,
    }
    return kept, meta
