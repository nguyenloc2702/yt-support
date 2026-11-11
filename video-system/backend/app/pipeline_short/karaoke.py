"""Karaoke ASS/SRT 1080x1920 từ word timeline (Phụ lục G.5).

Tag {\\k<centisecond>} từng từ; nhóm caption ≤ max_line_words hoặc ≤ 3.2s;
tách caption khi gap giữa từ > 0.45s. Nhấn từ chứa số/%/$ bằng \\b1 (đậm).
Safe margin trên/dưới theo render_spec_short.yaml (chừa UI nền tảng).
"""
from __future__ import annotations

import re

from ..config import RENDER_SPEC_SHORT


def _fmt_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _fmt_srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _is_emphasis(word: str) -> bool:
    return bool(re.search(r"[0-9%$€£]", word))


def _caption_groups(words: list[dict], max_words: int, max_sec: float = 3.2,
                    pause_split: float = 0.45) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            dur = w["end"] - cur[0]["start"]
            if (gap > pause_split or len(cur) >= max_words or dur > max_sec):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_short_ass(words: list[dict], out_path, total_duration: float) -> int:
    spec = RENDER_SPEC_SHORT["subtitle"]
    font_size = int(spec["font_size"])
    max_words = int(spec["max_line_words"])
    highlight = spec["highlight_color"]
    primary = spec["primary_color"]
    outline = spec["outline_color"]
    margin_v = int(int(RENDER_SPEC_SHORT["video"]["height"]) * float(spec["safe_margin_ratio"]))
    width = int(RENDER_SPEC_SHORT["video"]["width"])

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {int(RENDER_SPEC_SHORT['video']['height'])}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Primary = màu đã hát (highlight), Secondary = màu chờ (trắng), viền tối dày cho nền video
        f"Style: Karaoke,IBM Plex Sans,{font_size},{highlight},{primary},{outline},&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,40,40,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    n_cues = 0
    for group in _caption_groups(words, max_words):
        start = group[0]["start"]
        end = max(group[-1]["end"], start + 0.3)
        parts = []
        for w in group:
            cs = max(1, int(round((w["end"] - w["start"]) * 100)))
            text = str(w["text"]).replace("{", "(").replace("}", ")")
            bold = "{\\b1}" if _is_emphasis(text) else ""
            # Space nằm SAU từ, trong segment của từ đó → từ + khoảng cách
            # highlight cùng lúc, các từ không dính vào nhau
            parts.append(f"{bold}{{\\k{cs}}}{text} ")
        lines.append(
            f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},Karaoke,,0,0,0,,{''.join(parts)}"
        )
        n_cues += 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n_cues


def build_short_srt(words: list[dict], out_path, total_duration: float) -> int:
    max_words = int(RENDER_SPEC_SHORT["subtitle"]["max_line_words"])
    lines = []
    n = 0
    for group in _caption_groups(words, max_words):
        start = group[0]["start"]
        end = max(group[-1]["end"], start + 0.3)
        n += 1
        text = " ".join(w["text"] for w in group)
        lines.append(f"{n}\n{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n{text}\n")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return n
