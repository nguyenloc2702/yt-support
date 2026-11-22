"""Telegram bot inbound — chốt đăng Daily Brief từ chat owner.

Long-polling `getUpdates` (không cần webhook HTTPS), chỉ nhận tin từ
`VIDEO_TELEGRAM_CHAT_ID` (whitelist, tin khác ignore + log). Lệnh:

  /approve <short_id>        đăng YouTube (privacy theo news_config submission_flow.approve)
  /reject <short_id> [lý do]  bỏ bản render, owner nộp nội dung lại được
  /status                    trạng thái kỳ hôm nay

Idempotent: /approve 2 lần -> "already approved", không publish lại.
Conflict 409 (token bị consumer khác poll) -> tắt poller + log lỗi rõ.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

import httpx

from . import service_digest, service_news

logger = logging.getLogger("video-system.telegram_bot")


def _handle_command(store, shorts_svc, youtube_svc, text: str) -> str | None:
    """Parse + chạy 1 lệnh. Trả về text reply (None = im lặng)."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=2)
    cmd = parts[0].lower()

    if cmd == "/status":
        st = service_digest.get_digest_status_today(store)
        if not st.get("submission_id"):
            return (f"📊 Hôm nay chưa có kỳ Brief nào.\n"
                    "Chờ tin đòi nội dung 16:00 hoặc nộp: POST /api/v1/digest/submit")
        return (f"📊 Kỳ {st['date']}: <b>{st['status']}</b> · "
                f"{st['stories']} tin · short <code>{st['short_id'] or '—'}</code>")

    if cmd in {"/approve", "/reject"}:
        if len(parts) < 2:
            return f"Dùng: {cmd} &lt;short_id&gt;"
        short_id = parts[1].strip()
        try:
            if cmd == "/approve":
                res = service_digest.approve_digest(store, youtube_svc, short_id, actor="telegram")
                if res.get("already_approved"):
                    return f"✅ <code>{short_id}</code> đã được approve trước đó — không đăng lại."
                return (f"🎉 Đã đăng YouTube (privacy: {res.get('privacy_status')}) — "
                        f"upload <code>{res.get('upload_id')}</code> đang chạy.")
            reason = parts[2].strip() if len(parts) > 2 else ""
            res = service_digest.reject_digest(store, short_id, reason, actor="telegram")
            return (f"🗑 Đã reject kỳ {res.get('date')}. "
                    "Nộp nội dung lại qua POST /api/v1/digest/submit.")
        except PermissionError as exc:
            return f"⚠️ {exc}"
        except ValueError as exc:
            return f"⚠️ {exc}"
        except Exception as exc:  # noqa: BLE001 — publish lỗi phải báo về chat
            logger.exception("telegram command %s failed", cmd)
            return f"❌ Lỗi khi {cmd}: {str(exc)[:300]}"

    return None


def start_telegram_bot(store, shorts_svc, *, youtube_svc=None) -> threading.Thread | None:
    """Long-polling getUpdates trong daemon thread. None nếu Telegram chưa cấu hình."""
    if not service_news._telegram_configured():
        logger.info("telegram bot disabled (VIDEO_TELEGRAM_BOT_TOKEN / CHAT_ID chưa set)")
        return None
    token = os.environ["VIDEO_TELEGRAM_BOT_TOKEN"].strip()
    chat_id = str(os.environ["VIDEO_TELEGRAM_CHAT_ID"]).strip()
    api = f"https://api.telegram.org/bot{token}"

    def _loop() -> None:
        offset = 0
        consecutive_errors = 0
        while True:
            try:
                with httpx.Client(timeout=40.0) as client:
                    resp = client.get(f"{api}/getUpdates",
                                      params={"timeout": 25, "offset": offset,
                                              "allowed_updates": '["message"]'})
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    logger.warning("telegram getUpdates not ok: %s", str(data)[:200])
                    time.sleep(10)
                    continue
                consecutive_errors = 0
                for upd in data.get("result") or []:
                    offset = max(offset, int(upd.get("update_id") or 0) + 1)
                    msg = upd.get("message") or {}
                    if str((msg.get("chat") or {}).get("id")) != chat_id:
                        logger.info("telegram: ignore update from foreign chat %s",
                                    (msg.get("chat") or {}).get("id"))
                        continue
                    reply = _handle_command(store, shorts_svc, youtube_svc,
                                            msg.get("text") or "")
                    if reply:
                        try:
                            service_news._send_telegram(reply)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("telegram reply failed: %s", exc)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 409:
                    logger.error("telegram getUpdates 409 CONFLICT — token đang được "
                                 "poll/webhook bởi consumer khác. TẮT poller.")
                    return
                consecutive_errors += 1
                logger.warning("telegram poll http error: %s", exc)
                time.sleep(min(60, 10 * max(1, consecutive_errors)))
            except Exception as exc:  # noqa: BLE001
                consecutive_errors += 1
                logger.warning("telegram poll error: %s", exc)
                time.sleep(min(60, 10 * max(1, consecutive_errors)))

    th = threading.Thread(target=_loop, name="telegram-bot", daemon=True)
    th.start()
    return th
