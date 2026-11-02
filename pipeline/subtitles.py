"""
Subtitle generation for the narration track.

Produces:
- plain SRT (per-cue sentence subtitles);
- karaoke ASS from the TTS items (edge-tts WordBoundary sidecar timing,
  scaled by the cue's tempo factor so timing matches the sped-up audio);
- karaoke ASS from narration_words_<lang>.json, the audio-first word timeline
  the documentary assembler's final video is cut against.

Cue timing model: each narration item's audio starts at item["output_start"]
and, when tempo > 1, word times are divided by tempo.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from core.logging_config import get_logger

logger = get_logger(__name__)

_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narr,{font_name},{font_size},&H00FFFFFF,&H0000A5FF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,1,2,1,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _fmt_srt(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_ass(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _load_word_timing(audio_path: Path, tempo: float) -> Optional[List[Dict[str, Any]]]:
    """Load the .words.json sidecar for an mp3, scaled by tempo."""
    sidecar = Path(audio_path).with_suffix(".words.json")
    if not sidecar.is_file():
        return None
    try:
        words = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    t = max(1.0, float(tempo))
    return [
        {
            "start": float(w.get("offset", 0.0)) / t,
            "end": (float(w.get("offset", 0.0)) + float(w.get("duration", 0.0))) / t,
            "text": str(w.get("text", "")),
        }
        for w in words
    ]


def _parse_ass_time(text: str) -> float:
    h, m, rest = text.strip().split(":")
    s, cs = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0


def _parse_srt_time(text: str) -> float:
    h, m, s, ms = re.split(r"[:,]", text.strip())
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def caption_intervals(subtitle_path: Path) -> List[tuple]:
    """(start, end) seconds of every cue in an ASS or SRT file."""
    path = Path(subtitle_path)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    spans: List[tuple] = []
    if path.suffix.lower() == ".ass":
        for line in text.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            fields = line.split(",", 9)
            if len(fields) < 4:
                continue
            try:
                spans.append((_parse_ass_time(fields[1]), _parse_ass_time(fields[2])))
            except (ValueError, IndexError):
                continue
    else:
        for start, end in re.findall(
                r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", text):
            spans.append((_parse_srt_time(start), _parse_srt_time(end)))
    return [(max(0.0, s), max(0.0, e)) for s, e in spans if e > s]


def caption_coverage(subtitle_path: Optional[Path], duration_s: float) -> Dict[str, Any]:
    """Share of the runtime that carries a burned-in caption.

    Measured from the cue file the render actually used — a pixel guess on
    illustrated frames proved unusable (it read 0.03-0.17 on clips that
    demonstrably carry captions).
    """
    spans = caption_intervals(subtitle_path) if subtitle_path else []
    if not spans or duration_s <= 0:
        return {"available": bool(spans), "caption_share": 0.0, "cues": len(spans),
                "source": str(subtitle_path) if subtitle_path else None}
    merged: List[list] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    on_screen = sum(e - s for s, e in merged)
    return {
        "available": True,
        "caption_share": round(min(1.0, on_screen / duration_s), 3),
        "cues": len(spans),
        "source": str(subtitle_path),
    }


def build_srt(items: List[Dict[str, Any]], out_path: Path) -> Path:
    """Plain per-cue SRT. Each item: output_start, raw_duration, text, audio_path."""
    out_path = Path(out_path)
    lines: List[str] = []
    starts = [float(it.get("output_start", 0.0)) for it in items]
    for i, item in enumerate(items, 1):
        start = float(item.get("output_start", 0.0))
        dur = float(item.get("raw_duration", 0.0)) or 2.0
        end = start + dur
        if i < len(starts) and end > starts[i]:
            end = max(start + 0.5, starts[i])
        lines.append(f"{i}\n{_fmt_srt(start)} --> {_fmt_srt(end)}\n{str(item.get('text', '')).strip()}\n")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _wrap_chars(width: int, font_size: int = 40) -> int:
    """Approximate number of chars per line for the subtitle font size."""
    # Verdana at 40px in a 1280px frame fits ~55 Latin chars; scale linearly.
    return max(20, int(width * (40.0 / font_size) * 0.043))


def _wrap_words(parts: List[str], max_chars: int) -> str:
    """Join word parts (which may contain {\\k} tags), breaking long lines.

    Tags are not counted toward line length; the visible word is.
    """
    lines: List[str] = []
    current = ""
    visible = 0
    for part in parts:
        vis_len = len(part.replace("{\\k", "").replace("}", "")) + 1  # +1 space
        if current and visible + vis_len > max_chars:
            lines.append(current)
            current = part
            visible = vis_len
        else:
            current = f"{current} {part}".strip()
            visible += vis_len
    if current:
        lines.append(current)
    return "\\N".join(lines)


def build_karaoke_ass(
    items: List[Dict[str, Any]],
    out_path: Path,
    width: int = 1280,
    height: int = 720,
    font_size: int = 40,
    margin_v: int = 40,
    font_name: str = "Verdana",
    margin_lr: int = 40,
) -> Path:
    """
    Karaoke ASS: one dialogue line per cue; per-word \\k highlight when
    word-boundary sidecars are available, otherwise fall back to plain text.
    """
    out_path = Path(out_path)
    body = [_ASS_HEADER.format(width=width, height=height, font_size=font_size,
                               margin_v=margin_v, font_name=font_name, margin_lr=margin_lr)]
    # Pre-compute starts so each cue is clamped to the next one: TTS cue
    # audio can overlap slightly, but two subtitle lines must never show
    # at the same time.
    starts = [float(it.get("output_start", 0.0)) for it in items]
    for idx, item in enumerate(items):
        start = float(item.get("output_start", 0.0))
        dur = float(item.get("raw_duration", 0.0)) or 2.0
        end = start + dur
        if idx + 1 < len(starts) and end > starts[idx + 1]:
            end = max(start + 0.5, starts[idx + 1])
        tempo = float(item.get("tempo", 1.0))
        text = str(item.get("text", "")).strip()
        words = _load_word_timing(Path(item["audio_path"]), tempo) if item.get("audio_path") else None
        if words:
            # Edge word boundaries may not align perfectly with display text;
            # pair by index when counts match, else fall back to plain text.
            plain_words = text.split()
            if len(words) != len(plain_words):
                seg = text
            else:
                parts = []
                prev_end = 0.0
                for w, raw in zip(words, plain_words):
                    gap = max(0.0, w["start"] - prev_end)
                    dur_cs = max(1, int(round((w["end"] - w["start"]) * 100)))
                    if gap > 0.02:
                        parts.append(f"{{\\k{int(round(gap * 100))}}}")
                    parts.append(f"{{\\k{dur_cs}}}{raw}")
                    prev_end = w["end"]
                seg = _wrap_words(parts, _wrap_chars(width))
            # Fall back path: plain text still needs wrapping.
            if seg == text:
                seg = _wrap_words(text.split(), _wrap_chars(width))
        else:
            seg = _wrap_words(text.split(), _wrap_chars(width))
        body.append(
            f"Dialogue: 0,{_fmt_ass(start)},{_fmt_ass(end)},Narr,,0,0,0,,{seg}\n"
        )
    out_path.write_text("".join(body), encoding="utf-8")
    return out_path


# A caption chunk that runs long reads as a wall of text; the reference clips
# show a few words at a time. These three limits are what split a scene.
MAX_LINE_WORDS = 7
MAX_LINE_SECONDS = 3.2
PAUSE_SPLIT_S = 0.45
# A caption stays up while a short breath follows it: the reader is still
# parsing the line, and blanking the screen for a third of a second is what
# drops coverage below the standard's floor.
HOLD_GAP_S = 0.6

_EMPHASIS_RE = re.compile(r"[\d%$€₫]")
_EMPHASIS_WORDS = {"percent", "billion", "million", "trillion", "dollar", "dollars"}


def _is_emphasis_word(word: str) -> bool:
    """Numbers are the punchline of this genre, so they keep the highlight colour."""
    cleaned = str(word).strip().lower().strip(".,!?;:")
    return bool(_EMPHASIS_RE.search(cleaned)) or cleaned in _EMPHASIS_WORDS


def _caption_lines(
    words: List[Dict[str, Any]],
    max_line_words: int = MAX_LINE_WORDS,
    max_line_seconds: float = MAX_LINE_SECONDS,
    pause_split_s: float = PAUSE_SPLIT_S,
) -> List[List[Dict[str, Any]]]:
    """Split a scene's timed words into short caption chunks at pauses first."""
    lines: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for word in words:
        start, end = float(word["start"]), float(word["end"])
        if current:
            paused = start - float(current[-1]["end"]) >= pause_split_s
            long_enough = (len(current) >= max_line_words
                           or end - float(current[0]["start"]) > max_line_seconds)
            if paused or long_enough:
                lines.append(current)
                current = []
        current.append(word)
    if current:
        lines.append(current)
    return lines


