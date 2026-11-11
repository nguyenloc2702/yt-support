"""LLM adapter qua gateway OpenAI-compatible của xKiro (Phụ lục G.4).

Port tinh gọn logic `chat_json` từ support-video-edit/pipeline/llm_client.py:
- `response_format json_object`; nếu provider từ chối → gửi lại không response_format
  và nhắc model trả JSON thuần.
- JSON repair: bóc code fence, lấy khối {...} đầu tiên, bọc array trần.
- Fallback model chain từ VIDEO_XKIRO_LLM_FALLBACK_MODELS (phẩy-phân tách).
Fail-closed: không key → adapter không được cắm (main.py chỉ wire khi có key).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import httpx

logger = logging.getLogger("video_system.adapters.llm_xkiro")

DEFAULT_BASE_URL = "https://api.xkiro.com/v1"
DEFAULT_MODEL = "mistralai/mistral-large-4-0"
_TIMEOUT_S = 120.0


def _parse_llm_json(text: str) -> dict:
    """Repair tolerant: code fence → khối {...} đầu tiên → bọc array trần."""
    if not text or not text.strip():
        raise ValueError("empty LLM response")
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in LLM response")
        data = json.loads(raw[start : end + 1])
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict):
        raise ValueError(f"LLM JSON must be an object, got {type(data).__name__}")
    return data


def _rejects_response_format(exc: Exception) -> bool:
    err = str(exc).lower()
    return "response_format" in err or "json_schema" in err or "json_object" in err


class XkiroLlmAdapter:
    def __init__(self, api_key: str, base_url: str | None = None,
                 model: str | None = None, fallback_models: list[str] | None = None) -> None:
        self.api_key = api_key
        self.base_url = (base_url or os.environ.get("VIDEO_XKIRO_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("VIDEO_XKIRO_LLM_MODEL") or DEFAULT_MODEL
        raw_fb = fallback_models or [
            m.strip() for m in
            os.environ.get("VIDEO_XKIRO_LLM_FALLBACK_MODELS", "").split(",") if m.strip()
        ]
        self.models: list[str] = [self.model] + [m for m in raw_fb if m and m != self.model]

    def chat_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        last_err: Exception | None = None
        for model in self.models:
            use_rf = True  # thử response_format trước, tự hạ nếu provider từ chối
            for attempt in range(2):
                try:
                    return self._call(model, system, user, schema, use_rf)
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    if use_rf and _rejects_response_format(exc):
                        use_rf = False
                        continue
                    break
            logger.warning("LLM model %s failed: %s", model, last_err)
        raise RuntimeError(f"LLM request failed on all models: {last_err}")

    def _call(self, model: str, system: str, user: str, schema: dict | None,
              use_response_format: bool) -> dict:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        body: dict = {"model": model, "messages": messages, "temperature": 0.7}
        if use_response_format:
            if schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "short_script", "strict": False, "schema": schema},
                }
            else:
                body["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_auth_err: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=_TIMEOUT_S) as client:
                    resp = client.post(f"{self.base_url}/chat/completions",
                                       json=body, headers=headers)
                if resp.status_code in (401, 403):
                    raise RuntimeError(f"LLM auth failed ({resp.status_code}) — kiểm tra VIDEO_XKIRO_API_KEY")
                if resp.status_code == 429:
                    raise RuntimeError("LLM rate-limited (429)")
                resp.raise_for_status()
                data = resp.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                return _parse_llm_json(content)
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_auth_err = exc
                if attempt < 2:
                    time.sleep(0.8 * (attempt + 1))
        raise RuntimeError(f"LLM call error: {last_auth_err}")
