"""Skill bundle cho Agent video ngoài — Short EN "Breaking News Alert 9:16".

Nhân bản pattern `pipeline_en/skill_bundle.py` (STYLE_LOCK_V2) cho mảng short:
template tin nhanh kiểu TKMM/DailyMail — headline đậm đè lên trên, banner đỏ,
caption karaoke dưới, footage thật + map/satellite, punch-zoom nhịp 30-40-30.
Agent đọc bundle này trong job payload (`GET /api/v1/shorts/{id}/job`).
"""
from __future__ import annotations

from pathlib import Path

SKILL_NAME = "short-global-hybrid-916"
SKILL_VERSION = "2.0.0"

STYLE_GLOBAL_HYBRID_916 = """STYLE LOCKED — GLOBAL EXPLAINER HYBRID 9:16 (VI & EN International News/Finance)

OUTPUT: 1 vertical video clip per shot, MP4 h264, 1080x1920 @30fps, yuv420p,
duration EXACTLY matching the shot audio window (duration_sec ±0.25s).

VISUAL IDENTITY (pick the matching lane per shot, keep lighting cohesive):
- Lane A (Geopolitics / Macro / Trade / Conflict / Territory): 3D tactical relief
  map or high-altitude satellite view with subtle depth-of-field, crisp dark-navy
  ocean & desaturated terrain, highlighted borders/routes in glowing cyan (#00E5FF)
  or alert red (#E50914), subtle topographic grid.
- Lane B (Corporate / Tech / Banking / Real-World Event): Cinematic dark-toned
  editorial footage/3D render, muted slate & navy shadows, single high-contrast
  red (#E50914) or amber (#FFB800) accent focal element.

COMPOSITION & 3-LAYER DEPTH:
- Foreground: subtle edge-cropped silhouette or HUD framing element for depth.
- Midground (Focal Subject): the single hero subject of the shot — clear silhouette,
  uncluttered, immediately readable on a phone screen in <0.5s.
- Background: desaturated atmospheric environment or tactical grid.

SAFE-ZONE LAW (CRITICAL):
- Bottom 25% (y=1440..1920) AND right 130px margin (x=950..1080) MUST stay visually
  clean and darker-toned — system overlays 2-line ALL-CAPS Red/Gold Pill karaoke
  and TikTok UI icons sit there.
- Top-left corner (x=48..460, y=90..160): reserved for system source_note pill.

MOTION (30-40-30 RULE):
- 0–30%: decisive camera push-in / tilt onto focal subject.
- 30–70%: steady micro-tracking or single subject/route animation.
- 70–100%: settle/hold cleanly for hard cut to next shot.

NEGATIVE (hard bans): NO baked subtitles or captions, NO random AI gibberish text,
NO watermarks, NO stock logos, NO distorted AI faces/hands, NO letterboxing or
black bars — fill 1080x1920 completely.
"""

STYLE_NEWS_916 = STYLE_GLOBAL_HYBRID_916

SHORT_SHOT_TYPES = {
    "hook": {
        "when_to_use": "Scene 1 — the shock opener",
        "focal_point": "the single most dramatic visual: damage figure, scene of event",
        "motion": "hard punch-in 0-1s, hold, slight drift",
        "prompt_template": (
            "Vertical 9:16 breaking-news clip: {narration}. Dramatic real footage, "
            "urgent tone, bold headline overlay material 'TOP: {headline_hint}'."
        ),
    },
    "beat": {
        "when_to_use": "Core development scenes",
        "focal_point": "the mechanism/fact being explained: map, document, money flow, officials",
        "motion": "30-40-30 punch-zoom or slow lateral pan",
        "prompt_template": (
            "Vertical 9:16 news clip: {narration}. Real news footage or satellite/map "
            "imagery, headline overlay material 'TOP: {headline_hint}'."
        ),
    },
    "closing": {
        "when_to_use": "Final question scene — drives comments",
        "focal_point": "split-visual of the two sides of the question, or slow zoom out",
        "motion": "slow zoom-out, beat ends on hold",
        "prompt_template": (
            "Vertical 9:16 news clip: {narration}. Balanced two-sided visual, "
            "headline overlay material 'TOP: {headline_hint}'."
        ),
    },
}

GOLDEN_RULES = [
    "1. Duration is LAW: clip duration = scene audio window ±0.25s.",
    "2. Fill 9:16 completely — scale/crop, never letterbox.",
    "3. Keep the bottom 25% clean (system burns karaoke captions there).",
    "4. Headline overlay: 2 lines max, uppercase, key nouns/numbers only.",
    "5. Real footage/photos over AI-look; map & satellite imagery for geopolitics.",
    "6. Motion every clip: 30-40-30 punch-zoom minimum, no full-static camera.",
    "7. One clip may cover MULTIPLE consecutive scenes — pass covered_scene_indexes.",
    "8. No audio needed in clips — system mixes voice + BGM (muted submission ok).",
    "9. No watermark, no logo, no source bug overlay.",
    "10. Submit via POST /api/v1/shorts/{short_id}/clips with seq_num = scene seq.",
]