def _karaoke_text(words: List[Dict[str, Any]], max_chars: int = 48) -> str:
    """\\k tags for one caption chunk, timed against the chunk's own start.

    The file uses WrapStyle 2, so libass never re-wraps: a chunk wider than the
    frame would run off both edges. Words are packed into lines that fit
    ``max_chars`` and joined with literal ``\\N`` breaks.
    """
    lines: List[List[str]] = []
    current: List[str] = []
    visible = 0
    prev_end: Optional[float] = None
    for word in words:
        start, end = float(word["start"]), float(word["end"])
        cs = max(1, int(round((end - start) * 100)))
        gap = ""
        if prev_end is not None and start - prev_end > 0.02:
            gap = f"{{\\k{max(1, int(round((start - prev_end) * 100)))}}}"
        text = str(word.get("w", "")).strip()
        if _is_emphasis_word(text):
            part = f"{gap}{{\\k{cs}\\c&H0000A5FF&}}{text}{{\\r}}"
        else:
            part = f"{gap}{{\\k{cs}}}{text}"
        if current and visible + len(text) + 1 > max_chars:
            lines.append(current)
            current = []
            visible = 0
        current.append(part)
        visible += len(text) + 1
        prev_end = end
    if current:
        lines.append(current)
    return "\\N".join(" ".join(line) for line in lines)


