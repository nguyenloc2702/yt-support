"""Instagram Reels publishing service (Instagram API with Instagram Login).

Flow: OAuth (HMAC state, one-time nonce) -> long-lived token (60d, self-refresh)
-> create REELS container (public signed video URL) -> poll status_code ->
publish -> permalink. Mirrors service_youtube.py patterns: Fernet at-rest,
idempotency guard, state machine, startup sweep, webhook events.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from cryptography.fernet import Fernet, InvalidToken

from .config import (
    data_dir,
    ig_publish_wait_minutes,
    ig_token_encryption_key,
    instagram_app_id,
    instagram_app_secret,
    public_base_url,
    session_secret,
)
from .store import Store, new_id, now
from .webhooks import WebhookService

logger = logging.getLogger("video-system.instagram")

IG_AUTH_URL = "https://www.instagram.com/oauth/authorize"
IG_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
IG_GRAPH = "https://graph.instagram.com"

IG_SCOPES = [
    "instagram_business_basic",
    "instagram_business_content_publish",
]

IG_CAPTION_MAX_RUNES = 2200
IG_AD_TAG = "#ad"
IG_ABOVE_FOLD_RUNES = 125  # FTC: disclosure trước nút "more"
TOKEN_REFRESH_MARGIN_SEC = 7 * 24 * 3600
OAUTH_STATE_TTL_SEC = 600
MEDIA_URL_TTL_SEC = 24 * 3600
CONTAINER_MAX_AGE_SEC = 20 * 3600  # container sống ~24h; an toàn ở 20h
ACTIVE_PUBLISH_STATUSES = ("queued", "creating", "processing")
PT_TZ = timezone(timedelta(hours=-5), "EST")  # chỉ để reset message; quota reset theo rolling window


class InstagramError(Exception):
    """Base error with HTTP status code for API mapping."""

    def __init__(self, message: str, status_code: int = 400, code: str = "instagram_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class InstagramConfigError(InstagramError):
    def __init__(self, message: str):
        super().__init__(message, status_code=503, code="instagram_not_configured")


class InstagramQuotaExceededError(InstagramError):
    def __init__(self, message: str, reset_at: str | None = None):
        super().__init__(message, status_code=429, code="instagram_quota_exceeded")
        self.reset_at = reset_at


class InstagramConflictError(InstagramError):
    def __init__(self, message: str, publish_id: str | None = None):
        super().__init__(message, status_code=409, code="instagram_publish_in_progress")
        self.publish_id = publish_id


def build_caption(
    *,
    body: str,
    affiliate_slug: str | None = None,
    is_affiliate: bool = False,
    base_url: str | None = None,
    max_runes: int = IG_CAPTION_MAX_RUNES,
) -> str:
    """Caption chuẩn: hook -> body -> CTA -> disclosure. #ad luôn nằm trong
    125 rune đầu (FTC: disclosure trước nút "more"). Cắt mượt khi vượt limit,
    luôn giữ #ad + CTA ở cuối."""
    base_url = base_url or public_base_url()
    lines = [ln.strip() for ln in (body or "").strip().splitlines() if ln.strip()]
    hook = lines[0] if lines else ""
    rest = lines[1:]
    cta = f"🔗 Link in bio → {base_url}/b/{affiliate_slug}" if affiliate_slug else ""
    ad = IG_AD_TAG if is_affiliate else ""

    if ad and hook and len(hook) + len(ad) + 1 <= IG_ABOVE_FOLD_RUNES:
        head = f"{hook} {ad}"
        tail_lines = rest
    elif ad:
        head = hook
        tail_lines = ([ad, *rest] if hook else rest)
    else:
        head = hook
        tail_lines = rest

    caption = "\n".join([x for x in (head, *tail_lines, cta) if x])
    if len(caption) <= max_runes:
        return caption

    tail = "\n".join([x for x in ([ad] if ad and not head.endswith(ad) else [], cta) if x])
    budget = max_runes - (len(tail) + 1 if tail else 0)
    cur = head[:budget] if len(head) > budget else head
    kept: list[str] = []
    for ln in tail_lines:
        candidate = f"{cur}\n{ln}" if cur else ln
        if len(candidate) + (1 if tail else 0) > budget:
            break
        cur = candidate
        kept.append(ln)
    out = "\n".join([x for x in (cur, tail) if x])
    return out[:max_runes]


