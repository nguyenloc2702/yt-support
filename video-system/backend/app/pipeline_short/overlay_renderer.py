"""Pillow RGBA Overlay Engine cho Short Studio (TikTok/Reels/Shorts 1080x1920).

Hỗ trợ 2 preset chuẩn retention:
- `viral_redpill` (mặc định VI): Lower-third dark navy gradient hook card 0–2.6s,
  karaoke 2 dòng ALL-CAPS bold-italic trong safe-zone (x <= 932, y ~ 1290..1430),
  Red Pill #E50914 cho từ đang đọc & Gold Pill #FFB800 cho số tiền/tỷ lệ,
  source_note pill góc trên trái.
- `split_news_60` (mặc định EN): Split-screen 50/50 hook (headline Cyan/Trắng +
  badge giữa IN 30 SECONDS + badge tròn kênh), floating callout pill, karaoke gọn
  trong safe-zone + source_note pill.
"""
from __future__ import annotations

import logging
import math
import re
import struct
import subprocess
import wave
from pathlib import Path

logger = logging.getLogger("video_system.pipeline_short.overlay_renderer")

W, H, FPS = 1080, 1920, 30
_EMPHASIS_RE = re.compile(r"(\d+[.,]?\d*%?|\$\d[\d,.]*|tỷ|triệu|nghìn|billion|million)", re.IGNORECASE)


