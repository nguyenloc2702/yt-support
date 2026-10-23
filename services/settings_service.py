"""
Key/value app settings persisted in SQLite, with transparent encryption for
sensitive values (API keys).

- Plain settings (base_url, model name) are stored as-is.
- Sensitive settings (llm_api_key) are encrypted with Fernet. The master key
  lives in DATA_DIR/.secret_key, generated on first use. Because DATA_DIR is
  outside the release directory on VPS deployments (see deploy/), the master
  key survives version upgrades.

Read priority is decided by callers: DB first, fall back to .env.
"""

import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from config.settings import settings
from core.database import SessionLocal
from core.logging_config import get_logger
from core.models import AppSetting

logger = get_logger(__name__)

SENSITIVE_KEYS = {"llm_api_key"}

_SECRET_KEY_PATH = Path(settings.data_dir) / ".secret_key"


def _get_fernet() -> Fernet:
    """Load (or create) the master key and return a Fernet instance."""
    path = _SECRET_KEY_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            key = Fernet.generate_key()
            path.write_bytes(key)
            try:
                # Restrict permissions where supported (POSIX).
                os.chmod(path, 0o600)
            except OSError:
                pass
            logger.info("Generated new master secret key at %s", path)
        return Fernet(path.read_bytes())
    except OSError as exc:
        raise RuntimeError(f"Cannot access secret key file {path}: {exc}") from exc


def _encrypt(value: str) -> str:
    return _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str) -> str:
    try:
        return _get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Stored secret cannot be decrypted (master key changed?). "
            "Please re-enter the API key in Settings."
        ) from exc


def get_setting(key: str, default: str | None = None) -> str | None:
    """Return a setting value (decrypted if sensitive), or default."""
    with SessionLocal() as db:
        row = db.get(AppSetting, key)
        if row is None or row.value is None:
            return default
        if key in SENSITIVE_KEYS:
            try:
                return _decrypt(row.value)
            except ValueError:
                logger.error("Failed to decrypt setting '%s'", key)
                raise
        return row.value


def set_setting(key: str, value: str | None) -> None:
    """Create/update a setting. Sensitive keys are encrypted at rest."""
    with SessionLocal() as db:
        row = db.get(AppSetting, key)
        if value is None:
            if row is not None:
                db.delete(row)
                db.commit()
                logger.info("Setting '%s' deleted", key)
            return
        stored = _encrypt(value) if key in SENSITIVE_KEYS else value
        if row is None:
            db.add(AppSetting(key=key, value=stored))
        else:
            row.value = stored
        db.commit()
        logger.info("Setting '%s' saved", key)


def mask_secret(value: str | None) -> str | None:
    """Return a masked preview like 'sk-...ab12' for UI display."""
    if not value:
        return None
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}...{value[-4:]}"


# ---- LLM & VLM specific convenience keys & presets ----

DEFAULT_LLM_BASE_URL = "https://api.xkiro.com/v1"
LLM_KEY = "llm_api_key"
LLM_BASE_URL_KEY = "llm_base_url"
LLM_MODEL_KEY = "llm_model"
LLM_PROVIDER_PRESET_KEY = "llm_provider_preset"
VLM_MODEL_KEY = "vlm_model"
VLM_ENABLED_KEY = "vlm_enabled"

# Role-specific xKiro models (Phase 1: xKiro-first generation stack).
# Generation model handles translation, script rewrite, judging, character
# extraction. VLM model handles image understanding / style checks.
XKIRO_GENERATION_MODEL_KEY = "xkiro_generation_model"
DEFAULT_XKIRO_GENERATION_MODEL = "qwen/qwen3.8-omni-flash:free"
XKIRO_VLM_MODEL_KEY = "xkiro_vlm_model"
DEFAULT_XKIRO_VLM_MODEL = "qwen/qwen3.8-omni-flash:free"

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "openai": {
        "name": "OpenAI (Chính thức)",
        "base_url": "https://api.openai.com/v1",
        "default_llm_model": "gpt-4o-mini",
        "default_vlm_model": "gpt-4o-mini",
        "suggested_models": ["gpt-4o-mini", "gpt-4o"],
    },
    "gemini": {
        "name": "Google Gemini (Tương thích OpenAI)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_llm_model": "gemini-2.0-flash",
        "default_vlm_model": "gemini-2.0-flash",
        "suggested_models": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
    },
    "openrouter": {
        "name": "OpenRouter (Đa nhà cung cấp)",
        "base_url": "https://openrouter.ai/api/v1",
        "default_llm_model": "google/gemini-2.0-flash-001",
        "default_vlm_model": "google/gemini-2.0-flash-001",
        "suggested_models": [
            "google/gemini-2.0-flash-001",
            "openai/gpt-4o-mini",
            "anthropic/claude-3.5-sonnet",
        ],
    },
    "ollama": {
        "name": "Ollama Local (Offline trên máy)",
        "base_url": "http://localhost:11434/v1",
        "default_llm_model": "qwen2-vl",
        "default_vlm_model": "qwen2-vl",
        "suggested_models": ["qwen2-vl", "llava", "llama3.2-vision"],
    },
    "xkiro": {
        "name": "xKiro Gateway",
        "base_url": "https://api.xkiro.com/v1",
        "default_llm_model": "gemini-2.0-flash",
        "default_vlm_model": "gemini-2.0-flash",
        "suggested_models": ["gemini-2.0-flash", "gpt-4o-mini"],
    },
    "custom": {
        "name": "Tùy chỉnh (Custom Endpoint)",
        "base_url": "",
        "default_llm_model": "",
        "default_vlm_model": "",
        "suggested_models": [],
    },
}