class IgTokenManager:
    """Fernet at-rest + auto-refresh long-lived token (60 ngày, refresh được vô hạn)."""

    def __init__(self, store: Store, http_factory: Callable[[], httpx.Client]):
        self.store = store
        self._http_factory = http_factory
        self._refresh_lock = threading.Lock()

    @staticmethod
    def _fernet() -> Fernet:
        raw = ig_token_encryption_key()
        if not raw:
            raise InstagramConfigError(
                "Thiếu IG_TOKEN_ENCRYPTION_KEY trong cấu hình môi trường."
            )
        try:
            return Fernet(raw.encode("utf-8"))
        except Exception as exc:
            raise InstagramConfigError(f"IG_TOKEN_ENCRYPTION_KEY không hợp lệ: {exc}") from exc

    def encrypt(self, value: str) -> str:
        return self._fernet().encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, token_enc: str) -> str:
        try:
            return self._fernet().decrypt(token_enc.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise InstagramConfigError("Không giải mã được token Instagram (encryption key đã đổi?).") from exc

    def get_valid_access_token(self, account_id: str | None = None) -> tuple[dict[str, Any], str]:
        with self._refresh_lock:
            row = (
                self.store.query_one("SELECT * FROM ig_accounts WHERE id=?", (account_id,))
                if account_id
                else self.store.query_one(
                    "SELECT * FROM ig_accounts WHERE status='connected' "
                    "ORDER BY is_default DESC, updated_at DESC LIMIT 1"
                )
            )
            if not row or row["status"] != "connected":
                raise InstagramError(
                    "Kết nối Instagram đã hết hạn hoặc bị ngắt. Vui lòng kết nối lại.",
                    status_code=401,
                    code="instagram_disconnected",
                )
            access_token = self.decrypt(row["access_token_enc"])
            expires_at = float(row.get("token_expires_at") or 0.0)
            if expires_at - time.time() > TOKEN_REFRESH_MARGIN_SEC:
                return row, access_token

            app_secret = instagram_app_secret()
            if not app_secret:
                raise InstagramConfigError("Thiếu INSTAGRAM_APP_SECRET.")

            with self._http_factory() as client:
                resp = client.get(
                    f"{IG_GRAPH}/refresh_access_token",
                    params={"grant_type": "ig_refresh_token", "access_token": access_token},
                )
            if resp.status_code != 200:
                self.store.execute(
                    "UPDATE ig_accounts SET status='expired', updated_at=? WHERE id=?",
                    (now(), row["id"]),
                )
                raise InstagramError(
                    "Token Instagram đã bị thu hồi hoặc không refresh được. Vui lòng kết nối lại.",
                    status_code=401,
                    code="instagram_token_expired",
                )
            tok = resp.json()
            new_access = tok["access_token"]
            new_exp = time.time() + float(tok.get("expires_in", 60 * 24 * 3600))
            self.store.execute(
                "UPDATE ig_accounts SET access_token_enc=?, token_expires_at=?, updated_at=? WHERE id=?",
                (self.encrypt(new_access), new_exp, now(), row["id"]),
            )
            updated = self.store.query_one("SELECT * FROM ig_accounts WHERE id=?", (row["id"],))
            return updated or row, new_access


class InstagramService:
    """OAuth connection + REELS direct publishing via Content Publishing API."""

    def __init__(
        self,
        store: Store,
        webhooks: WebhookService,
        *,
        http_factory: Callable[[], httpx.Client] | None = None,
        poll_interval_sec: float = 30.0,
        scheduler_interval_sec: float = 60.0,
        retry_sleep_fn: Callable[[float], None] | None = None,
        enable_scheduler: bool = True,
    ):
        self.store = store
        self.webhooks = webhooks
        self._http_factory = http_factory or (lambda: httpx.Client(timeout=60.0))
        self.tokens = IgTokenManager(store, self._http_factory)
        self._poll_interval_sec = poll_interval_sec
        self._scheduler_interval_sec = scheduler_interval_sec
        self._sleep = retry_sleep_fn or time.sleep
        self._active_threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._scheduler_started = False
        self._enable_scheduler = enable_scheduler

    # -- config ---------------------------------------------------------------

    @staticmethod
    def is_configured() -> bool:
        return bool(instagram_app_id() and instagram_app_secret() and ig_token_encryption_key())

    @staticmethod
    def redirect_uri() -> str:
        return f"{public_base_url()}/api/v1/instagram/auth/callback"

    def _emit_event(self, event_type: str, item_id: str | None, payload: dict[str, Any]) -> None:
        ev = {
            "id": new_id("evt"),
            "item_id": item_id,
            "shot_id": None,
            "type": event_type,
            "actor": "instagram",
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "at": now(),
        }
        self.store.execute(
            "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) VALUES (?,?,?,?,?,?,?)",
            (ev["id"], ev["item_id"], ev["shot_id"], ev["type"], ev["actor"], ev["payload_json"], ev["at"]),
        )
        self.webhooks.dispatch_event(ev)

    # -- signed media URL (IG phải tải được video) ----------------------------

    def make_signed_media_url(self, item_id: str, target_type: str) -> str:
        exp = int(time.time()) + MEDIA_URL_TTL_SEC
        msg = f"igmedia.{item_id}.{target_type}.{exp}".encode("utf-8")
        sig = hmac.new(session_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()
        qs = urllib.parse.urlencode({"token": f"{exp}.{sig}"})
        return f"{public_base_url()}/api/v1/instagram/media/{urllib.parse.quote(item_id)}?{qs}&target_type={urllib.parse.quote(target_type)}"

    @staticmethod
    def verify_signed_media_token(item_id: str, target_type: str, token: str) -> bool:
        try:
            exp_str, sig = (token or "").split(".", 1)
            exp = int(exp_str)
        except (ValueError, TypeError):
            return False
        if exp < int(time.time()):
            return False
        msg = f"igmedia.{item_id}.{target_type}.{exp}".encode("utf-8")
        want = hmac.new(session_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, want)

    # -- OAuth 2.0 ------------------------------------------------------------

    def make_auth_url(self) -> str:
        cid = instagram_app_id()
        if not cid or not instagram_app_secret():
            raise InstagramConfigError("Chưa cấu hình INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET.")
        self.tokens._fernet()

        nonce = secrets.token_urlsafe(16)
        exp = int(time.time()) + OAUTH_STATE_TTL_SEC
        msg = f"{nonce}.{exp}".encode("utf-8")
        sig = hmac.new(session_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()
        state = f"{nonce}.{exp}.{sig}"
        self.store.execute(
            "INSERT INTO ig_oauth_nonces (nonce, expires_at, used_at) VALUES (?, ?, NULL)",
            (nonce, float(exp)),
        )
        params = {
            "client_id": cid,
            "redirect_uri": self.redirect_uri(),
            "response_type": "code",
            "scope": ",".join(IG_SCOPES),
            "state": state,
        }
        return f"{IG_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def _verify_and_consume_state(self, state: str) -> None:
        parts = (state or "").split(".")
        if len(parts) != 3:
            raise InstagramError("OAuth state không hợp lệ.", status_code=400, code="invalid_oauth_state")
        nonce, exp_str, sig = parts
        try:
            exp = int(exp_str)
        except ValueError as exc:
            raise InstagramError("OAuth state không hợp lệ.", status_code=400, code="invalid_oauth_state") from exc
        if exp < int(time.time()):
            raise InstagramError("OAuth state đã hết hạn.", status_code=400, code="expired_oauth_state")
        want_sig = hmac.new(
            session_secret().encode("utf-8"),
            f"{nonce}.{exp}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, want_sig):
            raise InstagramError("Chữ ký OAuth state không hợp lệ.", status_code=400, code="invalid_oauth_state")
        t = now()
        with self.store.transaction() as conn:
            cur = conn.execute(
                "UPDATE ig_oauth_nonces SET used_at=? "
                "WHERE nonce=? AND used_at IS NULL AND expires_at >= ?",
                (t, nonce, t),
            )
            if cur.rowcount != 1:
                raise InstagramError("OAuth state đã được sử dụng hoặc hết hạn.", status_code=400, code="replayed_oauth_state")

    def handle_callback(self, code: str, state: str) -> dict[str, Any]:
        self._verify_and_consume_state(state)
        cid = instagram_app_id()
        csec = instagram_app_secret()
        if not cid or not csec:
            raise InstagramConfigError("Chưa cấu hình INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET.")

        with self._http_factory() as client:
            tok_resp = client.post(
                IG_TOKEN_URL,
                data={
                    "client_id": cid,
                    "client_secret": csec,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri(),
                    "code": code,
                },
            )
            if tok_resp.status_code != 200:
                raise InstagramError(
                    f"Đổi OAuth code thất bại: HTTP {tok_resp.status_code} {tok_resp.text}",
                    status_code=400,
                    code="oauth_exchange_failed",
                )
            short_tok = tok_resp.json()
            access_token = short_tok.get("access_token") or ""
            if not access_token:
                raise InstagramError("Instagram không trả về access_token.", status_code=400, code="oauth_no_token")

            # short-lived (1h) -> long-lived (~60 ngày)
            ll_resp = client.get(
                f"{IG_GRAPH}/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": csec,
                    "access_token": access_token,
                },
            )
            if ll_resp.status_code != 200:
                raise InstagramError(
                    f"Đổi long-lived token thất bại: HTTP {ll_resp.status_code} {ll_resp.text}",
                    status_code=502,
                    code="oauth_exchange_failed",
                )
            ll = ll_resp.json()
            long_access = ll.get("access_token") or access_token
            expires_at = time.time() + float(ll.get("expires_in", 60 * 24 * 3600))

            me_resp = client.get(
                f"{IG_GRAPH}/me",
                params={"fields": "user_id,username,account_type,media_count", "access_token": long_access},
            )
            if me_resp.status_code != 200:
                raise InstagramError(
                    f"Không lấy được thông tin tài khoản Instagram: HTTP {me_resp.status_code}",
                    status_code=502,
                )
            me = me_resp.json()
            ig_user_id = str(me.get("user_id") or me.get("id") or "")
            if not ig_user_id:
                raise InstagramError(
                    "Tài khoản Instagram không trả về user_id (tài khoản phải là Business/Creator?).",
                    status_code=400,
                    code="no_ig_user",
                )

        t = now()
        with self.store.transaction() as conn:
            conn.execute("UPDATE ig_accounts SET is_default=0")
            existing = conn.execute(
                "SELECT id FROM ig_accounts WHERE ig_user_id=?", (ig_user_id,)
            ).fetchone()
            if existing:
                row_id = existing["id"]
                conn.execute(
                    "UPDATE ig_accounts SET username=?, account_type=?, media_count=?, "
                    "access_token_enc=?, token_expires_at=?, status='connected', "
                    "is_default=1, updated_at=? WHERE id=?",
                    (
                        me.get("username") or "",
                        me.get("account_type") or "BUSINESS",
                        int(me.get("media_count") or 0),
                        self.tokens.encrypt(long_access),
                        expires_at,
                        t,
                        row_id,
                    ),
                )
            else:
                row_id = new_id("iga")
                conn.execute(
                    "INSERT INTO ig_accounts "
                    "(id,ig_user_id,username,account_type,media_count,access_token_enc,"
                    "token_expires_at,status,is_default,connected_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row_id,
                        ig_user_id,
                        me.get("username") or "",
                        me.get("account_type") or "BUSINESS",
                        int(me.get("media_count") or 0),
                        self.tokens.encrypt(long_access),
                        expires_at,
                        "connected",
                        1,
                        t,
                        t,
                    ),
                )

        info = self.get_account()
        self._emit_event("instagram.account.connected", None, {"ig_user_id": ig_user_id, "username": info.get("username")})
        return info

    # -- Account status / disconnect ------------------------------------------

    @staticmethod
    def _sanitize_account(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": row["id"],
            "ig_user_id": row["ig_user_id"],
            "username": row["username"],
            "account_type": row["account_type"],
            "media_count": int(row.get("media_count") or 0),
            "status": row["status"],
            "token_expires_at": row.get("token_expires_at"),
            "connected_at": row.get("connected_at"),
            "updated_at": row.get("updated_at"),
        }

    def get_account(self) -> dict[str, Any]:
        row = self.store.query_one(
            "SELECT * FROM ig_accounts ORDER BY is_default DESC, updated_at DESC LIMIT 1"
        )
        if not row or row["status"] != "connected":
            raise InstagramError("Chưa kết nối tài khoản Instagram.", status_code=404, code="instagram_not_connected")
        return self._sanitize_account(row) or {}

    def get_quota(self) -> dict[str, Any]:
        """Đọc content_publishing_limit chính chủ từ Meta (100 bài/24h rolling)."""
        row, token = self.tokens.get_valid_access_token()
        with self._http_factory() as client:
            resp = client.get(
                f"{IG_GRAPH}/{row['ig_user_id']}/content_publishing_limit",
                params={"fields": "quota_total,quota_remaining,quota_reset_at", "access_token": token},
            )
        if resp.status_code != 200:
            return {"available": True, "quota_total": 100, "quota_remaining": None, "reset_at": None, "checked": False}
        data = ((resp.json() or {}).get("data") or [{}])[0]
        remaining = data.get("quota_remaining")
        return {
            "available": int(remaining or 0) > 0,
            "quota_total": int(data.get("quota_total") or 100),
            "quota_remaining": int(remaining) if remaining is not None else None,
            "reset_at": data.get("quota_reset_at"),
            "checked": True,
        }

    def get_status(self) -> dict[str, Any]:
        row = self.store.query_one(
            "SELECT * FROM ig_accounts ORDER BY is_default DESC, updated_at DESC LIMIT 1"
        )
        connected = bool(row and row["status"] == "connected")
        quota: dict[str, Any] | None = None
        if connected:
            try:
                quota = self.get_quota()
            except InstagramError as exc:
                # Token chết/fail-open: không chặn trang dashboard, chỉ báo checked=False
                row = self.store.query_one(
                    "SELECT * FROM ig_accounts ORDER BY is_default DESC, updated_at DESC LIMIT 1"
                )
                connected = bool(row and row["status"] == "connected")
                quota = {"checked": False, "available": False, "error": exc.message}
        return {
            "configured": self.is_configured(),
            "connected": connected,
            "account": self._sanitize_account(row) if row else None,
            "quota": quota,
            "redirect_uri": self.redirect_uri(),
        }

    def disconnect_account(self) -> dict[str, Any]:
        row = self.store.query_one(
            "SELECT * FROM ig_accounts ORDER BY is_default DESC, updated_at DESC LIMIT 1"
        )
        if not row:
            return {"disconnected": True}
        ig_user_id = row["ig_user_id"]
        t = now()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE ig_publishes SET status='canceled', error_message='Account disconnected', updated_at=? "
                "WHERE account_id=? AND status IN ('queued','creating','processing')",
                (t, row["id"]),
            )
            conn.execute("DELETE FROM ig_accounts WHERE id=?", (row["id"],))
        self._emit_event("instagram.account.disconnected", None, {"ig_user_id": ig_user_id})
        return {"disconnected": True, "ig_user_id": ig_user_id}

    # -- Artifacts ------------------------------------------------------------

    @staticmethod
    def _final_video_path(item_id: str) -> Path:
        return data_dir() / "objects" / item_id / "exports" / "final_en.mp4"

    def _resolve_video_path(self, target_type: str, item_id: str) -> tuple[bool, Path | None]:
        if target_type == "short":
            short = self.store.query_one(
                "SELECT video_asset_id FROM shorts WHERE id=?", (item_id,)
            )
            if short and short.get("video_asset_id"):
                v_asset = self.store.query_one(
                    "SELECT storage_key FROM assets WHERE id=?", (short["video_asset_id"],)
                )
                if v_asset and v_asset.get("storage_key"):
                    p = data_dir() / "objects" / v_asset["storage_key"]
                    return True, (p if p.exists() else None)
            return bool(short), None
        item = self.store.query_one(
            "SELECT id FROM items WHERE id=? AND status!='deleted'", (item_id,)
        )
        return bool(item), self._final_video_path(item_id)

    # -- Publish pipeline -----------------------------------------------------

    @staticmethod
    def _validate_publish_at(publish_at: str | None) -> str | None:
        if not publish_at:
            return None
        raw = publish_at.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise InstagramError("publish_at phải là chuỗi ISO-8601 hợp lệ.", status_code=422) from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < datetime.now(timezone.utc) + timedelta(minutes=1):
            raise InstagramError("publish_at phải ở tương lai.", status_code=422)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _format_publish(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "publish_id": row["id"],
            "item_id": row["item_id"],
            "target_type": row.get("target_type") or "todo",
            "username": row.get("username"),
            "caption": row.get("caption"),
            "share_to_feed": bool(row.get("share_to_feed", 1)),
            "is_ai_generated": bool(row.get("is_ai_generated", 1)),
            "affiliate_slug": row.get("affiliate_slug"),
            "container_id": row.get("container_id"),
            "ig_media_id": row.get("ig_media_id"),
            "permalink": row.get("permalink"),
            "publish_at": row.get("publish_at"),
            "status": row.get("status"),
            "attempt_count": int(row.get("attempt_count") or 0),
            "error_message": row.get("error_message"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def _join_affiliate_caption(self, item_id: str, caption: str | None) -> tuple[str, str | None]:
        """Gắn slug affiliate (nếu job có link) vào caption + auto #ad."""
        link = self.store.query_one(
            "SELECT slug, network FROM affiliate_links WHERE item_id=? ORDER BY created_at DESC LIMIT 1",
            (item_id,),
        )
        slug = link["slug"] if link else None
        final = build_caption(body=caption or "", affiliate_slug=slug, is_affiliate=bool(slug))
        return final, slug

    def publish(
        self,
        target_type: str,
        item_id: str,
        *,
        caption: str = "",
        affiliate_product_id: str | None = None,
        publish_at: str | None = None,
        share_to_feed: bool = True,
        is_ai_generated: bool = True,
        run_async: bool = True,
    ) -> dict[str, Any]:
        exists, video_path = self._resolve_video_path(target_type, item_id)
        if not exists:
            raise InstagramError(f"Không tìm thấy {target_type} {item_id}.", status_code=404)
        if not video_path or not video_path.exists() or video_path.stat().st_size == 0:
            raise InstagramError(
                "Chưa có file video MP4 thành phẩm.", status_code=422, code="missing_final_video"
            )
        size_mb = video_path.stat().st_size / (1024 * 1024)
        if size_mb > 300:
            raise InstagramError(
                f"Video quá lớn ({size_mb:.0f}MB > 300MB giới hạn Reels API).",
                status_code=422,
                code="video_too_large",
            )

        active = self.store.query_one(
            "SELECT * FROM ig_publishes "
            "WHERE item_id=? AND COALESCE(target_type,'todo')=? AND status IN ('queued','creating','processing') "
            "ORDER BY created_at DESC LIMIT 1",
            (item_id, target_type),
        )
        if active:
            label = "Short" if target_type == "short" else "Job"
            raise InstagramConflictError(
                f"{label} đang có tiến trình đăng Instagram ({active['status']}).",
                publish_id=active["id"],
            )

        norm_publish_at = self._validate_publish_at(publish_at)

        slug: str | None = None
        if affiliate_product_id:
            from .service_affiliate import AffiliateError, get_affiliate_service

            try:
                link = get_affiliate_service().attach_product(
                    item_id, affiliate_product_id, actor="instagram_publish"
                )
                slug = link["slug"]
            except AffiliateError as exc:
                raise InstagramError(exc.message, status_code=exc.status_code, code=exc.code) from exc
        else:
            link = self.store.query_one(
                "SELECT slug FROM affiliate_links WHERE item_id=? ORDER BY created_at DESC LIMIT 1",
                (item_id,),
            )
            slug = link["slug"] if link else None

        final_caption = build_caption(body=caption or "", affiliate_slug=slug, is_affiliate=bool(slug))

        account = self.get_account()
        publish_id = new_id("igp")
        t = now()
        status = "scheduled" if norm_publish_at else "queued"
        self.store.execute(
            "INSERT INTO ig_publishes "
            "(id,item_id,target_type,account_id,caption,share_to_feed,is_ai_generated,affiliate_slug,"
            "publish_at,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                publish_id,
                item_id,
                target_type,
                account["id"],
                final_caption,
                1 if share_to_feed else 0,
                1 if is_ai_generated else 0,
                slug,
                norm_publish_at,
                status,
                t,
                t,
            ),
        )
        if status == "queued":
            self._start_scheduler_if_needed()
            self._spawn_worker(publish_id, run_async=run_async)
        row = self.store.query_one("SELECT * FROM ig_publishes WHERE id=?", (publish_id,))
        return self._format_publish(row)

    # -- worker ---------------------------------------------------------------

    def _spawn_worker(self, publish_id: str, run_async: bool = True) -> None:
        if not run_async:
            self._run_publish(publish_id)
            return
        th = threading.Thread(target=self._run_publish, args=(publish_id,), daemon=True)
        with self._lock:
            self._active_threads[publish_id] = th
        th.start()

    def get_publish(self, publish_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM ig_publishes WHERE id=?", (publish_id,))
        if not row:
            raise InstagramError(f"Không tìm thấy publish {publish_id}.", status_code=404)
        return self._format_publish(row)

    def list_publishes(self, limit: int = 50) -> dict[str, Any]:
        rows = self.store.query("SELECT * FROM ig_publishes ORDER BY created_at DESC LIMIT ?", (max(1, min(200, limit)),))
        account = self.store.query_one(
            "SELECT * FROM ig_accounts ORDER BY is_default DESC, updated_at DESC LIMIT 1"
        )
        username = account["username"] if account else None
        out = []
        for r in rows:
            d = self._format_publish(r)
            d["username"] = username
            out.append(d)
        return {"publishes": out}

    def cancel_publish(self, publish_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM ig_publishes WHERE id=?", (publish_id,))
        if not row:
            raise InstagramError(f"Không tìm thấy publish {publish_id}.", status_code=404)
        if row["status"] in ("published", "canceled"):
            return self._format_publish(row)
        self.store.execute(
            "UPDATE ig_publishes SET status='canceled', error_message='Canceled by user', updated_at=? WHERE id=?",
            (now(), publish_id),
        )
        return self.get_publish(publish_id)

    def retry_publish(self, publish_id: str, run_async: bool = True) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM ig_publishes WHERE id=?", (publish_id,))
        if not row:
            raise InstagramError(f"Không tìm thấy publish {publish_id}.", status_code=404)
        if row["status"] == "published":
            return self._format_publish(row)
        if row["status"] in ACTIVE_PUBLISH_STATUSES:
            raise InstagramConflictError("Publish đang chạy.", publish_id=publish_id)
        self.store.execute(
            "UPDATE ig_publishes SET status='queued', container_id=NULL, error_message=NULL, updated_at=? WHERE id=?",
            (now(), publish_id),
        )
        self._start_scheduler_if_needed()
        self._spawn_worker(publish_id, run_async=run_async)
        return self.get_publish(publish_id)

    def startup_sweep(self, run_async: bool = True) -> list[str]:
        """Khôi phục interrupted publishes sau restart + hẹn giờ scheduler."""
        resumed: list[str] = []
        rows = self.store.query("SELECT * FROM ig_publishes WHERE status IN ('queued','creating','processing')")
        t = now()
        for r in rows:
            pid = r["id"]
            if r["status"] == "queued":
                self._spawn_worker(pid, run_async=run_async)
                resumed.append(pid)
                continue
            cid = r.get("container_id") or ""
            age = t - float(r.get("updated_at") or t)
            if not cid or age > CONTAINER_MAX_AGE_SEC:
                self.store.execute(
                    "UPDATE ig_publishes SET status='failed', "
                    "error_message='Interrupted on restart (container missing or expired)', updated_at=? WHERE id=?",
                    (t, pid),
                )
            else:
                self._spawn_worker(pid, run_async=run_async)
                resumed.append(pid)
        self._start_scheduler_if_needed()
        return resumed

    def start_scheduler(self) -> threading.Thread | None:
        """Thread nền: mỗi phút nhả các publish scheduled đến giờ."""
        self._start_scheduler_if_needed()
        return getattr(self, "_scheduler_thread", None)

    def _start_scheduler_if_needed(self) -> None:
        if self._scheduler_started or not self._enable_scheduler:
            return
        self._scheduler_started = True

        def _loop() -> None:
            while True:
                try:
                    due = self.store.query(
                        "SELECT id FROM ig_publishes WHERE status='scheduled' AND publish_at <= ?",
                        (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),),
                    )
                    for r in due:
                        # CAS: chỉ nhả việc nếu còn 'scheduled' (chống đúp giữa nhiều worker)
                        changed = self.store.cas_update(
                            "UPDATE ig_publishes SET status='queued', updated_at=? "
                            "WHERE id=? AND status='scheduled'",
                            (now(), r["id"]),
                        )
                        if changed:
                            self._spawn_worker(r["id"])
                except Exception as exc:  # noqa: BLE001
                    logger.exception("instagram scheduler loop error: %s", exc)
                self._sleep(self._scheduler_interval_sec)

        th = threading.Thread(target=_loop, name="ig-publish-scheduler", daemon=True)
        self._scheduler_thread = th
        th.start()

    def _is_canceled(self, publish_id: str) -> bool:
        r = self.store.query_one("SELECT status FROM ig_publishes WHERE id=?", (publish_id,))
        return not r or r["status"] == "canceled"

    def _mark_failed(self, publish_id: str, item_id: str, msg: str) -> None:
        self.store.execute(
            "UPDATE ig_publishes SET status='failed', error_message=?, updated_at=? WHERE id=?",
            (msg, now(), publish_id),
        )
        self._emit_event("instagram.publish.failed", item_id, {"publish_id": publish_id, "error": msg})

    def _run_publish(self, publish_id: str) -> None:
        try:
            row = self.store.query_one("SELECT * FROM ig_publishes WHERE id=?", (publish_id,))
            if not row or row["status"] in ("canceled", "published", "failed"):
                return  # đã xử lý xong bởi worker khác — không đúp
            item_id = row["item_id"]
            target_type = row.get("target_type") or "todo"
            account, token = self.tokens.get_valid_access_token(row["account_id"])

            quota = self.get_quota()
            if quota.get("checked") and quota.get("quota_remaining") == 0:
                raise InstagramQuotaExceededError(
                    "Hết quota đăng bài Instagram (100 bài/24h). Reset: "
                    f"{quota.get('reset_at') or 'khoảng 24 giờ sau bài cuối'}.",
                    reset_at=quota.get("reset_at"),
                )

            exists, video_path = self._resolve_video_path(target_type, item_id)
            if not video_path or not video_path.exists() or video_path.stat().st_size == 0:
                self._mark_failed(publish_id, item_id, "Không tìm thấy file video MP4 thành phẩm.")
                return

            container_id = row.get("container_id") or ""
            self.store.execute(
                "UPDATE ig_publishes SET status='creating', updated_at=? WHERE id=?",
                (now(), publish_id),
            )
            media_id: str | None = None
            with self._http_factory() as client:
                headers = {"Authorization": f"Bearer {token}"}

                if not container_id:
                    video_url = self.make_signed_media_url(item_id, target_type)
                    payload: dict[str, Any] = {
                        "media_type": "REELS",
                        "video_url": video_url,
                        "caption": row["caption"] or "",
                        "share_to_feed": "true" if row.get("share_to_feed", 1) else "false",
                        "access_token": token,
                    }
                    if row.get("is_ai_generated", 1):
                        payload["is_ai_generated"] = "true"
                    cr = client.post(f"{IG_GRAPH}/{account['ig_user_id']}/media", data=payload)
                    if cr.status_code != 200:
                        err_txt = cr.text
                        self._mark_failed(
                            publish_id,
                            item_id,
                            f"Tạo container thất bại: HTTP {cr.status_code} {err_txt[:500]}",
                        )
                        return
                    container_id = str((cr.json() or {}).get("id") or "")
                    if not container_id:
                        self._mark_failed(publish_id, item_id, "Instagram không trả về container_id.")
                        return
                    self.store.execute(
                        "UPDATE ig_publishes SET container_id=?, status='processing', updated_at=? WHERE id=?",
                        (container_id, now(), publish_id),
                    )

                # Poll status_code đến FINISHED
                deadline = time.time() + ig_publish_wait_minutes() * 60
                status_code = ""
                while time.time() < deadline:
                    if self._is_canceled(publish_id):
                        return
                    sr = client.get(
                        f"{IG_GRAPH}/{container_id}",
                        params={"fields": "status_code", "access_token": token},
                    )
                    if sr.status_code == 200:
                        status_code = str(((sr.json() or {}).get("status_code") or "")).upper()
                        if status_code in ("FINISHED", "ERROR", "EXPIRED"):
                            break
                    self._sleep(self._poll_interval_sec)

                if status_code == "ERROR":
                    self._mark_failed(
                        publish_id, item_id,
                        "Container báo ERROR (video không hợp lệ với Reels: duration/aspect/codec?). Thử lại hoặc kiểm tra file.",
                    )
                    return
                if status_code == "EXPIRED":
                    self.store.execute(
                        "UPDATE ig_publishes SET container_id=NULL, status='queued', updated_at=? WHERE id=?",
                        (now(), publish_id),
                    )
                    self._spawn_worker(publish_id)
                    return
                if status_code != "FINISHED":
                    self._mark_failed(
                        publish_id, item_id,
                        f"Container chưa FINISHED sau {ig_publish_wait_minutes()} phút (trạng thái cuối: {status_code or 'không rõ'}).",
                    )
                    return

                pr = client.post(
                    f"{IG_GRAPH}/{account['ig_user_id']}/media_publish",
                    data={"creation_id": container_id, "access_token": token},
                )
                if pr.status_code != 200:
                    body_txt = pr.text
                    if '"code"' in body_txt and "rate limit" in body_txt.lower() or pr.status_code == 429:
                        raise InstagramQuotaExceededError(f"Instagram trả 429 khi publish: {body_txt[:300]}")
                    self._mark_failed(
                        publish_id, item_id,
                        f"Publish container thất bại: HTTP {pr.status_code} {body_txt[:500]}",
                    )
                    return
                media_id = str((pr.json() or {}).get("id") or "")
                if not media_id:
                    self._mark_failed(publish_id, item_id, "Publish xong nhưng không nhận được media_id.")
                    return

                # Permalink (retry 3 lần)
                permalink = ""
                for attempt in range(3):
                    gr = client.get(
                        f"{IG_GRAPH}/{media_id}",
                        params={"fields": "permalink", "access_token": token},
                    )
                    if gr.status_code == 200:
                        permalink = str(((gr.json() or {}).get("permalink") or ""))
                        break
                    if attempt < 2:
                        self._sleep(10.0)

            self.store.execute(
                "UPDATE ig_publishes SET status='published', ig_media_id=?, permalink=?, updated_at=? WHERE id=?",
                (media_id, permalink or None, now(), publish_id),
            )
            self._emit_event(
                "instagram.publish.completed",
                item_id,
                {"publish_id": publish_id, "ig_media_id": media_id, "permalink": permalink, "slug": row.get("affiliate_slug")},
            )
        except InstagramError as exc:
            row = self.store.query_one("SELECT item_id FROM ig_publishes WHERE id=?", (publish_id,))
            if row:
                self._mark_failed(publish_id, row["item_id"], exc.message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Instagram publish worker failed (%s)", publish_id)
            row = self.store.query_one("SELECT item_id FROM ig_publishes WHERE id=?", (publish_id,))
            if row:
                self._mark_failed(publish_id, row["item_id"], str(exc))
        finally:
            with self._lock:
                self._active_threads.pop(publish_id, None)