def _find_font(candidates: list[str]) -> str | None:
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def _fonts_available() -> tuple[str, str, str] | None:
    bold = _find_font([
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ])
    bi = _find_font([
        "C:/Windows/Fonts/arialbi.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    ])
    ui = _find_font([
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ])
    if not (bold and bi and ui):
        return None
    return bold, bi, ui


def is_available() -> bool:
    try:
        import PIL  # noqa: F401
        return _fonts_available() is not None
    except ImportError:
        return False


def is_numeric_word(text: str) -> bool:
    return bool(_EMPHASIS_RE.search(str(text or "")))


def chunk_words(words: list[dict], max_words: int = 5) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_cue = None
    for w in words:
        c = w.get("cue_idx")
        if cur and (len(cur) >= max_words or (c is not None and c != cur_cue)):
            chunks.append(cur)
            cur = []
        cur.append(w)
        cur_cue = c
    if cur:
        chunks.append(cur)
    return chunks


def _draw_stroked_text(draw, xy: tuple[float, float], text: str, font, fill, stroke_fill, stroke_w: int = 5) -> None:
    x, y = xy
    for dx in range(-stroke_w, stroke_w + 1):
        for dy in range(-stroke_w, stroke_w + 1):
            if dx * dx + dy * dy <= stroke_w * stroke_w + 1:
                draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text((x, y), text, font=font, fill=fill)


def _wrap_headline(text: str, font, max_w: int, max_lines: int = 3) -> list[str]:
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if d.textbbox((0, 0), cand, font=font)[2] <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines - 1:
                break
    rest_idx = sum(len(l.split()) for l in lines)
    rem = " ".join(words[rest_idx:]).strip()
    if rem:
        lines.append(rem)
    return lines[:max_lines]


def _draw_source_pill(d, text: str, f_badge) -> None:
    if not text.strip():
        return
    txt = text.strip()[:48]
    tb = d.textbbox((0, 0), txt, font=f_badge)
    tw = tb[2] - tb[0]
    pill_w = min(W - 96, tw + 74)
    d.rounded_rectangle([48, 92, 48 + pill_w, 148], radius=24, fill=(14, 18, 28, 215), outline=(255, 255, 255, 85), width=2)
    d.ellipse([68, 111, 86, 129], fill=(229, 9, 20, 255))
    d.text((98, 104), txt, font=f_badge, fill=(245, 247, 250, 255))


def _make_vi_frame(chunk: list[dict] | None, active_idx: int, show_hook: bool,
                   hook_headline: str, source_note: str, badge_label: str = "TIN NÓNG QUỐC TẾ"):
    from PIL import Image, ImageDraw, ImageFont
    bold_p, bi_p, ui_p = _fonts_available()
    f_badge = ImageFont.truetype(ui_p, 27)
    f_hook = ImageFont.truetype(bold_p, 60)
    f_kara = ImageFont.truetype(bi_p, 56)
    f_tag = ImageFont.truetype(ui_p, 30)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _draw_source_pill(d, source_note, f_badge)

    if show_hook:
        y0 = 1140
        for row in range(y0, H):
            alpha = int(235 * min(1.0, (row - y0) / 260.0))
            d.line([(0, row), (W, row)], fill=(9, 13, 28, alpha))
        tb = d.textbbox((0, 0), badge_label, font=f_tag)
        bw = (tb[2] - tb[0]) + 44
        d.rounded_rectangle([68, 1212, 68 + bw, 1266], radius=16, fill=(217, 30, 24, 245), outline=(255, 255, 255, 230), width=3)
        d.text((90, 1221), badge_label, font=f_tag, fill=(255, 255, 255, 255))

        lines = _wrap_headline(hook_headline.upper(), f_hook, W - 180, max_lines=3)
        for li, line in enumerate(lines):
            ly = 1288 + li * 74
            if li == 0:
                _draw_stroked_text(d, (68, ly), line, f_hook, (255, 255, 255, 255), (0, 0, 0, 255), stroke_w=5)
            else:
                _draw_stroked_text(d, (68, ly), line, f_hook, (255, 36, 36, 255), (255, 255, 255, 255), stroke_w=5)
        return img

    if chunk:
        tokens = [str(w["text"]).upper() for w in chunk]
        mid = max(1, (len(tokens) + 1) // 2) if len(tokens) > 2 else len(tokens)
        rows = [(tokens[:mid], 0), (tokens[mid:], mid)] if mid < len(tokens) else [(tokens, 0)]
        safe_center_x = 498
        base_y = 1295 if len(rows) == 2 else 1350
        for r_idx, (row_toks, offset) in enumerate(rows):
            widths = [d.textbbox((0, 0), t, font=f_kara)[2] - d.textbbox((0, 0), t, font=f_kara)[0] for t in row_toks]
            gap = 22
            total_w = sum(widths) + gap * max(0, len(row_toks) - 1)
            cx = max(64, safe_center_x - total_w // 2)
            cy = base_y + r_idx * 78
            for ti, tok in enumerate(row_toks):
                g_idx = offset + ti
                tw = widths[ti]
                if g_idx == active_idx:
                    is_num = is_numeric_word(tok)
                    pill_col = (255, 184, 0, 255) if is_num else (229, 9, 20, 255)
                    txt_col = (12, 12, 16, 255) if is_num else (255, 255, 255, 255)
                    d.rounded_rectangle([cx - 14, cy - 8, cx + tw + 16, cy + 62], radius=14, fill=pill_col)
                    if is_num:
                        d.text((cx, cy), tok, font=f_kara, fill=txt_col)
                    else:
                        _draw_stroked_text(d, (cx, cy), tok, f_kara, txt_col, (0, 0, 0, 220), stroke_w=3)
                else:
                    _draw_stroked_text(d, (cx, cy), tok, f_kara, (255, 255, 255, 255), (0, 0, 0, 255), stroke_w=5)
                cx += tw + gap
    return img


def _make_en_frame(chunk: list[dict] | None, active_idx: int, show_split_hook: bool,
                   hook_headline: str, source_note: str, duration_s: float):
    from PIL import Image, ImageDraw, ImageFont
    bold_p, _bi_p, ui_p = _fonts_available()
    f_badge = ImageFont.truetype(ui_p, 26)
    f_split = ImageFont.truetype(bold_p, 56)
    f_mid = ImageFont.truetype(bold_p, 32)
    f_kara = ImageFont.truetype(bold_p, 50)
    f_ch = ImageFont.truetype(ui_p, 26)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _draw_source_pill(d, source_note, f_badge)

    # Top-right circular channel badge
    d.ellipse([876, 86, 996, 206], fill=(0, 184, 217, 235), outline=(255, 255, 255, 220), width=3)
    d.text((898, 126), "GLOBAL", font=f_ch, fill=(255, 255, 255, 255))
    d.text((912, 152), "BRIEF", font=f_ch, fill=(255, 255, 255, 255))

    if show_split_hook:
        lines = _wrap_headline(hook_headline, f_split, W - 160, max_lines=3)
        for li, line in enumerate(lines):
            col = (0, 229, 255, 255) if li == 0 else (255, 255, 255, 255)
            _draw_stroked_text(d, (58, 560 + li * 74), line, f_split, col, (0, 0, 0, 255), stroke_w=5)
        sec = max(15, int(round(duration_s)))
        mid_txt = f"IN {sec} SECONDS"
        mb = d.textbbox((0, 0), mid_txt, font=f_mid)
        mw = (mb[2] - mb[0]) + 48
        mx0 = (W - mw) // 2
        d.line([(0, 960), (W, 960)], fill=(255, 255, 255, 190), width=4)
        d.rounded_rectangle([mx0, 932, mx0 + mw, 988], radius=8, fill=(255, 255, 255, 255), outline=(12, 12, 18, 255), width=4)
        d.text((mx0 + 24, 941), mid_txt, font=f_mid, fill=(12, 12, 18, 255))

    if chunk:
        tokens = [str(w["text"]) for w in chunk]
        mid = max(1, (len(tokens) + 1) // 2) if len(tokens) > 3 else len(tokens)
        rows = [(tokens[:mid], 0), (tokens[mid:], mid)] if mid < len(tokens) else [(tokens, 0)]
        safe_center_x = 492
        base_y = 1360 if len(rows) == 2 else 1405
        for r_idx, (row_toks, offset) in enumerate(rows):
            widths = [d.textbbox((0, 0), t, font=f_kara)[2] - d.textbbox((0, 0), t, font=f_kara)[0] for t in row_toks]
            gap = 18
            total_w = sum(widths) + gap * max(0, len(row_toks) - 1)
            cx = max(60, safe_center_x - total_w // 2)
            cy = base_y + r_idx * 68
            for ti, tok in enumerate(row_toks):
                g_idx = offset + ti
                col = (255, 214, 0, 255) if g_idx == active_idx else ((0, 229, 255, 255) if is_numeric_word(tok) else (255, 255, 255, 255))
                _draw_stroked_text(d, (cx, cy), tok, f_kara, col, (0, 0, 0, 255), stroke_w=5)
                cx += widths[ti] + gap
    return img


def render_overlay_and_mux(
    base_mp4: Path,
    audio_wav: Path,
    duration: float,
    words: list[dict],
    out_mp4: Path,
    tmp_dir: Path,
    preset: str = "viral_redpill",
    hook_headline: str = "",
    source_note: str = "",
    hook_end_sec: float = 2.6,
    crf: str = "20",
    audio_rate: int = 48000,
) -> bool:
    """Render toàn bộ lớp chữ RGBA theo timeline word & ghép vào video gốc.
    Trả False nếu thiếu Pillow/font để caller fallback sang ASS."""
    if not is_available():
        return False

    chunks = chunk_words(words, max_words=5 if preset == "viral_redpill" else 6)
    events: list[tuple[float, float, list[dict] | None, int]] = []
    for ch in chunks:
        for wi, w in enumerate(ch):
            s = float(w["start"])
            e = float(w["end"])
            if e > s + 0.01:
                events.append((s, e, ch, wi))

    splits = {0.0, round(duration, 3)}
    if hook_end_sec > 0.2 and hook_end_sec < duration:
        splits.add(round(hook_end_sec, 3))
    for s, e, _, _ in events:
        splits.add(round(max(0.0, min(duration, s)), 3))
        splits.add(round(max(0.0, min(duration, e)), 3))
    pts = sorted(splits)

    ov_dir = tmp_dir / "overlays"
    ov_dir.mkdir(parents=True, exist_ok=True)
    concat_lines: list[str] = []
    last_png: Path | None = None
    idx = 0

    for a, b in zip(pts, pts[1:]):
        if b - a < 0.015:
            continue
        mid_t = (a + b) / 2.0
        cur_chunk, cur_wi = None, -1
        for s, e, ch, wi in events:
            if s <= mid_t <= e:
                cur_chunk, cur_wi = ch, wi
                break
        if preset == "split_news_60":
            img = _make_en_frame(cur_chunk, cur_wi, mid_t < hook_end_sec, hook_headline, source_note, duration)
        else:
            img = _make_vi_frame(cur_chunk, cur_wi, mid_t < hook_end_sec, hook_headline, source_note)
        p = ov_dir / f"ov_{idx:04d}.png"
        img.save(p)
        last_png = p
        concat_lines.append(f"file '{p.resolve().as_posix()}'")
        concat_lines.append(f"duration {b - a:.4f}")
        idx += 1

    if not last_png:
        return False
    concat_lines.append(f"file '{last_png.resolve().as_posix()}'")
    ov_txt = tmp_dir / "ov_concat.txt"
    ov_txt.write_text("\n".join(concat_lines), encoding="utf-8")

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(base_mp4),
        "-f", "concat", "-safe", "0", "-i", str(ov_txt),
        "-i", str(audio_wav),
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[vout]",
        "-map", "[vout]", "-map", "2:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", str(audio_rate),
        "-movflags", "+faststart",
        "-t", f"{duration:.3f}",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True, timeout=600)
    return True


def synth_sfx_pair(tmp_dir: Path, rate: int = 24000) -> tuple[Path, Path]:
    """Tạo Impact (0s) và Whoosh chuyển beat (chỉ dùng numpy-free stdlib)."""
    impact = tmp_dir / "sfx_impact.wav"
    whoosh = tmp_dir / "sfx_whoosh.wav"

    n_imp = int(0.45 * rate)
    with wave.open(str(impact), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        buf = bytearray(n_imp * 2)
        for i in range(n_imp):
            t = i / rate
            freq = 115.0 * math.exp(-t * 4.5) + 42.0
            val = int(15000 * math.exp(-t * 7.5) * math.sin(2.0 * math.pi * freq * t))
            struct.pack_into("<h", buf, i * 2, max(-32767, min(32767, val)))
        w.writeframes(buf)

    n_who = int(0.32 * rate)
    state = 1234567
    with wave.open(str(whoosh), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        buf = bytearray(n_who * 2)
        prev = 0.0
        for i in range(n_who):
            t = i / rate
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            noise = (state / 0x3FFFFFFF) - 1.0
            prev = 0.78 * prev + 0.22 * noise
            env = math.sin(math.pi * (t / 0.32)) ** 1.4
            val = int(9000 * env * prev)
            struct.pack_into("<h", buf, i * 2, max(-32767, min(32767, val)))
        w.writeframes(buf)
    return impact, whoosh