def get_llm_api_key() -> str | None:
    """API key from DB (encrypted) or fallback to environment/.env."""
    return get_setting(LLM_KEY) or os.environ.get("XKIRO_API_KEY") or os.environ.get("OPENAI_API_KEY")


def get_llm_base_url() -> str:
    return get_setting(LLM_BASE_URL_KEY) or DEFAULT_LLM_BASE_URL


def get_llm_model() -> str | None:
    return get_setting(LLM_MODEL_KEY)


def get_provider_preset() -> str:
    return get_setting(LLM_PROVIDER_PRESET_KEY) or "xkiro"


def set_provider_preset(preset: str) -> None:
    set_setting(LLM_PROVIDER_PRESET_KEY, preset)


def get_vlm_model() -> str:
    return get_setting(VLM_MODEL_KEY) or get_llm_model() or DEFAULT_XKIRO_GENERATION_MODEL


def set_vlm_model(model: str) -> None:
    set_setting(VLM_MODEL_KEY, model)


def get_xkiro_generation_model() -> str:
    """Model used for /remake content generation (translate, rewrite, judge, characters)."""
    return get_setting(XKIRO_GENERATION_MODEL_KEY) or DEFAULT_XKIRO_GENERATION_MODEL


def set_xkiro_generation_model(model: str) -> None:
    set_setting(XKIRO_GENERATION_MODEL_KEY, model)


def get_xkiro_vlm_model() -> str:
    """Vision model used for image/clip style checks on the xKiro gateway."""
    return get_setting(XKIRO_VLM_MODEL_KEY) or DEFAULT_XKIRO_VLM_MODEL


def set_xkiro_vlm_model(model: str) -> None:
    set_setting(XKIRO_VLM_MODEL_KEY, model)


def is_vlm_enabled() -> bool:
    val = get_setting(VLM_ENABLED_KEY)
    if val is None:
        return False
    return val.strip().lower() in ("true", "1", "yes")


def set_vlm_enabled(enabled: bool) -> None:
    set_setting(VLM_ENABLED_KEY, "true" if enabled else "false")


def verify_provider_connection(
    base_url: str,
    api_key: str | None,
    model_name: str,
    test_vision: bool = True,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Test connectivity and vision capabilities with a provider endpoint."""
    import time

    try:
        from openai import OpenAI
    except ImportError as exc:
        return {
            "success": False,
            "message": f"Package openai chưa được cài đặt: {exc}",
            "latency_ms": 0.0,
            "is_multimodal": False,
        }

    if not (base_url or "").strip():
        return {
            "success": False,
            "message": "Vui lòng nhập Base URL.",
            "latency_ms": 0.0,
            "is_multimodal": False,
        }

    start_t = time.perf_counter()
    try:
        client = OpenAI(
            api_key=api_key or "sk-no-key-required",
            base_url=base_url.strip(),
            timeout=timeout,
        )
        if test_vision:
            tiny_png = (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
            )
            response = client.chat.completions.create(
                model=model_name or "gemini-2.0-flash",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Ping test: trả về đúng chữ OK."},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{tiny_png}"},
                            },
                        ],
                    }
                ],
                max_tokens=10,
                temperature=0.0,
            )
            is_multimodal = True
        else:
            response = client.chat.completions.create(
                model=model_name or "gemini-2.0-flash",
                messages=[{"role": "user", "content": "Ping test: trả về đúng chữ OK."}],
                max_tokens=10,
                temperature=0.0,
            )
            is_multimodal = False

        latency = (time.perf_counter() - start_t) * 1000.0
        reply = response.choices[0].message.content if response.choices else "OK"
        return {
            "success": True,
            "message": f"Kết nối thành công! Phản hồi ({latency:.0f}ms): {str(reply).strip()[:50]}",
            "latency_ms": round(latency, 1),
            "is_multimodal": is_multimodal,
        }
    except Exception as exc:  # noqa: BLE001
        latency = (time.perf_counter() - start_t) * 1000.0
        err_msg = str(exc)
        if "401" in err_msg or "auth" in err_msg.lower():
            reason = "Lỗi xác thực (401 Unauthorized): API key không hợp lệ hoặc đã hết hạn."
        elif "404" in err_msg or "not found" in err_msg.lower():
            reason = f"Không tìm thấy model hoặc endpoint (404 Not Found): Hãy kiểm tra Base URL và tên model '{model_name}'."
        elif "vision" in err_msg.lower() or "image" in err_msg.lower():
            reason = f"Model '{model_name}' không hỗ trợ ảnh/Vision: Hãy chọn model có thị giác."
        elif "timeout" in err_msg.lower():
            reason = f"Hết thời gian chờ ({timeout}s): Server phản hồi quá chậm."
        else:
            reason = f"Lỗi kết nối: {err_msg[:200]}"

        return {
            "success": False,
            "message": reason,
            "latency_ms": round(latency, 1),
            "is_multimodal": False,
        }

