"""Deterministic first-pass matching of a user script to timeline evidence."""

from __future__ import annotations

import re
from typing import Any

_WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
_SPEECH_CUES_RE = re.compile(
    r"\b(nói|thoại|giới thiệu|chia sẻ|voice|say|speak|lời dẫn|thuyết minh|kể|hỏi|trả lời)\b",
    re.IGNORECASE | re.UNICODE,
)


def _words(text: str) -> set[str]:
    return {word.casefold() for word in _WORD_RE.findall(text or "") if len(word) > 1}


def _split_script(text: str) -> list[str]:
    if not text:
        return []
    chunks = [chunk.strip() for chunk in re.split(r"\n+|(?<=[.!?])\s+", text or "")]
    return [chunk for chunk in chunks if chunk]


def _evidence_for_segment(segment: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    script_words = _words(segment)
    best_row: dict[str, Any] | None = None
    best_score = 0.0

    for row in rows:
        evidence_text = " ".join(
            [
                str(row.get("transcript") or ""),
                str(row.get("visual_description") or ""),
            ]
        )
        evidence_words = _words(evidence_text)
        if not script_words:
            score = 0.0
        else:
            score = len(script_words & evidence_words) / max(1, len(script_words))

        if score > best_score or best_row is None:
            best_score = score
            best_row = row

    if best_row is None:
        return {
            "start": 0.0,
            "end": 0.0,
            "status": "miss",
            "severity": "high",
            "message": "Không có đoạn evidence để đối chiếu (timeline report rỗng).",
            "evidence": "timeline report trống",
        }

    start = float(best_row.get("output_start") or 0)
    end = float(best_row.get("output_end") or 0)
    audio_state = best_row.get("audio_state", "unknown")
    has_speech = bool(best_row.get("has_speech"))

    if best_score >= 0.45:
        status = "matched"
        severity = "low"
        message = f"từ {start:.1f}s đến {end:.1f}s: Nội dung script có evidence khớp tốt trong timeline."
    elif best_score >= 0.15:
        status = "partial"
        severity = "medium"
        message = f"từ {start:.1f}s đến {end:.1f}s: Chỉ khớp một phần; cần kiểm tra lại hình ảnh hoặc transcript."
    else:
        status = "miss"
        severity = "high"
        message = f"từ {start:.1f}s đến {end:.1f}s: Không tìm thấy evidence đủ rõ cho nội dung script."

    # Audio missing detection: script requires speech / narration cue
    requires_speech = bool(_SPEECH_CUES_RE.search(segment))
    if requires_speech and not has_speech:
        status = "miss"
        severity = "high"
        if audio_state == "silence":
            message = f"từ {start:.1f}s đến {end:.1f}s đang miss audio — script yêu cầu thoại nhưng vùng này chỉ có silence"
        else:
            message = f"từ {start:.1f}s đến {end:.1f}s đang thiếu thoại — script yêu cầu thoại nhưng vùng này chỉ có âm thanh nền ({audio_state})"

    return {
        "start": start,
        "end": end,
        "status": status,
        "severity": severity,
        "message": message,
        "evidence": (
            f"audio={audio_state}; transcript="
            f"{best_row.get('transcript') or '(không có transcript)'}; "
            f"visual={best_row.get('visual_description') or '(trống)'}"
        ),
    }


def match_script_to_report(script_text: str, report: dict[str, Any]) -> dict[str, Any]:
    """Return timestamped script issues without modifying the supplied script."""
    rows = [row for row in (report.get("rows") or []) if isinstance(row, dict)]
    segments = _split_script(script_text)

    issues = []
    for segment in segments:
        result = _evidence_for_segment(segment, rows)
        issues.append({"script_text": segment, **result})

    summary = {
        "total": len(issues),
        "matched": sum(item["status"] == "matched" for item in issues),
        "partial": sum(item["status"] == "partial" for item in issues),
        "miss": sum(item["status"] == "miss" for item in issues),
    }
    return {
        "schema_version": 1,
        "summary": summary,
        "issues": issues,
        "notes": [
            "Đây là first-pass lexical/evidence matching, không phải kết luận ngữ nghĩa tuyệt đối.",
            "Các issue HIGH cần người dùng xem lại video tại đúng timestamp.",
        ],
    }
