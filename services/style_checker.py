"""VLM style-check (Phase 3): AI verifies generated assets follow the Style Bible.

Until now AI generated 100% of the assets and verified 0% of them. This module
closes that gap for images: every generated image is judged by the xKiro VLM
(qwen3.8-omni vision) against the vector-cartoon Style Bible, and failed images
are regenerated with the judge's issues folded back into the prompt.

No silent fakes: when the VLM itself is unavailable the check raises — callers
surface the failure instead of pretending the style is fine.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.logging_config import get_logger

logger = get_logger(__name__)

MAX_SIDE_PX = 768

# The primary free VLM refuses some historical-figure likenesses at the
# provider safety layer. That is a judge limitation, not a style failure, so a
# refused image is re-judged by an alternate vision model instead of blocking
# the human gate. Every switch is logged.
STYLE_JUDGE_FALLBACKS = ("cohere/command-a-vision", "openai/gpt-6.1-sol")


def _is_safety_rejection(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "safety policy" in msg or "rejected by the provider" in msg

STYLE_JUDGE_SYSTEM = """Bạn là Art Director kiểm duyệt phong cách hình ảnh cho series video thuyết minh.
Chấm ảnh theo VECTOR CARTOON STYLE BIBLE, trả về duy nhất JSON object:
{"ok": <true nếu ĐẠT>, "score": <0-10 một chữ số thập phân>, "issues": ["các lỗi cụ thể cần sửa khi vẽ lại"], "summary": "<mô tả ngắn ảnh>"}
Tiêu chí ĐẠT (score >= 7.0 và không có lỗi nặng):
1. Clean flat vector cartoon: nét đều đậm vừa, khối phẳng có gradient nhẹ tiết chế, không texture giấy, không nét vẽ sần crayon/bút chì.
2. Nhân vật cartoon có khuôn mặt biểu cảm đơn giản, tỷ lệ dễ thương, nét viền sạch.
3. TUYỆT ĐỐI KHÔNG photorealistic: không ảnh chụp thật, không CGI chân thực, không 3D render.
4. Bố cục: composition đọc được ngay, 1-3 tiêu điểm rõ ràng, không rối rắm.
5. Nền sáng gọn gàng có chiều sâu nhẹ (văn phòng, phòng sạch, phố, nhà máy) — không nền đen u ám.
6. Palette hiện đại sáng: xanh da trời, trắng, navy, xám slate, điểm nhấn gỗ/đồng ấm.
Nếu ảnh sai chủ đề so với mô tả được cung cấp, đó là lỗi nặng (ok=false)."""


class StyleCheckError(RuntimeError):
    """Raised when the VLM style check itself cannot run."""


def _downscale_image(image_path: Path) -> bytes:
    """Downscale to <=768px JPEG to keep the multimodal payload small."""
    from PIL import Image

    img = Image.open(image_path)
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_SIDE_PX:
        scale = MAX_SIDE_PX / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def check_image_style(
    image_path: Path,
    subject: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Judge one image against the Style Bible.

    Returns {"ok", "score", "issues", "summary"}. Raises StyleCheckError when
    the VLM is unavailable or returns an unusable verdict.
    """
    from pipeline.llm_client import LLMResponseError, chat_json_multimodal
    from services.settings_service import get_xkiro_vlm_model

    image_path = Path(image_path)
    if not image_path.is_file():
        raise StyleCheckError(f"image not found: {image_path}")

    try:
        payload = _downscale_image(image_path)
    except Exception as exc:
        raise StyleCheckError(f"cannot read image for style check: {exc}") from exc

    user_text = "Hãy chấm ảnh này theo Style Bible."
    if subject:
        user_text += f"\nChủ đề ảnh phải là: {subject}"

    primary = model or get_xkiro_vlm_model()
    candidates = [primary]
    if not model:
        candidates.extend(m for m in STYLE_JUDGE_FALLBACKS if m != primary)

    parsed = None
    last_exc: Optional[Exception] = None
    for candidate in candidates:
        try:
            parsed = chat_json_multimodal(
                STYLE_JUDGE_SYSTEM,
                user_text,
                images=[payload],
                model=candidate,
                temperature=0.1,
                max_tokens=600,
            )
            if candidate != primary:
                logger.warning(
                    "Style judge '%s' refused the image (%s) — verdict from '%s'",
                    primary, last_exc, candidate,
                )
            break
        except LLMResponseError as exc:
            last_exc = exc
            if not _is_safety_rejection(exc):
                break
    if parsed is None:
        raise StyleCheckError(f"VLM style check failed: {last_exc}") from last_exc

    try:
        score = float(parsed.get("score", 0.0))
    except (TypeError, ValueError):
        raise StyleCheckError(f"VLM returned non-numeric score: {parsed.get('score')!r}")
    issues = [str(i) for i in (parsed.get("issues") or [])][:6]
    return {
        "ok": bool(parsed.get("ok")) and score >= 7.0,
        "score": max(0.0, min(10.0, score)),
        "issues": issues,
        "summary": str(parsed.get("summary", ""))[:300],
    }


def generate_with_style_check(
    generate_fn: Callable[[str, Path], None],
    prompt: str,
    out_path: Path,
    subject: Optional[str] = None,
    max_attempts: int = 2,
) -> Dict[str, Any]:
    """Generate -> VLM judge -> regenerate with issues folded back (max N tries).

    `generate_fn(prompt, out_path)` must create the image at out_path or raise.
    Returns the LAST style verdict; the caller decides what to do when ok=False
    (e.g. surface it to the human approval gate). Generation failures propagate.
    """
    current_prompt = prompt
    last_verdict: Optional[Dict[str, Any]] = None

    for attempt in range(1, max_attempts + 1):
        generate_fn(current_prompt, out_path)

        last_verdict = check_image_style(out_path, subject=subject)
        logger.info(
            "Style check attempt %d for %s: score %.1f ok=%s issues=%s",
            attempt, out_path.name, last_verdict["score"],
            last_verdict["ok"], last_verdict["issues"][:2],
        )
        if last_verdict["ok"]:
            break
        if attempt < max_attempts:
            fixes = "; ".join(last_verdict["issues"][:3]) or "stronger clean vector cartoon look"
            current_prompt = (
                f"{prompt}\n\nSTRICT STYLE REMINDERS (judge rejected previous attempt): {fixes}. "
                "Emphasize bold even outlines, flat shapes with soft subtle gradients, "
                "clean modern palette, tidy bright background, generous negative "
                "space, absolutely no photorealism and no 3D render."
            )

    return last_verdict or {"ok": False, "score": 0.0, "issues": ["no verdict"], "summary": ""}


def save_style_report(char_dir: Path, subject: str, verdict: Dict[str, Any]) -> Path:
    """Persist the verdict next to the asset for the QA scorecard (Phase 4)."""
    report = {"subject": subject, **verdict}
    out = Path(char_dir) / "style_check.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
