"""Phát webhook khi `next_action` của agreement đổi.

`duel.agreement(brief_id)` là read model; `next_action` của nó đổi ở nhiều đường:
route API (tạo brief / duyệt / proposal / finding / ký / owner decision) và cả
scheduler tự duyệt objection-window. Notifier này gom hai đường đó về một chỗ:

- `sync()`  — so `next_action` với row `brief_agreement_state`; chỉ khi khác mới
  ghi event `agreement.next_action_changed` rồi dispatch webhook.
- `sweep()` — quét brief `approved` và gọi `agreement_fn` để bắt các chuyển trạng
  thái không đi qua route (auto-approve của scheduler, hoặc process restart).

Bất biến: webhook là side-effect. Mọi lỗi (DB, serialize, dispatch) đều bị nuốt
và chỉ ghi log — nghiệp vụ brief/duel không bao giờ được hỏng vì notifier.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

from .store import Store, new_id, now

logger = logging.getLogger("video-system.agreement_notifier")

EVENT_TYPE = "agreement.next_action_changed"


class AgreementNotifier:
    def __init__(self, store: Store, webhooks: Any,
                 agreement_fn: Callable[[str], dict] | None = None) -> None:
        self.store = store
        self.webhooks = webhooks
        # Callable đọc agreement (thường là `duel.agreement`) — không import duel
        # ở đây để tránh vòng phụ thuộc service_script_duel -> ... -> notifier.
        self.agreement_fn = agreement_fn
        self._sweeper_thread: threading.Thread | None = None

    # -- sync ---------------------------------------------------------------

    def sync(self, brief_id: str, agreement: dict, reason: str = "") -> bool:
        """Phát event nếu `next_action` khác lần trước. True = đã phát."""
        try:
            next_action = str((agreement or {}).get("next_action") or "").strip()
            if not next_action:
                return False

            # Serialize TRƯỚC khi ghi state: payload hỏng thì không được đánh dấu
            # "đã phát" (nếu không sẽ mất event vĩnh viễn).
            payload_json = json.dumps(
                {"brief_id": brief_id, "next_action": next_action,
                 "agreement": agreement, "reason": reason},
                ensure_ascii=False, default=str)
            ts = now()
            evt_id = new_id("evt")
            # State + event phải cùng sống hoặc cùng chết: nếu INSERT event fail
            # mà state đã ghi next_action mới thì lần sau dedupe bỏ qua -> mất event.
            #
            # Dedupe đọc TRONG transaction (BEGIN IMMEDIATE): đọc ngoài thì hai
            # thread cùng sync thấy "chưa có row" và bắn 2 event.
            with self.store.transaction() as conn:
                prev = conn.execute(
                    "SELECT next_action FROM brief_agreement_state WHERE brief_id=?",
                    (brief_id,)).fetchone()
                if prev and prev["next_action"] == next_action:
                    return False  # dedupe: cùng bước kế thì không bắn lại
                conn.execute(
                    "INSERT INTO brief_agreement_state (brief_id,next_action,updated_at) "
                    "VALUES (?,?,?) ON CONFLICT(brief_id) DO UPDATE SET "
                    "next_action=excluded.next_action, updated_at=excluded.updated_at",
                    (brief_id, next_action, ts))
                conn.execute(
                    "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    # item_id/shot_id NULL: hook global (item_id IS NULL) vẫn nhận.
                    (evt_id, None, None, EVENT_TYPE, "system", payload_json, ts))
            # Dispatch chỉ sau khi commit — webhook không bao giờ thấy event rollback.
            row = self.store.query_one("SELECT * FROM events WHERE id=?", (evt_id,))
            if row:
                self.webhooks.dispatch_event({**dict(row), "type": EVENT_TYPE})
            return True
        except Exception as exc:  # noqa: BLE001 — webhook không được giết nghiệp vụ
            logger.warning("agreement notifier: sync %s thất bại: %s", brief_id, exc)
            return False

    # -- sweep --------------------------------------------------------------

    def sweep(self, limit: int | None = None) -> int:
        """Quét brief approved, sync từng cái. Trả số event đã phát.

        Mặc định KHÔNG limit: `ORDER BY updated_at DESC` + LIMIT sẽ bỏ đói brief
        cũ mãi mãi khi số brief approved vượt limit. `limit` chỉ dành cho test.
        """
        if not self.agreement_fn:
            return 0
        sql = ("SELECT id FROM job_briefs WHERE status='approved' "
               "ORDER BY updated_at DESC")
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (max(1, int(limit)),)
        try:
            rows = self.store.query(sql, params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agreement sweep: không đọc được danh sách brief: %s", exc)
            return 0
        emitted = 0
        for r in rows:
            brief_id = r["id"]
            try:
                agreement = self.agreement_fn(brief_id)
            except Exception as exc:  # noqa: BLE001 — 1 brief lỗi không chặn cả lượt
                logger.warning("agreement sweep: bỏ qua %s (%s)", brief_id, exc)
                continue
            if self.sync(brief_id, agreement, "sweep"):
                emitted += 1
        return emitted

    def start_sweeper(self, interval_sec: int = 30) -> threading.Thread | None:
        """Thread daemon — cùng pattern `start_objection_scheduler` của briefs."""
        if not self.agreement_fn:
            return None
        if self._sweeper_thread is not None and self._sweeper_thread.is_alive():
            return self._sweeper_thread  # chỉ 1 sweeper, không spawn thêm
        th = threading.Thread(target=self._sweep_loop, args=(interval_sec,),
                              name="agreement-notifier", daemon=True)
        self._sweeper_thread = th
        th.start()
        return th

    def _sweep_loop(self, interval_sec: int) -> None:
        while True:
            try:
                emitted = self.sweep()
                if emitted:
                    logger.info("agreement sweep: phát %s event next_action", emitted)
            except Exception as exc:  # noqa: BLE001 — thread không được chết
                logger.warning("agreement sweep lỗi: %s", exc)
            # Không cần stop event: process chết là thread daemon chết theo.
            threading.Event().wait(max(10, int(interval_sec)))
