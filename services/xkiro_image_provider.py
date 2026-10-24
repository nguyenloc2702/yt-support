"""xKiro image generation provider (async job API).

The gateway exposes OpenAI-style ``POST /v1/images/generations`` but returns
202 with a job object; the image is fetched by polling
``GET /v1/images/generations/{id}`` until ``status == "succeeded"`` and then
downloading ``data[0].url`` (or decoding ``data[0].b64_json``). The free
Sensenova model is used as an explicit, logged fallback when the primary model
is unavailable or rate-limited.
"""

from __future__ import annotations

import base64
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from core.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_IMAGE_MODEL = "openai/gpt-image-2.5"
FREE_IMAGE_MODEL = "sensenova/sensenova-u1.5-lite"
POLL_INTERVAL_SECONDS = 4.0
DEFAULT_JOB_TIMEOUT_SECONDS = 240.0
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT_SECONDS = 12.0
MIN_IMAGE_BYTES = 1000
FAILED_STATES = {"failed", "error", "cancelled", "canceled"}


class ImageGenerationError(RuntimeError):
    """Raised when xKiro cannot produce a usable image."""


def _api_base() -> str:
    base = os.environ.get("XKIRO_IMAGE_BASE_URL")
    if not base:
        from services.settings_service import get_llm_base_url

        base = get_llm_base_url()
    return base.rstrip("/")


def _api_key() -> Optional[str]:
    from services.settings_service import get_llm_api_key

    return get_llm_api_key()


def _submit_job(prompt: str, model: str, headers: dict, base: str, size: str = "1024x1024") -> dict:
    import requests

    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    for attempt in range(1, RATE_LIMIT_RETRIES + 1):
        resp = requests.post(
            f"{base}/images/generations", json=payload, headers=headers, timeout=60
        )
        if resp.status_code == 429 and attempt < RATE_LIMIT_RETRIES:
            logger.warning(
                "xKiro image rate-limited (%s), retrying in %.0fs (%d/%d)",
                model, RATE_LIMIT_WAIT_SECONDS, attempt, RATE_LIMIT_RETRIES,
            )
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
            continue
        if resp.status_code == 400 and size != "1024x1024":
            # Fallback models may not accept wide sizes; a square image still
            # beats a failed shot.
            logger.warning(
                "xKiro image model %s rejected size %s, retrying at 1024x1024",
                model, size,
            )
            payload["size"] = "1024x1024"
            continue
        if resp.status_code not in (200, 202):
            raise ImageGenerationError(
                f"xKiro image submit failed ({model}) status {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()
    raise ImageGenerationError(f"xKiro image rate limit persisted for {model}")


def _wait_for_job(job_id: str, headers: dict, base: str, poll_timeout: float) -> dict:
    import requests

    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        resp = requests.get(f"{base}/images/generations/{job_id}", headers=headers, timeout=60)
        if resp.status_code != 200:
            raise ImageGenerationError(
                f"xKiro image poll failed status {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        state = str(body.get("status") or body.get("state") or "").lower()
        if state == "succeeded":
            return body
        if state in FAILED_STATES:
            raise ImageGenerationError(
                f"xKiro image job {job_id} {state}: {str(body.get('error') or body)[:200]}"
            )
    raise ImageGenerationError(f"xKiro image job {job_id} timed out after {poll_timeout:.0f}s")


def _save_image(result: dict, out_path: Path) -> None:
    import requests

    data = result.get("data") or []
    if not data:
        raise ImageGenerationError("xKiro image job returned no data")
    first = data[0]
    if first.get("b64_json"):
        content = base64.b64decode(first["b64_json"])
    elif first.get("url"):
        resp = requests.get(first["url"], timeout=120)
        if resp.status_code != 200:
            raise ImageGenerationError(
                f"xKiro image download failed status {resp.status_code}"
            )
        content = resp.content
    else:
        raise ImageGenerationError("xKiro image job returned neither url nor b64_json")
    if len(content) < MIN_IMAGE_BYTES:
        raise ImageGenerationError(f"xKiro image payload too small ({len(content)} bytes)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(f".{out_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, out_path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _submit_with_heal(
    prompt: str,
    model: str,
    headers: dict,
    base: str,
    explicit_key: bool,
    key: str,
    size: str,
) -> tuple[dict, Optional[str]]:
    try:
        return _submit_job(prompt, model, headers, base, size), None
    except ImageGenerationError as exc:
        msg = str(exc)
        if explicit_key or ("status 401" not in msg and "status 403" not in msg):
            raise
        from pipeline.llm_client import _repair_credentials

        if not _repair_credentials(exc):
            raise
        retry_key = _api_key()
        if not retry_key or retry_key == key:
            raise
        headers["Authorization"] = f"Bearer {retry_key}"
        return _submit_job(prompt, model, headers, base, size), retry_key


def generate_xkiro_image(
    prompt: str,
    out_path: Path,
    model: Optional[str] = None,
    fallback_model: Optional[str] = None,
    poll_timeout: float = DEFAULT_JOB_TIMEOUT_SECONDS,
    api_key: Optional[str] = None,
    size: str = "1024x1024",
) -> str:
    """Generate one image and write it to out_path; returns the model used.

    Primary model failures fall back to the free model with a logged warning.
    A dead key (401/403) is re-paired from .env once via the same settings-DB
    self-heal used by TTS/LLM before giving up.
    """
    out_path = Path(out_path)
    explicit_key = api_key is not None
    key = api_key or _api_key()
    if not key:
        raise ImageGenerationError("xKiro API key is not configured (DB or XKIRO_API_KEY).")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    base = _api_base()

    models = [model or os.environ.get("XKIRO_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL]
    fallback = fallback_model or os.environ.get("XKIRO_IMAGE_FALLBACK_MODEL") or FREE_IMAGE_MODEL
    if fallback and fallback not in models:
        models.append(fallback)

    last_exc: Optional[Exception] = None
    for idx, candidate in enumerate(models):
        try:
            job, new_key = _submit_with_heal(
                prompt, candidate, headers, base, explicit_key, key, size
            )
            if new_key:
                key = new_key
        except ImageGenerationError as exc:
            last_exc = exc
            if idx + 1 < len(models):
                logger.warning(
                    "xKiro image model %s failed (%s) — falling back to %s",
                    candidate, exc, models[idx + 1],
                )
            continue

        job_id = job.get("id")
        if not job_id:
            if job.get("data"):
                _save_image(job, out_path)
                return candidate
            last_exc = ImageGenerationError(f"xKiro image submit returned no job id: {str(job)[:200]}")
        else:
            try:
                result = _wait_for_job(job_id, headers, base, poll_timeout)
                _save_image(result, out_path)
                logger.info("Character image generated via xKiro %s: %s", candidate, out_path)
                return candidate
            except ImageGenerationError as exc:
                last_exc = exc

        if idx + 1 < len(models):
            logger.warning(
                "xKiro image model %s failed (%s) — falling back to %s",
                candidate, last_exc, models[idx + 1],
            )

    raise ImageGenerationError(f"xKiro image generation failed: {last_exc}")
