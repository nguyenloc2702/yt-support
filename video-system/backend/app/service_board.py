"""Shot Board service (C-1, C-2, C-3, C-5) — dispatch, blind review & server scoring."""
from __future__ import annotations

import json
from typing import Any

from . import new_id, now
from .service_items import ItemService
from .store import Store

# Bảng trừ điểm §4.2 — nguồn chân lý duy nhất trên server (C-1).
DEFECT_TABLE: dict[str, tuple[float, bool, str]] = {
    "FACE_REFLECTION": (0.4, True, "Mặt/người phản chiếu trong kính/visor"),
    "SUBJECT_OUT_OF_FRAME": (0.4, True, "Subject chính ra khỏi khung khi drift"),
    "CAMERA_WRONG_DIRECTION": (1.0, True, "Camera sai hướng"),
    "INVENTED_ACTION": (10.0, True, "Hành động bịa, trái narration"),
    "DUPLICATE_OBJECT": (0.2, False, "Vật thể bị nhân đôi"),
    "HELMET_BALL": (0.2, False, "Mũ/bóng lỗi hình dạng"),
    "COSTUME_MORPH": (0.2, False, "Trang phục biến dạng"),
    "PANEL_COUNT_MORPH": (0.2, False, "Số lượng panel/khung biến đổi"),
    "TEXT_EMERGES": (0.2, False, "Chữ lạ tự xuất hiện"),
    "DISCLOSE_ONLY": (0.0, False, "Chỉ ghi chú, không trừ điểm"),
}


