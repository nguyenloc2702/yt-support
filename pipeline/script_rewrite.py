"""
Script rewrite: send transcript to the LLM and get back a "review" style
script split into beats, fitting a user-chosen TARGET DURATION.

Core principle: the LLM NEVER generates timestamps. It only references
existing transcript segment ids via `source_segment_ids`. Timestamp mapping
is done later by the script-led planner using real transcript data.

Output schema (also enforced via structured output JSON schema):
{
  "title": str,
  "tone": str,
  "beats": [
    {"id": int, "text": str, "source_segment_ids": [int], "emotion": str}
  ]
}

Duration model (script-led output timeline):
- User chooses target_duration_seconds (e.g. 60).
- Words-per-second budget (VIETNAM_WPS_RANGE) converts it to a word budget
  which is injected into the LLM prompt.
- Estimated narration duration is computed with estimate_script_duration()
  and stored on the script for the pre-approve duration check.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from core.exceptions import AnalysisError
from core.logging_config import get_logger
from pipeline.llm_client import chat_json

logger = get_logger(__name__)

DEFAULT_TARGET_DURATION_SECONDS = 60.0
DEFAULT_DURATION_TOLERANCE_SECONDS = 2.0

# Vietnamese narration speaking rate budget (words per second).
VIETNAM_WPS_RANGE = (2.4, 3.0)


def word_budget_for_duration(target_seconds: float) -> tuple[int, int]:
    """Return (min_words, max_words) fitting `target_seconds` at Vietnamese pace."""
    lo, hi = VIETNAM_WPS_RANGE
    return int(target_seconds * lo * 0.9), int(target_seconds * hi * 1.05)


def count_script_words(script: Dict[str, Any]) -> int:
    """Total words across all beat texts."""
    return sum(
        len(str(b.get("text") or "").split())
        for b in script.get("beats") or []
    )


def estimate_narration_seconds(script: Dict[str, Any], wps: float | None = None) -> float:
    """Estimate narration seconds from word count at `wps` (default mid-range pace)."""
    rate = wps or (VIETNAM_WPS_RANGE[0] + VIETNAM_WPS_RANGE[1]) / 2
    return count_script_words(script) / max(0.5, rate)


def duration_check(script: Dict[str, Any]) -> Dict[str, Any]:
    """Compare estimated narration duration vs target; returns check fields."""
    target = float(script.get("target_duration_seconds") or DEFAULT_TARGET_DURATION_SECONDS)
    tol = float(script.get("duration_tolerance_seconds") or DEFAULT_DURATION_TOLERANCE_SECONDS)
    est = estimate_narration_seconds(script)
    delta = est - target
    return {
        "target_duration_seconds": target,
        "estimated_duration_seconds": round(est, 1),
        "delta_seconds": round(delta, 1),
        "within_tolerance": abs(delta) <= tol,
    }


SYSTEM_PROMPT = """Bạn là biên kịch voiceover review tiếng Việt phong cách "bựa" \
(Kem Xôi, tooltip, review mem). Nhiệm vụ: biến transcript gốc thành kịch bản \
đọc GIỌNG, buồn cười thật sự, không kể chuyện lan man.

