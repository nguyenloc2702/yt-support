"""
Thin wrapper around the OpenAI SDK for the xKiro-compatible gateway.

Configuration comes from services.settings_service (DB-backed, encrypted API
key) with environment-variable fallback. All LLM features (script rewrite,
beat mapping hints) go through get_llm_client() so there is exactly one place
that knows how to talk to the provider.
"""

import json
from typing import Any

from core.logging_config import get_logger
from services.settings_service import (
    get_llm_api_key,
    get_llm_base_url,
    get_llm_model,
)

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120


class LLMNotConfiguredError(RuntimeError):
    """Raised when no API key is available (DB or env)."""


class LLMResponseError(RuntimeError):
    """Raised when the provider returns an unusable response."""


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
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """
    Multimodal chat completion: text + optional inline images (base64 JPEG/PNG).

    Used by the VLM keyframe captioning stage. Images are sent as data URLs;
    they are never logged. Raises LLMResponseError on unusable output.
    """
    import base64

    client = get_llm_client()
    model = get_llm_model() or "gemini-2.0-flash"

    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for img in images or []:
        b64 = base64.b64encode(img).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )

    kwargs: dict[str, Any] = {
        "model": model,
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
        raise LLMResponseError(f"VLM request failed: {exc}") from exc

    msg = response.choices[0].message if response.choices else None
    content_out = getattr(msg, "content", None)
    if not content_out:
        raise LLMResponseError("VLM trả về nội dung rỗng.")

    try:
        return json.loads(content_out)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(
            f"VLM trả về JSON không hợp lệ: {str(content_out)[:200]}..."
        ) from exc


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
    """Parse LLM output tolerantly: fences → raw → first JSON block."""
    import re

    candidates = [content, _strip_code_fences(content), _extract_json_object(content)]
    for cand in candidates:
        try:
            result = json.loads(cand)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue
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
) -> dict[str, Any]:
    """
    Send a chat completion and parse the response as JSON.

    When json_schema is provided, structured output is requested via
    response_format (OpenAI-compatible structured outputs). Falls back to a
    plain JSON-mode request if the provider rejects the schema.

    Returns the parsed dict. Raises LLMResponseError on unusable output.
    """
    client = get_llm_client()
    model = get_llm_model() or "gemini-2.0-flash"

    kwargs: dict[str, Any] = {
        "model": model,
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

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        # Some OpenAI-compatible providers reject response_format entirely.
        err = str(exc)
        if "response_format" in err or "json_schema" in err or "json_object" in err:
            kwargs.pop("response_format", None)
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as exc2:
                raise LLMResponseError(f"LLM request failed: {exc2}") from exc2
        else:
            raise LLMResponseError(f"LLM request failed: {exc}") from exc

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise LLMResponseError("LLM trả về nội dung rỗng.")

    return _parse_llm_json(content)
