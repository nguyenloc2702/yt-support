"""Skill bundle cho Agent video ngoài — Short EN "Breaking News Alert 9:16".

Nhân bản pattern `pipeline_en/skill_bundle.py` (STYLE_LOCK_V2) cho mảng short:
template tin nhanh kiểu TKMM/DailyMail — headline đậm đè lên trên, banner đỏ,
caption karaoke dưới, footage thật + map/satellite, punch-zoom nhịp 30-40-30.
Agent đọc bundle này trong job payload (`GET /api/v1/shorts/{id}/job`).
"""
from __future__ import annotations

from pathlib import Path

SKILL_NAME = "short-news-916"
SKILL_VERSION = "1.0.0"

STYLE_NEWS_916 = """STYLE LOCKED — BREAKING NEWS ALERT 9:16 (short EN, TikTok/Reels/Shorts)

OUTPUT: 1 vertical video clip per scene, MP4 h264, 1080x1920 @30fps, yuv420p,
duration EXACTLY matching the scene audio window (audio_start_sec → audio_end_sec
from the job package; ±0.25s tolerance).

LAYOUT (fixed, every clip):
- Top band (~18% height): bold uppercase headline, 2 lines max, white with
  black outline or red highlight bar — the key numbers/nouns of the scene.
- Top-left corner: red "BREAKING" badge with pulsing-free (static) banner.
- Middle: the visual — REAL footage/photos preferred (news, map, satellite,
  city, money, tech). Punch-zoom motion: 30% push-in during first third,
  40% hold, 30% pull-out or pan. NO static camera for the whole clip.
- Bottom (~25% height): keep CLEAR for karaoke captions burned by the system.
  Do NOT put text in the bottom quarter.

LOOK & TONE: urgent, fast, high contrast. Muted realistic palette with one
accent color (red #D93025 or amber). Capital letters for numbers.

NEGATIVE (hard bans): NO watermark, NO stock logo, NO subtitles in bottom
quarter (system burns them), NO cartoon/crayon style, NO AI-look faces,
NO letterboxing, NO 16:9 content pasted with black bars — fill the frame.
"""

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


def get_short_skill_summary() -> dict:
    return {
        "skill_name": f"{SKILL_NAME} (breaking-news vertical)",
        "version": SKILL_VERSION,
        "style_lock": STYLE_NEWS_916,
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
