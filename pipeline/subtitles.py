"""
Subtitle generation from TTS narration items.

Produces:
- plain SRT (per-cue sentence subtitles);
- karaoke ASS (per-word highlight using edge-tts WordBoundary sidecar timing,
  scaled by the cue's tempo factor so timing matches the sped-up audio).

Cue timing model: each narration item's audio starts at item["output_start"]
and, when tempo > 1, word times are divided by tempo.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_config import get_logger

logger = get_logger(__name__)

_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narr,Verdana,{font_size},&H00FFFFFF,&H0000A5FF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,1,2,1,2,40,40,{margin_v},1

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


def _wrap_chars(width: int) -> int:
    """Approximate number of chars per line for the subtitle font size."""
    # Verdana at 40px in a 1280px frame fits ~55 Latin chars; scale linearly.
    return max(20, int(width * 0.043))


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
) -> Path:
    """
    Karaoke ASS: one dialogue line per cue; per-word \\k highlight when
    word-boundary sidecars are available, otherwise fall back to plain text.
    """
    out_path = Path(out_path)
    body = [_ASS_HEADER.format(width=width, height=height, font_size=font_size, margin_v=margin_v)]
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
