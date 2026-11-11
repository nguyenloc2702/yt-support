"""Prompts + JSON schema cho 2 mode Short (Phụ lục G.1/G.5).

Kịch bản trả về shape (đồng bộ appendix + frontend):
{title, hook, beats[], closing, hashtags{tiktok,facebook,youtube}, description,
 source_note}
Dãy thoại = [hook] + beats + [closing]. Ngân sách từ tính trên toàn dãy thoại.
"""
from __future__ import annotations

import re

from ..config import SHORT_CONFIG

MODES = ("news", "decode")

_MODE_NAME_VI = {"news": "Tin nóng", "decode": "Giải mã tiền bạc"}

# Chủ đề né cho mảng Tin nóng (bắt buộc theo G.1 — an toàn nền tảng)
_BANNED_TOPIC_NOTE = (
    "KHÔNG chọn và KHÔNG tự chế nội dung về: chính trị, quan hệ ngoại giao, "
    "y tế/cơ thể người, tai nạn máu me, thiên tai đang gây thương vong, "
    "thông tin chưa kiểm chứng về cá nhân cụ thể. Nếu bài báo gốc rơi vào nhóm này, "
    "từ chối bằng trường \"rejected\"."
)

_SYSTEM_NEWS = """Bạn là biên tập viên video tin nhanh dạng dọc (TikTok/Reels/Shorts) cho một kênh tiền bạc & kinh doanh tiếng Việt.

NHIỆM VỤ: từ tin vắn người dùng dán vào, viết kịch bản_short 25-40 giây theo công thức:
1. hook (0-3s): câu mở giật gân nhất — dẫn số liệu thiệt hại/con số gây sốc hoặc câu hỏi đánh thẳng tò mò. KHÔNG mở đầu kiểu "Theo thông tin..."
2. beats (3 beat): diễn biến cốt lõi, câu ngắn dồn dập, cắt hết râu ria.
3. closing: 1 câu hỏi mở hai chiều để kích bình luận (vd: "Theo bạn đây là bước đi thiên tài hay tự hủy?").

QUY TẮC:
- Ngôn ngữ: tiếng Việt tự nhiên, nói được (không văn viết khô).
- Giữ đúng số liệu gốc trong tin; KHÔNG bịa số. Con số phải xuất hiện nguyên văn.
- {banned_topics}
- Nếu tin hợp lệ, KHÔNG điền trường "rejected".

Trả JSON: {{"title": "...", "hook": "...", "beats": ["...", "...", "..."], "closing": "...",
"hashtags": {{"tiktok": [...tối đa 6...], "facebook": [...tối đa 6...], "youtube": [...tối đa 5...]}},
"description": "1-2 câu mô tả đăng bài", "source_note": "Nguồn: <tên nguồn tin/nguồn trong tin>"}}"""

_SYSTEM_DECODE = """Bạn là nhà phân tích tài chính cá nhân viết kịch bản video dọc 40-60 giây tiếng Việt cho kênh "Giải mã tiền bạc".

NHIỆM VỤ: từ chủ đề người dùng nhập, viết kịch bản theo công thức:
1. hook: mở bằng NGHỊ LÝ ngược đời hoặc con số làm người xem phải dừng (vd: "Càng tiết kiệm, càng nghèo — và đây là lý do").
2. beats (2-4 beat): giải thích cơ chế ngầm bằng ví dụ đời thường (cà phê, chợ, điện thoại...). Mỗi beat 1 ý, ngắn, có nhịp.
3. closing: cú chốt bất ngờ NỐI NGƯỢC lại câu hook (loop) khiến người xem muốn xem lại.

QUY TẮC:
- Tiếng Việt nói tự nhiên. Con số ví dụ phải hợp lý, KHÔNG bịa tỷ lệ phạm pháp.
- {banned_topics}

Trả JSON: {{"title": "...", "hook": "...", "beats": ["...", "...", "..."], "closing": "...",
"hashtags": {{"tiktok": [...], "facebook": [...], "youtube": [...]}},
"description": "...", "source_note": ""}}"""

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "beats": {"type": "array", "items": {"type": "string"}},
        "closing": {"type": "string"},
        "hashtags": {
            "type": "object",
            "properties": {
                "tiktok": {"type": "array", "items": {"type": "string"}},
                "facebook": {"type": "array", "items": {"type": "string"}},
                "youtube": {"type": "array", "items": {"type": "string"}},
            },
        },
        "description": {"type": "string"},
        "source_note": {"type": "string"},
        "rejected": {"type": "string"},
    },
    "required": ["title", "hook", "beats", "closing"],
}