class BoardError(Exception):
    def __init__(self, status_code: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


def compute_score_and_verdict(
    defect_codes: list[str], pairing_ok: bool = True
) -> tuple[float, str]:
    """Server-computed score & verdict from defect_codes (§4.2 + C-1)."""
    for code in defect_codes:
        if code not in DEFECT_TABLE:
            raise BoardError(422, f"Unknown defect code: {code}", "invalid_defect_code")
    deduction = sum(DEFECT_TABLE[c][0] for c in defect_codes)
    score = round(max(0.0, min(10.0, 10.0 - deduction)), 2)
    has_fail_level = any(DEFECT_TABLE[c][1] for c in defect_codes)
    verdict = "fail" if (not pairing_ok or has_fail_level or score <= 9.5) else "pass"
    return score, verdict


class BoardService:
    def __init__(self, store: Store, items: ItemService) -> None:
        self.store = store
        self.items = items

    def _require_item(self, item_id: str) -> dict:
        item = self.items.get(item_id)
        if not item or item.get("status") == "deleted":
            raise KeyError(item_id)
        return item

    def _board_status(self, shot: dict) -> str:
        bs = shot.get("board_status")
        if bs:
            return str(bs)
        if shot.get("video_hash"):
            return "generated"
        return "empty"

    def _parse_review(self, row: dict) -> dict[str, Any]:
        codes = json.loads(row["defect_codes_json"]) if row.get("defect_codes_json") else []
        disclose = (
            json.loads(row["disclose_notes_json"])
            if row.get("disclose_notes_json")
            else []
        )
        regen_brief = (
            json.loads(row["regen_brief_json"])
            if row.get("regen_brief_json")
            else None
        )
        return {
            "id": row["id"],
            "item_id": row["item_id"],
            "shot_index": row["shot_index"],
            "video_hash": row["video_hash"],
            "video_version": row["video_version"],
            "reviewer": row["reviewer"],
            "defect_codes": codes,
            "disclose_notes": disclose,
            "note": row.get("note"),
            "pairing_ok": bool(row["pairing_ok"]),
            "score": float(row["score"]),
            "verdict": row["verdict"],
            "regen_brief": regen_brief,
            "recurring_defect": bool(row.get("recurring_defect")),
            "stale": bool(row.get("stale")),
            "created_at": row["created_at"],
        }

    def _latest_regen_brief(self, item_id: str, shot_index: int) -> dict | None:
        row = self.store.query_one(
            "SELECT regen_brief_json FROM shot_reviews "
            "WHERE item_id=? AND shot_index=? AND verdict='fail' AND regen_brief_json IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (item_id, shot_index),
        )
        if not row or not row.get("regen_brief_json"):
            return None
        return json.loads(row["regen_brief_json"])

    def _format_shot(self, shot: dict, include_reviews: bool = True) -> dict[str, Any]:
        bs = self._board_status(shot)
        exp_dur = round(
            max(0.0, float(shot.get("audio_end_sec") or 0.0) - float(shot.get("audio_start_sec") or 0.0)),
            3,
        )
        reviews: list[dict[str, Any]] = []
        if include_reviews and shot.get("video_hash"):
            rows = self.store.query(
                "SELECT * FROM shot_reviews "
                "WHERE item_id=? AND shot_index=? AND video_hash=? AND stale=0 "
                "ORDER BY created_at ASC",
                (shot["item_id"], shot["index_num"], shot["video_hash"]),
            )
            reviews = [self._parse_review(r) for r in rows]

        regen_brief = (
            self._latest_regen_brief(shot["item_id"], shot["index_num"])
            if bs == "red"
            else None
        )
        return {
            "id": shot["id"],
            "item_id": shot["item_id"],
            "index": shot["index_num"],
            "index_num": shot["index_num"],
            "status": bs,
            "pipeline_status": shot["status"],
            "video_hash": shot.get("video_hash"),
            "video_version": int(shot.get("video_version") or 0),
            "uploaded_by": shot.get("uploaded_by"),
            "declared_generator": shot.get("declared_generator"),
            "claimed_by": shot.get("claimed_by"),
            "visual_prompt": shot.get("visual_prompt"),
            "narration": shot.get("narration"),
            "expected_duration_sec": exp_dur,
            "regen_brief": regen_brief,
            "reviews": reviews,
        }

    def get_shot(self, item_id: str, shot_index: int) -> dict[str, Any]:
        self._require_item(item_id)
        shot = self.store.query_one(
            "SELECT * FROM shots WHERE item_id=? AND index_num=? AND status != 'superseded'",
            (item_id, shot_index),
        )
        if not shot:
            raise KeyError(f"shot {shot_index}")
        return self._format_shot(shot)

    def _is_owner_of_red(self, shot: dict, caller: str) -> bool:
        return caller in {
            shot.get("declared_generator"),
            shot.get("uploaded_by"),
            shot.get("claimed_by"),
        }

    def _has_info(self, shot: dict) -> bool:
        has_prompt = bool((shot.get("visual_prompt") or "").strip())
        exp_dur = float(shot.get("audio_end_sec") or 0.0) - float(shot.get("audio_start_sec") or 0.0)
        has_narr = bool((shot.get("narration") or "").strip())
        return has_prompt and (exp_dur > 0 or has_narr)

    def _reviewed_by(self, item_id: str, shot_index: int, video_hash: str, caller: str) -> bool:
        row = self.store.query_one(
            "SELECT 1 AS ok FROM shot_reviews "
            "WHERE item_id=? AND shot_index=? AND video_hash=? AND reviewer=? AND stale=0",
            (item_id, shot_index, video_hash, caller),
        )
        return row is not None

    def next_shot(self, item_id: str, caller: str) -> dict[str, Any]:
        """Dispatch priority (C-3): red_regen -> empty_ready -> waiting_review -> missing_info.

        Never returns red_unreviewed.
        """
        self._require_item(item_id)
        shots = self.store.query(
            "SELECT * FROM shots WHERE item_id=? AND status != 'superseded' ORDER BY index_num ASC",
            (item_id,),
        )
        buckets: dict[str, list[dict]] = {
            "red_regen": [],
            "empty_ready": [],
            "waiting_review": [],
            "missing_info": [],
        }
        for s in shots:
            bs = self._board_status(s)
            if bs == "red":
                if self._is_owner_of_red(s, caller):
                    buckets["red_regen"].append(s)
            elif bs == "empty":
                if self._has_info(s):
                    buckets["empty_ready"].append(s)
                else:
                    buckets["missing_info"].append(s)
            elif bs == "generated" and s.get("video_hash"):
                if (
                    caller not in {s.get("uploaded_by"), s.get("declared_generator")}
                    and not self._reviewed_by(item_id, s["index_num"], s["video_hash"], caller)
                ):
                    buckets["waiting_review"].append(s)

        for reason in ("red_regen", "empty_ready", "waiting_review", "missing_info"):
            if buckets[reason]:
                return {
                    "shot": self._format_shot(buckets[reason][0]),
                    "reason": reason,
                }
        return {"shot": None, "reason": "no_work"}

    def agent_inbox(self, caller: str) -> dict[str, Any]:
        """Inbox for caller (C-3): splits red_regen_shots (mine) and red_unclaimed (open)."""
        rows = self.store.query(
            "SELECT s.* FROM shots s JOIN items i ON s.item_id = i.id "
            "WHERE i.status != 'deleted' AND s.status != 'superseded' "
            "ORDER BY s.item_id ASC, s.index_num ASC",
        )
        red_regen_shots: list[dict[str, Any]] = []
        red_unclaimed: list[dict[str, Any]] = []
        waiting_review_shots: list[dict[str, Any]] = []
        empty_ready_shots: list[dict[str, Any]] = []

        for s in rows:
            bs = self._board_status(s)
            if bs == "red":
                if self._is_owner_of_red(s, caller):
                    red_regen_shots.append(self._format_shot(s, include_reviews=False))
                elif not s.get("claimed_by"):
                    red_unclaimed.append(self._format_shot(s, include_reviews=False))
            elif bs == "generated" and s.get("video_hash"):
                if (
                    caller not in {s.get("uploaded_by"), s.get("declared_generator")}
                    and not self._reviewed_by(s["item_id"], s["index_num"], s["video_hash"], caller)
                ):
                    waiting_review_shots.append(self._format_shot(s, include_reviews=False))
            elif bs == "empty" and self._has_info(s):
                empty_ready_shots.append(self._format_shot(s, include_reviews=False))

        return {
            "caller": caller,
            "red_regen_shots": red_regen_shots,
            "red_unclaimed": red_unclaimed,
            "waiting_review_shots": waiting_review_shots,
            "empty_ready_shots": empty_ready_shots,
        }

    def record_board_upload(
        self,
        item_id: str,
        shot_index: int,
        caller: str,
        video_hash: str,
        declared_generator: str | None = None,
    ) -> dict[str, Any]:
        """Attach uploaded video metadata to shot board state."""
        shot = self.store.query_one(
            "SELECT * FROM shots WHERE item_id=? AND index_num=? AND status != 'superseded'",
            (item_id, shot_index),
        )
        if not shot:
            raise KeyError(f"shot {shot_index}")
        t = now()
        gen = (declared_generator or "").strip() or caller
        new_ver = int(shot.get("video_version") or 0) + 1
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE shot_reviews SET stale=1 WHERE item_id=? AND shot_index=? AND stale=0",
                (item_id, shot_index),
            )
            conn.execute(
                "UPDATE shots SET board_status='generated', video_hash=?, video_version=?, "
                "uploaded_by=?, declared_generator=?, claimed_by=NULL, claim_lease_until=NULL, "
                "updated_at=? WHERE id=?",
                (video_hash, new_ver, caller, gen, t, shot["id"]),
            )
        return self.get_shot(item_id, shot_index)

    def submit_review(
        self,
        item_id: str,
        shot_index: int,
        caller: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit a blind review; server computes score & verdict from defect_codes (C-1)."""
        if "score" in body:
            raise BoardError(
                422,
                "Client cannot submit score; server computes score from defect_codes.",
                "client_score_rejected",
            )
        self._require_item(item_id)
        shot = self.store.query_one(
            "SELECT * FROM shots WHERE item_id=? AND index_num=? AND status != 'superseded'",
            (item_id, shot_index),
        )
        if not shot:
            raise KeyError(f"shot {shot_index}")
        v_hash = shot.get("video_hash")
        if not v_hash:
            raise BoardError(422, "Shot has no uploaded video to review.", "no_video")

        reviewed_hash = body.get("reviewed_video_hash")
        if reviewed_hash and reviewed_hash != v_hash:
            raise BoardError(
                409,
                "reviewed_video_hash does not match current video_hash.",
                "stale_video_hash",
            )

        if caller in {shot.get("uploaded_by"), shot.get("declared_generator")}:
            raise BoardError(
                403,
                "Generator/uploader cannot review their own shot.",
                "self_review",
            )

        if self._reviewed_by(item_id, shot_index, v_hash, caller):
            raise BoardError(
                409,
                "Caller already reviewed this video version.",
                "already_reviewed",
            )

        defect_codes = body.get("defect_codes")
        if defect_codes is None:
            defect_codes = []
        if not isinstance(defect_codes, list) or not all(isinstance(c, str) for c in defect_codes):
            raise BoardError(422, "defect_codes must be a list of strings.", "validation_error")

        pairing_ok = bool(body.get("pairing_ok", True))
        score, verdict = compute_score_and_verdict(defect_codes, pairing_ok=pairing_ok)

        disclose_notes = body.get("disclose_notes") or []
        note = body.get("note")
        regen_brief = body.get("regen_brief")

        rv_id = new_id("rv")
        t = now()
        v_ver = int(shot.get("video_version") or 1)

        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO shot_reviews (id,item_id,shot_id,shot_index,video_hash,"
                "video_version,reviewer,defect_codes_json,disclose_notes_json,note,"
                "pairing_ok,score,verdict,regen_brief_json,recurring_defect,stale,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?)",
                (
                    rv_id,
                    item_id,
                    shot["id"],
                    shot_index,
                    v_hash,
                    v_ver,
                    caller,
                    json.dumps(defect_codes, ensure_ascii=False),
                    json.dumps(disclose_notes, ensure_ascii=False),
                    note,
                    1 if pairing_ok else 0,
                    score,
                    verdict,
                    json.dumps(regen_brief, ensure_ascii=False) if regen_brief is not None else None,
                    t,
                ),
            )
            active = conn.execute(
                "SELECT verdict FROM shot_reviews "
                "WHERE item_id=? AND shot_index=? AND video_hash=? AND stale=0",
                (item_id, shot_index, v_hash),
            ).fetchall()
            verdicts = [r[0] for r in active]
            if "fail" in verdicts:
                new_status = "red"
            elif verdicts.count("pass") >= 2:
                new_status = "green"
            else:
                new_status = "generated"
            conn.execute(
                "UPDATE shots SET board_status=?, updated_at=? WHERE id=?",
                (new_status, t, shot["id"]),
            )

        rv_row = self.store.query_one("SELECT * FROM shot_reviews WHERE id=?", (rv_id,))
        return {
            "review": self._parse_review(rv_row),
            "shot": self.get_shot(item_id, shot_index),
        }