_GUIDE_RELPATH = "AGENT_SHORT_GUIDE.md"


def _has_numeric_metric(text: str) -> bool:
    import re
    return bool(re.search(r"(\d+[.,]?\d*\s*(%|tỷ|triệu|nghìn|billion|million|thousand|\$|USD|EUR)|\$\s*\d+)",
                          str(text or ""), flags=re.IGNORECASE))


def build_detailed_shot_prompt(
    shot_type: str,
    seq: int,
    narration: str,
    duration_sec: float,
    lang: str = "vi",
    headline_hint: str = "",
    stock_keywords: str = "",
) -> str:
    """Sinh prompt dài, chi tiết chuẩn 9:16 (Audio-First) cho 1 shot AI video."""
    role = SHORT_SHOT_TYPES.get(shot_type, SHORT_SHOT_TYPES["beat"])
    is_data = _has_numeric_metric(narration)
    render_hint = (
        "RENDER MODE: Animated Financial/Stat DataCard recommended (system can auto-render "
        "crisp numbers/charts) OR clean 3D macro visualization WITHOUT garbled AI text."
        if is_data
        else "RENDER MODE: AI Video / Keyframe-to-Video (3D Tactical Map or Cinematic Dark Editorial)."
    )
    return (
        f"[SHOT #{seq:02d} | TYPE: {shot_type.upper()} | DURATION: {duration_sec:.2f}s | LANG: {lang.upper()}]\n"
        f"{render_hint}\n"
        f"NARRATION BEAT: \"{narration.strip()}\"\n"
        f"VISUAL KEYWORDS: {stock_keywords or 'global finance, macro economy'}\n"
        f"HEADLINE CONTEXT: {headline_hint.strip()}\n"
        f"FOCAL POINT: {role['focal_point']}\n"
        f"CAMERA & 30-40-30 MOTION: {role['motion']} — 0-30% push-in, 30-70% steady tracking, "
        f"70-100% hold for hard cut.\n"
        f"3-LAYER COMPOSITION (9:16 1080x1920): Foreground subtle HUD/silhouette edge-crop; "
        f"Midground clear hero subject centered in upper-middle band (y=280..1180); "
        f"Background desaturated dark-navy/slate environment.\n"
        f"SAFE-ZONE: Keep bottom 25% (y=1440..1920) & right 130px clear for Red/Gold Pill karaoke & TikTok UI.\n\n"
        f"{STYLE_GLOBAL_HYBRID_916.strip()}"
    )


def slice_sub_shots(seq_num: int, narration: str, start_sec: float, end_sec: float,
                    lang: str, headline_hint: str, stock_keywords: str,
                    max_sub_dur: float = 3.5) -> list[dict]:
    """Chẻ 1 scene dài thành các sub-shot 2.5-3.5s để AI gen chuyển động mượt hơn."""
    dur = max(0.5, float(end_sec) - float(start_sec))
    n_parts = 2 if dur > max_sub_dur else 1
    shot_type = "hook" if seq_num == 1 else "beat"
    parts: list[dict] = []
    part_dur = dur / n_parts
    for pi in range(n_parts):
        s = round(start_sec + pi * part_dur, 3)
        e = round(end_sec if pi == n_parts - 1 else start_sec + (pi + 1) * part_dur, 3)
        parts.append({
            "sub_seq": f"{seq_num}.{pi + 1}",
            "audio_start_sec": s,
            "audio_end_sec": e,
            "duration_sec": round(e - s, 3),
            "recommended_mode": "datacard" if (_has_numeric_metric(narration) and pi == 0 and seq_num > 1) else "ai_video",
            "visual_prompt": build_detailed_shot_prompt(
                shot_type, seq_num, narration, round(e - s, 3),
                lang=lang, headline_hint=headline_hint, stock_keywords=stock_keywords,
            ),
        })
    return parts


def get_short_skill_summary() -> dict:
    return {
        "skill_name": f"{SKILL_NAME} (VI + EN international)",
        "version": SKILL_VERSION,
        "style_lock": STYLE_GLOBAL_HYBRID_916,
        "shot_types": SHORT_SHOT_TYPES,
        "golden_rules": GOLDEN_RULES,
        "agent_guide_url": "/api/v1/short-agent-guide",
    }


def get_full_short_bundle() -> dict:
    """Bundle đầy đủ + markdown guide nếu tồn tại trong repo (honest: thiếu → bỏ)."""
    bundle = get_short_skill_summary()
    root = Path(__file__).resolve().parents[3]
    guide = root / _GUIDE_RELPATH
    bundle["agent_guide_url"] = "/api/v1/short-agent-guide"
    if guide.exists():
        bundle["agent_guide_markdown"] = guide.read_text(encoding="utf-8")
    return bundle
