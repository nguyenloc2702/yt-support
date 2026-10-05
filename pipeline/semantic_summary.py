"""Semantic video summary pipeline (Phase 4).

Consumes filtered transcript + shot/keyframe data and produces a structured
outline (title, chapters, candidate clips with evidence) via the LLM.

Design constraints:
- The LLM only receives compact evidence (chunked transcript + shot metadata),
  never the raw video or a full multi-hour transcript.
- Every returned timestamp must snap to a real shot/transcript boundary.
- On any LLM failure the caller should fall back to transcript highlight mode.
"""

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging_config import get_logger
from pipeline.llm_client import LLMResponseError, chat_json, chat_json_multimodal

logger = get_logger(__name__)

WINDOW_SECONDS = 300.0  # 5-minute windows for map-reduce summarization

OUTLINE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "importance": {"type": "number"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                },
                "required": ["title", "summary", "importance", "start", "end"],
            },
        },
        "candidate_clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "reason": {"type": "string"},
                    "chapter_index": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
                "required": ["start", "end", "reason", "chapter_index", "confidence"],
            },
        },
    },
    "required": ["title", "summary", "chapters", "candidate_clips"],
}


def _chunk_transcript(
    segments: List[Dict[str, Any]], window: float = WINDOW_SECONDS
) -> List[List[Dict[str, Any]]]:
    """Group transcript segments into time windows."""
    chunks: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    window_start = 0.0
    for seg in segments:
        if current and float(seg["start"]) - window_start >= window:
            chunks.append(current)
            current = []
            window_start = float(seg["start"])
        current.append(seg)
    if current:
        chunks.append(current)
    return chunks


def _compact_evidence(
    chunks: List[List[Dict[str, Any]]],
    shots: Optional[List[Dict[str, Any]]],
) -> str:
    """Build a compact text evidence block for the LLM."""
    lines: List[str] = []
    for ci, chunk in enumerate(chunks):
        lines.append(f"[WINDOW {ci:02d}] {chunk[0]['start']:.1f}s - {chunk[-1]['end']:.1f}s")
        for seg in chunk:
            lines.append(f"  ({seg['start']:.1f}-{seg['end']:.1f}) {seg['text']}")
    if shots:
        lines.append("[SHOTS]")
        for s in shots:
            for kf in s.get("keyframes", [])[:1]:
                lines.append(
                    f"  shot@{s['start']:.1f}-{s['end']:.1f}s: {kf.get('caption', '')}"
                )
    return "\n".join(lines)


def _snap(value: float, boundaries: List[float], tolerance: float = 2.0) -> float:
    """Snap a timestamp to the nearest boundary if within tolerance."""
    best = min(boundaries, key=lambda b: abs(b - value), default=value)
    return best if abs(best - value) <= tolerance else value


def build_semantic_summary(
    segments: List[Dict[str, Any]],
    shots: Optional[List[Dict[str, Any]]] = None,
    topic: str = "",
    keywords: Optional[List[str]] = None,
    target_duration: float = 180.0,
    keyframe_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Produce a structured outline + candidate clips. Raises LLMResponseError
    when the LLM cannot produce a valid outline (caller should fall back).
    """
    if not segments:
        raise LLMResponseError("Không có transcript để tóm tắt.")

    chunks = _chunk_transcript(segments)
    evidence = _compact_evidence(chunks, shots)

    topic_line = f"Chủ đề do người dùng cung cấp: {topic}\n" if topic else ""
    keyword_line = f"Từ khóa ưu tiên: {', '.join(keywords)}\n" if keywords else ""

    system = (
        "Bạn là biên tập viên video chuyên nghiệp. Nhiệm vụ: hiểu nội dung video "
        "từ transcript và mô tả hình ảnh, sau đó tạo outline theo chapter và đề xuất "
        "các đoạn clip quan trọng nhất cho video tóm tắt. Chỉ dùng timestamp có trong "
        "evidence. Trả về JSON đúng schema."
    )
    user = (
        f"{topic_line}{keyword_line}"
        f"Thời lượng mục tiêu của video tóm tắt: {target_duration:.0f} giây.\n\n"
        f"EVIDENCE:\n{evidence}\n\n"
        "Yêu cầu:\n"
        "1. title + summary cho toàn bộ video.\n"
        "2. chapters: các phần nội dung chính, có importance 0-1.\n"
        "3. candidate_clips: các đoạn (start/end tính bằng giây, khớp với khoảng "
        "transcript) quan trọng nhất, tổng thời lượng xấp xỉ thời lượng mục tiêu, "
        "mỗi clip có reason và confidence 0-1, không trùng lặp ý."
    )

    # If keyframes with image files are available, send a sample to the VLM.
    images: List[bytes] = []
    if keyframe_dir is not None and shots:
        for s in shots:
            for kf in s.get("keyframes", [])[:1]:
                p = Path(kf["path"])
                if p.exists() and len(images) < 12:
                    images.append(p.read_bytes())
                    break  # one frame per shot

    if images:
        result = chat_json_multimodal(
            system, user, images=images, json_schema=OUTLINE_SCHEMA, temperature=0.2
        )
    else:
        result = chat_json(system, user, json_schema=OUTLINE_SCHEMA, temperature=0.2)

    return _validate_and_snap(result, segments, shots)


def _validate_and_snap(
    result: Dict[str, Any],
    segments: List[Dict[str, Any]],
    shots: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Ensure structure is sane and timestamps snap to real boundaries."""
    boundaries = sorted(
        [float(s["start"]) for s in segments]
        + [float(s["end"]) for s in segments]
        + ([float(s["start"]) for s in shots] if shots else [])
    )
    max_end = max((float(s["end"]) for s in segments), default=0.0)

    for clip in result.get("candidate_clips", []):
        start = max(_snap(float(clip.get("start", 0)), boundaries), 0.0)
        end = _snap(float(clip.get("end", 0)), boundaries)
        end = min(max(end, start + 1.0), max_end)
        clip["start"], clip["end"] = round(start, 3), round(end, 3)
        clip["reason"] = str(clip.get("reason", ""))[:300]
        clip["confidence"] = max(0.0, min(1.0, float(clip.get("confidence", 0.5))))
        clip["chapter_index"] = int(clip.get("chapter_index", -1))

    for ch in result.get("chapters", []):
        ch["importance"] = max(0.0, min(1.0, float(ch.get("importance", 0.5))))
        ch["start"] = float(ch.get("start", 0))
        ch["end"] = float(ch.get("end", 0))

    result["title"] = str(result.get("title", ""))[:200]
    result["summary"] = str(result.get("summary", ""))[:2000]
    return result
