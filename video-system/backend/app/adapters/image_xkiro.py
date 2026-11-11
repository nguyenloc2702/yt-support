"""Image-gen adapter qua gateway xKiro (Phụ lục G.4).

Port tinh gọn từ support-video-edit/services/xkiro_image_provider.py:
POST /images/generations → 202 + job id → poll GET /images/generations/{id}
đến khi succeeded → tải file. Model từ chối size dọc → thử lại size dự phòng
(vd 1024x1024) và trả `size_used` — assembler center-crop theo.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import httpx

logger = logging.getLogger("video_system.adapters.image_xkiro")

DEFAULT_BASE_URL = "https://api.xkiro.com/v1"
DEFAULT_IMAGE_MODEL = "openai/gpt-image-2.5"
FREE_IMAGE_MODEL = "sensenova/sensenova-u1.5-lite"
_POLL_TIMEOUT_S = 300.0
_POLL_INTERVAL_S = 2.5


class ImageGenError(RuntimeError):
    pass


class ImageGenSizeRejected(ImageGenError):
    """Model từ chối size yêu cầu — caller thử size dự phòng."""


class XkiroImageAdapter:
    def __init__(self, api_key: str, base_url: str | None = None,
                 model: str | None = None, fallback_model: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = (base_url or os.environ.get("VIDEO_XKIRO_IMAGE_BASE_URL")
                         or os.environ.get("VIDEO_XKIRO_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("VIDEO_XKIRO_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL
        self.fallback_model = fallback_model or os.environ.get(
            "VIDEO_XKIRO_IMAGE_FALLBACK_MODEL", FREE_IMAGE_MODEL)

    def generate_image(self, prompt: str, out_path: str, size: str = "1080x1920") -> dict:
        models = [self.model]
        if self.fallback_model and self.fallback_model not in models:
            models.append(self.fallback_model)
        last_err: Exception | None = None
        for model in models:
            try:
                return self._gen_with_model(model, prompt, out_path, size)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("image model %s failed: %s", model, exc)
        raise ImageGenError(f"image generation failed on all models: {last_err}")

    def _gen_with_model(self, model: str, prompt: str, out_path: str, size: str) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        sizes = [size]
        if size != "1024x1024":
            sizes.append("1024x1024")  # fallback phổ quát — assembler sẽ center-crop
        last_err: Exception | None = None
        for use_size in sizes:
            try:
                return self._gen_size(model, prompt, out_path, use_size, headers)
            except ImageGenSizeRejected as exc:
                last_err = exc
                logger.warning("model %s rejected size %s → thử %s", model, use_size, sizes[1])
                continue
        raise ImageGenError(f"size rejected on all variants: {last_err}")

    def _gen_size(self, model: str, prompt: str, out_path: str, size: str,
                  headers: dict) -> dict:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self.base_url}/images/generations",
                json={"model": model, "prompt": prompt, "size": size},
                headers=headers,
            )
            if resp.status_code == 400 and "size" in resp.text.lower():
                raise ImageGenSizeRejected(f"size {size} rejected by {model}")
            if resp.status_code in (401, 403):
                raise ImageGenError(f"image auth failed ({resp.status_code}) — kiểm tra VIDEO_XKIRO_API_KEY")
            resp.raise_for_status()
            data = resp.json()

        job_id = data.get("id") or data.get("job_id")
        if not job_id:
            raise ImageGenError(f"unexpected image API response (no job id): {list(data.keys())}")

        deadline = time.time() + _POLL_TIMEOUT_S
        while time.time() < deadline:
            with httpx.Client(timeout=30.0) as client:
                poll = client.get(f"{self.base_url}/images/generations/{job_id}",
                                  headers=headers)
            poll.raise_for_status()
            pdata = poll.json()
            status = str(pdata.get("status") or "").lower()
            if status in {"succeeded", "success", "completed"}:
                return self._download(pdata, out_path, model, size, headers)
            if status in {"failed", "error"}:
                raise ImageGenError(f"image job failed: {pdata.get('error') or status}")
            time.sleep(_POLL_INTERVAL_S)
        raise ImageGenError(f"image job {job_id} timed out after {_POLL_TIMEOUT_S}s")

    def _download(self, pdata: dict, out_path: str, model: str, size: str,
                  headers: dict) -> dict:
        url = pdata.get("url") or (pdata.get("data") or [{}])[0].get("url") or ""
        b64 = (pdata.get("data") or [{}])[0].get("b64_json") or pdata.get("b64_json")
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if b64:
            import base64
            out.write_bytes(base64.b64decode(b64))
        elif url:
            with httpx.Client(timeout=120.0) as client:
                dl = client.get(url, headers=headers)
            dl.raise_for_status()
            out.write_bytes(dl.content)
        else:
            raise ImageGenError("image job succeeded but no url/b64_json in response")
        if out.stat().st_size < 500:
            raise ImageGenError(f"downloaded image too small ({out.stat().st_size}B)")
        return {"path": str(out), "model": model, "size_used": size}
