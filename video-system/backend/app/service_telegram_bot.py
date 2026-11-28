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

import html
import logging
import os
import threading
import time
from typing import Any, Callable

import httpx

from . import service_digest, service_news

logger = logging.getLogger("video-system.telegram_bot")


def _handle_command(store, shorts_svc, youtube_svc, text: str,
                    proposals_svc=None) -> str | None:
    """Parse + chạy 1 lệnh. Trả về text reply (None = im lặng)."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=2)
    cmd = parts[0].lower()

    if cmd in {"/duyet", "/tuchoi"}:
        if proposals_svc is None:
            return "⚠️ Proposal service chưa được wire."
        if len(parts) < 2:
            return f"Dùng: {cmd} <proposal_id>"
        prop_id = parts[1].strip()
        try:
            if cmd == "/duyet":
                res = proposals_svc.approve_proposal(prop_id)
                return (f"✅ Đã duyệt <b>{html.escape(res['title'])}</b> "
                        f"(<code>{prop_id}</code>) → needs_script "
                        f"(priority {res['priority']}). "
                        "Vào hàng chờ gen kịch bản.")
            reason = parts[2].strip() if len(parts) > 2 else ""
            res = proposals_svc.reject_proposal(prop_id, reason or None)
            return (f"🗑 Đã từ chối <b>{html.escape(res['title'])}</b> "
                    f"(<code>{prop_id}</code>)"
                    + (f" — {html.escape(reason)}" if reason else ""))
        except Exception as exc:  # noqa: BLE001 — command lỗi phải báo về chat
            logger.exception("telegram command %s failed", cmd)
            return f"❌ Lỗi khi {cmd}: {str(exc)[:300]}"

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


def _handle_proposal_callback(api: str, cb: dict, proposals_svc,
                              notify_fn) -> None:
    """B-T1: xử lý nút ✅ Duyệt / ❌ Từ chối trên tin proposals (callback_query).

    Luật: MỌI nhánh đều phải answerCallbackQuery (bỏ sót → owner thấy spinner
    10s). Nguồn chân lý là DB — bấm khi proposal đã chốt/expired thì alert lý
    do, không đổi state. Sau xử lý edit tin gốc bỏ nút (nút chết không giữ
    lại); tin >48h Telegram không cho sửa → fallback gửi tin mới.
    callback_data chỉ là con trỏ `pp:ok:{pid}` / `pp:no:{pid}` — quyền nằm ở
    service call, không có đường tăng quyền mới."""
    cb_id = cb.get("id")
    data = cb.get("data") or ""
    msg = cb.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id"))
    message_id = msg.get("message_id")

    def _answer(text: str, alert: bool = False) -> None:
        try:
            with httpx.Client(timeout=15.0) as client:
                client.post(f"{api}/answerCallbackQuery", json={
                    "callback_query_id": cb_id, "text": text[:200],
                    "show_alert": alert,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("answerCallbackQuery failed: %s", exc)

    def _edit_or_fallback(new_text: str) -> None:
        edited = False
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(f"{api}/editMessageText", json={
                    "chat_id": chat_id, "message_id": message_id,
                    "text": new_text, "parse_mode": "HTML",
                })  # không reply_markup → nút biến mất
                resp.raise_for_status()
                edited = True
        except Exception as exc:  # noqa: BLE001 — tin >48h không sửa được
            logger.warning("editMessageText failed: %s", exc)
        if not edited:
            try:
                notify_fn(new_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("telegram fallback notify failed: %s", exc)

    if not proposals_svc:
        _answer("⚠️ Proposal service chưa được wire.", alert=True)
        return
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "pp" or parts[1] not in {"ok", "no"}:
        _answer("⚠️ Lệnh không rõ — dùng /duyet <id> / /tuchoi <id>")
        return
    pid = parts[2]
    base_text = msg.get("text") or ""
    try:
        if parts[1] == "ok":
            res = proposals_svc.approve_proposal(pid)
            _answer(f"✅ Đã duyệt — priority {res['priority']}, vào hàng chờ cần kịch bản")
            _edit_or_fallback(f"{base_text}\n\n→ ✅ <b>ĐÃ DUYỆT</b> "
                              f"(priority {res['priority']}) — needs_script, "
                              "hàng chờ gen kịch bản.")
        else:
            res = proposals_svc.reject_proposal(pid)
            _answer("🗑 Đã từ chối đề xuất")
            _edit_or_fallback(f"{base_text}\n\n→ ❌ <b>ĐÃ TỪ CHỐI</b> — "
                              "xem nhận xét reviewer trong web (Inbox đề xuất).")
    except Exception as exc:  # noqa: BLE001 — 409/404 đều báo lý do qua alert
        logger.warning("proposal callback %s failed: %s", data, exc)
        detail = str(getattr(exc, "message", "")) or str(exc)[:200]
        _answer(f"⚠️ {detail}", alert=True)
        _edit_or_fallback(f"{base_text}\n\n→ ⚠️ Không xử lý được: {detail}")


def start_telegram_bot(store, shorts_svc, *, youtube_svc=None,
                       proposals_svc=None) -> threading.Thread | None:
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
                                              "allowed_updates": '["message","callback_query"]'})
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    logger.warning("telegram getUpdates not ok: %s", str(data)[:200])
                    time.sleep(10)
                    continue
                consecutive_errors = 0
                for upd in data.get("result") or []:
                    offset = max(offset, int(upd.get("update_id") or 0) + 1)
                    # B-T1: nút Duyệt/Từ chối trên tin proposals (callback_query)
                    cb = upd.get("callback_query")
                    if cb:
                        cb_msg = cb.get("message") or {}
                        if str((cb_msg.get("chat") or {}).get("id")) != chat_id:
                            logger.info("telegram: ignore callback from foreign chat %s",
                                        (cb_msg.get("chat") or {}).get("id"))
                            continue
                        _handle_proposal_callback(api, cb, proposals_svc,
                                                  service_news._send_telegram)
                        continue
                    msg = upd.get("message") or {}
                    if str((msg.get("chat") or {}).get("id")) != chat_id:
                        logger.info("telegram: ignore update from foreign chat %s",
                                    (msg.get("chat") or {}).get("id"))
                        continue
                    reply = _handle_command(store, shorts_svc, youtube_svc,
                                            msg.get("text") or "",
                                            proposals_svc=proposals_svc)
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