def system_prompt(mode: str) -> str:
    _check_mode(mode)
    tpl = _SYSTEM_NEWS if mode == "news" else _SYSTEM_DECODE
    return tpl.replace("{banned_topics}", _BANNED_TOPIC_NOTE)


def user_prompt(mode: str, source_text: str, target_seconds: float | None,
                source_note: str | None) -> str:
    cfg = SHORT_CONFIG["modes"][mode]
    lo, hi = cfg["min_words"], cfg["max_words"]
    target = target_seconds or cfg["target_seconds"]
    note = f"\nNGUỒN TIN (ghi trên video): {source_note.strip()}" if source_note else ""
    return (
        f"CHỦ ĐỀ/TIN GỐC (mode {_MODE_NAME_VI[mode]}, mục tiêu ~{int(target)}s, "
        f"ngân sách {lo}-{hi} từ toàn kịch bản):\n"
        f"\"\"\"\n{source_text.strip()}\n\"\"\"{note}\n"
        f"Viết kịch bản JSON theo yêu cầu hệ thống."
    )


def schema() -> dict:
    return _JSON_SCHEMA


def narration_texts(script: dict) -> list[str]:
    """Dãy thoại theo thứ tự phát: hook → beats → closing."""
    texts = [str(script["hook"]).strip()]
    texts += [str(b).strip() for b in script["beats"]]
    texts.append(str(script["closing"]).strip())
    return [t for t in texts if t]


def count_words(script: dict) -> int:
    return sum(len(t.split()) for t in narration_texts(script))


def validate_script(mode: str, script: dict, source_note_fallback: str = "") -> dict:
    """Validate tiếng người (fail-closed). Trả script đã chuẩn hóa + word_count."""
    _check_mode(mode)
    if script.get("rejected"):
        raise ValueError(f"Kịch bản bị từ chối vì chủ đề nhạy cảm: {script['rejected']}")
    for field in ("title", "hook", "closing"):
        if not str(script.get(field) or "").strip():
            raise ValueError(f"Kịch bản thiếu trường bắt buộc '{field}' từ LLM.")
    beats = [str(b).strip() for b in (script.get("beats") or []) if str(b).strip()]
    if not beats:
        raise ValueError("Kịch bản không có beat nào từ LLM.")
    if len(beats) > 4:
        beats = beats[:4]

    cfg = SHORT_CONFIG["modes"][mode]
    lo, hi = int(cfg["min_words"]), int(cfg["max_words"])
    clean = {
        "title": str(script["title"]).strip()[:200],
        "hook": str(script["hook"]).strip(),
        "beats": beats,
        "closing": str(script["closing"]).strip(),
        "hashtags": _clean_hashtags(script.get("hashtags") or {}),
        "description": str(script.get("description") or "").strip(),
        "source_note": str(script.get("source_note") or "").strip() or source_note_fallback,
    }
    wc = count_words(clean)
    if not (lo <= wc <= hi):
        raise ValueError(
            f"Kịch bản {wc} từ, lệch ngân sách {lo}-{hi} từ cho mode {_MODE_NAME_VI[mode]}. "
            f"Bấm 'Đổi kịch bản' để gen lại."
        )
    clean["word_count"] = wc
    return clean


def _clean_hashtags(tags: dict) -> dict:
    out = {}
    for platform, limit in (("tiktok", 6), ("facebook", 6), ("youtube", 5)):
        raw = tags.get(platform) or []
        out[platform] = [
            "#" + re.sub(r"[^0-9a-zA-Zàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ_]", "", str(t).lstrip("#"), flags=re.IGNORECASE)
            for t in raw
        ][:limit]
        out[platform] = [t for t in out[platform] if len(t) > 1]
    return out


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode không hợp lệ: {mode} (chấp nhận: {', '.join(MODES)})")
