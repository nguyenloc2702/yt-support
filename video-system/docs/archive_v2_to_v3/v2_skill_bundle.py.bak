"""Skill Bundle loader for External Image/Keyframe & Video Generation Agents.

Packages the `shot-generation-expert` v2 (Crayon Capital Vector Mold) and
`video-production-expert` v1 rules directly into every TODO item so an external
agent pulling `GET /api/v1/todos/{item_id}` or `GET /api/v1/todos/{item_id}/skills`
gets the complete style lock, 4 shot-type templates, character vs crowd rules,
14-slot framework, and 30-40-30 motion timeline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

STYLE_LOCK_V2 = """STYLE LOCKED:
Simple flat 2D cartoon explainer, clean sharp professional line art.
Crisp, smooth, bold black outlines, clean vector edges.
Flat colours, minimal shading: exactly one flat grey side shadow
on the shaded side of a head or body, no gradient, no detailed shading.
Characters are stick figures: oversized round heads, very small dot eyes,
simple curved eyebrows, wide toothy grins OR shocked open mouths,
a small oval or tube body, thin single-line stick arms and legs,
hands as small plain blobs, no fingers, no detailed clothing folds.
Main character: peach skin face, detailed hair, dark or coloured outfit,
sharp and high contrast. Crowd characters: plain white faces,
no skin tone, one flat grey side shadow, simple flat hair,
repeated identical oval bodies, lower contrast than the main character.
Muted-but-clear palette, slightly desaturated: dusty blue, muted navy,
muted red, off-white, slate grey, warm earth tones.
One accent colour per shot, muted red, reserved for the focal object,
number, or stamp. Everything else stays muted so the accent punches.
Bright, simple backgrounds, one clear scene, no clutter, still bright and readable.
Aspect ratio: 16:9, unless the format requires 9:16.
Negative: NO crayon texture, NO grain, NO paper texture,
NO rough sketchy wobbly lines, NO childish doodle texture,
NO overly vivid candy colours, NO dark grey muddy look,
no photorealism, no 3D render, no detailed shading, no gradient,
no blurry, no extra limbs, no distorted hands,
no crowded background, no tiny unreadable text, no garbled text,
no realistic torso, shirt, or jacket with folds and buttons,
no crowd character with the same skin tone and detail as the main character."""

SHOT_TYPES_V2: dict[str, dict[str, str]] = {
    "scene": {
        "name": "1. Cảnh rộng / Cảnh kể (Scene Shot)",
        "when_to_use": "Giới thiệu bối cảnh, tương phản kích thước, nhân vật làm việc",
        "focal_point": "Một vật lớn hoặc một nhân vật chính (mặt da đào)",
        "video_motion_30_40_30": "Chớp mắt, sáng dần, vật phụ chuyển động nhẹ; máy quay tĩnh (static camera)",
        "prompt_template": (
            "[STYLE LOCKED], [CHARACTER chính: peach skin face, stick figure, dark suit], "
            "[CROWD nếu có: white faces, grey side shadow, repeated oval bodies], "
            "[CHỦ THỂ] is [HÀNH ĐỘNG 1 ĐỘNG TỪ], [BIỂU CẢM], "
            "foreground: [VẬT/NGƯỜI BỊ CẮT VIỀN Ở MÉP KHUNG], "
            "in [BỐI CẢNH, DÃY VẬT LẶP LẠI PHÍA SAU], [CỠ CẢNH], [GÓC MÁY], "
            "one accent colour muted red on [VẬT TIÊU ĐIỂM]. Sharp, clean lines, no clutter."
        ),
    },
    "quote": {
        "name": "2. Trích dẫn / Bong bóng suy nghĩ (Quote / Thought Bubble)",
        "when_to_use": "Lời thoại là câu nói trực tiếp, câu đùa, lời thú nhận, suy nghĩ",
        "focal_point": "Câu chữ viết tay hoặc bong bóng mây trắng viền đen (giữ nguyên câu đầy đủ làm punchline), mặt người nói là thứ hai",
        "video_motion_30_40_30": "0-30% giữ khung, 30-70% chữ/bong bóng hiện dần + miệng người nói mở ra, 70-100% giữ yên để đọc kịp chữ",
        "prompt_template": (
            "[STYLE LOCKED], [CHARACTER chính: peach skin face], [CROWD: white faces, flat worried], "
            "[NGƯỜI NÓI] is [HÀNH ĐỘNG], [BIỂU CẢM KHỚP CÂU NÓI], "
            "large white cloud thought bubble with thick black outline in centre foreground, "
            "with black handwritten text inside: \"[QUOTE EXACT WORDS]\". "
            "The quote is the focal point, speaker's face is second focal point."
        ),
    },
    "crowd": {
        "name": "3. Đám đông / Phòng họp (Crowd / Boardroom Shot)",
        "when_to_use": "Nhiều người cùng họp, làm việc, xem, chờ đợi",
        "focal_point": "Nhân vật chính là mặt da đào duy nhất có chi tiết; đám đông mặt trắng bóng xám làm nền",
        "video_motion_30_40_30": "Nhân vật chính đổi biểu cảm nhẹ, đám đông gần như đứng yên",
        "prompt_template": (
            "[STYLE LOCKED], [MAIN CHARACTER: peach skin face, detailed hair, high contrast in centre], "
            "surrounded by [CROWD: plain white faces, no skin tone, one flat grey side shadow, identical oval bodies], "
            "foreground: silhouettes of heads cut by frame edge, in [BỐI CẢNH PHÒNG HỌP/VĂN PHÒNG]."
        ),
    },
    "diagram": {
        "name": "4. Sơ đồ / Infographic (Diagram Shot)",
        "when_to_use": "Số liệu, thời gian, chi phí, so sánh, quy trình, tăng trưởng",
        "focal_point": "Vật lớn silhouette (3-4 chi tiết trong) + con số/con dấu màu đỏ nhấn (muted red), đọc trái qua phải, trên xuống dưới",
        "video_motion_30_40_30": "Đường biểu đồ đi lên / cột mọc lên / con dấu đóng xuống, giữ yên 30% cuối để đọc số",
        "prompt_template": (
            "[STYLE LOCKED], two flat bands split by a thick black horizontal line, "
            "top band pale blue, bottom band beige, reading order left to right, top to bottom. "
            "Large silhouette icon on left, large muted red handwritten number/stamp on right: \"[NUMBER/TEXT]\". "
            "No character, no room, no crowd. Sharp, clean lines, one idea."
        ),
    },
}

GOLDEN_RULES_V2: list[str] = [
    "1. Khóa trước, gen sau: Dán nguyên văn khối STYLE LOCKED và Character Sheet vào mọi prompt.",
    "2. Người que — Chính da đào (peach skin face), Quần chúng mặt trắng (plain white faces + 1 mảng bóng xám phẳng).",
    "3. Khung hình 3 lớp (Foreground cắt viền mép khung -> Midground nhân vật chính -> Background lặp lại đơn giản).",
    "4. Một màu nhấn đỏ (muted red) mỗi shot dành riêng cho vật tiêu điểm, con số hoặc con dấu.",
    "5. Chữ trích dẫn là Punchline — giữ nguyên câu ngắn đầy đủ trong bong bóng mây trắng hoặc chữ viết tay.",
    "6. Vẽ vật lớn — nhận diện bằng đường viền ngoài (silhouette) chiếm >= 1/3 khung hình, bên trong chỉ 3-4 chi tiết.",
    "7. Tỉ lệ khung hình 16:9 (1280x720) — tuyệt đối không crop làm mất đầu/vai.",
    "8. Timeline video 30-40-30 & Máy đứng yên (static camera): 0-30% mở -> 30-70% 1 chuyển động chậm -> 70-100% giữ yên.",
    "9. Nối shot bằng cắt cứng (hard cut), cho phép gộp nhiều câu kịch bản liên tiếp vào 1 video tuần tự.",
    "10. Ảnh tham khảo làm mỏ neo sáng tạo (Visual Idea Anchor): Khi shot có ảnh tư liệu tham khảo (`reference_image_url` hoặc từ danh sách 10-20 ảnh xuyên suốt script), Agent BẮT BUỘC quan sát chi tiết đặc thù của vật thể/bối cảnh/công nghệ/nhân vật trong ảnh tham khảo để làm ý tưởng sáng tạo cho cảnh quay, sau đó CHUYỂN HÓA (stylize) sang chuẩn phong cách vector Crayon Capital (nét mực đen đậm, người que, 1 màu đỏ nhấn, tuyệt đối không dán ảnh thật) giúp video chân thực và lôi cuốn.",
]


def _skills_root() -> Path:
    repo_skills = Path(__file__).resolve().parents[3] / "skills"
    if repo_skills.exists():
        return repo_skills
    fallback = Path(r"C:\app\work\support-video-edit\.agents\skills")
    return fallback


def get_skill_summary(item_id: str | None = None) -> dict[str, Any]:
    """Compact structured skill specification embedded directly in every TODO response."""
    return {
        "skill_name": "shot-generation-expert-v2 + video-production-expert-v1",
        "version": "2.1.0",
        "mold": "Crayon Capital Vector Mold v2 (with Reference Image Visual Anchor)",
        "style_lock_v2": STYLE_LOCK_V2,
        "golden_rules": GOLDEN_RULES_V2,
        "reference_image_rule": {
            "principle": "Ground each scene with authentic real-world reference photos collected across the script (10-20 reference images).",
            "action": "Inspect reference photo details (car shape, battery layout, headquarters, trading floor, executive appearance) and use as the creative visual anchor for that scene.",
            "fusion_law": "STYLIZATION MANDATE: Never paste raw photos directly into the animation. Always translate real-world reference shapes into clean Crayon Capital 2D vector style (bold black outlines, stick figures with peach skin for protagonist or white faces for crowd, 1 muted red accent, 3-layer composition).",
            "motion_coordination": "Follow the 30-40-30 timeline so viewers instantly recognize the stylized real-world reference subject.",
        },
        "shot_types": SHOT_TYPES_V2,
        "video_timeline_law": {
            "0_to_30_pct": "Establish frame & hold initial pose so viewer registers composition",
            "30_to_70_pct": "Execute exactly ONE slow deliberate motion (or text/bubble reveal for quote/diagram shots)",
            "70_to_100_pct": "Hold final frame steady so viewer can read numbers/quotes before hard cut",
            "camera": "static camera (no shaky pan/zoom)",
        },
        "full_skills_url": f"/api/v1/todos/{item_id}/skills" if item_id else "/api/v1/skills",
        "agent_guide_url": "/api/v1/agent-guide",
    }



def get_full_skill_bundle(item_id: str | None = None) -> dict[str, Any]:
    """Full skill bundle including raw Markdown contents of all skill documents."""
    root = _skills_root()
    files_map: dict[str, str] = {}
    for rel in [
        "shot_generation/SKILL.md",
        "shot_generation/CRAYON_EXPLAINER_MOLD.md",
        "shot_generation/UNIVERSAL_SHOT_FRAMEWORK.md",
        "video_production/SKILL.md",
        "video_production/UNIVERSAL_VIDEO_ALGORITHM.md",
        "video_production/VIDEO_PRODUCTION_SKILL.md",
        "video_production/style_bible_Crayon_Capital.md",
        "video_production/VIDEO_QA_CHECKLIST.md",
    ]:
        p = root / rel
        if p.exists():
            files_map[rel] = p.read_text(encoding="utf-8", errors="replace")

    return {
        **get_skill_summary(item_id),
        "documents": files_map,
    }
