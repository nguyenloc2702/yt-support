"""Brief authoring — Phase 0 of the agent script→job flow (2026-11-23).

Owner (hoặc manager khi có registry) tạo brief; brief phải `approved` trước khi
agent được nộp script proposal. Ba chế độ duyệt:

- `calendar`  : chủ đề nằm trong content calendar đã duyệt → approved ngay.
                CHỈ owner được tạo (tránh agent tự duyệt chủ đề).
- `objection` : tự approved sau `objection_window_sec` nếu owner không phản đối.
                Duyệt bằng scheduler 60s trong giờ làm việc 08:00–18:00 (VN),
                KHÔNG duyệt ở read-path (tránh việc mở dashboard duyệt hàng loạt).
- `hard`      : owner phải bấm approve. Brief `sensitive=1` luôn bị ép về `hard`.

Brief còn giữ `critic` — agent được chỉ định review. Chưa có registry thì chỉ
owner set critic; mọi chữ ký/finding của non-author phải đến từ critic đó.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import Store, new_id, now

logger = logging.getLogger("video-system.briefs")

APPROVAL_MODES = {"calendar", "objection", "hard"}
DEFAULT_OBJECTION_WINDOW_SEC = 1800
DEFAULT_TARGET_DURATION_SEC = 780.0
DEFAULT_DEADLINE_AFTER_SEC = 8 * 3600
WORK_START_HOUR = 8
WORK_END_HOUR = 18

# ZoneInfo cần tzdata; container slim/Windows có thể thiếu -> fallback UTC+7 cố định
# (VN không có DST) thay vì chết lúc import và kéo cả service xuống.
try:
    from zoneinfo import ZoneInfo

    _VN_TZ: Any = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:  # noqa: BLE001
    logger.warning("tzdata thiếu — dùng UTC+7 cố định cho lịch làm việc brief")
    _VN_TZ = timezone(timedelta(hours=7))


class BriefError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "brief_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def in_working_hours(ts: float) -> bool:
    if os.environ.get("VIDEO_BRIEF_SWEEP_ALWAYS") == "1":
        return True
    return WORK_START_HOUR <= datetime.fromtimestamp(ts, _VN_TZ).hour < WORK_END_HOUR


class BriefService:
    def __init__(self, store: Store) -> None:
        self.store = store

    # -- create / read ------------------------------------------------------

    def create(self, *, title: str, created_by: str, angle: str | None = None,
               target_duration_sec: float = DEFAULT_TARGET_DURATION_SEC,
               language: str = "en", style_lock_version: str = "v3",
               source_links: list[str] | None = None,
               deadline_at: float | None = None,
               budget: dict[str, Any] | None = None,
               approval_mode: str = "objection",
               sensitive: bool = False,
               objection_window_sec: int = DEFAULT_OBJECTION_WINDOW_SEC,
               critic: str | None = None) -> dict:
        title = (title or "").strip()
        if not title:
            raise BriefError("Brief thiếu title.", status_code=422, code="empty_title")
        if approval_mode not in APPROVAL_MODES:
            raise BriefError(f"approval_mode không hợp lệ: {approval_mode}",
                             status_code=422, code="bad_approval_mode")
        if not (60 <= float(target_duration_sec) <= 3600):
            raise BriefError("target_duration_sec phải trong 60–3600s.",
                             status_code=422, code="bad_duration")
        if sensitive:
            approval_mode = "hard"
        critic = (critic or "").strip() or None
        if critic and critic == created_by:
            raise BriefError("Critic không được trùng người tạo brief — cần người ký đối trọng.",
                             status_code=422, code="critic_is_author")

        ts = now()
        brief_id = new_id("brf")
        approved = approval_mode == "calendar"
        self.store.execute(
            "INSERT INTO job_briefs (id,title,angle,target_duration_sec,language,"
            "style_lock_version,source_links_json,deadline_at,budget_json,status,"
            "approval_mode,sensitive,objection_window_sec,critic,approved_by,approved_at,"
            "created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (brief_id, title, (angle or "").strip() or None, float(target_duration_sec),
             language or "en", style_lock_version or "v3",
             json.dumps(source_links or [], ensure_ascii=False),
             deadline_at or (ts + DEFAULT_DEADLINE_AFTER_SEC),
             json.dumps(budget or {}, ensure_ascii=False),
             "approved" if approved else "draft", approval_mode, 1 if sensitive else 0,
             int(objection_window_sec), critic,
             "calendar" if approved else None, ts if approved else None,
             created_by, ts, ts),
        )
        self._event(created_by, brief_id, "brief.created",
                    {"approval_mode": approval_mode, "sensitive": bool(sensitive),
                     "critic": critic})
        return self.get(brief_id)

    def get(self, brief_id: str) -> dict:
        row = self.store.query_one("SELECT * FROM job_briefs WHERE id=?", (brief_id,))
        if not row:
            raise BriefError(f"Brief không tồn tại: {brief_id}", status_code=404,
                             code="not_found")
        return dict(row)

    def list(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        limit_n = max(1, min(200, int(limit)))
        if status:
            rows = self.store.query(
                "SELECT * FROM job_briefs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit_n))
        else:
            rows = self.store.query(
                "SELECT * FROM job_briefs ORDER BY created_at DESC LIMIT ?", (limit_n,))
        return [dict(r) for r in rows]

    # -- decisions ----------------------------------------------------------

    def approve(self, brief_id: str, *, approved_by: str) -> dict:
        brief = self.get(brief_id)
        if brief["status"] == "approved":
            return brief
        if brief["status"] != "draft":
            raise BriefError(
                f"Brief đang {brief['status']} — không duyệt được.",
                status_code=409, code="bad_status")
        ts = now()
        changed = self.store.cas_update(
            "UPDATE job_briefs SET status='approved', approved_by=?, approved_at=?, "
            "updated_at=? WHERE id=? AND status='draft'",
            (approved_by, ts, ts, brief_id))
        if not changed:
            cur = self.get(brief_id)
            raise BriefError(
                f"Brief vừa đổi trạng thái sang {cur['status']} — không duyệt được.",
                status_code=409, code="status_race")
        self._event(approved_by, brief_id, "brief.approved", {"mode": brief["approval_mode"]})
        return self.get(brief_id)

    def cancel(self, brief_id: str, *, actor: str) -> dict:
        brief = self.get(brief_id)
        if brief["status"] in {"done", "cancelled"}:
            return brief
        ts = now()
        changed = self.store.cas_update(
            "UPDATE job_briefs SET status='cancelled', updated_at=? "
            "WHERE id=? AND status IN ('draft','approved')", (ts, brief_id))
        if not changed:
            cur = self.get(brief_id)
            if cur["status"] == "cancelled":
                return cur  # thread khác vừa huỷ — coi như thành công
            raise BriefError(
                f"Brief vừa đổi trạng thái sang {cur['status']} — không huỷ được.",
                status_code=409, code="status_race")
        self._event(actor, brief_id, "brief.cancelled", {})
        return self.get(brief_id)

    def set_critic(self, brief_id: str, *, critic: str, actor: str) -> dict:
        brief = self.get(brief_id)
        if brief["status"] in {"done", "cancelled"}:
            raise BriefError(f"Brief đang {brief['status']} — không đổi critic.",
                             409, "bad_status")
        critic = (critic or "").strip() or None
        if critic and critic == brief["created_by"]:
            raise BriefError("Critic không được trùng người tạo brief — cần người ký đối trọng.",
                             422, "critic_is_author")
        signed = self.store.query_one(
            "SELECT 1 AS x FROM script_signatures s "
            "JOIN script_proposals p ON p.id=s.proposal_id WHERE p.brief_id=? LIMIT 1",
            (brief_id,))
        if signed and (brief.get("critic") or None) != (critic or None):
            raise BriefError("Đã có chữ ký — không đổi critic được.", 409, "critic_locked")
        ts = now()
        self.store.execute(
            "UPDATE job_briefs SET critic=?, updated_at=? WHERE id=?",
            ((critic or "").strip() or None, ts, brief_id))
        self._event(actor, brief_id, "brief.critic_set", {"critic": critic})
        return self.get(brief_id)

    def require_approved(self, brief_id: str) -> dict:
        brief = self.get(brief_id)
        if brief["status"] != "approved":
            raise BriefError(
                f"Brief chưa approved (đang {brief['status']}) — chưa được nộp script.",
                status_code=409, code="brief_not_approved")
        return brief

    # -- objection sweep ----------------------------------------------------

    def sweep_objection_windows(self, *, now_ts: float | None = None) -> dict:
        """Duyệt brief `objection` đã hết cửa sổ. Chạy bằng scheduler, không ở read-path."""
        ts = now() if now_ts is None else float(now_ts)
        if not in_working_hours(ts):
            return {"approved": 0, "checked": 0, "skipped": "outside_working_hours"}
        rows = self.store.query(
            "SELECT id, created_at, objection_window_sec FROM job_briefs "
            "WHERE status='draft' AND approval_mode='objection'")
        approved = 0
        for r in rows:
            due = float(r["created_at"] or 0) + int(
                r["objection_window_sec"] or DEFAULT_OBJECTION_WINDOW_SEC)
            if ts < due:
                continue
            changed = self.store.cas_update(
                "UPDATE job_briefs SET status='approved', "
                "approved_by='auto:objection-window', approved_at=?, updated_at=? "
                "WHERE id=? AND status='draft'", (ts, ts, r["id"]))
            if changed:
                approved += 1
                self._event("system", r["id"], "brief.approved",
                            {"mode": "objection", "auto": True})
        return {"approved": approved, "checked": len(rows), "at": ts}

    def start_objection_scheduler(self, interval_sec: int = 60) -> threading.Thread | None:
        if os.environ.get("VIDEO_BRIEF_SCHEDULER", "1").strip() in {"0", "false", "False"}:
            return None
        th = threading.Thread(target=self._sweep_loop, args=(interval_sec,),
                              name="brief-objection", daemon=True)
        th.start()
        return th

    def _sweep_loop(self, interval_sec: int) -> None:
        while True:
            try:
                res = self.sweep_objection_windows()
                if res.get("approved"):
                    logger.info("brief objection sweep: %s", res)
            except Exception as exc:  # noqa: BLE001
                logger.warning("brief objection sweep failed: %s", exc)
            threading.Event().wait(max(10, int(interval_sec)))

    # -- internals ----------------------------------------------------------

    def _event(self, actor: str, brief_id: str, type_: str, payload: dict) -> None:
        try:
            self.store.execute(
                "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
                "VALUES (?,?,?,?,?,?,?)",
                (new_id("evt"), None, None, type_, actor,
                 json.dumps({"brief_id": brief_id, **payload}, ensure_ascii=False), now()))
        except Exception as exc:  # noqa: BLE001 — audit không được giết nghiệp vụ
            logger.warning("brief event log failed: %s", exc)
