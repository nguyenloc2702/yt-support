"""Config loader — YAML files are the SINGLE source of every number.

domain.py / qc.py must read from here; no number is repeated in code.
If a config key is missing the app refuses to start (fail-closed).
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("VIDEO_CONFIG_DIR", REPO_ROOT / "config"))


def _load_dotenv_env() -> None:
    """Đọc .env từ REPO_ROOT (KEY=VALUE) — KHÔNG đè env đã set ngoài.
    Không có .env → bỏ qua (không bắt buộc)."""
    p = REPO_ROOT / ".env"
    if not p.exists():
        return
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv_env()


def _load(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        raise RuntimeError(f"missing required config: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"invalid config: {path}")
    return data


RENDER_SPEC = _load("render_spec.yaml")
AUDIO_CONFIG = _load("audio_config.yaml")
STYLE_LOCK = _load("style_lock_v3.yaml")
# Phụ lục G — Short Studio (9:16). File mới, không đụng render_spec.yaml.
RENDER_SPEC_SHORT = _load("render_spec_short.yaml")
SHORT_CONFIG = _load("short_config.yaml")
# Phụ lục G.10 — hộp tin nóng RSS + Telegram.
NEWS_CONFIG = _load("news_config.yaml")


def data_dir() -> Path:
    d = Path(os.environ.get("VIDEO_DATA_DIR", REPO_ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    (d / "objects").mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "video_system.sqlite3"


def api_keys() -> dict[str, str]:
    """Parse VIDEO_API_KEYS 'key:scope,key:scope'. Empty dict = unconfigured."""
    raw = os.environ.get("VIDEO_API_KEYS", "").strip()
    keys: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise RuntimeError(f"invalid VIDEO_API_KEYS entry (want key:scope): {part}")
        k, scope = part.rsplit(":", 1)
        if scope not in {"read", "ingest", "admin"}:
            raise RuntimeError(f"invalid scope in VIDEO_API_KEYS: {scope}")
        keys[k] = scope
    return keys


def allowed_hosts() -> list[str]:
    raw = os.environ.get("VIDEO_ALLOWED_HOSTS", "vidfac.gacdero.vn,talk.gacdero.vn,localhost,127.0.0.1")
    return [h.strip() for h in raw.split(",") if h.strip()]


def public_base_url() -> str:
    return os.environ.get("VIDEO_PUBLIC_BASE_URL", "https://vidfac.gacdero.vn").rstrip("/")


def webhook_secret() -> str:
    return os.environ.get("VIDEO_WEBHOOK_SECRET", "")


def web_pin() -> str:
    """Mã PIN 6 số mở khóa web UI (đăng nhập session cookie)."""
    pin = os.environ.get("VIDEO_WEB_PIN", "997220").strip()
    if len(pin) != 6 or not pin.isdigit():
        raise RuntimeError("VIDEO_WEB_PIN must be exactly 6 digits")
    return pin


def session_secret() -> str:
    """Khóa HMAC ký token session. Ưu tiên VIDEO_SESSION_SECRET;
    nếu unset thì sinh ngẫu nhiên và lưu vào data dir (giữ session qua restart)."""
    env = os.environ.get("VIDEO_SESSION_SECRET", "").strip()
    if env:
        return env
    key_file = data_dir() / "session_secret.key"
    if not key_file.exists():
        import secrets
        key_file.write_text(secrets.token_hex(32), encoding="utf-8")
    return key_file.read_text(encoding="utf-8").strip()

