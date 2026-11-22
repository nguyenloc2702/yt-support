"""Bảng chat chung cho agent (chốt 3 bên 2026-11-23).

Model: thread 1-1 — tối đa 2 participants (created_by + participant_b).
Agent thứ 2 tự claim slot trống bằng CAS khi gửi tin đầu tiên. Nội dung
thread chỉ participants + owner đọc được; metadata thread list là public
(read scope) để agent mới tìm được agent cũ để hỏi.

Delivery: REST polling + cursor `since_id` (seq AUTOINCREMENT — không rewind
sau retention). Chọn phương án này vì agent là script HTTP (precedent workflow
todos), long-poll đậu anyio threadpool, SSE gãy với GZipMiddleware + nginx
mặc định, WS/broker là dependency thứ 2 cho <10 agent.

Luật vệ sinh: body ≤ 2000 ký tự; 20 tin / 5 phút / sender (429);
30 tin/thread → thread tự `locked` (409 cho POST mới); retention 30 ngày
(VIDEO_CHAT_RETENTION_DAYS) — AUTOINCREMENT giữ cursor không tái sử dụng.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import Store, new_id, now

logger = logging.getLogger("video-system.chat")

MAX_BODY_CHARS = 2000
MAX_MESSAGES_PER_THREAD = 30
RATE_LIMIT_MESSAGES = 20
RATE_LIMIT_WINDOW_S = 300
DEFAULT_RETENTION_DAYS = 30


class ChatError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "chat_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class ChatService:
    def __init__(self, store: Store) -> None:
        self.store = store

    # -- threads ------------------------------------------------------------

    def create_thread(self, sender: str, title: str, *, item_id: str | None = None,
                      actor_scope: str = "ingest") -> dict:
        if actor_scope == "read":
            raise ChatError("Scope read không được tạo thread.", status_code=403, code="forbidden")
        title = (title or "").strip()[:200]
        tid = new_id("thr")
        ts = now()
        self.store.execute(
            "INSERT INTO chat_threads (id,title,created_by,item_id,status,created_at,updated_at) "
            "VALUES (?,?,?,?, 'open', ?, ?)",
            (tid, title, sender, item_id, ts, ts),
        )
        self._event(sender, tid, "thread.created", {"title": title})
        return self.get_thread(tid)

    def list_threads(self, limit: int = 50) -> list[dict]:
        rows = self.store.query(
            "SELECT t.*, "
            "(SELECT body FROM chat_messages m WHERE m.thread_id=t.id ORDER BY m.seq DESC LIMIT 1) AS last_body, "
            "(SELECT sender FROM chat_messages m WHERE m.thread_id=t.id ORDER BY m.seq DESC LIMIT 1) AS last_sender, "
            "(SELECT COUNT(*) FROM chat_messages m WHERE m.thread_id=t.id) AS message_count "
            "FROM chat_threads t ORDER BY t.last_message_at DESC, t.created_at DESC LIMIT ?",
            (max(1, min(200, limit)),),
        )
        return [dict(r) for r in rows]

    def get_thread(self, thread_id: str) -> dict:
        row = self.store.query_one(
            "SELECT t.*, "
            "(SELECT COUNT(*) FROM chat_messages m WHERE m.thread_id=t.id) AS message_count "
            "FROM chat_threads t WHERE t.id=?",
            (thread_id,),
        )
        if not row:
            raise ChatError(f"Thread không tồn tại: {thread_id}", status_code=404, code="not_found")
        return dict(row)

    def close_thread(self, thread_id: str) -> dict:
        thread = self.get_thread(thread_id)
        self.store.execute(
            "UPDATE chat_threads SET status='closed', updated_at=? WHERE id=?", (now(), thread_id))
        return {**thread, "status": "closed"}

    def _participants(self, thread: dict) -> set[str]:
        allowed = {thread["created_by"]}
        if thread.get("participant_b"):
            allowed.add(thread["participant_b"])
        allowed.add("owner")  # session PIN — xem + tham gia mọi thread
        return allowed

    # -- messages -----------------------------------------------------------

    def post_message(self, thread_id: str, sender: str, body: str,
                     *, is_owner: bool = False) -> dict:
        body = (body or "").strip()
        if not body:
            raise ChatError("Tin nhắn rỗng.", status_code=422, code="empty_body")
        if len(body) > MAX_BODY_CHARS:
            raise ChatError(
                f"Tin nhắn {len(body)} ký tự vượt giới hạn {MAX_BODY_CHARS}.",
                status_code=422, code="body_too_long")

        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM chat_threads WHERE id=?", (thread_id,)
            ).fetchone()
            if row is None:
                raise ChatError(f"Thread không tồn tại: {thread_id}", status_code=404, code="not_found")
            thread = dict(row)
            if thread["status"] != "open":
                raise ChatError(
                    f"Thread đã {thread['status']} — không nhắn được nữa.",
                    status_code=409, code="thread_locked")

            participants = self._participants(thread)
            if not is_owner and sender not in participants:
                if not thread.get("participant_b"):
                    # Slot trống — agent thứ 2 claim bằng CAS (2 agent claim cùng lúc: 1 thắng)
                    claimed = conn.execute(
                        "UPDATE chat_threads SET participant_b=?, updated_at=? "
                        "WHERE id=? AND participant_b IS NULL",
                        (sender, now(), thread_id)).rowcount
                    if claimed == 0:
                        raise ChatError(
                            "Thread vừa được agent khác claim slot cuối.", status_code=409,
                            code="slot_taken")
                    thread["participant_b"] = sender
                else:
                    raise ChatError(
                        f"Thread này chỉ dành cho {thread['created_by']} + "
                        f"{thread['participant_b']} (luật tối đa 2 agent).",
                        status_code=403, code="not_participant")

            count = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE thread_id=?", (thread_id,)).fetchone()[0]
            locked_now = False
            if count >= MAX_MESSAGES_PER_THREAD:
                # Đánh dấu locked rồi commit — raise sau (raise trong tx sẽ rollback mất UPDATE)
                conn.execute(
                    "UPDATE chat_threads SET status='locked', updated_at=? WHERE id=?",
                    (now(), thread_id))
                locked_now = True

            if not locked_now:
                recent = conn.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE sender=? AND created_at > ?",
                    (sender, time.time() - RATE_LIMIT_WINDOW_S)).fetchone()[0]
                if recent >= RATE_LIMIT_MESSAGES:
                    raise ChatError(
                        f"Rate limit: tối đa {RATE_LIMIT_MESSAGES} tin / {RATE_LIMIT_WINDOW_S // 60} phút / sender.",
                        status_code=429, code="rate_limited")

                cur = conn.execute(
                    "INSERT INTO chat_messages (thread_id,sender,body,created_at) VALUES (?,?,?,?)",
                    (thread_id, sender, body, now()))
                seq = cur.lastrowid
                conn.execute(
                    "UPDATE chat_threads SET last_message_seq=?, last_message_at=?, updated_at=? "
                    "WHERE id=?",
                    (seq, now(), now(), thread_id))

        if locked_now:
            raise ChatError(
                f"Thread đã đạt {MAX_MESSAGES_PER_THREAD} tin — tự khóa.",
                status_code=409, code="thread_locked")

        self._event(sender, thread_id, "message.posted", {"seq": seq})
        return {"thread_id": thread_id, "seq": seq, "sender": sender, "body": body}

    def get_messages(self, thread_id: str, sender: str, *, since_id: int = 0,
                     limit: int = 100, is_owner: bool = False) -> dict:
        thread = self.get_thread(thread_id)
        if not is_owner and sender not in self._participants(thread):
            raise ChatError(
                "Chỉ participants của thread đọc được nội dung (metadata thread list là public).",
                status_code=403, code="not_participant")
        limit_n = max(1, min(200, limit))
        rows = self.store.query(
            "SELECT seq, sender, body, created_at FROM chat_messages "
            "WHERE thread_id=? AND seq > ? ORDER BY seq ASC LIMIT ?",
            (thread_id, max(0, int(since_id)), limit_n + 1),
        )
        has_more = len(rows) > limit_n
        rows = rows[:limit_n]
        last_seq = rows[-1]["seq"] if rows else int(since_id)
        return {
            "thread_id": thread_id,
            "messages": [dict(r) for r in rows],
            "last_seq": last_seq,
            "has_more": has_more,
            "participants": sorted(p for p in self._participants(thread) if p != "owner"),
            "status": thread["status"],
        }

    # -- retention ------------------------------------------------------------

    def cleanup_old_messages(self, retention_days: int | None = None) -> dict:
        if retention_days is None:
            days = int(os.environ.get("VIDEO_CHAT_RETENTION_DAYS", "") or DEFAULT_RETENTION_DAYS)
        else:
            days = int(retention_days)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        deleted_msgs = self.store.execute(
            "DELETE FROM chat_messages WHERE created_at < ?", (cutoff,))
        deleted_threads = self.store.execute(
            "DELETE FROM chat_threads WHERE COALESCE(last_message_at, created_at) < ? "
            "AND status IN ('closed','locked')", (cutoff,))
        return {"deleted_messages": max(0, deleted_msgs), "deleted_threads": max(0, deleted_threads),
                "retention_days": days}

    def start_retention_worker(self) -> threading.Thread | None:
        """Dọn tin cũ 1 lần/ngày (daemon) — giữ cursor AUTOINCREMENT an toàn."""
        th = threading.Thread(target=self._retention_loop, name="chat-retention", daemon=True)
        th.start()
        return th

    def _retention_loop(self) -> None:
        while True:
            try:
                res = self.cleanup_old_messages()
                if res["deleted_messages"] or res["deleted_threads"]:
                    logger.info("chat retention: %s", res)
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat retention error: %s", exc)
            time.sleep(6 * 3600)

    # -- events ----------------------------------------------------------------

    def _event(self, actor: str, thread_id: str, type_: str, payload: dict) -> None:
        try:
            import json as _json
            self.store.execute(
                "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
                "VALUES (?,?,?,?,?,?,?)",
                (new_id("evt"), None, None, type_, actor,
                 _json.dumps({"thread_id": thread_id, **payload}, ensure_ascii=False), now()),
            )
        except Exception as exc:  # noqa: BLE001 — event log không được giết chat
            logger.warning("chat event log failed: %s", exc)
