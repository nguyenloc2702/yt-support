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
import re
import time

from fastapi import HTTPException, Request

from .config import agent_codes, api_keys, session_secret, web_pin

log = logging.getLogger("video-system.auth")

SCOPE_RANK = {"read": 0, "ingest": 1, "manager": 2, "admin": 3}

# Identity đi vào token dạng chuỗi phân tách bằng '.', nên phải chặn ký tự '.'
# và mọi thứ không an toàn cho cookie/header.
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

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

    def identity_for(self, request: Request) -> tuple[str, str]:
        """(định danh agent, scope).

        Ba nguồn, theo thứ tự:
        1. Session cookie (PIN owner hoặc mã agent đã đổi token) — token mang
           sẵn identity + scope, ký HMAC.
        2. `Authorization: Bearer <key>` với key dài trong VIDEO_API_KEYS.
        3. `Authorization: Bearer <token>` — token lấy từ /auth/login, để agent
           không phải giữ key dài trong script.

        Key `video-agent-1:ingest` → identity `video-agent-1`. Chat board dùng
        identity này làm sender, vậy mỗi agent cần key riêng.
        """
        session = webauth.session_identity(request)
        if session:
            return session
        keys = self.keys or api_keys()
        if keys:
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                raise HTTPException(401, "missing bearer key")
            key = header[len("Bearer "):].strip()
            scope = keys.get(key)
            if scope is not None:
                name = key.rsplit(":", 1)[0] if ":" in key else key
                return (name or "agent"), scope
            as_token = self.verify_token(key)
            if as_token:
                return as_token
            raise HTTPException(401, "unknown key")
        if not webauth.is_direct_loopback(request):
            raise HTTPException(401, "API keys unconfigured; remote access denied")
        return "loopback", "admin"

    def scope_for(self, request: Request) -> str:
        _, scope = self.identity_for(request)
        return scope

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

    def login_with_code(self, code: str, ip: str) -> tuple[str, str]:
        """Đổi mã 6 số của agent lấy (identity, scope). Sai thì 401, khoá theo IP.

        Mã 6 số chỉ 10^6 tổ hợp, nên khoá sau 5 lần sai là bắt buộc — không có
        nó thì một script dò hết bảng mã trong vài phút.
        """
        now_ts = time.time()
        entry = self._fails.get(ip, [0, 0.0])
        if entry[1] > now_ts:
            wait = int(entry[1] - now_ts) + 1
            raise HTTPException(429, f"Nhập sai quá nhiều lần — thử lại sau {wait}s")

        match = agent_codes().get((code or "").strip())
        if not match:
            self._register_fail(ip, now_ts)
            return ("", "")  # không tới được: _register_fail luôn ném
        self._fails.pop(ip, None)
        return match

    def _register_fail(self, ip: str, now_ts: float) -> None:
        entry = self._fails.setdefault(ip, [0, 0.0])
        entry[0] += 1
        if entry[0] >= PIN_MAX_FAILS:
            entry[1] = now_ts + PIN_LOCKOUT_SEC
            entry[0] = 0
            log.warning("code lockout %ss for %s", PIN_LOCKOUT_SEC, ip)
            raise HTTPException(429, f"Nhập sai {PIN_MAX_FAILS} lần — khóa {PIN_LOCKOUT_SEC}s")
        raise HTTPException(401, f"Mã không đúng (còn {PIN_MAX_FAILS - entry[0]} lần thử)")

    # -- session token -------------------------------------------------------

    def make_token(self, identity: str = "owner", scope: str = "admin") -> str:
        """Token mang sẵn identity + scope, ký HMAC — không cần tra DB.

        Định dạng `exp.identity.scope.mac`. Đổi từ `exp.mac` (v1) sang v2 vì
        token cũ luôn ngầm hiểu là owner/admin; muốn nhiều agent dùng mã 6 số
        thì identity phải nằm trong token, nếu không mọi agent thành `owner`.
        """
        exp = format(int(time.time()) + SESSION_TTL_SEC, "x")
        mac = self._sign(exp, identity, scope)
        return f"{exp}.{identity}.{scope}.{mac}"

    def _sign(self, exp: str, identity: str, scope: str) -> str:
        msg = f"v2:{exp}:{identity}:{scope}".encode()
        return hmac.new(self.secret, msg, hashlib.sha256).hexdigest()

    def verify_token(self, token: str) -> tuple[str, str] | None:
        """(identity, scope) nếu token hợp lệ và chưa hết hạn, ngược lại None."""
        try:
            exp, identity, scope, mac = token.split(".")
        except (ValueError, AttributeError):
            return None
        if not identity or not _IDENTITY_RE.fullmatch(identity):
            return None
        if scope not in SCOPE_RANK:
            return None
        if not hmac.compare_digest(mac, self._sign(exp, identity, scope)):
            return None
        try:
            if int(exp, 16) <= time.time():
                return None
        except ValueError:
            return None
        return identity, scope

    def session_identity(self, request: Request) -> tuple[str, str] | None:
        token = request.cookies.get(SESSION_COOKIE, "")
        return self.verify_token(token) if token else None

    def session_ok(self, request: Request) -> bool:
        return self.session_identity(request) is not None

    def bearer_ok(self, request: Request) -> bool:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        key = header[len("Bearer "):].strip()
        if key in api_keys():
            return True
        return self.verify_token(key) is not None

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