def build_ass_from_word_timeline(
    timeline: Dict[str, Any],
    out_path: Path,
    width: int = 1920,
    height: int = 1080,
    font_size: int = 62,
    margin_v: int = 150,
    margin_lr: int = 90,
    font_name: str = "Verdana",
    max_line_words: int = MAX_LINE_WORDS,
    max_line_seconds: float = MAX_LINE_SECONDS,
    exclude_scene_indexes: Optional[Sequence[int]] = None,
) -> Path:
    """Karaoke ASS straight from ``narration_words_<lang>.json``.

    That sidecar already holds absolute per-word times for the full narration
    track, which is exactly the final video's timeline, so no tempo scaling is
    needed here (the older :func:`build_karaoke_ass` still serves the per-cue
    TTS flow). Scenes listed in ``exclude_scene_indexes`` stay caption-free:
    black cards and full-bleed screenshots carry their own text.
    """
    out_path = Path(out_path)
    skip = {int(i) for i in (exclude_scene_indexes or ())}
    line_chars = _wrap_chars(max(320, width - 2 * margin_lr), font_size)
    cues: List[tuple] = []
    for scene in timeline.get("scenes", []):
        if int(scene.get("index", 0)) in skip:
            continue
        words = [w for w in scene.get("words", [])
                 if str(w.get("w", "")).strip() and float(w["end"]) > float(w["start"])]
        for line in _caption_lines(words, max_line_words=max_line_words,
                                   max_line_seconds=max_line_seconds):
            cues.append((float(line[0]["start"]), float(line[-1]["end"]),
                         _karaoke_text(line, line_chars)))
    cues.sort(key=lambda c: (c[0], c[1]))

    body = [_ASS_HEADER.format(width=width, height=height, font_size=font_size,
                               margin_v=margin_v, margin_lr=margin_lr, font_name=font_name)]
    for i, (start, end, text) in enumerate(cues):
        if i + 1 < len(cues):
            next_start = cues[i + 1][0]
            if end > next_start:
                end = max(start + 0.2, next_start)
            elif next_start - end <= HOLD_GAP_S:
                end = next_start
        body.append(f"Dialogue: 0,{_fmt_ass(start)},{_fmt_ass(end)},Narr,,0,0,0,,{text}\n")
    out_path.write_text("".join(body), encoding="utf-8")
    logger.info("Karaoke ASS written: %d cues -> %s", len(cues), out_path.name)
    return out_path
