"""YouTube Channel OAuth 2.0 & Direct Publishing Service (YouTube Data API v3)."""
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
from zoneinfo import ZoneInfo

import httpx
from cryptography.fernet import Fernet, InvalidToken

from .config import (
    data_dir,
    public_base_url,
    session_secret,
    youtube_client_id,
    youtube_client_secret,
    youtube_token_encryption_key,
    youtube_upload_chunk_bytes,
)
from .store import Store, new_id, now
from .webhooks import WebhookService

logger = logging.getLogger("video-system.youtube")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
YT_API_BASE = "https://www.googleapis.com/youtube/v3"
YT_UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3"

YT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

QUOTA_COSTS = {
    "videos.insert": 1600,
    "captions.insert": 50,
    "thumbnails.set": 50,
    "videos.update": 50,
    "videos.list": 1,
    "playlistItems.list": 1,
    "channels.list": 1,
}
QUOTA_DAILY_LIMIT = 10000
QUOTA_UPLOAD_GUARD = 9000
OAUTH_STATE_TTL_SEC = 600
SESSION_URI_MAX_AGE_SEC = 24 * 3600
ACTIVE_UPLOAD_STATUSES = ("queued", "uploading", "processing")
_PT_TZ = ZoneInfo("America/Los_Angeles")


class YouTubeError(Exception):
    """Base error with HTTP status code for API mapping."""

    def __init__(self, message: str, status_code: int = 400, code: str = "youtube_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class YouTubeConfigError(YouTubeError):
    def __init__(self, message: str):
        super().__init__(message, status_code=503, code="youtube_not_configured")


class YouTubeQuotaExceededError(YouTubeError):
    def __init__(self, message: str):
        super().__init__(message, status_code=429, code="youtube_quota_exceeded")


class YouTubeConflictError(YouTubeError):
    def __init__(self, message: str, upload_id: str | None = None):
        super().__init__(message, status_code=409, code="youtube_upload_in_progress")
        self.upload_id = upload_id


class QuotaLedger:
    """Tracks daily YouTube Data API v3 quota usage (reset at 00:00 America/Los_Angeles)."""

    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def current_day() -> str:
        return datetime.now(_PT_TZ).strftime("%Y-%m-%d")

    def used_today(self) -> int:
        row = self.store.query_one(
            "SELECT units_used FROM youtube_quota_ledger WHERE day=?",
            (self.current_day(),),
        )
        return int(row["units_used"]) if row else 0

    def add(self, operation: str) -> int:
        cost = QUOTA_COSTS.get(operation, 1)
        day = self.current_day()
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO youtube_quota_ledger (day, units_used) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET units_used = units_used + excluded.units_used",
                (day, cost),
            )
            row = conn.execute(
                "SELECT units_used FROM youtube_quota_ledger WHERE day=?",
                (day,),
            ).fetchone()
            return int(row["units_used"]) if row else cost

    def ensure_upload_budget(self) -> None:
        used = self.used_today()
        if used + QUOTA_COSTS["videos.insert"] > QUOTA_UPLOAD_GUARD:
            raise YouTubeQuotaExceededError(
                f"Đã dùng {used}/{QUOTA_DAILY_LIMIT} quota units hôm nay (ngưỡng an toàn {QUOTA_UPLOAD_GUARD}). "
                f"YouTube reset quota vào 00:00 giờ Pacific (khoảng 14:00–15:00 giờ VN)."
            )


