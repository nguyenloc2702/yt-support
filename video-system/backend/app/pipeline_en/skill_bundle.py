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
Hybrid cinematic editorial illustration (Stick-Hero in Epic Cinematic World).
Environment: ultra-detailed, atmospheric semi-realistic 3D / cinema-grade environment
with dramatic volumetric lighting, realistic textures (wet steel, ocean swells,
deep space curvature, brushed metal, glowing glass), shallow depth of field,
and rich mood lighting.
One focal accent colour per shot (crimson red, glowing amber, or electric cyan)
to anchor the viewer's eye cleanly against the atmospheric backdrop.
Characters: clean minimalist 2D stick-figure characters (smooth peach-skin round
head with bold dark outline, small expressive dot eyes, curved eyebrows,
thin single-line stick arms and legs, small plain blob hands) keeping ONLY 2-3
signature recognizable traits per key figure (distinctive hairstyle, jacket/suit
colour, or glasses). Crowd characters use plain white round heads with minimal traits.
Integration law: characters MUST receive subtle ambient rim-lighting on hair/edges
and cast soft grounded shadows/reflections onto the floor so they feel naturally
inside the epic cinematic world (never flat pasted stickers).
Macro-Shot pacing: group 2-4 sentences into 12-18s macro-scenes with ONE slow
continuous cinematic camera movement (slow push-in, gentle orbital pan, or tilt).
Aspect ratio: 16:9, unless the format requires 9:16.
Negative: NO flat empty 2D white-void backgrounds, NO childish crayon/doodle texture,
NO distorted hands with fingers, NO photorealistic human anatomy on stick characters,
NO garbled or unreadable text, NO cluttered HUD chaos, NO crowded bottom 20% subtitle zone."""

SHOT_TYPES_V2: dict[str, dict[str, str]] = {
    "scene": {
        "name": "1. Đại cảnh điện ảnh / Cảnh kể (Epic Macro-Scene)",
        "when_to_use": "Thiết lập quy mô, độ hoành tráng, tương phản nhân vật que nhỏ bé trước đại cảnh khổng lồ (12-18s)",
        "focal_point": "Đại cảnh/công trình tả thực hoành tráng + nhân vật que đặc trưng có rim-light và bóng đổ tự nhiên",
        "video_motion_30_40_30": "Slow continuous push-in / gentle orbital pan; nhân vật đổi biểu cảm nhẹ hoặc chỉ tay; môi trường có khói/sóng/ánh sáng chuyển động tự nhiên",
        "prompt_template": (
            "[STYLE LOCKED], [CAMERA & LENS: e.g. 24mm low-angle wide shot], "
            "[EPIC ENVIRONMENT: rich semi-realistic 3D scene with volumetric lighting, atmospheric depth, textures], "
            "[CHARACTER: minimalist peach-skin stick figure with ONLY signature hairstyle + outfit, catching ambient rim-light and casting floor shadow], "
            "one glowing focal accent on [TIÊU ĐIỂM], slow continuous cinematic camera push-in, zero garbled text."
        ),
    },
    "quote": {
        "name": "2. Trích dẫn / Câu hỏi chốt (Quote / Thought Bubble)",
        "when_to_use": "Câu nói trực tiếp, nghịch lý gây sốc hoặc câu hỏi mở chốt nhịp",
        "focal_point": "Câu chữ punchline rõ nét hoặc bong bóng suy nghĩ sạch sẽ đặt trên nền đại cảnh có độ sâu",
        "video_motion_30_40_30": "0-30% giữ khung đại cảnh, 30-70% chữ/bong bóng hiện dần + nhân vật đổi biểu cảm, 70-100% giữ yên để đọc kịp",
        "prompt_template": (
            "[STYLE LOCKED], close-medium cinematic shot of [CHARACTER: peach-skin stick figure with signature hair & jacket] "
            "inside atmospheric [BỐI CẢNH SÂU CÓ BOKEH], crisp clean speech/thought callout with exact text: \"[QUOTE EXACT WORDS]\", "
            "soft rim lighting matching background."
        ),
    },
    "crowd": {
        "name": "3. Đám đông / Phòng họp (Crowd / Command Center)",
        "when_to_use": "Phòng điều khiển, sàn giao dịch, đám đông kỹ sư hay hội trường lớn",
        "focal_point": "Nhân vật chính mặt da đào nổi bật dưới ánh sáng chính giữa đám đông mặt trắng tối giản trong đại cảnh phòng họp/trung tâm công nghệ",
        "video_motion_30_40_30": "Slow dolly pan nhẹ; màn hình/đèn nền nhấp nháy nhẹ; nhân vật chính nổi bật",
        "prompt_template": (
            "[STYLE LOCKED], atmospheric wide shot of [HỘI TRƯỜNG / MISSION CONTROL TẢ THỰC], "
            "[MAIN CHARACTER: peach-skin stick figure with signature hair/outfit under dramatic spotlight] "
            "surrounded by minimalist white-head stick-figure crowd, volumetric light beams from screens."
        ),
    },
    "diagram": {
        "name": "4. Sơ đồ 3D / Infographic trên nền thật (3D Diorama Diagram)",
        "when_to_use": "Số liệu, quỹ đạo vệ tinh, khoảng cách, cơ chế vật lý, so sánh quy mô",
        "focal_point": "Mô hình 3D chi tiết cao (Trái Đất, vệ tinh, chip, tàu) kết hợp đường vector phát sáng & 1 con số/nhãn lớn nổi bật",
        "video_motion_30_40_30": "Đường quỹ đạo/tia sáng chạy mượt từ A tới B; số liệu hiện lên dứt khoát; giữ yên 30% cuối để đọc",
        "prompt_template": (
            "[STYLE LOCKED], dramatic 3D editorial visualization over [MÔ HÌNH TẢ THỰC: e.g. rim-lit Earth from orbit / cutaway mechanical hardware], "
            "clean glowing vector trajectory lines and ONE large crisp focal readout: \"[NUMBER/LABEL]\". "
            "Volumetric space/studio lighting, deep contrast, zero garbled text."
        ),
    },
}

GOLDEN_RULES_V2: list[str] = [
    "1. Stick-Hero in Epic World (v3): Nhân vật giữ form que tối giản (chỉ giữ 2-3 nét đặc trưng như kiểu tóc, áo khoác, kính), nhưng đặt trong ĐẠI CẢNH 3D/tả thực hoành tráng có chiều sâu, vật liệu thật và ánh sáng điện ảnh.",
    "2. Quy tắc hòa nhập ánh sáng: Nhân vật que BẮT BUỘC có viền sáng môi trường (rim-light) và bóng đổ/phản chiếu xuống mặt đất để không bị cảm giác dán sticker.",
    "3. Macro-Shot 12–18 giây: Gom 2–4 câu thoại chung một ý lớn thành 1 Macro-Scene (~35–45 từ/shot, ~30–38 cảnh cho video 8–10 phút) để dễ gen video và giữ nhịp xem mượt mà.",
    "4. Công thức 6 ô cho mỗi prompt: Camera & Lens -> Hero Subject & Material -> Visual Metaphor/Overlay -> Lighting & 1 Accent -> Slow Continuous Camera Motion -> Negative/Clean bottom 20%.",
    "5. Một màu nhấn chủ đạo (cam lửa / đỏ / xanh cyan) mỗi cảnh để dẫn mắt người xem ngay lập tức.",
    "6. Tỉ lệ khung hình 16:9 (1280x720), giữ vùng 20% đáy khung thoáng sạch cho phụ đề.",
    "7. Ảnh tham khảo làm mỏ neo chân thực: Quan sát chi tiết đặc thù từ `reference_images` (dáng tên lửa, chảo Starlink, boong tàu) để dựng đúng bối cảnh 3D tả thực hoành tráng, vẫn đặt nhân vật que StickSto làm người dẫn chuyện.",
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
        "skill_name": "shot-generation-expert-v3 + video-production-expert-v1",
        "version": "3.0.0",
        "mold": "Stick-Hero in Epic Cinematic World (v3 Hybrid Mold + 12-18s Macro-Shots)",
        "style_lock_v2": STYLE_LOCK_V2,
        "style_lock_v3": STYLE_LOCK_V2,
        "golden_rules": GOLDEN_RULES_V2,
        "reference_image_rule": {
            "principle": "Ground each macro-scene with authentic real-world reference photos collected across the script (10-20 reference images).",
            "action": "Inspect reference photo details (rocket body, Starlink dish, carrier deck, hardware, lighting) and build an epic, detailed semi-realistic 3D environment around them.",
            "fusion_law": "HYBRID STICK-HERO MANDATE: Keep environments epic, atmospheric, and richly detailed (volumetric lighting, realistic materials, depth of field) while keeping foreground narrators/characters as clean minimalist stick figures with ONLY 2-3 signature traits (hairstyle, jacket/suit, glasses) catching rim-light and casting floor shadows.",
            "motion_coordination": "Group 2-4 sentences into 12-18s macro-shots with ONE smooth continuous cinematic camera movement (slow push-in, orbital pan, or tilt).",
        },
        "shot_types": SHOT_TYPES_V2,
        "video_timeline_law": {
            "macro_shot_target_sec": "12-18s per macro-scene (35-45 words)",
            "0_to_30_pct": "Establish epic wide composition & rim-lit stick-hero stance",
            "30_to_70_pct": "Execute ONE slow continuous cinematic camera push-in/pan + primary subject action or graphic progression",
            "70_to_100_pct": "Hold/settle cleanly so viewer absorbs scale or reads focal numbers before cut",
            "camera": "Slow smooth cinematic push-in / orbital pan / tilt (no frantic cuts)",
        },
        "full_skills_url": f"/api/v1/todos/{item_id}/skills" if item_id else "/api/v1/skills",
        "agent_guide_url": "/api/v1/agent-guide",
    }



def get_full_skill_bundle(item_id: str | None = None) -> dict[str, Any]:
    """Full skill bundle including raw Markdown contents of v3-aligned skill documents."""
    root = _skills_root()
    files_map: dict[str, str] = {}
    for rel in [
        "shot_generation/SKILL.md",
        "video_production/SKILL.md",
        "video_production/UNIVERSAL_VIDEO_ALGORITHM.md",
        "video_production/VIDEO_QA_CHECKLIST.md",
    ]:
        p = root / rel
        if p.exists():
            files_map[rel] = p.read_text(encoding="utf-8", errors="replace")

    return {
        **get_skill_summary(item_id),
        "documents": files_map,
    }
