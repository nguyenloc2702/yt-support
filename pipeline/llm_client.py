"""
Thin wrapper around the OpenAI SDK for the xKiro-compatible gateway.

Configuration comes from services.settings_service (DB-backed, encrypted API
key) with environment-variable fallback. All LLM features (script rewrite,
beat mapping hints) go through get_llm_client() so there is exactly one place
that knows how to talk to the provider.
"""

import json
import os
from pathlib import Path
from typing import Any

from core.logging_config import get_logger
from services.ai_breaker import get_default_breaker
from services.settings_service import (
    get_llm_api_key,
    get_llm_base_url,
    get_llm_model,
    get_xkiro_generation_model,
)

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120

# Known-good free model on the xKiro gateway; used to self-heal a settings DB
# row that a stale Google Drive sync reverted to an unroutable model.
PREFERRED_XKIRO_MODEL = "qwen/qwen3.8-omni-flash:free"


class LLMNotConfiguredError(RuntimeError):
    """Raised when no API key is available (DB or env)."""


class LLMResponseError(RuntimeError):
    """Raised when the provider returns an unusable response."""


def _env_llm_key() -> str | None:
    return os.environ.get("XKIRO_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _is_auth_error(exc: Exception) -> bool:
    err = str(exc).lower()
    return any(
        t in err
        for t in ("authentication_error", "invalid or disabled clientapikey", "401", "403", "unauthorized")
    )


def _is_model_routing_error(exc: Exception) -> bool:
    err = str(exc).lower()
    return any(
        t in err
        for t in ("503", "unroutable", "model_not_found", "no available model", "does not exist or is not routable")
    )


def _repair_credentials(exc: Exception) -> bool:
    """Self-heal stale settings-DB rows before giving up.

    The settings DB lives on Google Drive and a delayed cloud sync can revert
    the llm_api_key/llm_model rows to dead values. On an auth rejection, if the
    environment (.env, loaded into os.environ) holds a different key, persist
    it back to the DB. On a model-routing rejection, restore the preferred
    gateway model. Returns True when something was re-paired and a retry is
    worth one attempt.
    """
    from services.settings_service import LLM_KEY, LLM_MODEL_KEY, get_setting, mask_secret, set_setting

    repaired = False
    if _is_auth_error(exc):
        env_key = _env_llm_key()
        db_key = get_setting(LLM_KEY)
        if env_key and db_key and env_key != db_key:
            set_setting(LLM_KEY, env_key)
            logger.warning(
                "LLM API key rejected by provider — re-paired settings DB from environment key (%s)",
                mask_secret(env_key),
            )
            repaired = True
    if _is_model_routing_error(exc):
        db_model = get_setting(LLM_MODEL_KEY)
        if db_model and db_model != PREFERRED_XKIRO_MODEL:
            set_setting(LLM_MODEL_KEY, PREFERRED_XKIRO_MODEL)
            logger.warning(
                "LLM model '%s' not routable — restored '%s' in settings DB",
                db_model, PREFERRED_XKIRO_MODEL,
            )
            repaired = True
    return repaired


def get_llm_client():
    """Return a configured OpenAI client, or raise LLMNotConfiguredError."""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise LLMNotConfiguredError(
            "The 'openai' package is not installed. Run: pip install openai"
        ) from exc

    api_key = get_llm_api_key()
    if not api_key:
        raise LLMNotConfiguredError(
            "Chưa cấu hình API key. Vào trang Settings để nhập API key."
        )
    return OpenAI(
        api_key=api_key,
        base_url=get_llm_base_url(),
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )


def list_models() -> list[str]:
    """Fetch available model IDs from the provider (used by Settings UI)."""
    client = get_llm_client()
    try:
        response = client.models.list()
    except Exception as exc:
        raise LLMResponseError(f"Không lấy được danh sách models: {exc}") from exc
    return sorted(m.id for m in response.data)


def chat_json_multimodal(
    system_prompt: str,
    user_text: str,
    images: list[bytes] | None = None,
    json_schema: dict[str, Any] | None = None,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """
    Multimodal chat completion: text + optional inline images (base64 JPEG/PNG).

    Used by the VLM keyframe captioning stage. Images are sent as data URLs;
    they are never logged. Robust against markdown code fences and unsupported
    response_format on custom providers. Raises LLMResponseError on unusable output.
    """
    import base64

    from services.settings_service import get_vlm_model

    client = get_llm_client()
    selected_model = model or get_vlm_model()

    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for img in images or []:
        if isinstance(img, (str, Path)):
            raw_bytes = Path(img).read_bytes()
        else:
            raw_bytes = img
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )

    kwargs: dict[str, Any] = {
        "model": selected_model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
    }
    if json_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "output", "strict": True, "schema": json_schema},
        }
    else:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        err = str(exc)
        if "response_format" in err or "json_schema" in err or "json_object" in err:
            kwargs.pop("response_format", None)
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as exc2:
                raise LLMResponseError(f"VLM request failed: {exc2}") from exc2
        elif _repair_credentials(exc):
            client = get_llm_client()
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as exc2:
                raise LLMResponseError(f"VLM request failed: {exc2}") from exc2
        else:
            raise LLMResponseError(f"VLM request failed: {exc}") from exc

    msg = response.choices[0].message if response.choices else None
    content_out = getattr(msg, "content", None)
    if not content_out:
        raise LLMResponseError("VLM trả về nội dung rỗng.")

    return _parse_llm_json(content_out)



