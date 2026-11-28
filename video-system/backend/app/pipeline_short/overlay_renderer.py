"""Pillow RGBA Overlay Engine cho Short Studio (TikTok/Reels/Shorts 1080x1920).

Hỗ trợ 2 preset chuẩn retention:
- `viral_redpill` (mặc định VI): Lower-third dark navy gradient hook card 0–2.6s,
  karaoke 2 dòng ALL-CAPS bold-italic trong safe-zone (x <= 932, y ~ 1290..1430),
  Red Pill #E50914 cho từ đang đọc & Gold Pill #FFB800 cho số tiền/tỷ lệ,
  source_note pill góc trên trái.
- `split_news_60` (mặc định EN): Split-screen 50/50 hook (headline Cyan/Trắng +
  badge giữa IN 30 SECONDS + badge tròn kênh), floating callout pill, karaoke gọn
  trong safe-zone + source_note pill.
- `clean_karaoke` (mode cinematic): CHỈ karaoke theo word trong safe-zone — không
  badge, không headline, không source pill. Preset lạ vẫn rơi về hành vi cũ.
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
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ])
    bi = _find_font([
        "C:/Windows/Fonts/arialbi.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-BoldOblique.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ])
    ui = _find_font([
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ])
    if not bold:
        return None
    return bold, bi or bold, ui or bold


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


def _draw_vi_karaoke(d, chunk: list[dict] | None, active_idx: int, f_kara) -> None:
    """Vẽ khối karaoke 2 dòng ALL-CAPS trong safe-zone (dùng chung cho preset VI)."""
    if not chunk:
        return
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
        _draw_vi_karaoke(d, chunk, active_idx, f_kara)
    return img


def _make_clean_karaoke_frame(chunk: list[dict] | None, active_idx: int):
    """Preset `clean_karaoke` (mode cinematic): CHỈ vẽ karaoke theo word — không
    badge "TIN NÓNG QUỐC TẾ", không headline split-screen, không source pill."""
    from PIL import Image, ImageDraw, ImageFont
    _bold_p, bi_p, _ui_p = _fonts_available()
    f_kara = ImageFont.truetype(bi_p, 56)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if chunk:
        d = ImageDraw.Draw(img)
        _draw_vi_karaoke(d, chunk, active_idx, f_kara)
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


_DIGEST_CAT_COLORS = {
    "WORLD": (37, 99, 235, 240),
    "MARKETS": (5, 150, 105, 240),
    "BUSINESS": (16, 185, 129, 240),
    "TECH": (124, 58, 237, 240),
    "SCIENCE": (13, 148, 136, 240),
    "ENERGY": (217, 119, 6, 240),
    "SPORTS": (234, 88, 12, 240),
}


def _make_digest_base(scene_meta: dict, total_stories: int, brand_header: str, signature: str):
    """Render static background scrim + top bar + progress bar + story card cho preset daily_digest."""
    from PIL import Image, ImageDraw, ImageFont
    bold_p, _bi_p, ui_p = _fonts_available()
    f_brand = ImageFont.truetype(ui_p, 26)
    f_count = ImageFont.truetype(bold_p, 28)
    f_cat = ImageFont.truetype(ui_p, 24)
    f_head = ImageFont.truetype(bold_p, 52)
    f_stat = ImageFont.truetype(bold_p, 84)
    f_sub = ImageFont.truetype(ui_p, 30)
    f_sig = ImageFont.truetype(ui_p, 26)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Scrim gradient: tối phần trên (headline) & phần dưới (subtitles), giữ giữa thoáng để thấy ảnh/video
    for y in range(0, 560):
        a = int(195 - 125 * (y / 560.0))
        d.line([(0, y), (W, y)], fill=(8, 12, 20, a))
    d.rectangle([0, 560, W, 1220], fill=(8, 12, 20, 68))
    for y in range(1220, H):
        a = int(68 + 135 * ((y - 1220) / float(H - 1220)))
        d.line([(0, y), (W, y)], fill=(8, 12, 20, a))

    # Top bar: brand header trái (@STICKSTO · DAILY BRIEF) + chỉ số tin phải (01 / 15)
    hdr = (brand_header or "@STICKSTO · DAILY BRIEF").upper()
    if signature and signature.upper() in hdr:
        sig_u = signature.upper()
        pre, mid, rest = hdr.partition(sig_u)
        x_cur = 56
        if pre:
            d.text((x_cur, 72), pre, font=f_brand, fill=(226, 232, 240, 235))
            x_cur += d.textbbox((0, 0), pre, font=f_brand)[2]
        d.text((x_cur, 72), mid, font=f_brand, fill=(56, 189, 248, 255))
        x_cur += d.textbbox((0, 0), mid, font=f_brand)[2]
        if rest:
            d.text((x_cur, 72), rest, font=f_brand, fill=(226, 232, 240, 225))
    else:
        d.text((56, 72), hdr, font=f_brand, fill=(226, 232, 240, 235))

    story_idx = int(scene_meta.get("story_idx") or 0)
    if 1 <= story_idx <= max(1, total_stories):
        cnt_txt = f"{story_idx:02d} / {total_stories:02d}"
    elif story_idx == 0:
        cnt_txt = "INTRO"
    else:
        cnt_txt = "OUTRO"
    cb = d.textbbox((0, 0), cnt_txt, font=f_count)
    d.text((W - 56 - (cb[2] - cb[0]), 70), cnt_txt, font=f_count, fill=(248, 250, 252, 245))

    # Segmented story progress bar (N đoạn)
    segs = max(1, total_stories)
    gap = 6
    bar_x0, bar_x1, bar_y, bar_h = 56, W - 56, 116, 6
    seg_w = max(8, (bar_x1 - bar_x0 - gap * (segs - 1)) / segs)
    for si in range(segs):
        sx0 = int(round(bar_x0 + si * (seg_w + gap)))
        sx1 = int(round(sx0 + seg_w))
        if story_idx > segs or (si + 1) < story_idx:
            col = (56, 189, 248, 235)
        elif (si + 1) == story_idx:
            col = (250, 204, 21, 255)
        else:
            col = (255, 255, 255, 58)
        d.rounded_rectangle([sx0, bar_y, sx1, bar_y + bar_h], radius=3, fill=col)

    if story_idx == 0:
        # Hook card
        pill = f"{signature or '@StickSto'} · GLOBAL EDITION".upper()
        pb = d.textbbox((0, 0), pill, font=f_cat)
        pw = (pb[2] - pb[0]) + 36
        d.rounded_rectangle([56, 230, 56 + pw, 276], radius=10, fill=(37, 99, 235, 235))
        d.text((74, 239), pill, font=f_cat, fill=(255, 255, 255, 255))
        _draw_stroked_text(d, (56, 320), f"{total_stories} WORLD STORIES", f_stat, (255, 255, 255, 255), (0, 0, 0, 255), stroke_w=5)
        _draw_stroked_text(d, (56, 424), "IN UNDER 3 MINUTES", f_head, (56, 189, 248, 255), (0, 0, 0, 255), stroke_w=4)
    elif 1 <= story_idx <= total_stories:
        cat = str(scene_meta.get("category") or "WORLD").upper()
        cat_col = _DIGEST_CAT_COLORS.get(cat, (37, 99, 235, 240))
        cb2 = d.textbbox((0, 0), cat, font=f_cat)
        cw = (cb2[2] - cb2[0]) + 32
        d.rounded_rectangle([56, 152, 56 + cw, 194], radius=8, fill=cat_col)
        d.text((72, 160), cat, font=f_cat, fill=(255, 255, 255, 255))

        headline = str(scene_meta.get("headline") or "").strip()
        if headline:
            hlines = _wrap_headline(headline, f_head, W - 124, max_lines=3)
            for li, line in enumerate(hlines):
                _draw_stroked_text(d, (56, 218 + li * 66), line, f_head, (255, 255, 255, 255), (0, 0, 0, 240), stroke_w=4)

        stat = str(scene_meta.get("hero_stat") or "").strip()
        sub = str(scene_meta.get("hero_sub") or "").strip()
        if stat:
            _draw_stroked_text(d, (56, 740), stat, f_stat, (56, 189, 248, 255), (0, 0, 0, 245), stroke_w=5)
        if sub:
            slines = _wrap_headline(sub, f_sub, W - 140, max_lines=2)
            for li, sline in enumerate(slines):
                _draw_stroked_text(d, (56, 842 + li * 38), sline, f_sub, (226, 232, 240, 245), (0, 0, 0, 220), stroke_w=3)
    else:
        # Outro card
        _draw_stroked_text(d, (56, 360), "THAT'S YOUR BRIEF.", f_stat, (255, 255, 255, 255), (0, 0, 0, 255), stroke_w=5)
        _draw_stroked_text(d, (56, 464), f"FOLLOW {signature or '@StickSto'}", f_head, (250, 204, 21, 255), (0, 0, 0, 255), stroke_w=4)

    return img


def _render_moving_watermark_png(text: str, out_png: Path, opacity: float = 0.22, font_size: int = 36) -> bool:
    """Render PNG chữ ký mờ (RGBA) giống video dài để chạy chuyển động trong ffmpeg."""
    fonts = _fonts_available()
    if not fonts or not text.strip():
        return False
    from PIL import Image, ImageDraw, ImageFont
    bold_p, _bi_p, _ui_p = fonts
    font = ImageFont.truetype(bold_p, font_size)
    dummy = Image.new("RGBA", (10, 10))
    d0 = ImageDraw.Draw(dummy)
    tb = d0.textbbox((0, 0), text.strip(), font=font)
    pad = 10
    w = max(32, (tb[2] - tb[0]) + pad * 2)
    h = max(24, (tb[3] - tb[1]) + pad * 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    alpha_fg = max(16, min(255, int(round(255 * opacity))))
    alpha_bg = max(10, int(round(alpha_fg * 0.65)))
    tx, ty = pad - tb[0], pad - tb[1]
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (2, 2)):
        d.text((tx + dx, ty + dy), text.strip(), font=font, fill=(0, 0, 0, alpha_bg))
    d.text((tx, ty), text.strip(), font=font, fill=(255, 255, 255, alpha_fg))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return True


def _draw_digest_subtitles(base_img, chunk: list[dict] | None, active_idx: int):
    """Vẽ 2 dòng phụ đề gọn lên bản copy của base_img cho preset daily_digest."""
    from PIL import ImageDraw, ImageFont
    img = base_img.copy()
    if not chunk:
        return img
    bold_p, _bi_p, _ui_p = _fonts_available()
    f_kara = ImageFont.truetype(bold_p, 46)
    d = ImageDraw.Draw(img)

    tokens = [str(w["text"]) for w in chunk]
    mid = max(1, (len(tokens) + 1) // 2) if len(tokens) > 3 else len(tokens)
    rows = [(tokens[:mid], 0), (tokens[mid:], mid)] if mid < len(tokens) else [(tokens, 0)]
    safe_center_x = 492
    base_y = 1320 if len(rows) == 2 else 1365
    for r_idx, (row_toks, offset) in enumerate(rows):
        widths = [d.textbbox((0, 0), t, font=f_kara)[2] - d.textbbox((0, 0), t, font=f_kara)[0] for t in row_toks]
        gap = 16
        total_w = sum(widths) + gap * max(0, len(row_toks) - 1)
        cx = max(56, safe_center_x - total_w // 2)
        cy = base_y + r_idx * 62
        for ti, tok in enumerate(row_toks):
            g_idx = offset + ti
            if g_idx == active_idx:
                col = (250, 204, 21, 255) if is_numeric_word(tok) else (255, 255, 255, 255)
            elif g_idx < active_idx:
                col = (241, 245, 249, 240)
            else:
                col = (148, 163, 184, 225)
            _draw_stroked_text(d, (cx, cy), tok, f_kara, col, (0, 0, 0, 245), stroke_w=4)
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
    digest_spec: dict | None = None,
    watermark_spec: dict | None = None,
) -> bool:
    """Render toàn bộ lớp chữ RGBA theo timeline word & ghép vào video gốc.
    Trả False nếu thiếu Pillow/font để caller fallback sang ASS."""
    if not is_available():
        return False

    digest_scenes = list((digest_spec or {}).get("scenes") or [])
    if preset == "daily_digest" and digest_scenes:
        # Chống lem phụ đề giữa 2 tin liên tiếp nếu word timeline cũ chưa có cue_idx
        tagged: list[dict] = []
        for w in words:
            ww = dict(w)
            if ww.get("cue_idx") is None:
                wm = (float(ww["start"]) + float(ww["end"])) / 2.0
                for si, sc in enumerate(digest_scenes):
                    if float(sc.get("start_sec") or 0.0) <= wm <= float(sc.get("end_sec") or duration):
                        ww["cue_idx"] = si
                        break
            tagged.append(ww)
        words = tagged

    chunks = chunk_words(words, max_words=5 if preset == "viral_redpill" else 6)
    word_events: list[tuple[float, float, int, int]] = []
    for ci, ch in enumerate(chunks):
        for wi, w in enumerate(ch):
            s = float(w["start"])
            e = float(w["end"])
            if e > s + 0.005:
                word_events.append((s, e, ci, wi))

    # Pre-render static story bases cho preset daily_digest (nhanh gấp 5x)
    digest_bases: dict[int, object] = {}
    if preset == "daily_digest":
        d_spec = digest_spec or {}
        total_stories = int(d_spec.get("total_stories") or max(1, len(digest_scenes) - 2))
        brand_hdr = str(d_spec.get("brand_header") or "@STICKSTO · DAILY BRIEF")
        sig = str(d_spec.get("signature") or "@StickSto")
        for idx_s, sc in enumerate(digest_scenes):
            digest_bases[idx_s] = _make_digest_base(sc, total_stories, brand_hdr, sig)

    # Frame-exact Run-Length Encoding theo lưới 30fps: KHÔNG BAO GIỜ bỏ sót khe hở
    # 0.012s giữa các từ (nguyên nhân làm tổng thời lượng overlay tụt ~3.2s cuối video).
    total_frames = max(1, int(round(duration * FPS)))
    ev_ptr = 0
    n_ev = len(word_events)
    last_active: tuple[int, int, float] | None = None  # (ci, wi, end_sec)

    frame_states: list[tuple[int, int, int]] = []  # (sc_or_hook, chunk_idx, word_idx)
    sc_ptr = 0
    for f_idx in range(total_frames):
        t = (f_idx + 0.5) / float(FPS)
        while ev_ptr < n_ev and word_events[ev_ptr][1] < t:
            ev_ptr += 1
        cur_ci, cur_wi = -1, -1
        if ev_ptr < n_ev and word_events[ev_ptr][0] <= t <= word_events[ev_ptr][1]:
            cur_ci, cur_wi = word_events[ev_ptr][2], word_events[ev_ptr][3]
            last_active = (cur_ci, cur_wi, word_events[ev_ptr][1])
        elif last_active is not None and (t - last_active[2]) <= 0.09:
            # Giữ highlight mượt qua khe nghỉ 12ms giữa 2 từ (không nhấp nháy, không trôi)
            if ev_ptr < n_ev and word_events[ev_ptr][2] == last_active[0]:
                cur_ci, cur_wi = last_active[0], last_active[1]

        if preset == "daily_digest" and digest_scenes:
            while sc_ptr + 1 < len(digest_scenes) and t > float(digest_scenes[sc_ptr].get("end_sec") or duration):
                sc_ptr += 1
            sc_or_hook = sc_ptr
        elif preset == "clean_karaoke":
            sc_or_hook = 0  # karaoke-only: không hook card, không badge, không headline
        else:
            sc_or_hook = 1 if t < hook_end_sec else 0
        frame_states.append((sc_or_hook, cur_ci, cur_wi))

    runs: list[tuple[tuple[int, int, int], int]] = []
    for st in frame_states:
        if runs and runs[-1][0] == st:
            runs[-1] = (st, runs[-1][1] + 1)
        else:
            runs.append((st, 1))

    ov_dir = tmp_dir / "overlays"
    ov_dir.mkdir(parents=True, exist_ok=True)
    concat_lines: list[str] = []
    last_png: Path | None = None
    for idx, ((sc_or_hook, cur_ci, cur_wi), n_frames) in enumerate(runs):
        cur_chunk = chunks[cur_ci] if 0 <= cur_ci < len(chunks) else None
        if preset == "daily_digest" and digest_bases:
            img = _draw_digest_subtitles(digest_bases[sc_or_hook], cur_chunk, cur_wi)
        elif preset == "clean_karaoke":
            img = _make_clean_karaoke_frame(cur_chunk, cur_wi)
        elif preset == "split_news_60":
            img = _make_en_frame(cur_chunk, cur_wi, bool(sc_or_hook), hook_headline, source_note, duration)
        else:
            img = _make_vi_frame(cur_chunk, cur_wi, bool(sc_or_hook), hook_headline, source_note)
        p = ov_dir / f"ov_{idx:04d}.png"
        img.save(p, compress_level=1)
        last_png = p
        concat_lines.append(f"file '{p.resolve().as_posix()}'")
        concat_lines.append(f"duration {n_frames / float(FPS):.6f}")

    if not last_png:
        return False
    concat_lines.append(f"file '{last_png.resolve().as_posix()}'")
    ov_txt = tmp_dir / "ov_concat.txt"
    ov_txt.write_text("\n".join(concat_lines), encoding="utf-8")

    wm_cfg = watermark_spec or {}
    wm_text = str(wm_cfg.get("text") or "").strip()
    wm_png = tmp_dir / "wm_moving.png"
    wm_ready = bool(wm_text and _render_moving_watermark_png(
        wm_text, wm_png,
        opacity=float(wm_cfg.get("opacity", 0.22)),
        font_size=int(wm_cfg.get("font_size", 36)),
    ))

    if wm_ready:
        fc = (
            f"[1:v]fps={FPS}:round=near[ov_cfr];"
            "[0:v][ov_cfr]overlay=0:0:shortest=1[ov];"
            "[ov][2:v]overlay=x='(W-w)/2+(W-w)*0.32*sin(t*0.23)':"
            "y='(H-h)/2+(H-h)*0.28*cos(t*0.17)':format=auto[vout]"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(base_mp4),
            "-f", "concat", "-safe", "0", "-i", str(ov_txt),
            "-i", str(wm_png),
            "-i", str(audio_wav),
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "3:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", str(audio_rate),
            "-movflags", "+faststart",
            "-t", f"{duration:.3f}",
            str(out_mp4),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(base_mp4),
            "-f", "concat", "-safe", "0", "-i", str(ov_txt),
            "-i", str(audio_wav),
            "-filter_complex", f"[1:v]fps={FPS}:round=near[ov_cfr];[0:v][ov_cfr]overlay=0:0:shortest=1[vout]",
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
