"""VLM keyframe captioning pipeline.

Extracts semantic visual context (subject, action, environment) from shot keyframes
using Vision-Language Models (OpenAI, Gemini, OpenRouter, Ollama, xKiro, Custom).
Provides robust graceful fallback to deterministic descriptions on network or key errors.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from core.logging_config import get_logger
from pipeline.llm_client import (
    LLMNotConfiguredError,
    LLMResponseError,
    chat_json_multimodal,
)
from services.settings_service import get_vlm_model

logger = get_logger(__name__)

DEFAULT_VLM_PROMPT = """Bạn là chuyên gia phân tích video. Hãy quan sát khung hình và mô tả ngắn gọn, chính xác bằng tiếng Việt:
1. Chủ thể chính (ai/cái gì, đặc điểm nhận dạng).
2. Hành động hoặc diễn biến chính đang diễn ra.
3. Bối cảnh không gian, địa điểm, môi trường xung quanh.

Trả về duy nhất JSON hợp lệ (không kèm văn bản tự do):
{
  "subject": "chủ thể chính",
  "action": "hành động đang làm",
  "context": "bối cảnh không gian",
  "summary": "1-2 câu tóm tắt tự nhiên mô tả người xem thấy gì trên màn hình"
}
"""


def generate_deterministic_description(shot: dict[str, Any], reason: str = "") -> str:
    """Generate a reliable fallback visual description from shot metadata."""
    start = float(shot.get("start", 0))
    end = float(shot.get("end", 0))
    dur = max(0.1, end - start)
    shot_id = shot.get("shot_id", 0)

    if dur < 2.0:
        pace = "cắt cảnh nhanh"
    elif dur > 6.0:
        pace = "cảnh dài / tĩnh"
    else:
        pace = "cảnh tiêu chuẩn"

    base = f"Shot {shot_id} ({dur:.1f}s, {pace})"
    if reason:
        return f"{base} [Tự động: {reason}]"
    return base


def caption_single_keyframe(
    image_path: str | Path,
    model: str | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """Analyze a single keyframe image with VLM and return structured description.

    Gracefully falls back if image cannot be read, model is not configured,
    or provider request fails.
    """
    path = Path(image_path)
    if not path.exists() or path.stat().st_size == 0:
        return {
            "success": False,
            "summary": "",
            "subject": "",
            "action": "",
            "context": "",
            "error": f"Keyframe file not found or empty: {path}",
            "is_vlm": False,
        }

    target_model = model or get_vlm_model()
    prompt = system_prompt or DEFAULT_VLM_PROMPT

    try:
        raw_bytes = path.read_bytes()
        res = chat_json_multimodal(
            user_text="Hãy phân tích khung hình này theo đúng cấu trúc JSON yêu cầu.",
            images=[raw_bytes],
            system_prompt=prompt,
            model=target_model,
        )

        subject = str(res.get("subject") or "").strip()
        action = str(res.get("action") or "").strip()
        context = str(res.get("context") or "").strip()
        summary = str(res.get("summary") or "").strip()

        if not summary:
            parts = [p for p in [subject, action, context] if p]
            summary = " • ".join(parts) if parts else "Khung hình video ghi nhận bối cảnh thực tế"

        return {
            "success": True,
            "summary": summary,
            "subject": subject,
            "action": action,
            "context": context,
            "model_used": target_model,
            "is_vlm": True,
            "error": None,
        }

    except (LLMNotConfiguredError, LLMResponseError) as exc:
        logger.warning("VLM captioning unavailable for %s: %s", path.name, exc)
        return {
            "success": False,
            "summary": "",
            "subject": "",
            "action": "",
            "context": "",
            "error": str(exc),
            "is_vlm": False,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error in VLM captioning for %s: %s", path.name, exc)
        return {
            "success": False,
            "summary": "",
            "subject": "",
            "action": "",
            "context": "",
            "error": str(exc),
            "is_vlm": False,
        }


def caption_shot(
    shot: dict[str, Any],
    keyframe_path: str | Path | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Augment a single shot dictionary with VLM description and metadata."""
    out = dict(shot)

    # 1. Resolve keyframe path
    kf_target: Path | None = None
    if keyframe_path:
        p = Path(keyframe_path)
        if p.exists() and p.stat().st_size > 0:
            kf_target = p

    if kf_target is None:
        keyframes = shot.get("keyframes") or []
        for kf in keyframes:
            p_str = kf.get("path") if isinstance(kf, dict) else str(kf)
            if p_str:
                p = Path(p_str)
                if p.exists() and p.stat().st_size > 0:
                    kf_target = p
                    break

    # 2. If no keyframe, deterministic fallback
    if kf_target is None:
        out["vlm_description"] = generate_deterministic_description(shot, "không có keyframe")
        out["vlm_subject"] = ""
        out["vlm_action"] = ""
        out["vlm_context"] = ""
        out["vlm_success"] = False
        return out

    # 3. Call VLM
    res = caption_single_keyframe(kf_target, model=model)
    if res.get("success"):
        out["vlm_description"] = res["summary"]
        out["vlm_subject"] = res.get("subject", "")
        out["vlm_action"] = res.get("action", "")
        out["vlm_context"] = res.get("context", "")
        out["vlm_success"] = True
        out["vlm_model"] = res.get("model_used")
        out["vlm_keyframe_path"] = str(kf_target)
    else:
        reason = res.get("error") or "VLM không phản hồi"
        out["vlm_description"] = generate_deterministic_description(shot, reason)
        out["vlm_subject"] = ""
        out["vlm_action"] = ""
        out["vlm_context"] = ""
        out["vlm_success"] = False
        out["vlm_keyframe_path"] = str(kf_target)

    return out


def caption_all_shots_parallel(
    shots: list[dict[str, Any]],
    model: str | None = None,
    max_workers: int = 3,
    progress_callback: Callable[[int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """Caption all shots in parallel with bounded concurrency and progress reporting.

    Returns the shots list preserved in original chronological order.
    """
    if not shots:
        return []

    workers = max(1, min(max_workers, len(shots), 5))
    total = len(shots)
    completed_count = 0

    if progress_callback:
        progress_callback(0, f"Bắt đầu phân tích hình ảnh {total} shot...")

    results: list[dict[str, Any] | None] = [None] * total

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(caption_shot, shot, None, model): idx
            for idx, shot in enumerate(shots)
        }

        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                captioned_shot = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("Shot %d captioning thread failed: %s", idx, exc)
                captioned_shot = dict(shots[idx])
                captioned_shot["vlm_description"] = generate_deterministic_description(
                    shots[idx], f"Thread error: {exc}"
                )
                captioned_shot["vlm_success"] = False

            results[idx] = captioned_shot
            completed_count += 1
            if progress_callback:
                pct = int((completed_count / total) * 100)
                progress_callback(pct, f"Đã phân tích {completed_count}/{total} shot...")

    final_results = [r if r is not None else dict(shots[i]) for i, r in enumerate(results)]
    return final_results
