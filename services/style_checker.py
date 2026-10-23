"""VLM style-check (Phase 3): AI verifies generated assets follow the Style Bible.

Until now AI generated 100% of the assets and verified 0% of them. This module
closes that gap for images: every generated image is judged by the xKiro VLM
(qwen3.8-omni vision) against the crayon Style Bible, and failed images are
regenerated with the judge's issues folded back into the prompt.

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

STYLE_JUDGE_SYSTEM = """Bạn là Art Director kiểm duyệt phong cách hình ảnh cho series documentary vẽ tay.
Chấm ảnh theo CRAYON STYLE BIBLE, trả về duy nhất JSON object:
{"ok": <true nếu ĐẠT>, "score": <0-10 một chữ số thập phân>, "issues": ["các lỗi cụ thể cần sửa khi vẽ lại"], "summary": "<mô tả ngắn ảnh>"}
Tiêu chí ĐẠT (score >= 7.0 và không có lỗi nặng):
1. Hand-drawn: nét sáp crayon / bút chì màu thô, có texture giấy vẽ sần.
2. Flat 2D minimalist: KHÔNG gradient shading, KHÔNG 3D render, KHÔNG đổ bóng đa lớp.
3. TUYỆT ĐỐI KHÔNG photorealistic: không ảnh chụp thật, không CGI chân thực.
4. Bố cục: khoảng negative space rộng (60-70%), 1-3 tiêu điểm rõ ràng, không rối rắm.
5. Nền giấy off-white (hoặc bảng đen/sepia khi chủ đề khủng hoảng/hồi ức).
6. Màu nhấn crayon tiết chế: vàng cho tiền bạc/ý tưởng, đỏ cảnh báo, xanh dương trung tính.
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

    try:
        parsed = chat_json_multimodal(
            STYLE_JUDGE_SYSTEM,
            user_text,
            images=[payload],
            model=model or get_xkiro_vlm_model(),
            temperature=0.1,
            max_tokens=600,
        )
    except LLMResponseError as exc:
        raise StyleCheckError(f"VLM style check failed: {exc}") from exc

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
            fixes = "; ".join(last_verdict["issues"][:3]) or "stronger flat 2D crayon look"
            current_prompt = (
                f"{prompt}\n\nSTRICT STYLE REMINDERS (judge rejected previous attempt): {fixes}. "
                "Emphasize rough wax crayon strokes, grainy paper texture, flat 2D, "
                "generous negative space, absolutely no photorealism."
            )

    return last_verdict or {"ok": False, "score": 0.0, "issues": ["no verdict"], "summary": ""}


def save_style_report(char_dir: Path, subject: str, verdict: Dict[str, Any]) -> Path:
    """Persist the verdict next to the asset for the QA scorecard (Phase 4)."""
    report = {"subject": subject, **verdict}
    out = Path(char_dir) / "style_check.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
