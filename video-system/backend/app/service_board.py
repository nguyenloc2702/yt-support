"""Shot Board service (C-1, C-2, C-3, C-5) — dispatch, blind review & server scoring."""
from __future__ import annotations

import json
import os
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

# Khoảng nghỉ gợi ý cho vòng lặp reviewer khi mode=review hết việc.
REVIEW_RETRY_AFTER_SEC = 30


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


def defect_codes_public() -> list[dict[str, Any]]:
    """Bảng §4.2 cho UI render checkbox — UI không được hardcode số trừ."""
    return [
        {
            "code": code,
            "deduction": deduction,
            "fail_level": fail_level,
            "description": description,
        }
        for code, (deduction, fail_level, description) in DEFECT_TABLE.items()
    ]


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

    def _parse_comment(self, row: dict) -> dict[str, Any]:
        return {
            "id": row["id"],
            "item_id": row["item_id"],
            "shot_index": row["shot_index"],
            "video_version": int(row.get("video_version") or 0),
            "author": row["author"],
            "body": row["body"],
            "created_at": row["created_at"],
        }

    def _comments_block(
        self,
        shot: dict,
        caller: str | None,
        privileged: bool,
        reviews: list[dict],
    ) -> dict[str, Any]:
        """Comment per-shot với visibility theo luật chấm mù C-4.

        Thread đầy đủ chỉ cho: manager/admin, người gen/upload/claimed_by
        (chính là agent quay lại gen sửa shot — đọc comment để sửa chuẩn),
        và caller đã chấm version hiện tại. Worker chưa chấm chỉ thấy số
        lượng: nội dung comment có thể hé lộ lỗi trước khi chấm, phá chấm
        mù y như scores_summary.
        """
        rows = self.store.query(
            "SELECT * FROM shot_comments WHERE item_id=? AND shot_index=? ORDER BY created_at ASC",
            (shot["item_id"], shot["index_num"]),
        )
        visible = bool(privileged) or (
            caller is not None
            and (
                caller
                in {shot.get("uploaded_by"), shot.get("declared_generator"), shot.get("claimed_by")}
                or any(r["reviewer"] == caller for r in reviews)
            )
        )
        return {
            "comments": [self._parse_comment(r) for r in rows] if visible else [],
            "comments_visible": visible,
            "comment_count": len(rows),
        }

    def _format_shot(
        self,
        shot: dict,
        include_reviews: bool = True,
        caller: str | None = None,
        privileged: bool = False,
    ) -> dict[str, Any]:
        bs = self._board_status(shot)
        exp_dur = round(
            max(0.0, float(shot.get("audio_end_sec") or 0.0) - float(shot.get("audio_start_sec") or 0.0)),
            3,
        )
        reviews: list[dict[str, Any]] = []
        scores_summary: dict[str, Any] | None = None
        comments_info: dict[str, Any] = {
            "comments": [],
            "comments_visible": False,
            "comment_count": 0,
        }
        if include_reviews and shot.get("video_hash"):
            rows = self.store.query(
                "SELECT * FROM shot_reviews "
                "WHERE item_id=? AND shot_index=? AND video_hash=? AND stale=0 "
                "ORDER BY created_at ASC",
                (shot["item_id"], shot["index_num"], shot["video_hash"]),
            )
            reviews = [self._parse_review(r) for r in rows]
            caller_reviewed = privileged or (
                caller is not None and any(r["reviewer"] == caller for r in reviews)
            )
            if caller_reviewed:
                scores_summary = {
                    "count": len(reviews),
                    "distinct_reviewers": len({r["reviewer"] for r in reviews}),
                    "pass_count": sum(1 for r in reviews if r["verdict"] == "pass"),
                    "fail_count": sum(1 for r in reviews if r["verdict"] == "fail"),
                    "scores": [r["score"] for r in reviews],
                }
            else:
                for rv in reviews:
                    rv["score"] = None
                    rv["defect_codes"] = None
        if include_reviews:
            comments_info = self._comments_block(shot, caller, privileged, reviews)

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
            "grandfathered": bool(shot.get("grandfathered")),
            "video_hash": shot.get("video_hash"),
            "video_version": int(shot.get("video_version") or 0),
            "uploaded_by": shot.get("uploaded_by"),
            "declared_generator": shot.get("declared_generator"),
            "claimed_by": shot.get("claimed_by"),
            "visual_prompt": shot.get("visual_prompt"),
            "narration": shot.get("narration"),
            "expected_duration_sec": exp_dur,
            "regen_brief": regen_brief,
            "scores_summary": scores_summary,
            "reviews": reviews,
            "comments": comments_info["comments"],
            "comments_visible": comments_info["comments_visible"],
            "comment_count": comments_info["comment_count"],
        }

    def get_shot(
        self,
        item_id: str,
        shot_index: int,
        caller: str | None = None,
        privileged: bool = False,
    ) -> dict[str, Any]:
        self._require_item(item_id)
        shot = self.store.query_one(
            "SELECT * FROM shots WHERE item_id=? AND index_num=? AND status != 'superseded'",
            (item_id, shot_index),
        )
        if not shot:
            raise BoardError(404, f"Shot {shot_index} not found.", "shot_not_found")
        return self._format_shot(shot, caller=caller, privileged=privileged)

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

    def _group_active_reviews(
        self, item_id: str | None = None
    ) -> dict[tuple[str, int, str], list[dict]]:
        """Active reviews nhóm theo (item, shot, video_hash) — 1 query thay vì N."""
        if item_id is None:
            rows = self.store.query(
                "SELECT * FROM shot_reviews WHERE stale=0 ORDER BY created_at ASC"
            )
        else:
            rows = self.store.query(
                "SELECT * FROM shot_reviews WHERE item_id=? AND stale=0 ORDER BY created_at ASC",
                (item_id,),
            )
        by_shot: dict[tuple[str, int, str], list[dict]] = {}
        for r in rows:
            key = (r["item_id"], r["shot_index"], r["video_hash"])
            by_shot.setdefault(key, []).append(self._parse_review(r))
        return by_shot

    def _review_bucket(
        self,
        caller: str,
        shots: list[dict],
        by_shot: dict[tuple[str, int, str], list[dict]],
    ) -> list[dict]:
        """Shot đang chờ chấm mà caller đủ điều kiện chấm, theo thứ tự ưu tiên.

        Loại trước khi trả (từng caller):
        - người gen/upload version hiện tại (nếu để lọt sẽ ăn 403 self_review);
        - đã chấm version hiện tại (nếu để lọt sẽ ăn 409 already_reviewed);
        - `grandfathered` (C-6): video trước ngày bật board không cần chấm —
          dispatch nó là phí một lượt review của người thật.

        Thứ tự (C-7 thu hẹp còn "thứ tự ưu tiên", không hạ số review):
          1. version đã có 1 pass — thêm đúng 1 lượt là xanh, mỗi lượt review
             sinh giá trị cao nhất;
          2. version chưa ai chấm — cần đủ 2 lượt.
        Trong mỗi nhóm giữ thứ tự index_num ASC của danh sách vào.
        """
        tier_close: list[dict] = []
        tier_open: list[dict] = []
        for s in shots:
            if not s.get("video_hash") or bool(s.get("grandfathered")):
                continue
            if self._board_status(s) != "generated":
                continue
            if caller in {s.get("uploaded_by"), s.get("declared_generator")}:
                continue
            reviews = by_shot.get((s["item_id"], s["index_num"], s["video_hash"]), [])
            if any(r["reviewer"] == caller for r in reviews):
                continue
            passes = sum(1 for r in reviews if r["verdict"] == "pass")
            if passes >= 2:
                continue  # đã xanh — phòng hờ cho trường hợp board_status lệch
            (tier_close if passes == 1 else tier_open).append(s)
        return tier_close + tier_open

    def next_shot(
        self, item_id: str, caller: str, privileged: bool = False, mode: str | None = None
    ) -> dict[str, Any]:
        """Dispatch priority (C-3): red_regen -> empty_ready -> waiting_review -> missing_info.

        Never returns red_unreviewed.

        mode="review" (C-7): chỉ trả việc chấm cho caller — reviewer gọi liên
        tục không bị nhét việc gen xen vào, vì job chấm vốn nằm dưới đáy thứ tự
        mặc định. Hết việc trả `no_review_work` + `retry_after_sec` để vòng lặp
        agent biết nghỉ, thay vì quét tay từng shot rồi ăn 403/409.
        """
        if mode and mode != "review":
            raise BoardError(422, f"Unknown dispatch mode: {mode}", "invalid_mode")
        self._require_item(item_id)
        shots = self.store.query(
            "SELECT * FROM shots WHERE item_id=? AND status != 'superseded' ORDER BY index_num ASC",
            (item_id,),
        )
        if mode == "review":
            reviewable = self._review_bucket(
                caller, shots, self._group_active_reviews(item_id)
            )
            if not reviewable:
                return {
                    "shot": None,
                    "reason": "no_review_work",
                    "retry_after_sec": REVIEW_RETRY_AFTER_SEC,
                }
            return {
                "shot": self._format_shot(
                    reviewable[0], caller=caller, privileged=privileged
                ),
                "reason": "waiting_review",
            }

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

        buckets["waiting_review"] = self._review_bucket(
            caller, shots, self._group_active_reviews(item_id)
        )

        for reason in ("red_regen", "empty_ready", "waiting_review", "missing_info"):
            if buckets[reason]:
                return {
                    "shot": self._format_shot(
                        buckets[reason][0], caller=caller, privileged=privileged
                    ),
                    "reason": reason,
                }
        return {"shot": None, "reason": "no_work"}

    def agent_inbox(self, caller: str, privileged: bool = False) -> dict[str, Any]:
        """Inbox for caller (C-3): splits red_regen_shots (mine) and red_unclaimed (open).

        `waiting_review_shots` là những shot caller ĐỦ ĐIỀU KIỆN chấm (khác
        người gen/upload, chưa chấm version này, không grandfathered), xếp
        1-pass trước. `reviewable_counts` nhóm theo item để reviewer thấy job
        nào còn chỗ chấm mà không phải quét hết danh sách shot.
        """
        rows = self.store.query(
            "SELECT s.* FROM shots s JOIN items i ON s.item_id = i.id "
            "WHERE i.status != 'deleted' AND s.status != 'superseded' "
            "ORDER BY s.item_id ASC, s.index_num ASC",
        )
        red_regen_shots: list[dict[str, Any]] = []
        red_unclaimed: list[dict[str, Any]] = []
        empty_ready_shots: list[dict[str, Any]] = []

        for s in rows:
            bs = self._board_status(s)
            if bs == "red":
                if self._is_owner_of_red(s, caller):
                    red_regen_shots.append(self._format_shot(s, include_reviews=False))
                elif not s.get("claimed_by"):
                    red_unclaimed.append(self._format_shot(s, include_reviews=False))
            elif bs == "empty" and self._has_info(s):
                empty_ready_shots.append(self._format_shot(s, include_reviews=False))

        waiting_review_shots = [
            self._format_shot(s, include_reviews=False)
            for s in self._review_bucket(caller, rows, self._group_active_reviews())
        ]

        reviewable_counts: dict[str, int] = {}
        for s in waiting_review_shots:
            reviewable_counts[s["item_id"]] = reviewable_counts.get(s["item_id"], 0) + 1

        return {
            "caller": caller,
            "red_regen_shots": red_regen_shots,
            "red_unclaimed": red_unclaimed,
            "waiting_review_shots": waiting_review_shots,
            "empty_ready_shots": empty_ready_shots,
            "reviewable_counts": reviewable_counts,
        }

    def preflight_upload(
        self,
        item_id: str,
        shot_index: int,
        caller: str,
        addresses_review_id: str | None = None,
        staging_changed: bool | None = None,
    ) -> None:
        """Validate board upload constraints BEFORE reading/saving media (C-2)."""
        self._require_item(item_id)
        shot = self.store.query_one(
            "SELECT * FROM shots WHERE item_id=? AND index_num=? AND status != 'superseded'",
            (item_id, shot_index),
        )
        if not shot:
            raise BoardError(404, f"Shot {shot_index} not found in item {item_id}.", "shot_not_found")

        bs = self._board_status(shot)
        next_ver = int(shot.get("video_version") or 0) + 1

        if next_ver >= 3 and staging_changed is False:
            prior_reviews = self.store.query(
                "SELECT id, verdict, defect_codes_json FROM shot_reviews "
                "WHERE item_id=? AND shot_index=? ORDER BY created_at DESC",
                (item_id, shot_index),
            )
            prior_fails = [r for r in prior_reviews if r["verdict"] == "fail"]
            if len(prior_fails) >= 2:
                codes_list = [set(json.loads(r["defect_codes_json"] or "[]")) for r in prior_fails]
                if codes_list[0] & codes_list[1]:
                    raise BoardError(
                        409,
                        "Upload lần >= 3 cùng lỗi mà staging_changed=false.",
                        "must_change_staging",
                    )
            if addresses_review_id and any(
                r["id"] == addresses_review_id
                and (r["verdict"] == "fail" or json.loads(r["defect_codes_json"] or "[]"))
                for r in prior_reviews
            ):
                raise BoardError(
                    409,
                    "Upload lần >= 3 sau review có lỗi bắt buộc phải đổi staging (staging_changed=true).",
                    "must_change_staging",
                )

        if bs == "red":
            if not addresses_review_id:
                raise BoardError(
                    422,
                    "Shot đang red — upload bắt buộc kèm addresses_review_id.",
                    "brief_required",
                )
            rv = self.store.query_one(
                "SELECT * FROM shot_reviews "
                "WHERE id=? AND item_id=? AND shot_index=? AND verdict='fail' AND stale=0",
                (addresses_review_id, item_id, shot_index),
            )
            if not rv or rv.get("video_hash") != shot.get("video_hash"):
                raise BoardError(
                    422,
                    "addresses_review_id không phải fail review còn hiệu lực trên version hiện tại.",
                    "stale_brief",
                )

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
            raise BoardError(404, f"Shot {shot_index} not found in item {item_id}.", "shot_not_found")
        t = now()
        gen = (declared_generator or "").strip() or caller
        new_ver = int(shot.get("video_version") or 0) + 1
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE shot_reviews SET stale=1 WHERE item_id=? AND shot_index=? AND stale=0",
                (item_id, shot_index),
            )
            conn.execute(
                "UPDATE shots SET board_status='generated', grandfathered=0, video_hash=?, video_version=?, "
                "uploaded_by=?, declared_generator=?, claimed_by=NULL, claim_lease_until=NULL, "
                "updated_at=? WHERE id=?",
                (video_hash, new_ver, caller, gen, t, shot["id"]),
            )
        return self.get_shot(item_id, shot_index, caller=caller)

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
            raise BoardError(404, f"Shot {shot_index} not found.", "shot_not_found")
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
        if verdict == "fail" and (not isinstance(regen_brief, dict) or not regen_brief):
            raise BoardError(
                422,
                "regen_brief là bắt buộc khi verdict=fail.",
                "regen_brief_required",
            )

        rv_id = new_id("rv")
        t = now()
        v_ver = int(shot.get("video_version") or 1)

        recurring = 0
        if defect_codes:
            prev_fails = self.store.query(
                "SELECT defect_codes_json FROM shot_reviews "
                "WHERE item_id=? AND shot_index=? AND verdict='fail' AND video_version < ?",
                (item_id, shot_index, v_ver),
            )
            prev_codes: set[str] = set()
            for pf in prev_fails:
                prev_codes.update(json.loads(pf["defect_codes_json"] or "[]"))
            if set(defect_codes) & prev_codes:
                recurring = 1

        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO shot_reviews (id,item_id,shot_id,shot_index,video_hash,"
                "video_version,reviewer,defect_codes_json,disclose_notes_json,note,"
                "pairing_ok,score,verdict,regen_brief_json,recurring_defect,stale,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    recurring,
                    0,
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
            "shot": self.get_shot(item_id, shot_index, caller=caller),
        }

    # -- comment per-shot (web Review Board) -----------------------------------

    def _get_board_shot(self, item_id: str, shot_index: int) -> dict:
        shot = self.store.query_one(
            "SELECT * FROM shots WHERE item_id=? AND index_num=? AND status != 'superseded'",
            (item_id, shot_index),
        )
        if not shot:
            raise BoardError(404, f"Shot {shot_index} not found.", "shot_not_found")
        return shot

    def add_comment(
        self, item_id: str, shot_index: int, caller: str, body: str
    ) -> dict[str, Any]:
        """Ghi comment của agent vào shot. Author = danh tính caller."""
        self._require_item(item_id)
        text = (body or "").strip()
        if not text:
            raise BoardError(422, "Nội dung comment không được rỗng.", "validation_error")
        if len(text) > 2000:
            raise BoardError(422, "Comment quá 2000 ký tự.", "comment_too_long")
        shot = self._get_board_shot(item_id, shot_index)
        cmt_id = new_id("cmt")
        t = now()
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO shot_comments (id,item_id,shot_id,shot_index,video_version,"
                "author,body,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    cmt_id,
                    item_id,
                    shot["id"],
                    shot_index,
                    int(shot.get("video_version") or 0),
                    caller,
                    text,
                    t,
                ),
            )
        row = self.store.query_one("SELECT * FROM shot_comments WHERE id=?", (cmt_id,))
        return {
            "comment": self._parse_comment(row),
            "shot": self.get_shot(item_id, shot_index, caller=caller),
        }

    def list_comments(
        self,
        item_id: str,
        shot_index: int,
        caller: str | None = None,
        privileged: bool = False,
    ) -> dict[str, Any]:
        """Thread comment của 1 shot, áp cùng luật visibility như _format_shot."""
        self._require_item(item_id)
        shot = self._get_board_shot(item_id, shot_index)
        v_hash = shot.get("video_hash")
        reviews: list[dict] = []
        if v_hash:
            rows = self.store.query(
                "SELECT * FROM shot_reviews "
                "WHERE item_id=? AND shot_index=? AND video_hash=? AND stale=0 "
                "ORDER BY created_at ASC",
                (item_id, shot_index, v_hash),
            )
            reviews = [self._parse_review(r) for r in rows]
        info = self._comments_block(shot, caller, privileged, reviews)
        info.update({"item_id": item_id, "shot_index": shot_index})
        return info

    # -- C-6: grandfathered migration, summary & assemble gate ----------------

    def migrate_grandfathered(self, board_enabled_at: float | None = None) -> dict[str, Any]:
        """Idempotent C-6 migration: mark pre-board shots with existing video as grandfathered=1."""
        cutoff = (
            float(board_enabled_at)
            if board_enabled_at is not None
            else float(os.environ.get("VIDEO_BOARD_ENABLED_AT") or 0.0)
        )
        already = self.store.query_one(
            "SELECT COUNT(*) AS c FROM shots WHERE COALESCE(grandfathered, 0) = 1"
        )
        already_count = int((already or {}).get("c") or 0)
        if cutoff <= 0:
            return {"migrated": 0, "already_grandfathered": already_count, "cutoff": cutoff}

        candidates = self.store.query(
            "SELECT id, item_id, index_num FROM shots "
            "WHERE COALESCE(grandfathered, 0) = 0 "
            "AND status != 'superseded' "
            "AND created_at IS NOT NULL AND created_at < ? "
            "AND ("
            "  video_hash IS NOT NULL "
            "  OR COALESCE(agent_checked, 0) = 1 "
            "  OR COALESCE(generated_duration_sec, 0) > 0"
            ")",
            (cutoff,),
        )
        t = now()
        if candidates:
            with self.store.transaction() as conn:
                for row in candidates:
                    conn.execute(
                        "UPDATE shots SET grandfathered=1, "
                        "board_status=COALESCE(board_status, 'generated'), "
                        "updated_at=? WHERE id=? AND COALESCE(grandfathered, 0) = 0",
                        (t, row["id"]),
                    )
                    conn.execute(
                        "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            new_id("evt"),
                            row["item_id"],
                            row["id"],
                            "shot.grandfathered",
                            "migration",
                            json.dumps({"shot_index": row["index_num"], "cutoff": cutoff}),
                            t,
                        ),
                    )
        return {
            "migrated": len(candidates),
            "already_grandfathered": already_count,
            "total_grandfathered": already_count + len(candidates),
            "cutoff": cutoff,
        }

    def board_summary(self, item_id: str) -> dict[str, Any]:
        """Header counts for C-6: green vs grandfathered vs red."""
        self._require_item(item_id)
        shots = self.store.query(
            "SELECT board_status, video_hash, grandfathered FROM shots "
            "WHERE item_id=? AND status != 'superseded'",
            (item_id,),
        )
        green = 0
        grandfathered = 0
        red = 0
        generated = 0
        empty = 0
        for s in shots:
            bs = self._board_status(s)
            gf = bool(s.get("grandfathered"))
            if bs == "green":
                green += 1
            elif bs == "red":
                red += 1
            elif gf:
                grandfathered += 1
            elif bs == "generated":
                generated += 1
            else:
                empty += 1
        return {
            "item_id": item_id,
            "total_shots": len(shots),
            "green": green,
            "grandfathered": grandfathered,
            "red": red,
            "generated": generated,
            "empty": empty,
            "header_text": f"Xanh {green} · Cũ (chưa chấm) {grandfathered} · Đỏ {red}",
        }

    def check_assemble_gate(self, item_id: str) -> None:
        """C-6 assemble gate: grandfathered=true passes; new board videos must be green."""
        shots = self.store.query(
            "SELECT index_num, board_status, video_hash, grandfathered FROM shots "
            "WHERE item_id=? AND status != 'superseded'",
            (item_id,),
        )
        red_shots = [s["index_num"] for s in shots if self._board_status(s) == "red"]
        if red_shots:
            raise BoardError(
                409,
                f"Job còn shot đỏ chưa xử lý: {red_shots}",
                "assemble_blocked_red",
            )
        unreviewed_new = [
            s["index_num"]
            for s in shots
            if s.get("video_hash")
            and not bool(s.get("grandfathered"))
            and self._board_status(s) != "green"
        ]
        if unreviewed_new:
            raise BoardError(
                409,
                f"Video mới sau khi bật board phải qua 2 review pass (xanh) trước khi assemble: {unreviewed_new}",
                "assemble_blocked_unreviewed",
            )