class TokenManager:
    """Encrypts/decrypts OAuth tokens at rest using Fernet and auto-refreshes expiring tokens."""

    def __init__(self, store: Store, http_factory: Callable[[], httpx.Client]):
        self.store = store
        self._http_factory = http_factory
        self._refresh_lock = threading.Lock()

    @staticmethod
    def _fernet() -> Fernet:
        raw = youtube_token_encryption_key()
        if not raw:
            raise YouTubeConfigError(
                "Thiếu YOUTUBE_TOKEN_ENCRYPTION_KEY trong cấu hình môi trường."
            )
        try:
            return Fernet(raw.encode("utf-8"))
        except Exception as exc:
            raise YouTubeConfigError(f"YOUTUBE_TOKEN_ENCRYPTION_KEY không hợp lệ: {exc}") from exc

    def encrypt(self, value: str) -> str:
        return self._fernet().encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, token_enc: str) -> str:
        try:
            return self._fernet().decrypt(token_enc.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise YouTubeConfigError("Không giải mã được token YouTube (encryption key đã đổi?).") from exc

    def get_active_channel_row(self) -> dict[str, Any]:
        row = self.store.query_one(
            "SELECT * FROM youtube_channels WHERE status='connected' "
            "ORDER BY is_default DESC, updated_at DESC LIMIT 1"
        )
        if not row:
            raise YouTubeError("Chưa có kênh YouTube nào được kết nối.", status_code=404, code="youtube_not_connected")
        return row

    def get_valid_access_token(self, channel_id: str | None = None) -> tuple[dict[str, Any], str]:
        with self._refresh_lock:
            row = (
                self.store.query_one("SELECT * FROM youtube_channels WHERE channel_id=?", (channel_id,))
                if channel_id
                else self.get_active_channel_row()
            )
            if not row or row["status"] != "connected":
                raise YouTubeError(
                    "Kết nối kênh YouTube đã hết hạn hoặc bị ngắt. Vui lòng kết nối lại.",
                    status_code=401,
                    code="youtube_disconnected",
                )
            access_token = self.decrypt(row["access_token_enc"])
            expires_at = float(row.get("token_expires_at") or 0.0)
            if expires_at - time.time() > 120:
                return row, access_token

            refresh_token = self.decrypt(row["refresh_token_enc"])
            cid = youtube_client_id()
            csec = youtube_client_secret()
            if not cid or not csec:
                raise YouTubeConfigError("Thiếu YOUTUBE_CLIENT_ID hoặc YOUTUBE_CLIENT_SECRET.")

            with self._http_factory() as client:
                resp = client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": cid,
                        "client_secret": csec,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
            if resp.status_code != 200:
                body_txt = resp.text
                if "invalid_grant" in body_txt or resp.status_code in (400, 401):
                    self.store.execute(
                        "UPDATE youtube_channels SET status='expired', updated_at=? WHERE id=?",
                        (now(), row["id"]),
                    )
                    raise YouTubeError(
                        "Token YouTube đã bị thu hồi hoặc hết hạn (invalid_grant). Vui lòng kết nối lại kênh.",
                        status_code=401,
                        code="youtube_token_expired",
                    )
                raise YouTubeError(f"Lỗi làm mới token YouTube: HTTP {resp.status_code}", status_code=502)

            tok = resp.json()
            new_access = tok["access_token"]
            new_exp = time.time() + float(tok.get("expires_in", 3600))
            new_refresh_enc = (
                self.encrypt(tok["refresh_token"]) if tok.get("refresh_token") else row["refresh_token_enc"]
            )
            self.store.execute(
                "UPDATE youtube_channels SET access_token_enc=?, refresh_token_enc=?, "
                "token_expires_at=?, updated_at=? WHERE id=?",
                (self.encrypt(new_access), new_refresh_enc, new_exp, now(), row["id"]),
            )
            updated = self.store.query_one("SELECT * FROM youtube_channels WHERE id=?", (row["id"],))
            return updated or row, new_access


class YouTubeService:
    """Manages OAuth connection, channel/video listing, metadata updates, and resumable publishing."""

    def __init__(
        self,
        store: Store,
        webhooks: WebhookService,
        *,
        http_factory: Callable[[], httpx.Client] | None = None,
        poll_interval_sec: float = 15.0,
        poll_max_attempts: int = 40,
        retry_sleep_fn: Callable[[float], None] | None = None,
    ):
        self.store = store
        self.webhooks = webhooks
        self._http_factory = http_factory or (lambda: httpx.Client(timeout=60.0))
        self.quota = QuotaLedger(store)
        self.tokens = TokenManager(store, self._http_factory)
        self._poll_interval_sec = poll_interval_sec
        self._poll_max_attempts = poll_max_attempts
        self._sleep = retry_sleep_fn or time.sleep
        self._active_threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    @staticmethod
    def is_configured() -> bool:
        return bool(youtube_client_id() and youtube_client_secret() and youtube_token_encryption_key())

    @staticmethod
    def redirect_uri() -> str:
        return f"{public_base_url()}/api/v1/youtube/auth/callback"

    def _emit_event(self, event_type: str, item_id: str | None, payload: dict[str, Any]) -> None:
        ev = {
            "id": new_id("evt"),
            "item_id": item_id,
            "shot_id": None,
            "type": event_type,
            "actor": "youtube",
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "at": now(),
        }
        self.store.execute(
            "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) VALUES (?,?,?,?,?,?,?)",
            (ev["id"], ev["item_id"], ev["shot_id"], ev["type"], ev["actor"], ev["payload_json"], ev["at"]),
        )
        self.webhooks.dispatch_event(ev)

    # -- OAuth 2.0 -----------------------------------------------------------

    def make_auth_url(self) -> str:
        cid = youtube_client_id()
        csec = youtube_client_secret()
        if not cid or not csec:
            raise YouTubeConfigError("Chưa cấu hình YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET.")
        # Verify encryption key is present before sending user through OAuth
        self.tokens._fernet()

        nonce = secrets.token_urlsafe(16)
        exp = int(time.time()) + OAUTH_STATE_TTL_SEC
        msg = f"{nonce}.{exp}".encode("utf-8")
        sig = hmac.new(session_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()
        state = f"{nonce}.{exp}.{sig}"

        self.store.execute(
            "INSERT INTO youtube_oauth_nonces (nonce, expires_at, used_at) VALUES (?, ?, NULL)",
            (nonce, float(exp)),
        )
        params = {
            "client_id": cid,
            "redirect_uri": self.redirect_uri(),
            "response_type": "code",
            "scope": " ".join(YT_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def _verify_and_consume_state(self, state: str) -> None:
        parts = (state or "").split(".")
        if len(parts) != 3:
            raise YouTubeError("OAuth state không hợp lệ.", status_code=400, code="invalid_oauth_state")
        nonce, exp_str, sig = parts
        try:
            exp = int(exp_str)
        except ValueError as exc:
            raise YouTubeError("OAuth state không hợp lệ.", status_code=400, code="invalid_oauth_state") from exc
        if exp < int(time.time()):
            raise YouTubeError("OAuth state đã hết hạn.", status_code=400, code="expired_oauth_state")
        want_sig = hmac.new(
            session_secret().encode("utf-8"),
            f"{nonce}.{exp}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, want_sig):
            raise YouTubeError("Chữ ký OAuth state không hợp lệ.", status_code=400, code="invalid_oauth_state")

        t = now()
        with self.store.transaction() as conn:
            cur = conn.execute(
                "UPDATE youtube_oauth_nonces SET used_at=? "
                "WHERE nonce=? AND used_at IS NULL AND expires_at >= ?",
                (t, nonce, t),
            )
            if cur.rowcount != 1:
                raise YouTubeError("OAuth state đã được sử dụng hoặc hết hạn.", status_code=400, code="replayed_oauth_state")

    def handle_callback(self, code: str, state: str) -> dict[str, Any]:
        self._verify_and_consume_state(state)
        cid = youtube_client_id()
        csec = youtube_client_secret()
        if not cid or not csec:
            raise YouTubeConfigError("Thiếu YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET.")

        with self._http_factory() as client:
            tok_resp = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": cid,
                    "client_secret": csec,
                    "redirect_uri": self.redirect_uri(),
                    "grant_type": "authorization_code",
                },
            )
            if tok_resp.status_code != 200:
                raise YouTubeError(
                    f"Đổi OAuth code thất bại: HTTP {tok_resp.status_code} {tok_resp.text}",
                    status_code=400,
                    code="oauth_exchange_failed",
                )
            tok = tok_resp.json()
            access_token = tok["access_token"]
            refresh_token = tok.get("refresh_token")
            if not refresh_token:
                raise YouTubeError(
                    "Google không trả về refresh_token. Hãy thử ngắt quyền app trong Google Account rồi kết nối lại.",
                    status_code=400,
                    code="missing_refresh_token",
                )
            expires_at = time.time() + float(tok.get("expires_in", 3600))

            ch_resp = client.get(
                f"{YT_API_BASE}/channels",
                params={"part": "snippet,statistics,contentDetails", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if ch_resp.status_code != 200:
                raise YouTubeError(
                    f"Không lấy được thông tin kênh YouTube: HTTP {ch_resp.status_code}",
                    status_code=502,
                )
            self.quota.add("channels.list")
            items = ch_resp.json().get("items") or []
            if not items:
                raise YouTubeError("Tài khoản Google này chưa có kênh YouTube.", status_code=400, code="no_youtube_channel")

            ch = items[0]
            sn = ch.get("snippet") or {}
            st = ch.get("statistics") or {}
            cd = ch.get("contentDetails") or {}
            uploads_pl = (cd.get("relatedPlaylists") or {}).get("uploads") or ""
            thumbs = sn.get("thumbnails") or {}
            thumb_url = (thumbs.get("medium") or thumbs.get("default") or {}).get("url") or ""

            ch_id = ch["id"]
            t = now()
            row_id = new_id("ytc")
            acc_enc = self.tokens.encrypt(access_token)
            ref_enc = self.tokens.encrypt(refresh_token)
            scopes_str = tok.get("scope") or " ".join(YT_SCOPES)

            with self.store.transaction() as conn:
                conn.execute("UPDATE youtube_channels SET is_default=0")
                existing = conn.execute(
                    "SELECT id FROM youtube_channels WHERE channel_id=?", (ch_id,)
                ).fetchone()
                if existing:
                    row_id = existing["id"]
                    conn.execute(
                        "UPDATE youtube_channels SET title=?, custom_url=?, thumbnail_url=?, "
                        "uploads_playlist_id=?, subscriber_count=?, video_count=?, "
                        "access_token_enc=?, refresh_token_enc=?, token_expires_at=?, "
                        "scopes=?, status='connected', is_default=1, updated_at=? WHERE id=?",
                        (
                            sn.get("title") or "",
                            sn.get("customUrl") or "",
                            thumb_url,
                            uploads_pl,
                            int(st.get("subscriberCount") or 0),
                            int(st.get("videoCount") or 0),
                            acc_enc,
                            ref_enc,
                            expires_at,
                            scopes_str,
                            t,
                            row_id,
                        ),
                    )
                else:
                    conn.execute(
                        "INSERT INTO youtube_channels "
                        "(id,channel_id,title,custom_url,thumbnail_url,uploads_playlist_id,"
                        "subscriber_count,video_count,access_token_enc,refresh_token_enc,"
                        "token_expires_at,scopes,status,is_default,connected_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            row_id,
                            ch_id,
                            sn.get("title") or "",
                            sn.get("customUrl") or "",
                            thumb_url,
                            uploads_pl,
                            int(st.get("subscriberCount") or 0),
                            int(st.get("videoCount") or 0),
                            acc_enc,
                            ref_enc,
                            expires_at,
                            scopes_str,
                            "connected",
                            1,
                            t,
                            t,
                        ),
                    )

        info = self.get_channel()
        self._emit_event("youtube.channel.connected", None, {"channel_id": ch_id, "title": info.get("title")})
        return info

    # -- Channel Status & Disconnect -----------------------------------------

    @staticmethod
    def _sanitize_channel(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": row["id"],
            "channel_id": row["channel_id"],
            "title": row["title"],
            "custom_url": row["custom_url"],
            "thumbnail_url": row["thumbnail_url"],
            "uploads_playlist_id": row["uploads_playlist_id"],
            "subscriber_count": int(row.get("subscriber_count") or 0),
            "video_count": int(row.get("video_count") or 0),
            "status": row["status"],
            "connected_at": row["connected_at"],
            "updated_at": row["updated_at"],
        }

    def get_channel(self) -> dict[str, Any]:
        row = self.store.query_one(
            "SELECT * FROM youtube_channels ORDER BY is_default DESC, updated_at DESC LIMIT 1"
        )
        if not row or row["status"] != "connected":
            raise YouTubeError("Chưa kết nối kênh YouTube.", status_code=404, code="youtube_not_connected")
        return self._sanitize_channel(row) or {}

    def get_status(self) -> dict[str, Any]:
        row = self.store.query_one(
            "SELECT * FROM youtube_channels ORDER BY is_default DESC, updated_at DESC LIMIT 1"
        )
        connected = bool(row and row["status"] == "connected")
        return {
            "configured": self.is_configured(),
            "connected": connected,
            "channel_status": row["status"] if row else "disconnected",
            "channel": self._sanitize_channel(row) if row else None,
            "quota_used_today": self.quota.used_today(),
            "quota_limit": QUOTA_DAILY_LIMIT,
            "quota_upload_guard": QUOTA_UPLOAD_GUARD,
            "redirect_uri": self.redirect_uri(),
        }

    def disconnect_channel(self) -> dict[str, Any]:
        row = self.store.query_one(
            "SELECT * FROM youtube_channels ORDER BY is_default DESC, updated_at DESC LIMIT 1"
        )
        if not row:
            return {"disconnected": True}
        channel_id = row["channel_id"]
        # Best-effort revoke
        try:
            token_to_revoke = self.tokens.decrypt(row["refresh_token_enc"])
            with self._http_factory() as client:
                client.post(GOOGLE_REVOKE_URL, params={"token": token_to_revoke})
        except Exception as exc:
            logger.warning("YouTube token revoke warning: %s", exc)

        t = now()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE youtube_uploads SET upload_status='canceled', error_message='Channel disconnected', updated_at=? "
                "WHERE channel_id=? AND upload_status IN ('queued','uploading','processing')",
                (t, channel_id),
            )
            conn.execute("DELETE FROM youtube_channels WHERE id=?", (row["id"],))
        self._emit_event("youtube.channel.disconnected", None, {"channel_id": channel_id})
        return {"disconnected": True, "channel_id": channel_id}

    # -- Channel Videos & Metadata -------------------------------------------

    def list_videos(self, limit: int = 25) -> dict[str, Any]:
        ch_row, token = self.tokens.get_valid_access_token()
        uploads_pl = ch_row.get("uploads_playlist_id") or ""
        headers = {"Authorization": f"Bearer {token}"}
        videos: list[dict[str, Any]] = []

        with self._http_factory() as client:
            if not uploads_pl:
                cr = client.get(
                    f"{YT_API_BASE}/channels",
                    params={"part": "contentDetails,snippet,statistics", "mine": "true"},
                    headers=headers,
                )
                if cr.status_code == 200:
                    self.quota.add("channels.list")
                    citems = cr.json().get("items") or []
                    if citems:
                        uploads_pl = ((citems[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads") or ""
                        self.store.execute(
                            "UPDATE youtube_channels SET uploads_playlist_id=?, updated_at=? WHERE id=?",
                            (uploads_pl, now(), ch_row["id"]),
                        )

            if uploads_pl:
                pr = client.get(
                    f"{YT_API_BASE}/playlistItems",
                    params={"part": "contentDetails,snippet", "playlistId": uploads_pl, "maxResults": max(1, min(50, limit))},
                    headers=headers,
                )
                if pr.status_code != 200:
                    raise YouTubeError(f"Lỗi lấy danh sách video kênh: HTTP {pr.status_code}", status_code=502)
                self.quota.add("playlistItems.list")
                video_ids = [
                    it["contentDetails"]["videoId"]
                    for it in (pr.json().get("items") or [])
                    if (it.get("contentDetails") or {}).get("videoId")
                ]
                if video_ids:
                    vr = client.get(
                        f"{YT_API_BASE}/videos",
                        params={"part": "snippet,statistics,status,contentDetails", "id": ",".join(video_ids)},
                        headers=headers,
                    )
                    if vr.status_code != 200:
                        raise YouTubeError(f"Lỗi lấy chi tiết video: HTTP {vr.status_code}", status_code=502)
                    self.quota.add("videos.list")
                    for v in vr.json().get("items") or []:
                        sn = v.get("snippet") or {}
                        st = v.get("statistics") or {}
                        vs = v.get("status") or {}
                        thumbs = sn.get("thumbnails") or {}
                        t_url = (thumbs.get("medium") or thumbs.get("default") or {}).get("url") or ""
                        videos.append(
                            {
                                "video_id": v["id"],
                                "youtube_url": f"https://www.youtube.com/watch?v={v['id']}",
                                "title": sn.get("title") or "",
                                "description": sn.get("description") or "",
                                "tags": sn.get("tags") or [],
                                "category_id": sn.get("categoryId") or "22",
                                "published_at": sn.get("publishedAt") or "",
                                "thumbnail_url": t_url,
                                "privacy_status": vs.get("privacyStatus") or "private",
                                "publish_at": vs.get("publishAt"),
                                "view_count": int(st.get("viewCount") or 0),
                                "like_count": int(st.get("likeCount") or 0),
                                "comment_count": int(st.get("commentCount") or 0),
                            }
                        )

        uploads = self.store.query(
            "SELECT * FROM youtube_uploads ORDER BY created_at DESC LIMIT 50"
        )
        return {
            "channel": self._sanitize_channel(ch_row),
            "videos": videos,
            "uploads": [self._format_upload(u) for u in uploads],
            "quota_used_today": self.quota.used_today(),
            "quota_limit": QUOTA_DAILY_LIMIT,
        }

    def patch_video(
        self,
        video_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        privacy_status: str | None = None,
    ) -> dict[str, Any]:
        _, token = self.tokens.get_valid_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        with self._http_factory() as client:
            gr = client.get(
                f"{YT_API_BASE}/videos",
                params={"part": "snippet,status", "id": video_id},
                headers=headers,
            )
            if gr.status_code != 200:
                raise YouTubeError(f"Không đọc được metadata video {video_id}: HTTP {gr.status_code}", status_code=502)
            self.quota.add("videos.list")
            items = gr.json().get("items") or []
            if not items:
                raise YouTubeError(f"Không tìm thấy video {video_id} trên YouTube.", status_code=404)

            cur = items[0]
            sn = dict(cur.get("snippet") or {})
            st = dict(cur.get("status") or {})
            if title is not None:
                sn["title"] = title
            if description is not None:
                sn["description"] = description
            if tags is not None:
                sn["tags"] = tags
            if not sn.get("categoryId"):
                sn["categoryId"] = "22"

            new_status: dict[str, Any] = {"privacyStatus": privacy_status or st.get("privacyStatus") or "private"}
            if st.get("publishAt") and new_status["privacyStatus"] == "private":
                new_status["publishAt"] = st["publishAt"]

            ur = client.put(
                f"{YT_API_BASE}/videos",
                params={"part": "snippet,status"},
                headers=headers,
                json={"id": video_id, "snippet": sn, "status": new_status},
            )
            if ur.status_code != 200:
                raise YouTubeError(
                    f"Cập nhật video thất bại: HTTP {ur.status_code} {ur.text}",
                    status_code=400 if ur.status_code < 500 else 502,
                )
            self.quota.add("videos.update")
            updated = ur.json()
            u_sn = updated.get("snippet") or sn
            u_st = updated.get("status") or new_status
            return {
                "video_id": video_id,
                "title": u_sn.get("title"),
                "description": u_sn.get("description"),
                "tags": u_sn.get("tags") or [],
                "category_id": u_sn.get("categoryId"),
                "privacy_status": u_st.get("privacyStatus"),
            }

    # -- Publish Pipeline ----------------------------------------------------

    @staticmethod
    def _validate_publish_at(publish_at: str | None, privacy_status: str) -> tuple[str | None, str]:
        if not publish_at:
            return None, privacy_status
        raw = publish_at.strip()
        if raw.endswith("Z"):
            raw_dt = raw[:-1] + "+00:00"
        else:
            raw_dt = raw
        try:
            dt = datetime.fromisoformat(raw_dt)
        except ValueError as exc:
            raise YouTubeError("publish_at phải là chuỗi ISO-8601 hợp lệ.", status_code=422) from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < datetime.now(timezone.utc) + timedelta(minutes=10):
            raise YouTubeError("publish_at phải cách hiện tại ít nhất 10 phút.", status_code=422)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "private"

    @staticmethod
    def _final_video_path(item_id: str) -> Path:
        return data_dir() / "objects" / item_id / "exports" / "final_en.mp4"

    @staticmethod
    def _thumbnail_path(store: Store, item_id: str) -> Path | None:
        row = store.query_one(
            "SELECT a.storage_key FROM todo_images ti "
            "JOIN assets a ON a.id = ti.image_asset_id "
            "WHERE ti.item_id=? AND ti.kind='thumbnail' ORDER BY ti.seq_num ASC LIMIT 1",
            (item_id,),
        )
        if not row:
            return None
        sk = str(row["storage_key"] or "")
        # storage_key: items/{item_id}/images/thumbnail_0001.png -> data/objects/{item_id}/images/...
        if sk.startswith(f"items/{item_id}/"):
            p = data_dir() / "objects" / item_id / sk[len(f"items/{item_id}/"):]
            if p.exists():
                return p
        p2 = data_dir() / "objects" / item_id / "images" / Path(sk).name
        return p2 if p2.exists() else None

    @staticmethod
    def _srt_path(item_id: str) -> Path | None:
        p = data_dir() / "objects" / item_id / "todo" / "subtitles_en.srt"
        return p if p.exists() else None

    @staticmethod
    def _format_upload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "upload_id": row["id"],
            "item_id": row["item_id"],
            "channel_id": row["channel_id"],
            "youtube_video_id": row["youtube_video_id"],
            "youtube_url": row["youtube_url"],
            "title": row["title"],
            "privacy_status": row["privacy_status"],
            "publish_at": row["publish_at"],
            "upload_status": row["upload_status"],
            "progress_pct": round(float(row.get("progress_pct") or 0.0), 1),
            "bytes_uploaded": int(row.get("bytes_uploaded") or 0),
            "total_bytes": int(row.get("total_bytes") or 0),
            "thumbnail_synced": bool(row.get("thumbnail_synced")),
            "captions_synced": bool(row.get("captions_synced")),
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def publish_todo(
        self,
        item_id: str,
        *,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        privacy_status: str = "private",
        publish_at: str | None = None,
        upload_captions: bool = True,
        category_id: str = "22",
        run_async: bool = True,
    ) -> dict[str, Any]:
        item = self.store.query_one("SELECT id, title FROM items WHERE id=? AND status!='deleted'", (item_id,))
        if not item:
            raise YouTubeError(f"Không tìm thấy todo {item_id}.", status_code=404)

        final_mp4 = self._final_video_path(item_id)
        if not final_mp4.exists() or final_mp4.stat().st_size == 0:
            raise YouTubeError("Job chưa có video thành phẩm final_en.mp4.", status_code=422, code="missing_final_video")

        thumb_path = self._thumbnail_path(self.store, item_id)
        if not thumb_path:
            raise YouTubeError("Job chưa có ảnh thumbnail (kind=thumbnail).", status_code=422, code="missing_thumbnail")

        # Idempotency guard
        active = self.store.query_one(
            "SELECT * FROM youtube_uploads WHERE item_id=? AND upload_status IN ('queued','uploading','processing') "
            "ORDER BY created_at DESC LIMIT 1",
            (item_id,),
        )
        if active:
            raise YouTubeConflictError(
                f"Job đang có tiến trình đăng YouTube ({active['upload_status']}).",
                upload_id=active["id"],
            )
        done = self.store.query_one(
            "SELECT * FROM youtube_uploads WHERE item_id=? AND upload_status='uploaded' ORDER BY created_at DESC LIMIT 1",
            (item_id,),
        )
        if done:
            res = self._format_upload(done)
            res["already_published"] = True
            return res

        norm_publish_at, norm_privacy = self._validate_publish_at(publish_at, privacy_status)
        ch_row, _ = self.tokens.get_valid_access_token()
        self.quota.ensure_upload_budget()

        upload_id = new_id("ytu")
        total_bytes = final_mp4.stat().st_size
        t = now()
        self.store.execute(
            "INSERT INTO youtube_uploads "
            "(id,item_id,target_type,channel_id,title,description,tags_json,privacy_status,publish_at,"
            "upload_status,progress_pct,bytes_uploaded,total_bytes,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                upload_id,
                item_id,
                "todo",
                ch_row["channel_id"],
                title,
                description,
                json.dumps(tags or [], ensure_ascii=False),
                norm_privacy,
                norm_publish_at,
                "queued",
                0.0,
                0,
                total_bytes,
                t,
                t,
            ),
        )
        self._spawn_worker(upload_id, upload_captions=upload_captions, category_id=category_id, run_async=run_async)
        row = self.store.query_one("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,))
        return self._format_upload(row)

    def _spawn_worker(
        self, upload_id: str, *, upload_captions: bool = True, category_id: str = "22", run_async: bool = True
    ) -> None:
        if not run_async:
            self._run_upload(upload_id, upload_captions=upload_captions, category_id=category_id)
            return
        th = threading.Thread(
            target=self._run_upload,
            args=(upload_id,),
            kwargs={"upload_captions": upload_captions, "category_id": category_id},
            daemon=True,
        )
        with self._lock:
            self._active_threads[upload_id] = th
        th.start()

    def get_upload(self, upload_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,))
        if not row:
            raise YouTubeError(f"Không tìm thấy upload {upload_id}.", status_code=404)
        return self._format_upload(row)

    def cancel_upload(self, upload_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,))
        if not row:
            raise YouTubeError(f"Không tìm thấy upload {upload_id}.", status_code=404)
        if row["upload_status"] in ("uploaded", "canceled"):
            return self._format_upload(row)
        self.store.execute(
            "UPDATE youtube_uploads SET upload_status='canceled', error_message='Canceled by user', updated_at=? WHERE id=?",
            (now(), upload_id),
        )
        return self.get_upload(upload_id)

    def retry_upload(self, upload_id: str, run_async: bool = True) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,))
        if not row:
            raise YouTubeError(f"Không tìm thấy upload {upload_id}.", status_code=404)
        if row["upload_status"] == "uploaded":
            return self._format_upload(row)
        if row["upload_status"] in ACTIVE_UPLOAD_STATUSES:
            raise YouTubeConflictError("Upload đang chạy.", upload_id=upload_id)
        self.quota.ensure_upload_budget()
        self.store.execute(
            "UPDATE youtube_uploads SET upload_status='queued', error_message=NULL, updated_at=? WHERE id=?",
            (now(), upload_id),
        )
        self._spawn_worker(upload_id, upload_captions=True, category_id="22", run_async=run_async)
        return self.get_upload(upload_id)

    def startup_sweep(self, run_async: bool = True) -> list[str]:
        """Recover/fail interrupted uploads after process restart."""
        resumed: list[str] = []
        rows = self.store.query(
            "SELECT * FROM youtube_uploads WHERE upload_status IN ('queued','uploading','processing')"
        )
        t = now()
        for r in rows:
            uid = r["id"]
            if r["upload_status"] == "queued":
                self._spawn_worker(uid, run_async=run_async)
                resumed.append(uid)
                continue
            session_uri = r.get("upload_session_uri") or ""
            age = t - float(r.get("created_at") or t)
            if not session_uri or age > SESSION_URI_MAX_AGE_SEC:
                self.store.execute(
                    "UPDATE youtube_uploads SET upload_status='failed', "
                    "error_message='Interrupted on restart (session missing or expired >24h)', updated_at=? WHERE id=?",
                    (t, uid),
                )
            else:
                self._spawn_worker(uid, run_async=run_async)
                resumed.append(uid)
        return resumed

    def _is_canceled(self, upload_id: str) -> bool:
        r = self.store.query_one("SELECT upload_status FROM youtube_uploads WHERE id=?", (upload_id,))
        return not r or r["upload_status"] == "canceled"

    def _mark_failed(self, upload_id: str, item_id: str, msg: str) -> None:
        self.store.execute(
            "UPDATE youtube_uploads SET upload_status='failed', error_message=?, updated_at=? WHERE id=?",
            (msg, now(), upload_id),
        )
        self._emit_event("youtube.upload.failed", item_id, {"upload_id": upload_id, "error": msg})

    def _run_upload(self, upload_id: str, *, upload_captions: bool = True, category_id: str = "22") -> None:
        try:
            row = self.store.query_one("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,))
            if not row or row["upload_status"] == "canceled":
                return
            item_id = row["item_id"]
            final_mp4 = self._final_video_path(item_id)
            if not final_mp4.exists():
                self._mark_failed(upload_id, item_id, "Không tìm thấy final_en.mp4.")
                return

            total_bytes = final_mp4.stat().st_size
            _, token = self.tokens.get_valid_access_token(row["channel_id"])
            headers = {"Authorization": f"Bearer {token}"}

            with self._http_factory() as client:
                session_uri = row.get("upload_session_uri") or ""
                offset = int(row.get("bytes_uploaded") or 0)

                if not session_uri:
                    status_body: dict[str, Any] = {
                        "privacyStatus": row["privacy_status"],
                        "selfDeclaredMadeForKids": False,
                    }
                    if row.get("publish_at"):
                        status_body["publishAt"] = row["publish_at"]
                        status_body["privacyStatus"] = "private"

                    meta = {
                        "snippet": {
                            "title": row["title"],
                            "description": row["description"] or "",
                            "tags": json.loads(row["tags_json"] or "[]"),
                            "categoryId": category_id,
                        },
                        "status": status_body,
                    }
                    init_resp = client.post(
                        f"{YT_UPLOAD_BASE}/videos",
                        params={"uploadType": "resumable", "part": "snippet,status"},
                        headers={
                            **headers,
                            "X-Upload-Content-Length": str(total_bytes),
                            "X-Upload-Content-Type": "video/mp4",
                        },
                        json=meta,
                    )
                    if init_resp.status_code not in (200, 201):
                        self._mark_failed(upload_id, item_id, f"Khởi tạo upload session thất bại: HTTP {init_resp.status_code}")
                        return
                    session_uri = init_resp.headers.get("Location") or init_resp.headers.get("location") or ""
                    if not session_uri:
                        self._mark_failed(upload_id, item_id, "Google không trả về Location upload session URI.")
                        return
                    self.quota.add("videos.insert")
                    offset = 0
                    self.store.execute(
                        "UPDATE youtube_uploads SET upload_status='uploading', upload_session_uri=?, "
                        "total_bytes=?, bytes_uploaded=0, updated_at=? WHERE id=?",
                        (session_uri, total_bytes, now(), upload_id),
                    )
                else:
                    self.store.execute(
                        "UPDATE youtube_uploads SET upload_status='uploading', updated_at=? WHERE id=?",
                        (now(), upload_id),
                    )
                    # Probe actual server offset before resuming
                    probe = client.put(
                        session_uri,
                        headers={"Content-Range": f"bytes */{total_bytes}", "Content-Length": "0"},
                    )
                    if probe.status_code == 410:
                        self._mark_failed(upload_id, item_id, "Upload session URI đã hết hạn (410). Bấm Thử lại để tạo session mới.")
                        self.store.execute("UPDATE youtube_uploads SET upload_session_uri=NULL WHERE id=?", (upload_id,))
                        return
                    if probe.status_code == 308:
                        offset = self._parse_range_end(probe.headers.get("Range"))

                # Chunk upload loop
                chunk_size = youtube_upload_chunk_bytes()
                video_id: str | None = row.get("youtube_video_id")
                if not video_id:
                    retries = 0
                    with final_mp4.open("rb") as fh:
                        while offset < total_bytes:
                            if self._is_canceled(upload_id):
                                return
                            fh.seek(offset)
                            chunk = fh.read(chunk_size)
                            if not chunk:
                                break
                            end_byte = offset + len(chunk) - 1
                            try:
                                put_resp = client.put(
                                    session_uri,
                                    headers={
                                        "Content-Type": "video/mp4",
                                        "Content-Length": str(len(chunk)),
                                        "Content-Range": f"bytes {offset}-{end_byte}/{total_bytes}",
                                    },
                                    content=chunk,
                                )
                            except httpx.HTTPError as exc:
                                put_resp = None
                                err_str = str(exc)
                            else:
                                err_str = ""

                            if put_resp is not None and put_resp.status_code in (200, 201):
                                body = put_resp.json()
                                video_id = body.get("id")
                                offset = total_bytes
                                self.store.execute(
                                    "UPDATE youtube_uploads SET bytes_uploaded=?, progress_pct=100.0, "
                                    "youtube_video_id=?, updated_at=? WHERE id=?",
                                    (total_bytes, video_id, now(), upload_id),
                                )
                                break
                            if put_resp is not None and put_resp.status_code == 308:
                                parsed = self._parse_range_end(put_resp.headers.get("Range"))
                                offset = parsed if parsed > 0 else (end_byte + 1)
                                retries = 0
                                pct = min(99.0, (offset / max(1, total_bytes)) * 100.0)
                                self.store.execute(
                                    "UPDATE youtube_uploads SET bytes_uploaded=?, progress_pct=?, updated_at=? WHERE id=?",
                                    (offset, pct, now(), upload_id),
                                )
                                continue
                            if put_resp is not None and put_resp.status_code == 410:
                                self.store.execute("UPDATE youtube_uploads SET upload_session_uri=NULL WHERE id=?", (upload_id,))
                                self._mark_failed(upload_id, item_id, "Session URI hết hạn (410).")
                                return

                            # Retry on 5xx / timeout
                            retries += 1
                            self.store.execute(
                                "UPDATE youtube_uploads SET attempt_count=attempt_count+1, updated_at=? WHERE id=?",
                                (now(), upload_id),
                            )
                            if retries > 5:
                                code_info = put_resp.status_code if put_resp is not None else err_str
                                self._mark_failed(upload_id, item_id, f"Upload chunk thất bại sau 5 lần thử ({code_info}).")
                                return
                            self._sleep(min(60.0, float(2 ** retries)))
                            probe = client.put(
                                session_uri,
                                headers={"Content-Range": f"bytes */{total_bytes}", "Content-Length": "0"},
                            )
                            if probe.status_code == 308:
                                offset = self._parse_range_end(probe.headers.get("Range"))

                if not video_id:
                    self._mark_failed(upload_id, item_id, "Upload kết thúc nhưng không nhận được video_id.")
                    return

                # Processing poll
                self.store.execute(
                    "UPDATE youtube_uploads SET upload_status='processing', youtube_video_id=?, "
                    "youtube_url=?, progress_pct=100.0, updated_at=? WHERE id=?",
                    (video_id, f"https://www.youtube.com/watch?v={video_id}", now(), upload_id),
                )
                _, token = self.tokens.get_valid_access_token(row["channel_id"])
                headers = {"Authorization": f"Bearer {token}"}
                for _ in range(self._poll_max_attempts):
                    if self._is_canceled(upload_id):
                        return
                    pr = client.get(
                        f"{YT_API_BASE}/videos",
                        params={"part": "processingDetails,status", "id": video_id},
                        headers=headers,
                    )
                    if pr.status_code == 200:
                        self.quota.add("videos.list")
                        vitems = pr.json().get("items") or []
                        proc_status = ((vitems[0].get("processingDetails") or {}).get("processingStatus")) if vitems else None
                        if proc_status in ("succeeded", "terminated", None):
                            break
                    self._sleep(self._poll_interval_sec)

                # Set thumbnail (up to 3 retries)
                warnings: list[str] = []
                thumb_synced = 0
                thumb_path = self._thumbnail_path(self.store, item_id)
                if thumb_path:
                    thumb_bytes = thumb_path.read_bytes()
                    ctype = "image/jpeg" if thumb_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
                    for attempt in range(3):
                        tr = client.post(
                            f"{YT_UPLOAD_BASE}/thumbnails/set",
                            params={"videoId": video_id},
                            headers={**headers, "Content-Type": ctype},
                            content=thumb_bytes,
                        )
                        if tr.status_code in (200, 201):
                            self.quota.add("thumbnails.set")
                            thumb_synced = 1
                            break
                        if tr.status_code == 403:
                            warnings.append("Không set được thumbnail (kênh cần xác minh số điện thoại trên YouTube Studio).")
                            break
                        if attempt < 2:
                            self._sleep(self._poll_interval_sec)
                        else:
                            warnings.append(f"Set thumbnail thất bại: HTTP {tr.status_code}")

                # Optional captions (.srt)
                cap_synced = 0
                srt_path = self._srt_path(item_id)
                if upload_captions and srt_path:
                    meta_part = json.dumps(
                        {"snippet": {"videoId": video_id, "language": "en", "name": "English"}},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    cr = client.post(
                        f"{YT_UPLOAD_BASE}/captions",
                        params={"part": "snippet"},
                        headers=headers,
                        files={
                            "metadata": ("metadata.json", meta_part, "application/json; charset=utf-8"),
                            "media": ("subtitles_en.srt", srt_path.read_bytes(), "text/plain"),
                        },
                    )
                    if cr.status_code in (200, 201):
                        self.quota.add("captions.insert")
                        cap_synced = 1
                    else:
                        warnings.append(f"Upload phụ đề .srt thất bại: HTTP {cr.status_code}")

                yt_url = f"https://www.youtube.com/watch?v={video_id}"
                err_note = " | ".join(warnings) if warnings else None
                self.store.execute(
                    "UPDATE youtube_uploads SET upload_status='uploaded', youtube_video_id=?, youtube_url=?, "
                    "thumbnail_synced=?, captions_synced=?, error_message=?, updated_at=? WHERE id=?",
                    (video_id, yt_url, thumb_synced, cap_synced, err_note, now(), upload_id),
                )
                self._emit_event(
                    "youtube.upload.completed",
                    item_id,
                    {
                        "upload_id": upload_id,
                        "youtube_video_id": video_id,
                        "youtube_url": yt_url,
                        "thumbnail_synced": bool(thumb_synced),
                        "captions_synced": bool(cap_synced),
                    },
                )
        except Exception as exc:
            logger.exception("YouTube upload worker failed (%s)", upload_id)
            row = self.store.query_one("SELECT item_id FROM youtube_uploads WHERE id=?", (upload_id,))
            if row:
                self._mark_failed(upload_id, row["item_id"], str(exc))
        finally:
            with self._lock:
                self._active_threads.pop(upload_id, None)

    @staticmethod
    def _parse_range_end(range_hdr: str | None) -> int:
        if not range_hdr or "-" not in range_hdr:
            return 0
        try:
            end_str = range_hdr.rsplit("-", 1)[1].strip()
            return int(end_str) + 1
        except ValueError:
            return 0
