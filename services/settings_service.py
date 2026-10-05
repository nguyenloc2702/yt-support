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

from cryptography.fernet import Fernet, InvalidToken

from config.settings import settings
from core.database import SessionLocal
from core.models import AppSetting
from core.logging_config import get_logger

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


# ---- LLM-specific convenience keys ----

DEFAULT_LLM_BASE_URL = "https://api.xkiro.com/v1"
LLM_KEY = "llm_api_key"
LLM_BASE_URL_KEY = "llm_base_url"
LLM_MODEL_KEY = "llm_model"


def get_llm_api_key() -> str | None:
    """API key from DB (encrypted) or fallback to environment/.env."""
    return get_setting(LLM_KEY) or os.environ.get("XKIRO_API_KEY") or os.environ.get("OPENAI_API_KEY")


def get_llm_base_url() -> str:
    return get_setting(LLM_BASE_URL_KEY) or DEFAULT_LLM_BASE_URL


def get_llm_model() -> str | None:
    return get_setting(LLM_MODEL_KEY)
