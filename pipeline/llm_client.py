"""
Thin wrapper around the OpenAI SDK for the xKiro-compatible gateway.

Configuration comes from services.settings_service (DB-backed, encrypted API
key) with environment-variable fallback. All LLM features (script rewrite,
beat mapping hints) go through get_llm_client() so there is exactly one place
that knows how to talk to the provider.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

from core.logging_config import get_logger
from services.ai_breaker import get_ai_breaker
from services.settings_service import (
    get_llm_api_key,
    get_llm_base_url,
    get_llm_model,
    get_xkiro_fallback_models,
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


MAX_IN_MODEL_RECOVERIES = 2


def model_chain(model: str | None = None) -> list[str]:
    """Requested/primary model first, then the configured backups, in order."""
    # Default to the xKiro generation model: the gateway 404s on
    # "gemini-2.0-flash", which stranded every call without an explicit model
    # when the settings DB had no llm_model row.
    primary = model or get_llm_model() or get_xkiro_generation_model()
    chain = [primary]
    for backup in get_xkiro_fallback_models():
        if backup not in chain:
            chain.append(backup)
    return chain


def _is_failover_error(exc: Exception) -> bool:
    """Gateway rejection that another model may serve.

    Auth failures and bare network errors are excluded on purpose: they are
    gateway-wide, so walking the chain would only burn every backup.
    """
    err = str(exc).lower()
    return any(
        t in err
        for t in (
            "404", "409", "429", "500", "502", "503", "504",
            "conflict", "rate limit", "overloaded", "unroutable",
            "model_not_found", "no available model", "does not exist",
        )
    )


def _rejects_response_format(exc: Exception) -> bool:
    err = str(exc).lower()
    return "response_format" in err or "json_schema" in err or "json_object" in err


def _chat_completion(
    messages: list[dict[str, Any]],
    *,
    parse: Any,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    json_schema: dict[str, Any] | None = None,
    model: str | None = None,
    description: str = "chat",
):
    """Run one completion across the model chain and hand the response to `parse`.

    A model is skipped when the gateway rejects the call or `parse` rejects the
    output (empty / truncated), so a throttled free-tier primary cannot fail a
    job that a backup would have served. Raises LLMResponseError only after the
    whole chain failed.
    """
    last_error: Exception | None = None
    for chain_model in model_chain(model):
        kwargs: dict[str, Any] = {
            "model": chain_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "strict": True, "schema": json_schema},
            }
        else:
            kwargs["response_format"] = {"type": "json_object"}

        client = get_llm_client()
        response = None
        for _ in range(MAX_IN_MODEL_RECOVERIES):
            try:
                response = get_ai_breaker(kwargs["model"]).call(
                    lambda: client.chat.completions.create(**kwargs), description
                )
                break
            except Exception as exc:
                last_error = exc
                if kwargs.get("response_format") and _rejects_response_format(exc):
                    # Some OpenAI-compatible providers reject response_format.
                    kwargs.pop("response_format", None)
                    continue
                if _repair_credentials(exc):
                    client = get_llm_client()
                    healed = get_llm_model()
                    if healed:
                        kwargs["model"] = healed
                    continue
                break

        if response is None:
            if not _is_failover_error(last_error):
                raise LLMResponseError(f"LLM request failed: {last_error}") from last_error
            logger.warning(
                "Model '%s' rejected by gateway (%s); trying next model",
                kwargs["model"], str(last_error)[:120],
            )
            continue

        try:
            return parse(response)
        except LLMResponseError as exc:
            last_error = exc
            logger.warning(
                "Model '%s' returned unusable output (%s); trying next model",
                kwargs["model"], str(exc)[:120],
            )

    raise LLMResponseError(
        f"LLM request failed trên mọi model dự phòng ({' -> '.join(model_chain(model))}): "
        + str(last_error)[:200]
    ) from last_error


PROBE_PROMPT = 'Trả về đúng JSON: {"ok": true}'


def probe_model_chain(model: str | None = None) -> list[dict[str, Any]]:
    """Ping every model of the chain so the operator sees which one is sick.

    Used by the Settings page: latency and breaker state per model is what
    distinguishes a throttled free tier from a dead model name.
    """
    client = get_llm_client()
    results: list[dict[str, Any]] = []
    for chain_model in model_chain(model):
        breaker = get_ai_breaker(chain_model)
        started = time.perf_counter()
        entry: dict[str, Any] = {
            "model": chain_model,
            "breaker": breaker.state,
            "latency_ms": 0,
            "ok": False,
            "error": None,
        }
        try:
            client.chat.completions.create(
                model=chain_model,
                temperature=0.0,
                max_tokens=32,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Chỉ trả về JSON."},
                    {"role": "user", "content": PROBE_PROMPT},
                ],
            )
            entry["ok"] = True
        except Exception as exc:
            entry["error"] = str(exc)[:200]
        entry["latency_ms"] = int((time.perf_counter() - started) * 1000)
        results.append(entry)
    return results


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


def _parse_completion_json(response: Any) -> dict[str, Any]:
    """Pull JSON out of a completion, or raise so the chain fails over."""
    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise LLMResponseError("LLM trả về nội dung rỗng.")
    return _parse_llm_json(content)


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
    return _chat_completion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        parse=_parse_completion_json,
        temperature=temperature,
        max_tokens=max_tokens,
        json_schema=json_schema,
        model=model,
        description="chat_json",
    )


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
    return _chat_completion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        parse=_parse_completion_text,
        temperature=temperature,
        max_tokens=max_tokens,
        model=model,
        description="chat_text",
    )


def _parse_completion_text(response: Any) -> str:
    """Pull text out of a completion, or raise so the chain fails over."""
    content = response.choices[0].message.content if response.choices else None
    if not content or not content.strip():
        raise LLMResponseError("LLM trả về nội dung rỗng.")
    return content.strip()