QUY TẮC BẮT BUỘC:
1. KHÔNG BAO GIỜ tự chế thời gian (timestamp). Chỉ tham chiếu đoạn gốc qua \
source_segment_ids (id của transcript segment).
2. Mỗi beat PHẢI tham chiếu ít nhất 1 source_segment_id.
3. MỖI CÂU tối đa 15 chữ. Mỗi beat 3-6 câu ngắn, dồn dập như đang nói với \
bạn thân, KHÔNG dùng văn viết trang trọng.
4. CÔNG THỨC mỗi beat: (a) 1 câu tả sự việc SHOCK bằng từ láy/phóng đại, \
(b) 1-2 câu phản ứng quáy (vd: "Tôi chết lặng", "Anh bạn ơi...", "Ủa???"), \
(c) 1 câu chốt meme/trend (vd: "đỉnh chóp", "xin vía", "thôi xong", \
"tự nhiên chột dạ", "10 điểm không có nhưng").
5. NÓI THẲNG cảm xúc vào chữ: dùng chữ IN HOA cho từ nhấn, dùng "..." và \
"!!" đúng chỗ bất ngờ.
6. emotion PHẢI chọn đúng 1 trong: "hype", "hoang_mang", "bua", "xuc_dong", \
"gian", "thuong_hai" — KHÔNG dùng "neutral".
7. Chỉ dùng thông tin có trong transcript. Không bịa chi tiết mới.
8. Viết tiếng Việt tự nhiên, tiếng lóng mạng được khuyến khích."""

OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_segment_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "emotion": {"type": "string"},
                },
                "required": ["text", "source_segment_ids", "emotion"],
            },
        },
    },
    "required": ["title", "beats"],
}


def _format_transcript(segments: List[Dict[str, Any]], max_chars: int = 24000) -> str:
    """Render transcript segments as numbered lines for the prompt."""
    lines = []
    used = 0
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        line = f"[{seg['id']}] {text}"
        if used + len(line) > max_chars:
            lines.append("[...transcript bị cắt vì quá dài...]")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def rewrite_script(
    transcript: Dict[str, Any],
    tone: str = "bựa bựa, hài hước, bắt trend",
    video_url: str | None = None,
    target_duration_seconds: float = DEFAULT_TARGET_DURATION_SECONDS,
    duration_tolerance_seconds: float = DEFAULT_DURATION_TOLERANCE_SECONDS,
) -> Dict[str, Any]:
    """
    Generate a review-style script from a transcript, fitting `target_duration_seconds`.

    Returns dict with keys: title, tone, beats, status="draft",
    target_duration_seconds, duration_tolerance_seconds.
    Beats contain text + source_segment_ids only (no timestamps).
    """
    segments = transcript.get("segments") or []
    if not segments:
        raise AnalysisError("Transcript rỗng, không thể viết lại script.")

    target = max(10.0, float(target_duration_seconds))
    tol = max(0.5, float(duration_tolerance_seconds))
    min_words, max_words = word_budget_for_duration(target)

    user_prompt = f"""Tông giọng yêu cầu: {tone}

NGÂN SÁCH THỜI LƯỢNG (BẮT BUỘC):
- Kịch bản đọc to sẽ kéo dài khoảng {target:.0f} giây (dung sai ±{tol:.0f} giây).
- Tiếng Việt đọc ~2.4-3.0 chữ/giây. TỔNG số chữ toàn kịch bản phải nằm trong
  khoảng {min_words}-{max_words} chữ. Không viết dài hơn, không viết ngắn hơn nhiều.
- Chia đều ngân sách chữ vào các beat; beat hook và beat chốt có thể ngắn hơn.

TRANSCRIPT (mỗi dòng [id] là một segment gốc):
{_format_transcript(segments)}

Viết kịch bản review gồm nhiều beats theo đúng công thức ở hệ thống prompt. \
Nhớ: mỗi beat cần emotion từ danh sách cho phép, câu ngắn, có quáy, có chốt meme. \
Mỗi beat tham chiếu các segment gốc liên quan qua source_segment_ids. \
Trả về JSON theo schema."""

    data = chat_json(SYSTEM_PROMPT, user_prompt, json_schema=OUTPUT_SCHEMA)

    valid_ids = {int(s["id"]) for s in segments}
    beats: List[Dict[str, Any]] = []
    for i, beat in enumerate(data.get("beats") or []):
        ref_ids = [sid for sid in (beat.get("source_segment_ids") or []) if int(sid) in valid_ids]
        if not ref_ids:
            logger.warning("Beat %d bị bỏ vì không tham chiếu segment hợp lệ", i)
            continue
        beats.append(
            {
                "id": len(beats),
                "text": (beat.get("text") or "").strip(),
                "source_segment_ids": ref_ids,
                "emotion": (beat.get("emotion") or "neutral").strip(),
            }
        )
    if not beats:
        raise AnalysisError("LLM không trả về beat nào tham chiếu transcript hợp lệ.")

    return {
        "title": (data.get("title") or "Review script").strip(),
        "tone": tone,
        "beats": beats,
        "status": "draft",
        "target_duration_seconds": target,
        "duration_tolerance_seconds": tol,
    }


def load_script(project_dir: Path) -> Dict[str, Any] | None:
    """Load scripts/current.json for a project, or None if absent."""
    path = project_dir / "scripts" / "current.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_script(project_dir: Path, script: Dict[str, Any]) -> Path:
    """Persist script atomically to scripts/current.json."""
    script_dir = project_dir / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    path = script_dir / "current.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path
