"""Build a user-facing report for the output timeline.

The report deliberately uses output timestamps first. Source timestamps are kept
beside them so a user can trace a selected output moment back to the source clip.
This deterministic implementation leverages shot boundaries, keyframes metadata,
transcript, and silence evidence to construct detailed visual and audio descriptions.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _overlap(start: float, end: float, other_start: float, other_end: float) -> bool:
    return start < other_end and end > other_start


def _format_audio_state(
    transcript: list[dict[str, Any]],
    silences: list[dict[str, Any]],
    start: float,
    end: float,
) -> str:
    has_speech = any(
        _overlap(start, end, _as_float(item.get("start")), _as_float(item.get("end")))
        for item in transcript
    )
    has_silence = any(
        _overlap(start, end, _as_float(item.get("start")), _as_float(item.get("end")))
        for item in silences
    )
    if has_speech:
        return "speech_detected"
    if has_silence:
        return "silence"
    return "noise_or_music_or_unknown"


def _clip_source_ranges(clip: dict[str, Any]) -> list[tuple[float, float]]:
    """Return source ranges, supporting both current and future clip schemas."""
    ranges = clip.get("source_ranges")
    if isinstance(ranges, list):
        result = []
        for item in ranges:
            if isinstance(item, dict):
                result.append((_as_float(item.get("start")), _as_float(item.get("end"))))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                result.append((_as_float(item[0]), _as_float(item[1])))
        if result:
            return result
    return [(_as_float(clip.get("source_start")), _as_float(clip.get("source_end")))]


def _transcript_for_range(
    transcript: list[dict[str, Any]], start: float, end: float
) -> list[dict[str, Any]]:
    return [
        item
        for item in transcript
        if _overlap(
            start,
            end,
            _as_float(item.get("start")),
            _as_float(item.get("end")),
        )
    ]


def _normalize_item(item: Any, fallback_id_prefix: str = "item") -> dict[str, Any]:
    """Normalize scene or shot data from dict or Pydantic model into standard float seconds."""
    if hasattr(item, "model_dump"):
        data = item.model_dump()
    elif isinstance(item, dict):
        data = item
    else:
        return {}

    # Handle millisecond vs second keys
    if "start_ms" in data or "end_ms" in data:
        start = _as_float(data.get("start_ms")) / 1000.0
        end = _as_float(data.get("end_ms")) / 1000.0
    else:
        start = _as_float(data.get("start"))
        end = _as_float(data.get("end"))

    item_id = str(
        data.get("shot_id")
        or data.get("scene_id")
        or data.get("id")
        or f"{fallback_id_prefix}_{int(start)}"
    )

    return {
        "id": item_id,
        "start": start,
        "end": end,
        "description": str(data.get("description") or "").strip(),
        "vlm_description": str(data.get("vlm_description") or "").strip(),
        "vlm_subject": str(data.get("vlm_subject") or "").strip(),
        "vlm_action": str(data.get("vlm_action") or "").strip(),
        "vlm_context": str(data.get("vlm_context") or "").strip(),
        "vlm_success": bool(data.get("vlm_success")),
        "keyframes": data.get("keyframes") or [],
        "confidence": _as_float(data.get("confidence"), 1.0),
    }


def _generate_visual_description(
    clip: dict[str, Any],
    matched_scenes: list[dict[str, Any]],
    matched_shots: list[dict[str, Any]],
    speech_text: str,
    audio_state: str,
    duration: float,
) -> str:
    """Generate a rich description of visual and action elements with VLM priority."""
    # 1. Direct explicit description on clip
    explicit_desc = clip.get("vlm_description") or clip.get("visual_description") or clip.get("description")
    if explicit_desc:
        return str(explicit_desc).strip()

    # 2. VLM descriptions from matched shots (AI Vision)
    vlm_descs = [s["vlm_description"] for s in matched_shots if s.get("vlm_description")]
    if vlm_descs:
        unique_vlm = list(dict.fromkeys(vlm_descs))
        return " | ".join(unique_vlm)

    # 3. Traditional shot descriptions
    shot_descs = [s["description"] for s in matched_shots if s.get("description")]
    if shot_descs:
        return " | ".join(dict.fromkeys(shot_descs))

    parts: list[str] = []

    # 1. Shot / Scene classification
    if matched_shots:
        shot_ids = [s["id"] for s in matched_shots if s.get("id")]
        parts.append(f"Cảnh/Shot: {', '.join(shot_ids[:3])}")
    elif matched_scenes:
        scene_ids = [s["id"] for s in matched_scenes if s.get("id")]
        parts.append(f"Cảnh: #{', #'.join(scene_ids[:3])}")

    # 2. Camera pace & shot duration
    if duration <= 1.5:
        pace = "nhịp cắt nhanh / chuyển cảnh ngắn"
    elif duration <= 4.0:
        pace = "cận cảnh / trung cảnh hành động"
    elif duration <= 8.0:
        pace = "trung cảnh / toàn cảnh tiêu chuẩn"
    else:
        pace = "toàn cảnh dài / cú máy tĩnh duy trì"
    parts.append(f"nhịp độ: {pace} ({duration:.1f}s)")

    # 3. Keyframes note if available
    all_kf = []
    for s in matched_shots:
        for kf in s.get("keyframes", []):
            if isinstance(kf, dict) and "time" in kf:
                all_kf.append(f"{_as_float(kf['time']):.1f}s")
    if all_kf:
        parts.append(f"keyframe mốc: {', '.join(all_kf[:3])}")

    # 4. Action and subject context
    if speech_text:
        snippet = speech_text[:60] + ("..." if len(speech_text) > 60 else "")
        parts.append(f"chủ thể phát biểu/thuyết minh: \"{snippet}\"")
    elif audio_state == "silence":
        parts.append("cảnh quay nền tĩnh, không có âm thanh hội thoại")
    else:
        parts.append("cảnh hành động/tư liệu kèm âm thanh nền hiện trường")

    return " | ".join(parts)


def build_timeline_report(
    timeline: dict[str, Any],
    transcript: list[dict[str, Any]] | None = None,
    scenes: list[dict[str, Any]] | None = None,
    silences: list[dict[str, Any]] | None = None,
    shots: list[Any] | None = None,
) -> dict[str, Any]:
    """Create timestamped report rows for an output timeline.

    A row is created per clip and split at transcript & scene boundaries.
    Enriched with deterministic visual, speech, and audio evidence.
    """
    transcript = transcript or []
    silences = silences or []
    norm_scenes = [_normalize_item(s, "scene") for s in (scenes or [])]
    norm_shots = [_normalize_item(s, "shot") for s in (shots or [])]

    rows: list[dict[str, Any]] = []
    output_cursor = 0.0

    for clip_index, clip in enumerate(
        item for item in timeline.get("clips", []) if item.get("enabled", True)
    ):
        source_start = _as_float(clip.get("source_start"))
        source_end = _as_float(clip.get("source_end"))
        source_duration = max(0.0, source_end - source_start)
        output_start = _as_float(clip.get("output_start"), output_cursor)
        output_duration = _as_float(clip.get("duration"), source_duration)
        if output_duration <= 0:
            output_duration = source_duration
        output_end = _as_float(clip.get("output_end"), output_start + output_duration)

        local_transcript = _transcript_for_range(transcript, source_start, source_end)
        local_scenes = [
            sc for sc in norm_scenes
            if _overlap(source_start, source_end, sc["start"], sc["end"])
        ]
        local_shots = [
            sh for sh in norm_shots
            if _overlap(source_start, source_end, sh["start"], sh["end"])
        ]

        # Gather split boundaries
        boundaries = [source_start, source_end]
        for item in local_transcript:
            boundaries.append(max(source_start, min(source_end, _as_float(item.get("start")))))
            boundaries.append(max(source_start, min(source_end, _as_float(item.get("end")))))
        for sc in local_scenes:
            boundaries.append(max(source_start, min(source_end, sc["start"])))
            boundaries.append(max(source_start, min(source_end, sc["end"])))
        for sh in local_shots:
            boundaries.append(max(source_start, min(source_end, sh["start"])))
            boundaries.append(max(source_start, min(source_end, sh["end"])))

        boundaries = sorted({round(b, 4) for b in boundaries})
        if len(boundaries) < 2:
            boundaries = [source_start, source_end]

        for part_start, part_end in pairwise(boundaries):
            part_len = part_end - part_start
            if part_len <= 0.001:
                continue

            source_ratio_start = (part_start - source_start) / max(source_duration, 0.001)
            source_ratio_end = (part_end - source_start) / max(source_duration, 0.001)
            row_output_start = output_start + source_ratio_start * output_duration
            row_output_end = output_start + source_ratio_end * output_duration
            row_transcript = _transcript_for_range(transcript, part_start, part_end)

            matched_scenes = [
                s for s in local_scenes
                if _overlap(part_start, part_end, s["start"], s["end"])
            ]
            matched_shots = [
                s for s in local_shots
                if _overlap(part_start, part_end, s["start"], s["end"])
            ]

            scene_ids = [s["id"] for s in (matched_shots or matched_scenes)]
            speech_text = " ".join(
                str(item.get("text") or "").strip() for item in row_transcript
            ).strip()

            audio_state = _format_audio_state(transcript, silences, part_start, part_end)

            # Determine confidence
            has_speech = bool(speech_text)
            has_vlm = any(bool(s.get("vlm_description")) for s in matched_shots) or bool(clip.get("vlm_description"))
            has_visual_evidence = bool(matched_scenes or matched_shots)

            if (has_speech and (has_vlm or has_visual_evidence)) or (has_vlm and has_visual_evidence):
                confidence = "high"
            elif has_vlm or has_speech or has_visual_evidence:
                confidence = "medium"
            else:
                confidence = "low"

            visual_desc = _generate_visual_description(
                clip=clip,
                matched_scenes=matched_scenes,
                matched_shots=matched_shots,
                speech_text=speech_text,
                audio_state=audio_state,
                duration=part_len,
            )

            rows.append(
                {
                    "row_id": f"output_{len(rows):04d}",
                    "output_start": round(row_output_start, 3),
                    "output_end": round(row_output_end, 3),
                    "source_start": round(part_start, 3),
                    "source_end": round(part_end, 3),
                    "clip_index": clip_index,
                    "scene_ids": scene_ids,
                    "visual_description": visual_desc,
                    "transcript": speech_text,
                    "has_speech": has_speech,
                    "has_vlm": has_vlm,
                    "audio_state": audio_state,
                    "confidence": confidence,
                }
            )

        output_cursor = max(output_cursor, output_end)

    return {
        "schema_version": 1,
        "timeline_version": timeline.get("version", 1),
        "output_duration_seconds": round(
            _as_float(timeline.get("actual_duration"), output_cursor), 3
        ),
        "rows": rows,
        "notes": [
            "Timestamp output là mốc chính để đối chiếu script.",
            "Mô tả hình ảnh kết hợp evidence cảnh/shot/nhịp độ/keyframe và thoại theo phương thức tất định.",
        ],
    }
