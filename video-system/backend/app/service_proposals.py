"""Muse proposals — đề xuất ý tưởng short từ muse agents, review chéo, chốt cho owner.

Luồng (milestone 2026-11-28):

    proposed (muse đẩy lên, tự schedule mỗi giờ)
      → under_review (3 reviewer bất kỳ trong pool, chấm mù, loại người đề xuất)
      → awaiting_owner (≥2/3 pass ≥9.5 → Telegram cho owner)
      → needs_script   (owner Duyệt qua web hoặc /duyet — hàng chờ ưu tiên)
    nhánh: rejected (không đủ pass / owner từ chối), expired (chờ owner quá hạn)

Tái dùng: pattern chấm mù N-reviewer của Shot Board (service_board.py) — chặn
tự chấm, chặn chấm lại, blind redact theo caller; scheduler daemon thread +
gate env (service_news); Telegram outbound đọc cùng VIDEO_TELEGRAM_BOT_TOKEN/
CHAT_ID (service_news._send_telegram) — chưa cấu hình thì im lặng, không lỗi.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
from typing import Any

import httpx

from . import new_id, now
from .store import Store

logger = logging.getLogger("video-system.proposals")

PROPOSAL_STATUSES = (
    "proposed", "under_review", "awaiting_owner", "needs_script", "rejected", "expired",
)
ALLOWED_MODES = ("news", "decode", "trailer")
ALLOWED_LANGS = ("vi", "en")

DEDUPE_WINDOW_SEC = 72 * 3600


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def pass_score() -> float:
    return _env_float("VIDEO_PROPOSAL_PASS_SCORE", 9.5)


def reviews_required() -> int:
    return max(1, _env_int("VIDEO_PROPOSAL_REVIEWS_REQUIRED", 3))


def pass_required() -> int:
    return max(1, _env_int("VIDEO_PROPOSAL_PASS_REQUIRED", 2))


def expire_hours() -> float:
    return _env_float("VIDEO_PROPOSAL_EXPIRE_HOURS", 12.0)


class ProposalError(Exception):
    def __init__(self, status_code: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


def _normalize_title(title: str) -> str:
    """Chuẩn hóa title để so trùng: thường hóa, bỏ dấu câu, gộp khoảng trắng."""
    t = (title or "").lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _priority_from_avg(avg: float | None) -> int:
    if avg is None:
        return 3
    if avg >= 9.8:
        return 1
    if avg >= pass_score():
        return 2
    return 3


class ProposalService:
    def __init__(self, store: Store) -> None:
        self.store = store

    # -- helpers ---------------------------------------------------------------

    def _get(self, proposal_id: str) -> dict:
        row = self.store.query_one(
            "SELECT * FROM muse_proposals WHERE id=?", (proposal_id,)
        )
        if not row:
            raise ProposalError(404, f"Proposal {proposal_id} not found.", "proposal_not_found")
        return row

    def _reviews(self, proposal_id: str) -> list[dict]:
        rows = self.store.query(
            "SELECT * FROM muse_proposal_reviews WHERE proposal_id=? ORDER BY created_at ASC",
            (proposal_id,),
        )
        return [self._parse_review(r) for r in rows]

    def _parse_review(self, row: dict) -> dict[str, Any]:
        dims = json.loads(row["dimensions_json"]) if row.get("dimensions_json") else None
        return {
            "id": row["id"],
            "proposal_id": row["proposal_id"],
            "reviewer": row["reviewer"],
            "score": float(row["score"]),
            "dimensions": dims,
            "verdict": row["verdict"],
            "comment": row.get("comment"),
            "created_at": row["created_at"],
        }

    def _format_proposal(
        self, row: dict, caller: str | None = None, privileged: bool = False
    ) -> dict[str, Any]:
        """Blind theo luật C-4: điểm/review của người khác ẩn cho tới khi caller
        đã chấm proposal đó (manager/admin thấy hết). Sau khi chốt thì verdict
        tổng hiện với tất cả — progress (review_count) luôn hiện cho UI."""
        reviews = self._reviews(row["id"])
        settled = row["status"] in {"awaiting_owner", "needs_script", "rejected", "expired"}
        caller_reviewed = (
            privileged or settled
            or (caller is not None and any(r["reviewer"] == caller for r in reviews))
        )
        visible_reviews = []
        for r in reviews:
            if caller_reviewed:
                visible_reviews.append(r)
            else:
                visible_reviews.append({
                    "id": r["id"],
                    "reviewer": r["reviewer"],
                    "created_at": r["created_at"],
                    "score": None,
                    "dimensions": None,
                    "verdict": None,
                    "comment": None,
                })
        return {
            "id": row["id"],
            "proposer": row["proposer"],
            "title": row["title"],
            "hook": row["hook"],
            "angle": row.get("angle"),
            "source_url": row.get("source_url"),
            "source_note": row.get("source_note"),
            "mode": row["mode"],
            "lang": row["lang"],
            "target_seconds": float(row["target_seconds"] or 60.0),
            "status": row["status"],
            "priority": int(row["priority"] or 3),
            "signals": int(row["signals"] or 1),
            "avg_score": row.get("avg_score"),
            "review_count": int(row["review_count"] or 0),
            "pass_count": row.get("pass_count") if caller_reviewed else None,
            "reviews_required": reviews_required(),
            "pass_required": pass_required(),
            "reviews": visible_reviews,
            "reviews_visible": caller_reviewed,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- create + dedupe ---------------------------------------------------------

    def create_proposal(self, caller: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Muse đẩy đề xuất. Trùng ý (title chuẩn hóa hoặc source_url) trong 72h
        → gộp: signals +1 trên proposal cũ, trả deduped=true — cho thấy nhiều
        muse cùng nghĩ một ý thay vì tạo trùng."""
        title = (payload.get("title") or "").strip()
        hook = (payload.get("hook") or "").strip()
        if not title or not hook:
            raise ProposalError(422, "title và hook là bắt buộc.", "validation_error")
        if len(title) > 300 or len(hook) > 600:
            raise ProposalError(422, "title ≤ 300 ký tự, hook ≤ 600 ký tự.", "validation_error")
        mode = (payload.get("mode") or "news").strip()
        if mode not in ALLOWED_MODES:
            raise ProposalError(422, f"mode phải là 1 trong {ALLOWED_MODES}.", "validation_error")
        lang = (payload.get("lang") or "en").strip()
        if lang not in ALLOWED_LANGS:
            raise ProposalError(422, f"lang phải là 1 trong {ALLOWED_LANGS}.", "validation_error")
        target_seconds = float(payload.get("target_seconds") or 60.0)
        if not (10.0 <= target_seconds <= 300.0):
            raise ProposalError(422, "target_seconds trong khoảng 10-300.", "validation_error")
        source_url = (payload.get("source_url") or "").strip() or None
        angle = (payload.get("angle") or "").strip() or None
        source_note = (payload.get("source_note") or "").strip() or None

        cutoff = now() - DEDUPE_WINDOW_SEC
        title_norm = _normalize_title(title)
        existing = self.store.query_one(
            "SELECT id, signals FROM muse_proposals "
            "WHERE status IN ('proposed','under_review','awaiting_owner','needs_script') "
            "AND created_at >= ? AND ("
            "  (source_url IS NOT NULL AND source_url = ?) "
            "  OR title_norm = ?) "
            "ORDER BY created_at DESC LIMIT 1",
            (cutoff, source_url or "\0none", title_norm),
        )
        if existing:
            self.store.execute(
                "UPDATE muse_proposals SET signals = signals + 1, updated_at = ? WHERE id = ?",
                (now(), existing["id"]),
            )
            return {
                "deduped": True,
                "proposal_id": existing["id"],
                "signals": int(existing["signals"]) + 1,
            }

        pid = new_id("prop")
        t = now()
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO muse_proposals (id,proposer,title,title_norm,hook,angle,"
                "source_url,source_note,mode,lang,target_seconds,status,priority,"
                "signals,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'proposed',3,1,?,?)",
                (pid, caller, title, title_norm, hook, angle, source_url, source_note,
                 mode, lang, target_seconds, t, t),
            )
        return {"deduped": False, "proposal": self.get_proposal(pid, caller=caller)}

    # -- review dispatch ---------------------------------------------------------

    def next_review_proposal(self, caller: str) -> dict[str, Any]:
        """Dispatch kiểu board: loại proposal của chính mình, đã chấm; ưu tiên
        proposal đã có lượt chấm (chốt sớm) rồi đề xuất cũ nhất. Hết việc trả
        no_review_proposal + retry_after_sec để vòng lặp agent biết nghỉ."""
        rows = self.store.query(
            "SELECT * FROM muse_proposals WHERE status IN ('proposed','under_review') "
            "ORDER BY created_at ASC"
        )
        tier_progress: list[dict] = []
        tier_open: list[dict] = []
        for row in rows:
            if row["proposer"] == caller:
                continue
            if self._has_reviewed(row["id"], caller):
                continue
            reviews = self._reviews(row["id"])
            if len(reviews) < reviews_required():
                (tier_progress if reviews else tier_open).append(row)
        bucket = tier_progress + tier_open
        if not bucket:
            return {"proposal": None, "reason": "no_review_proposal", "retry_after_sec": 60}
        return {
            "proposal": self._format_proposal(bucket[0], caller=caller),
            "reason": "awaiting_review",
        }

    def _has_reviewed(self, proposal_id: str, caller: str | None) -> bool:
        if not caller:
            return False
        row = self.store.query_one(
            "SELECT 1 AS ok FROM muse_proposal_reviews WHERE proposal_id=? AND reviewer=?",
            (proposal_id, caller),
        )
        return row is not None

    # -- submit review + settle --------------------------------------------------

    def submit_proposal_review(
        self,
        caller: str,
        proposal_id: str,
        score: float,
        dimensions: dict | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        row = self._get(proposal_id)
        if row["status"] not in {"proposed", "under_review"}:
            raise ProposalError(
                409, f"Proposal đã chốt ({row['status']}), không chấm tiếp được.",
                "proposal_settled",
            )
        if row["proposer"] == caller:
            raise ProposalError(403, "Người đề xuất không chấm proposal của chính mình.", "self_review")
        if self._has_reviewed(proposal_id, caller):
            raise ProposalError(409, "Caller đã chấm proposal này rồi.", "already_reviewed")
        try:
            score = round(float(score), 2)
        except (TypeError, ValueError):
            raise ProposalError(422, "score phải là số.", "validation_error")
        if not (0.0 <= score <= 10.0):
            raise ProposalError(422, "score trong khoảng 0-10.", "validation_error")
        if comment and len(comment) > 2000:
            raise ProposalError(422, "comment quá 2000 ký tự.", "comment_too_long")
        verdict = "pass" if score >= pass_score() else "fail"

        rv_id = new_id("prv")
        t = now()
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO muse_proposal_reviews (id,proposal_id,reviewer,score,"
                "dimensions_json,verdict,comment,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (rv_id, proposal_id, caller, score,
                 json.dumps(dimensions, ensure_ascii=False) if dimensions else None,
                 verdict, comment, t),
            )
        return self._settle(proposal_id, caller)

    def _settle(self, proposal_id: str, caller: str) -> dict[str, Any]:
        """Recompute sau mỗi lượt chấm: đủ pass → awaiting_owner + Telegram;
        đủ fail (không thể đạt 2/3) hoặc chấm đủ số lượt mà thiếu pass → rejected."""
        row = self._get(proposal_id)
        reviews = self._reviews(proposal_id)
        n = len(reviews)
        passes = sum(1 for r in reviews if r["verdict"] == "pass")
        fails = n - passes
        need_req = reviews_required()
        pass_req = pass_required()

        new_status: str | None = None
        avg = round(sum(r["score"] for r in reviews) / n, 2) if n else None
        if passes >= pass_req:
            new_status = "awaiting_owner"
        elif fails > need_req - pass_req:  # mất khả năng đạt 2/3 pass → chốt sớm
            new_status = "rejected"
        elif n >= need_req:
            new_status = "rejected"

        t = now()
        if new_status:
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE muse_proposals SET status=?, avg_score=?, review_count=?, "
                    "pass_count=?, priority=?, decided_at=?, updated_at=? WHERE id=?",
                    (new_status, avg, n, passes,
                     _priority_from_avg(avg) if new_status == "awaiting_owner" else row["priority"],
                     t, t, proposal_id),
                )
            if new_status == "awaiting_owner":
                self._notify_owner(proposal_id)
        else:
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE muse_proposals SET status='under_review', review_count=?, "
                    "pass_count=?, avg_score=?, updated_at=? WHERE id=?",
                    (n, passes, avg, t, proposal_id),
                )
        return self.get_proposal(proposal_id, caller=caller)

    # -- owner decisions -----------------------------------------------------------

    def approve_proposal(self, proposal_id: str, priority: int | None = None) -> dict[str, Any]:
        """Owner duyệt: awaiting_owner → needs_script (hàng chờ ưu tiên gen kịch bản)."""
        row = self._get(proposal_id)
        if row["status"] != "awaiting_owner":
            raise ProposalError(
                409, f"Chỉ duyệt được proposal đang awaiting_owner (hiện: {row['status']}).",
                "proposal_not_awaiting",
            )
        prio = int(priority) if priority is not None else int(row["priority"] or 3)
        if prio not in (1, 2, 3):
            raise ProposalError(422, "priority phải là 1, 2 hoặc 3.", "validation_error")
        t = now()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE muse_proposals SET status='needs_script', priority=?, updated_at=? "
                "WHERE id=?",
                (prio, t, proposal_id),
            )
        return self.get_proposal(proposal_id)

    def reject_proposal(self, proposal_id: str, reason: str | None = None) -> dict[str, Any]:
        row = self._get(proposal_id)
        if row["status"] not in {"awaiting_owner", "under_review", "proposed"}:
            raise ProposalError(
                409, f"Không từ chối được proposal ở trạng thái {row['status']}.",
                "proposal_not_rejectable",
            )
        t = now()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE muse_proposals SET status='rejected', decided_at=?, updated_at=? "
                "WHERE id=?",
                (t, t, proposal_id),
            )
        return self.get_proposal(proposal_id)

    def expire_stale(self) -> dict[str, Any]:
        """Scheduler gọi: awaiting_owner quá hạn → expired (owner không phản hồi)."""
        cutoff = now() - expire_hours() * 3600.0
        rows = self.store.query(
            "SELECT id FROM muse_proposals WHERE status='awaiting_owner' AND decided_at < ?",
            (cutoff,),
        )
        t = now()
        for r in rows:
            self.store.execute(
                "UPDATE muse_proposals SET status='expired', decided_at=?, updated_at=? WHERE id=?",
                (t, t, r["id"]),
            )
        return {"expired": len(rows)}

    # -- queries -----------------------------------------------------------------

    def list_proposals(self, status: str | None = None) -> dict[str, Any]:
        if status and status not in PROPOSAL_STATUSES:
            raise ProposalError(422, f"status phải là 1 trong {PROPOSAL_STATUSES}.", "validation_error")
        if status:
            rows = self.store.query(
                "SELECT * FROM muse_proposals WHERE status=? ORDER BY priority ASC, created_at ASC",
                (status,),
            )
        else:
            rows = self.store.query(
                "SELECT * FROM muse_proposals ORDER BY "
                "CASE status WHEN 'awaiting_owner' THEN 0 WHEN 'under_review' THEN 1 "
                "WHEN 'proposed' THEN 2 WHEN 'needs_script' THEN 3 ELSE 4 END, "
                "priority ASC, created_at ASC",
            )
        return {"proposals": [self._format_proposal(r) for r in rows]}

    def get_proposal(
        self, proposal_id: str, caller: str | None = None, privileged: bool = False
    ) -> dict[str, Any]:
        row = self._get(proposal_id)
        return self._format_proposal(row, caller=caller, privileged=privileged)

    def needs_script_queue(self) -> dict[str, Any]:
        rows = self.store.query(
            "SELECT * FROM muse_proposals WHERE status='needs_script' "
            "ORDER BY priority ASC, created_at ASC",
        )
        return {"queue": [self._format_proposal(r) for r in rows], "total": len(rows)}

    # -- scheduler ----------------------------------------------------------------

    def start_expire_scheduler(self) -> threading.Thread | None:
        """Gate env VIDEO_PROPOSAL_SCHEDULER (mặc định bật, '0'/'false' để tắt).
        Tick 60s nhưng chỉ quét expire mỗi giờ (khóa theo giờ lịch)."""
        gate = os.environ.get("VIDEO_PROPOSAL_SCHEDULER", "1").strip().lower()
        if gate in {"0", "false", "no", "off"}:
            logger.info("proposal scheduler disabled (VIDEO_PROPOSAL_SCHEDULER=%s)", gate)
            return None
        state = {"last_hour": None}

        def _tick() -> None:
            hour_key = int(now() // 3600)
            if state["last_hour"] == hour_key:
                return
            state["last_hour"] = hour_key
            res = self.expire_stale()
            if res.get("expired"):
                logger.info("proposal scheduler: expired %s awaiting_owner", res["expired"])

        def _loop() -> None:
            while True:
                try:
                    _tick()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("proposal scheduler error: %s", exc)
                time.sleep(60)

        th = threading.Thread(target=_loop, name="proposal-expire", daemon=True)
        th.start()
        return th

    # -- telegram ----------------------------------------------------------------

    def _telegram_configured(self) -> bool:
        return bool(
            os.environ.get("VIDEO_TELEGRAM_BOT_TOKEN", "").strip()
            and os.environ.get("VIDEO_TELEGRAM_CHAT_ID", "").strip()
        )

    def _send_telegram(self, text: str) -> None:
        token = os.environ["VIDEO_TELEGRAM_BOT_TOKEN"].strip()
        chat_id = os.environ["VIDEO_TELEGRAM_CHAT_ID"].strip()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(url, json={
                "chat_id": chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            })
        resp.raise_for_status()

    def _notify_owner(self, proposal_id: str) -> None:
        """Gửi Telegram cho owner khi proposal đủ 2/3 pass. Telegram chưa cấu
        hình → log và bỏ qua (không làm hỏng flow chấm)."""
        try:
            if not self._telegram_configured():
                logger.info("proposal %s awaiting_owner (telegram chưa cấu hình)", proposal_id)
                return
            row = self._get(proposal_id)
            reviews = self._reviews(proposal_id)
            e = html.escape
            lines = [
                f"🛡 <b>Đề xuất short CHỜ DUYỆT</b> "
                f"({row['pass_count']}/{reviews_required()} pass ≥ {pass_score()})",
                f"📌 <b>{e(row['title'])}</b>",
                f"🎣 {e(row['hook'])}",
            ]
            if row.get("source_url"):
                lines.append(f"🔗 {e(row['source_url'])}")
            lines.append(
                f"👤 đề xuất: {e(row['proposer'])} · {e(row['mode'])}/{e(row['lang'])} "
                f"· ~{int(row['target_seconds'] or 60)}s · avg {row['avg_score']}"
            )
            for r in reviews:
                c = f" — {e(str(r['comment'])[:120])}" if r.get("comment") else ""
                lines.append(f"  • {e(r['reviewer'])}: {r['score']}/{r['verdict']}{c}")
            lines.append(
                f"\n✅ Duyệt: <code>/duyet {proposal_id}</code> · "
                f"❌ Từ chối: <code>/tuchoi {proposal_id} [lý do]</code>"
            )
            lines.append("(hoặc nút Duyệt trong web: Short Studio → Inbox đề xuất)")
            self._send_telegram("\n".join(lines))
        except Exception as exc:  # noqa: BLE001 — notify phải không bao giờ làm đỏ flow
            logger.warning("proposal telegram notify failed: %s", exc)