_FENCE_RE = None  # compiled lazily


def _strip_code_fences(content: str) -> str:
    """Remove markdown code fences some models wrap around JSON output."""
    global _FENCE_RE
    if _FENCE_RE is None:
        import re

        _FENCE_RE = re.compile(
            r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL
        )
    m = _FENCE_RE.match(content)
    return m.group(1) if m else content


def _extract_json_object(content: str) -> str:
    """Last-resort: extract the first {...} or [...] block from the text."""
    import re

    m = re.search(r"[\[{].*[\]}]", content, re.DOTALL)
    return m.group(0) if m else content


def _parse_llm_json(content: str) -> dict[str, Any]:
    """Parse LLM output tolerantly: fences → raw → first JSON block.

    A bare JSON array is wrapped as {"items": [...]} so callers that expect
    an object can still normalize it (e.g. treat items as beats).
    """

    candidates = [content, _strip_code_fences(content), _extract_json_object(content)]
    for cand in candidates:
        try:
            result = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"items": result}
    raise LLMResponseError(
        "LLM trả về JSON không hợp lệ: " + content[:200].encode(
            "ascii", errors="replace"
        ).decode("ascii") + "..."
    )


def chat_json(
    system_prompt: str,
    user_prompt: str,
    json_schema: dict[str, Any] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str | None = None,
) -> dict[str, Any]:
    """
    Send a chat completion and parse the response as JSON.

    When json_schema is provided, structured output is requested via
    response_format (OpenAI-compatible structured outputs). Falls back to a
    plain JSON-mode request if the provider rejects the schema.

    Returns the parsed dict. Raises LLMResponseError on unusable output.
    """
    client = get_llm_client()
    # Default to the xKiro generation model: the gateway (default base URL)
    # 404s on "gemini-2.0-flash", which stranded every call without an
    # explicit model when the settings DB had no llm_model row.
    selected_model = model or get_llm_model() or get_xkiro_generation_model()

    kwargs: dict[str, Any] = {
        "model": selected_model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if json_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "output", "strict": True, "schema": json_schema},
        }
    else:
        kwargs["response_format"] = {"type": "json_object"}

    breaker = get_default_breaker()
    try:
        response = breaker.call(lambda: client.chat.completions.create(**kwargs), "chat_json")
    except Exception as exc:
        if _repair_credentials(exc):
            client = get_llm_client()
            kwargs["model"] = get_llm_model() or kwargs["model"]
            try:
                response = breaker.call(lambda: client.chat.completions.create(**kwargs), "chat_json")
            except Exception as exc2:
                raise LLMResponseError(f"LLM request failed: {exc2}") from exc2
        else:
            # Some OpenAI-compatible providers reject response_format entirely.
            err = str(exc)
            if "response_format" in err or "json_schema" in err or "json_object" in err:
                kwargs.pop("response_format", None)
                try:
                    response = breaker.call(lambda: client.chat.completions.create(**kwargs), "chat_json")
                except Exception as exc2:
                    raise LLMResponseError(f"LLM request failed: {exc2}") from exc2
            else:
                raise LLMResponseError(f"LLM request failed: {exc}") from exc

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise LLMResponseError("LLM trả về nội dung rỗng.")

    return _parse_llm_json(content)


def chat_text(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    model: str | None = None,
) -> str:
    """
    Send a chat completion and return the raw text response (no JSON parsing).

    Used for plain-text generation such as per-block script drafts
    (Phase 8 block-based workflow). Raises LLMResponseError on unusable output.
    """
    client = get_llm_client()
    # Default to the xKiro generation model: the gateway (default base URL)
    # 404s on "gemini-2.0-flash", which stranded every call without an
    # explicit model when the settings DB had no llm_model row.
    selected_model = model or get_llm_model() or get_xkiro_generation_model()

    breaker = get_default_breaker()
    try:
        response = breaker.call(
            lambda: client.chat.completions.create(
                model=selected_model,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            ),
            "chat_text",
        )
    except LLMResponseError:
        raise
    except Exception as exc:
        if _repair_credentials(exc):
            client = get_llm_client()
            selected_model = get_llm_model() or selected_model
            try:
                response = breaker.call(
                    lambda: client.chat.completions.create(
                        model=selected_model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    ),
                    "chat_text",
                )
            except Exception as exc2:
                raise LLMResponseError(f"LLM request failed: {exc2}") from exc2
        else:
            raise LLMResponseError(f"LLM request failed: {exc}") from exc

    content = response.choices[0].message.content if response.choices else None
    if not content or not content.strip():
        raise LLMResponseError("LLM trả về nội dung rỗng.")

    return content.strip()
