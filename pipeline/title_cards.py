"""Intentional black title cards (the "Crayon standard" P2).

The 17-clip reference corpus carries 1.0-1.8 black typography cards per
minute — full-black frames with one short line of text, dropped on the
scenes that close an idea or land a big number. These are NOT placeholder
cards: the zero-fake policy (services/image_kenburns_provider.py) still
fails a shot whose generation died; a card here replaces a SUCCESSFUL shot's
picture for its own narration span, with the narration playing underneath.

Cards never change the film's duration: a card clip is rendered for the
scene's existing window, so the master narration track stays aligned.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from core.logging_config import get_logger

logger = get_logger(__name__)

# Corpus law: 1.4 cards per minute of finished film (p10-p90 1.0-1.8).
CARDS_PER_MINUTE = 1.4
# Never dress more than this share of scenes as cards: the film still needs
# pictures, and a card-heavy cut reads as a slideshow. At the corpus pacing
# (~14 scenes/min) the 1.4 cards/min rate lands near 10%, so this cap only
# bites on unusually long scenes.
MAX_CARD_SHARE = 0.15
# The hook (first scenes) must show the subject, not a black frame.
HOOK_PROTECTED_SCENES = 2
CARD_FADE_SEC = 0.15
CARD_FPS = 24
CARD_WIDTH = 1920
CARD_HEIGHT = 1080
# A card carries one short line: the scene's key sentence, capped so the
# drawtext block stays legible at 1080p.
CARD_TEXT_MAX_CHARS = 90

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)

# A "big number" scene: percents, money, years, multiples — the beats the
# reference films put on a card.
_BIG_NUMBER_RE = re.compile(r"(\d+[\d.,]*\s?%|\d{4}|\d[\d.,]*\s?(triệu|tỷ|nghìn|lion|usd|đ)|gấp\s?\d+)", re.IGNORECASE)


def find_font() -> Optional[str]:
    for cand in _FONT_CANDIDATES:
        if Path(cand).is_file():
            return cand
    return None


def card_text(scene_text: str) -> str:
    """The one line a card may carry: the scene's key sentence, trimmed."""
    text = " ".join(str(scene_text or "").split())
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    if sentences:
        # Prefer the shortest sentence that still closes an idea: cards carry
        # a punch, not a paragraph.
        shortest = min(sentences, key=len)
        if len(shortest) >= 12 or len(sentences) == 1:
            text = shortest
    if len(text) > CARD_TEXT_MAX_CHARS:
        text = text[: CARD_TEXT_MAX_CHARS - 1].rstrip(" ,;:") + "…"
    return text


def _card_score(scene: Dict[str, Any]) -> int:
    """Higher = better card candidate (chốt ý / số lớn).

    `is_key_scene` is deliberately NOT a signal: nearly every scene carries it,
    so it would make every scene a candidate. A card goes to a punchline, a
    big number, a short dramatic line or an on-screen question.
    """
    text = str(scene.get("text", ""))
    score = 0
    if _BIG_NUMBER_RE.search(text):
        score += 3
    if scene.get("is_punchline") or scene.get("punchline"):
        score += 3
    words = len(text.split())
    if 0 < words <= 8:
        # Short dramatic line: the beat a narrator pauses after.
        score += 2
    if text.strip().endswith("?"):
        score += 1
    return score


def plan_cards(
    scenes: Sequence[Dict[str, Any]],
    shot_durations: Optional[Sequence[float]] = None,
    cards_per_minute: float = CARDS_PER_MINUTE,
    max_share: float = MAX_CARD_SHARE,
) -> List[int]:
    """Indexes (0-based, into `scenes`) whose picture becomes a black card.

    The count follows the corpus rate (1.4/min), never exceeds `max_share` of
    the scenes, and never touches the hook. Candidates rank by the card score
    above; ties keep script order.
    """
    total = len(scenes)
    if not total:
        return []
    total_sec = (
        sum(float(d or 0.0) for d in shot_durations)
        if shot_durations
        else sum(float(sc.get("duration", 0.0) or 0.0) for sc in scenes)
    )
    if total_sec <= 0:
        return []
    target = int(round(total_sec / 60.0 * cards_per_minute))
    target = max(1, min(target, int(total * max_share)))
    first_free = min(HOOK_PROTECTED_SCENES, total)
    candidates = [
        (i, _card_score(sc))
        for i, sc in enumerate(scenes)
        if i >= first_free and float(sc.get("duration", 0.0) or 0.0) >= 1.0
    ]
    candidates = [(i, s) for i, s in candidates if s > 0]
    if not candidates:
        return []
    candidates.sort(key=lambda t: (-t[1], t[0]))
    chosen = sorted(i for i, _ in candidates[:target])
    return chosen


def _escape_filter_path(path: str) -> str:
    """Make a Windows/POSIX path usable inside an ffmpeg filtergraph.

    ffmpeg needs two levels here: the drive colon escaped (``C\\:``) and the
    whole value single-quoted, or the option parser splits at ``C:``.
    """
    return "'" + path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'") + "'"


def build_card(
    text: str,
    duration: float,
    out_path: Path,
    fps: int = CARD_FPS,
    width: int = CARD_WIDTH,
    height: int = CARD_HEIGHT,
) -> Path:
    """Render one black card with centered white text (ffmpeg drawtext).

    The clip carries no audio: the assembler overlays the master narration
    track, whose timeline the card's span already belongs to. The line goes to
    a UTF-8 textfile because drawtext over Windows argv mangles Vietnamese.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.8, float(duration))
    fontfile = find_font()
    textfile = out_path.with_suffix(".txt")
    textfile.write_text(card_text(text), encoding="utf-8")
    fontsize = 62 if len(card_text(text)) <= 60 else 52
    drawtext = (
        f"drawtext=textfile={_escape_filter_path(str(textfile))}"
        f":fontcolor=white:fontsize={fontsize}"
        ":x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=14"
    )
    if fontfile:
        drawtext += f":fontfile={_escape_filter_path(fontfile)}"
    fade_out_start = max(0.0, duration - CARD_FADE_SEC)
    filterchain = (
        f"{drawtext},"
        f"fade=t=in:st=0:d={CARD_FADE_SEC},"
        f"fade=t=out:st={fade_out_start:.3f}:d={CARD_FADE_SEC},"
        "format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration:.3f}",
        "-vf", filterchain,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 1000:
        raise RuntimeError(f"title card render failed: {(res.stderr or '')[-300:]}")
    return out_path


def write_cards_sidecar(video_path: Path, cards: Sequence[Dict[str, Any]]) -> Path:
    """Record which spans are intentional cards for the standard report."""
    sidecar = Path(video_path).with_suffix(".cards.json")
    sidecar.write_text(
        json.dumps({"cards": list(cards)}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return sidecar
