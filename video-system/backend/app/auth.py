"""Auth — API key scopes (read < ingest < admin), deny-by-default.

Unconfigured VIDEO_API_KEYS: ONLY loopback clients are served (local-dev),
with scope admin, and a startup warning is logged. Remote clients get 401
always. Configured: Bearer key required, scope enforced per endpoint.

Web UI auth: PIN 6 số (VIDEO_WEB_PIN) → session cookie HttpOnly ký HMAC.
Browser requests (có Sec-Fetch-Site) phải có session hoặc Bearer key hợp lệ;
programmatic clients (curl/agent/test) giữ nguyên luật loopback/bearer cũ.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time

from fastapi import HTTPException, Request

from .config import api_keys, session_secret, web_pin

log = logging.getLogger("video-system.auth")

SCOPE_RANK = {"read": 0, "ingest": 1, "admin": 2}

SESSION_COOKIE = "vs_session"
SESSION_TTL_SEC = 30 * 24 * 3600  # 30 ngày
PIN_MAX_FAILS = 5
PIN_LOCKOUT_SEC = 60


class Auth:
    def __init__(self) -> None:
        self.keys = api_keys()
        if not self.keys:
            log.warning(
                "VIDEO_API_KEYS is UNSET — only loopback clients are served. "
                "Set VIDEO_API_KEYS before exposing this API.")

    def scope_for(self, request: Request) -> str:
        if webauth.session_ok(request):
            return "admin"  # chủ hệ thống đăng nhập PIN = toàn quyền
        keys = self.keys or api_keys()
        if keys:
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                raise HTTPException(401, "missing bearer key")
            key = header[len("Bearer "):].strip()
            scope = keys.get(key)
            if scope is None:
                raise HTTPException(401, "unknown key")
            return scope
        if not webauth.is_direct_loopback(request):
            raise HTTPException(401, "API keys unconfigured; remote access denied")
        return "admin"

    def require(self, request: Request, needed: str) -> str:
        scope = self.scope_for(request)
        if SCOPE_RANK[scope] < SCOPE_RANK[needed]:
            raise HTTPException(403, f"requires scope {needed}")
        return scope


def is_unsafe_open() -> bool:
    return not api_keys()


class WebAuth:
    """PIN gate cho web UI: verify PIN, ký/kiểm session token, khóa dần khi sai."""

    def __init__(self) -> None:
        self.pin_sha = hashlib.sha256(web_pin().encode()).digest()
        self.secret = session_secret().encode()
        self._fails: dict[str, list] = {}  # ip -> [số lần sai, locked_until]

    @staticmethod
    def client_ip(request: Request) -> str:
        """Lấy IP thật của client khi đứng sau Nginx reverse proxy."""
        xff = request.headers.get("x-forwarded-for", "").strip()
        if xff:
            return xff.split(",")[0].strip()
        xri = request.headers.get("x-real-ip", "").strip()
        if xri:
            return xri
        return request.client.host if request.client else ""

    @staticmethod
    def is_direct_loopback(request: Request) -> bool:
        """Chỉ coi là loopback khi kết nối trực tiếp (không qua reverse proxy)."""
        if request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
            return False
        client = request.client.host if request.client else ""
        return client in {"127.0.0.1", "::1", "testclient"}

    # -- PIN ---------------------------------------------------------------

    def verify_pin(self, pin: str, ip: str) -> None:
        """Ném HTTPException nếu sai PIN hoặc đang bị khóa."""
        now_ts = time.time()
        entry = self._fails.get(ip, [0, 0.0])
        _, locked_until = entry
        if locked_until > now_ts:
            wait = int(locked_until - now_ts) + 1
            raise HTTPException(429, f"Nhập sai quá nhiều lần — thử lại sau {wait}s")
        if hmac.compare_digest(self.pin_sha, hashlib.sha256((pin or "").encode()).digest()):
            self._fails.pop(ip, None)
            return
        entry[0] += 1
        if entry[0] >= PIN_MAX_FAILS:
            entry[1] = now_ts + PIN_LOCKOUT_SEC
            entry[0] = 0
            self._fails[ip] = entry
            log.warning("PIN lockout %ss for %s", PIN_LOCKOUT_SEC, ip)
            raise HTTPException(429, f"Nhập sai {PIN_MAX_FAILS} lần — khóa {PIN_LOCKOUT_SEC}s")
        self._fails[ip] = entry
        raise HTTPException(401, f"Mã PIN không đúng (còn {PIN_MAX_FAILS - entry[0]} lần thử)")

    # -- session token -------------------------------------------------------

    def make_token(self) -> str:
        exp = format(int(time.time()) + SESSION_TTL_SEC, "x")
        mac = hmac.new(self.secret, f"v1:{exp}".encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{mac}"

    def verify_token(self, token: str) -> bool:
        try:
            exp, mac = token.split(".", 1)
            want = hmac.new(self.secret, f"v1:{exp}".encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(mac, want):
                return False
            return int(exp, 16) > time.time()
        except (ValueError, TypeError):
            return False

    def session_ok(self, request: Request) -> bool:
        token = request.cookies.get(SESSION_COOKIE, "")
        return bool(token) and self.verify_token(token)

    def bearer_ok(self, request: Request) -> bool:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            key = header[len("Bearer "):].strip()
            if key in api_keys():
                return True
        return False

    # -- middleware rule -----------------------------------------------------

    @staticmethod
    def is_browser(request: Request) -> bool:
        """Browser luôn gửi Sec-Fetch-Site hoặc Sec-Fetch-Mode trên fetch/XHR; curl/agent không gửi."""
        return bool(request.headers.get("sec-fetch-site") or request.headers.get("sec-fetch-mode"))

    def request_allowed(self, request: Request) -> bool:
        """Browser BẮT BUỘC phải có session cookie PIN hợp lệ (kể cả khi reverse proxy chèn devkey-read).
        Programmatic client / Agent (không có Sec-Fetch-*) dùng Bearer key hoặc direct loopback."""
        if self.session_ok(request):
            return True
        if self.is_browser(request):
            return False
        if self.bearer_ok(request):
            return True
        if not api_keys() and self.is_direct_loopback(request):
            return True
        return False


webauth = WebAuth()  # singleton dùng chung cho middleware + scope_for

