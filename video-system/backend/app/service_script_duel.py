"""Script duel — Phase 1: hai agent chốt kịch bản bằng record có hash.

Luồng (owner chốt 2026-11-23):
    author tạo proposal (shots + narration + visual_prompt)
      → server chạy validator máy (duration, shot count, style, nguồn)
      → critic ghi findings có cấu trúc (category/severity/blocking)
      → author sửa bằng version mới (parent_id) — tối đa 2 vòng review
      → author + critic ký `pass` trên CÙNG content_hash ⇒ agreed

Bất biến:
- Agreement cần >=2 chữ ký khác người, trong đó BẮT BUỘC có author.
- Chỉ author, critic được owner chỉ định, hoặc owner mới được ký/finding.
- Còn finding `blocking=1` thì không ký pass được.
- Proposal `agreed` là đóng băng: không ký thêm, không sửa; reopen qua `script_decisions`.
- Tối đa 2 vòng review (proposal valid đã nhận finding/chữ ký); v3 cần owner quyết.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
from typing import Any

from .service_briefs import BriefService
from .store import Store, new_id, now

logger = logging.getLogger("video-system.script_duel")

DEFAULT_WORDS_PER_MINUTE = 150.0
LANGUAGE_WPM = {"en": 150.0, "vi": 160.0}
DURATION_TOLERANCE = 0.10
MAX_ROUNDS = 2

_NUMBER_CLAIM_RE = re.compile(
    r"\b(?:18|19|20)\d{2}\b"                      # năm
    r"|\d+(?:[.,]\d+)?\s?%"                       # phần trăm
    r"|[$€£]\s?\d"                                 # tiền
    r"|\b\d{1,3}(?:[.,]\d{3})+\b"                 # số lớn có phân cách
    r"|\b\d+(?:[.,]\d+)?\s?(?:percent|million|billion|thousand|triệu|tỷ)\b"
)

_FINDING_CATEGORIES = {"fact", "structure", "style", "duration"}
_FINDING_SEVERITIES = {"low", "medium", "high"}


class ScriptDuelError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "script_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def words_per_minute(language: str | None) -> tuple[float, bool]:
    env = os.environ.get("VIDEO_SCRIPT_WPM", "").strip()
    if env:
        try:
            return float(env), True
        except ValueError:
            pass
    lang = (language or "en").lower()
    if lang in LANGUAGE_WPM:
        return LANGUAGE_WPM[lang], True
    return DEFAULT_WORDS_PER_MINUTE, False


def normalize_shots(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, s in enumerate(shots, start=1):
        narration = " ".join(str(s.get("narration") or s.get("text") or "").split())
        visual = " ".join(str(s.get("visual_prompt") or s.get("visual_prompt_clean") or "").split())
        out.append({
            "index": int(s.get("index") or i),
            "shot_type": str(s.get("shot_type") or "scene").strip() or "scene",
            "narration": narration,
            "visual_prompt": visual,
            "on_screen_text": s.get("on_screen_text"),
            "on_screen_type": s.get("on_screen_type"),
            "beat_type": (str(s.get("beat_type")).strip().lower() or None)
            if s.get("beat_type") else None,
            "source_url": (str(s.get("source_url")).strip() or None)
            if s.get("source_url") else None,
        })
    return out


def content_hash(shots: list[dict[str, Any]], script_text: str | None = None) -> str:
    payload = {"shots": shots, "script_text": (script_text or "").strip() or None}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate(brief: dict, shots: list[dict[str, Any]],
             script_text: str | None = None) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    target = float(brief.get("target_duration_sec") or 780.0)
    wpm, known_lang = words_per_minute(brief.get("language"))
    if not known_lang:
        warnings.append({"code": "unknown_language_wpm",
                         "message": f"language={brief.get('language')!r} — dùng WPM mặc định {wpm:.0f}"})

    if str(brief.get("style_lock_version") or "v3") != "v3":
        errors.append({"code": "bad_style_lock", "message": "style_lock_version phải là v3"})

    indexes = [int(s["index"]) for s in shots]
    if len(indexes) != len(set(indexes)):
        errors.append({"code": "duplicate_index", "message": "shot index bị trùng"})
    if indexes != sorted(indexes):
        warnings.append({"code": "unordered_index",
                         "message": "shot index không tăng dần; server vẫn chấp nhận"})

    seen_narration: dict[str, int] = {}
    total_words = 0
    for s in shots:
        idx = int(s["index"])
        narration = s.get("narration") or ""
        words = len(narration.split())
        total_words += words
        if not narration:
            errors.append({"code": "missing_narration", "shot_index": idx,
                           "message": f"shot {idx} thiếu narration"})
        if not s.get("visual_prompt"):
            errors.append({"code": "missing_visual_prompt", "shot_index": idx,
                           "message": f"shot {idx} thiếu visual_prompt"})
        shot_sec = words / wpm * 60.0 if words else 0.0
        if words and shot_sec < 4:
            errors.append({"code": "shot_too_short", "shot_index": idx,
                           "message": f"shot {idx} quá ngắn ({shot_sec:.1f}s)"})
        elif words and shot_sec > 40:
            errors.append({"code": "shot_too_long", "shot_index": idx,
                           "message": f"shot {idx} quá dài ({shot_sec:.1f}s)"})
        elif words and not (10 <= shot_sec <= 22):
            warnings.append({"code": "shot_duration_off", "shot_index": idx,
                             "message": f"shot {idx} ≈ {shot_sec:.1f}s, chuẩn 12–18s"})
        if narration:
            key = narration.lower()
            if key in seen_narration:
                errors.append({"code": "duplicate_narration", "shot_index": idx,
                               "message": f"shot {idx} trùng narration shot {seen_narration[key]}"})
            else:
                seen_narration[key] = idx
        if narration and _NUMBER_CLAIM_RE.search(narration) and not s.get("source_url"):
            errors.append({"code": "missing_source", "shot_index": idx,
                           "message": f"shot {idx} có số liệu/năm nhưng thiếu source_url"})

    est = total_words / wpm * 60.0
    lo, hi = target * (1 - DURATION_TOLERANCE), target * (1 + DURATION_TOLERANCE)
    if est < lo or est > hi:
        errors.append({"code": "duration_out_of_range",
                       "message": f"ước tính {est:.0f}s ngoài khoảng {lo:.0f}–{hi:.0f}s "
                                  f"(target {target:.0f}s, {total_words} từ, {wpm:.0f} wpm)"})

    min_shots = max(1, int(math.floor(target / 22.0)))
    max_shots = max(min_shots, int(math.ceil(target / 10.0)))
    if not (min_shots <= len(shots) <= max_shots):
        errors.append({"code": "shot_count_out_of_range",
                       "message": f"{len(shots)} shot ngoài khoảng {min_shots}–{max_shots} "
                                  f"cho target {target:.0f}s"})

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "shots": len(shots),
            "words": total_words,
            "wpm": wpm,
            "estimated_duration_sec": round(est, 2),
            "target_duration_sec": target,
            "min_shots": min_shots,
            "max_shots": max_shots,
        },
    }


class ScriptDuelService:
    def __init__(self, store: Store, briefs: BriefService) -> None:
        self.store = store
        self.briefs = briefs

    # -- proposals ----------------------------------------------------------

    def create_proposal(self, *, brief_id: str, author: str,
                        shots: list[dict[str, Any]],
                        script_text: str | None = None,
                        parent_id: str | None = None) -> dict:
        brief = self.briefs.require_approved(brief_id)
        if (brief.get("critic") or None) == author:
            raise ScriptDuelError(
                "Critic của brief trùng author — không ai ký đối trọng được. "
                "Owner phải đổi critic trước.", 409, "critic_is_author")
        norm = normalize_shots(shots)
        if not norm:
            raise ScriptDuelError("Proposal không có shot nào.", 422, "empty_shots")
        if parent_id:
            parent = self.get_proposal(parent_id)
            if parent["brief_id"] != brief_id:
                raise ScriptDuelError("parent_id thuộc brief khác.", 409, "parent_mismatch")
            if parent["status"] in {"agreed", "superseded", "stale"}:
                raise ScriptDuelError(
                    f"Không sửa được proposal đang {parent['status']} — tạo version mới từ brief.",
                    409, "parent_locked")

        validation = validate(brief, norm, script_text)
        status = "draft" if validation["ok"] else "invalid"
        h = content_hash(norm, script_text)

        pid = new_id("prp")
        for attempt in (1, 2):
            try:
                with self.store.transaction() as conn:
                    frozen = conn.execute(
                        "SELECT id, version FROM script_proposals WHERE brief_id=? "
                        "AND status='agreed' ORDER BY version DESC LIMIT 1", (brief_id,)
                    ).fetchone()
                    if frozen:
                        raise ScriptDuelError(
                            f"Brief đã chốt ở proposal v{frozen['version']} — sửa kịch bản "
                            f"phải tạo brief/job mới.", 409, "brief_agreed_frozen")
                    rounds = self._review_rounds(conn, brief_id)
                    if rounds >= MAX_ROUNDS:
                        raise ScriptDuelError(
                            f"Đã dùng {rounds}/{MAX_ROUNDS} vòng review — cần owner quyết "
                            f"trước khi tạo version mới.", 409, "round_limit_reached")
                    dup = conn.execute(
                        "SELECT id, version FROM script_proposals WHERE brief_id=? "
                        "AND content_hash=? AND status IN ('draft','invalid','under_review') "
                        "ORDER BY version DESC LIMIT 1", (brief_id, h)).fetchone()
                    if dup:
                        raise ScriptDuelError(
                            f"Nội dung trùng proposal v{dup['version']} ({dup['id']}) — "
                            f"không tạo bản sao.", 409, "duplicate_content")
                    row = conn.execute(
                        "SELECT COALESCE(MAX(version),0) AS v FROM script_proposals "
                        "WHERE brief_id=?", (brief_id,)).fetchone()
                    version = int(row["v"]) + 1
                    ts = now()
                    conn.execute(
                        "INSERT INTO script_proposals (id,brief_id,parent_id,version,author,"
                        "content_json,content_hash,status,validator_json,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, brief_id, parent_id, version, author,
                         json.dumps({"shots": norm, "script_text": script_text},
                                    ensure_ascii=False),
                         h, status, json.dumps(validation, ensure_ascii=False), ts, ts))
                    if parent_id:
                        conn.execute(
                            "UPDATE script_proposals SET status='superseded', updated_at=? "
                            "WHERE id=? AND status IN "
                            "('draft','invalid','under_review','rejected')",
                            (ts, parent_id))
                break
            except sqlite3.IntegrityError:
                if attempt == 2:
                    raise ScriptDuelError(
                        "Hai agent tạo version cùng lúc — thử lại với parent_id mới nhất.",
                        409, "version_conflict")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt == 1:
                    continue
                if "locked" in str(exc).lower():
                    # DB bận quá lâu: trả 503 để agent backoff, KHÔNG 500.
                    raise ScriptDuelError(
                        "Database đang bận — thử lại sau vài giây.",
                        503, "database_busy") from exc
                raise

        self._event(author, "script.proposed",
                    {"brief_id": brief_id, "proposal_id": pid, "version": version,
                     "valid": validation["ok"], "hash": h})
        return self._public(self._row(pid))

    def get_proposal(self, proposal_id: str) -> dict:
        return self._public(self._row(proposal_id))

    def list_proposals(self, brief_id: str) -> list[dict]:
        self.briefs.get(brief_id)
        rows = self.store.query(
            "SELECT * FROM script_proposals WHERE brief_id=? ORDER BY version ASC",
            (brief_id,))
        return [self._public(r) for r in rows]

    def add_finding(self, proposal_id: str, *, reviewer: str, category: str,
                    severity: str, evidence: str | None = None,
                    blocking: bool = False, is_owner: bool = False) -> dict:
        if category not in _FINDING_CATEGORIES:
            raise ScriptDuelError(f"category phải thuộc {sorted(_FINDING_CATEGORIES)}",
                                  422, "bad_category")
        if severity not in _FINDING_SEVERITIES:
            raise ScriptDuelError(f"severity phải thuộc {sorted(_FINDING_SEVERITIES)}",
                                  422, "bad_severity")
        fid = new_id("fnd")
        ts = now()
        with self.store.transaction() as conn:
            proposal = conn.execute(
                "SELECT * FROM script_proposals WHERE id=?", (proposal_id,)).fetchone()
            if not proposal:
                raise ScriptDuelError(f"Proposal không tồn tại: {proposal_id}",
                                      404, "not_found")
            if proposal["status"] in {"agreed", "superseded", "stale"}:
                raise ScriptDuelError(
                    f"Proposal đang {proposal['status']} — không thêm finding.",
                    409, "proposal_locked")
            if reviewer == proposal["author"]:
                raise ScriptDuelError("Không được tự review proposal của mình.",
                                      422, "self_review")
            brief = conn.execute(
                "SELECT critic, status FROM job_briefs WHERE id=?",
                (proposal["brief_id"],)).fetchone()
            if brief and brief["status"] == "cancelled":
                raise ScriptDuelError("Brief đã bị huỷ — không thêm finding.",
                                      409, "brief_cancelled")
            self._require_reviewer(brief, reviewer, is_owner)
            conn.execute(
                "INSERT INTO script_findings (id,proposal_id,reviewer,category,severity,"
                "evidence_json,blocking,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (fid, proposal_id, reviewer, category, severity,
                 json.dumps({"text": evidence} if evidence else {}, ensure_ascii=False),
                 1 if blocking else 0, ts))
            validation = json.loads(proposal["validator_json"] or "{}")
            if proposal["status"] == "draft" and validation.get("ok"):
                conn.execute(
                    "UPDATE script_proposals SET status='under_review', updated_at=? "
                    "WHERE id=? AND status='draft'", (ts, proposal_id))
        self._event(reviewer, "script.finding",
                    {"proposal_id": proposal_id, "category": category,
                     "blocking": bool(blocking)})
        return {"id": fid, "proposal_id": proposal_id, "reviewer": reviewer,
                "category": category, "severity": severity, "blocking": bool(blocking),
                "evidence": evidence, "created_at": ts}

    def list_findings(self, proposal_id: str) -> list[dict]:
        self._row(proposal_id)
        rows = self.store.query(
            "SELECT * FROM script_findings WHERE proposal_id=? ORDER BY created_at ASC",
            (proposal_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["blocking"] = bool(d.get("blocking"))
            try:
                d["evidence"] = json.loads(d.get("evidence_json") or "{}").get("text")
            except (TypeError, ValueError):
                d["evidence"] = None
            out.append(d)
        return out

    # -- signatures ---------------------------------------------------------

    def sign(self, proposal_id: str, *, signer: str, verdict: str,
             note: str | None = None, is_owner: bool = False) -> dict:
        if verdict not in {"pass", "block"}:
            raise ScriptDuelError("verdict phải là pass|block.", 422, "bad_verdict")
        proposal = self._row(proposal_id)

        # Gửi lại y hệt chữ ký cũ = replay idempotent, kể cả khi đã agreed.
        existing = self.store.query_one(
            "SELECT * FROM script_signatures WHERE proposal_id=? AND signer=?",
            (proposal_id, signer))
        if existing and existing["content_hash"] == proposal["content_hash"] \
                and existing["verdict"] == verdict:
            return self._with_agreement(self._public(self._row(proposal_id)))

        validation = json.loads(proposal.get("validator_json") or "{}")
        if not validation.get("ok"):
            raise ScriptDuelError(
                "Proposal chưa qua validator — sửa lỗi trước khi ký.",
                409, "proposal_invalid")

        ts = now()
        agreed_now = False
        with self.store.transaction() as conn:
            cur = conn.execute(
                "SELECT status, content_hash FROM script_proposals WHERE id=?",
                (proposal_id,)).fetchone()
            if cur["status"] in {"superseded", "stale"}:
                raise ScriptDuelError(
                    f"Proposal đang {cur['status']} — không ký.", 409, "proposal_locked")
            if cur["status"] == "agreed":
                raise ScriptDuelError(
                    "Proposal đã agreed — không ký thêm; reopen qua owner decision.",
                    409, "proposal_locked")
            if cur["status"] == "rejected" and verdict == "pass":
                raise ScriptDuelError("Proposal đã bị block — tạo version mới.",
                                      409, "proposal_rejected")
            brief = conn.execute(
                "SELECT critic, status FROM job_briefs WHERE id=?",
                (proposal["brief_id"],)).fetchone()
            if brief and brief["status"] == "cancelled":
                raise ScriptDuelError(
                    "Brief đã bị huỷ — không ký được.", 409, "brief_cancelled")
            self._require_reviewer(brief, signer, is_owner,
                                   author=proposal["author"])
            if verdict == "pass":
                open_blocking = conn.execute(
                    "SELECT COUNT(*) AS n FROM script_findings "
                    "WHERE proposal_id=? AND blocking=1", (proposal_id,)).fetchone()["n"]
                if open_blocking:
                    raise ScriptDuelError(
                        f"Còn {open_blocking} finding blocking chưa xử lý — "
                        f"không ký pass được.", 409, "open_blocking_findings")
            conn.execute(
                "INSERT INTO script_signatures (proposal_id,signer,content_hash,verdict,note,"
                "signed_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(proposal_id,signer) DO UPDATE SET "
                "content_hash=excluded.content_hash, verdict=excluded.verdict, "
                "note=excluded.note, signed_at=excluded.signed_at",
                (proposal_id, signer, proposal["content_hash"], verdict, note, ts))
            if verdict == "block":
                conn.execute(
                    "UPDATE script_proposals SET status='rejected', updated_at=? WHERE id=?",
                    (ts, proposal_id))
            else:
                # Chốt agreement TRONG cùng transaction với chữ ký: crash giữa 2 bước
                # không được để lại trạng thái "2 chữ ký nhưng vẫn under_review".
                passes = {r["signer"] for r in conn.execute(
                    "SELECT signer, verdict FROM script_signatures WHERE proposal_id=?",
                    (proposal_id,)).fetchall() if r["verdict"] == "pass"}
                if len(passes) >= 2 and proposal["author"] in passes:
                    agreed_now = conn.execute(
                        "UPDATE script_proposals SET status='agreed', updated_at=? "
                        "WHERE id=? AND status IN ('draft','under_review')",
                        (ts, proposal_id)).rowcount > 0

        if agreed_now:
            self._event("system", "script.agreed",
                        {"proposal_id": proposal_id, "brief_id": proposal["brief_id"],
                         "hash": proposal["content_hash"]})
        self._event(signer, f"script.signed.{verdict}",
                    {"proposal_id": proposal_id, "hash": proposal["content_hash"]})
        return self._with_agreement(self._public(self._row(proposal_id)))

    def decide(self, brief_id: str, *, decided_by: str, decision: str,
               proposal_id: str, reason: str) -> dict:
        if decision not in {"force_agree", "reject"}:
            raise ScriptDuelError("decision phải là force_agree|reject.", 422, "bad_decision")
        if not (reason or "").strip():
            raise ScriptDuelError("Decision phải có reason.", 422, "missing_reason")
        brief = self.briefs.get(brief_id)
        if brief["status"] == "cancelled":
            raise ScriptDuelError("Brief đã huỷ — không quyết được.", 409, "brief_cancelled")

        ts = now()
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM script_proposals WHERE id=?",
                               (proposal_id,)).fetchone()
            if not row:
                raise ScriptDuelError(f"Proposal không tồn tại: {proposal_id}",
                                      404, "not_found")
            if row["brief_id"] != brief_id:
                raise ScriptDuelError("Proposal thuộc brief khác.", 409, "proposal_mismatch")
            if row["status"] in {"superseded", "stale"}:
                raise ScriptDuelError("Proposal đã bị thay thế — không quyết được.",
                                      409, "proposal_locked")
            if row["status"] == "agreed":
                raise ScriptDuelError(
                    "Proposal đã chốt — không quyết lại được; tạo brief/new job nếu cần.",
                    409, "already_agreed")
            try:
                valid = bool(json.loads(row["validator_json"] or "{}").get("ok"))
            except (TypeError, ValueError):
                valid = False
            if decision == "force_agree" and not valid:
                raise ScriptDuelError(
                    "Proposal chưa qua validator — không force_agree được; "
                    "reject để agent làm lại.", 409, "proposal_invalid")

            h = row["content_hash"]
            if decision == "force_agree":
                conn.execute(
                    "INSERT INTO script_signatures (proposal_id,signer,content_hash,verdict,"
                    "note,signed_at) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(proposal_id,signer) DO UPDATE SET "
                    "content_hash=excluded.content_hash, verdict=excluded.verdict, "
                    "note=excluded.note, signed_at=excluded.signed_at",
                    (proposal_id, decided_by, h, "pass", f"owner_forced: {reason}", ts))
                changed = conn.execute(
                    "UPDATE script_proposals SET status='agreed', updated_at=? "
                    "WHERE id=? AND status NOT IN ('superseded','stale')",
                    (ts, proposal_id)).rowcount
            else:
                changed = conn.execute(
                    "UPDATE script_proposals SET status='rejected', updated_at=? "
                    "WHERE id=? AND status NOT IN ('superseded','stale','agreed')",
                    (ts, proposal_id)).rowcount
            if not changed:
                raise ScriptDuelError("Proposal vừa đổi trạng thái — không quyết được.",
                                      409, "proposal_locked")
            conn.execute(
                "INSERT INTO script_decisions (id,brief_id,proposal_id,decided_by,decision,"
                "reason,content_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (new_id("dec"), brief_id, proposal_id, decided_by, decision,
                 reason.strip(), h, ts))
        self._event(decided_by, f"script.decision.{decision}",
                    {"brief_id": brief_id, "proposal_id": proposal_id,
                     "hash": h, "reason": reason})
        return self._with_agreement(self._public(self._row(proposal_id)))

    def agreement(self, brief_id: str) -> dict:
        self.briefs.get(brief_id)
        proposals = self.list_proposals(brief_id)
        agreed = [p for p in proposals if p["status"] == "agreed"]
        latest = proposals[-1] if proposals else None
        rounds = self._rounds_used(brief_id)
        if agreed:
            decision = self.store.query_one(
                "SELECT decision, decided_by, reason, created_at FROM script_decisions "
                "WHERE brief_id=? AND proposal_id=? ORDER BY created_at DESC LIMIT 1",
                (brief_id, agreed[-1]["id"]))
        else:
            decision = self.store.query_one(
                "SELECT decision, decided_by, reason, created_at FROM script_decisions "
                "WHERE brief_id=? ORDER BY created_at DESC LIMIT 1", (brief_id,))
        return {
            "brief_id": brief_id,
            "status": "agreed" if agreed else (latest["status"] if latest else "none"),
            "agreement": agreed[-1] if agreed else None,
            "latest": latest,
            "decision": decision,
            "rounds_used": rounds,
            "max_rounds": MAX_ROUNDS,
            # Phase 2 (POST /jobs/from-agreement) chưa mở: agreed là terminal,
            # owner phải build job thủ công.
            "phase2_open": False,
            "next_action": self._next_action(agreed, latest, rounds),
        }

    # -- internals ----------------------------------------------------------

    def _round_since(self, conn: sqlite3.Connection, brief_id: str) -> float:
        """Mốc reset quota: lần owner reject gần nhất.

        Nếu không reset, owner reject ở vòng 2 thì author vẫn dính
        `round_limit_reached` -> không tạo version mới được, chỉ còn cách huỷ brief
        và mất toàn bộ audit chain.
        """
        row = conn.execute(
            "SELECT COALESCE(MAX(created_at),0) AS ts FROM script_decisions "
            "WHERE brief_id=? AND decision='reject'", (brief_id,)).fetchone()
        return float(row["ts"] or 0.0)

    def _review_rounds(self, conn: sqlite3.Connection, brief_id: str) -> int:
        since = self._round_since(conn, brief_id)
        rows = conn.execute(
            "SELECT p.id, p.validator_json, p.status, "
            "(SELECT COUNT(*) FROM script_findings f WHERE f.proposal_id=p.id) AS findings, "
            "(SELECT COUNT(*) FROM script_signatures s WHERE s.proposal_id=p.id) AS sigs "
            "FROM script_proposals p WHERE p.brief_id=? AND p.created_at>=?",
            (brief_id, since)).fetchall()
        return self._count_rounds(rows)

    def _rounds_used(self, brief_id: str) -> int:
        dec = self.store.query_one(
            "SELECT COALESCE(MAX(created_at),0) AS ts FROM script_decisions "
            "WHERE brief_id=? AND decision='reject'", (brief_id,))
        since = float(dec["ts"] or 0.0) if dec else 0.0
        rows = self.store.query(
            "SELECT p.id, p.validator_json, p.status, "
            "(SELECT COUNT(*) FROM script_findings f WHERE f.proposal_id=p.id) AS findings, "
            "(SELECT COUNT(*) FROM script_signatures s WHERE s.proposal_id=p.id) AS sigs "
            "FROM script_proposals p WHERE p.brief_id=? AND p.created_at>=?",
            (brief_id, since))
        return self._count_rounds(rows)

    @staticmethod
    def _count_rounds(rows) -> int:
        """1 vòng = 1 proposal QUA VALIDATOR đã thực sự được review (finding/chữ ký).

        Proposal invalid không tính vòng — nếu tính, agent sửa lỗi validator vài lần là
        hết quota và cả hai bên kẹt không lối ra.
        """
        n = 0
        for r in rows:
            try:
                valid = bool(json.loads(r["validator_json"] or "{}").get("ok"))
            except (TypeError, ValueError):
                valid = False
            if valid and (r["findings"] or r["sigs"]):
                n += 1
        return n

    def _require_reviewer(self, brief_row, actor: str, is_owner: bool,
                          author: str | None = None) -> None:
        if author is not None and actor == author:
            return
        if is_owner:
            return
        critic = (brief_row["critic"] if brief_row else None) or None
        if not critic:
            raise ScriptDuelError(
                "Brief chưa được owner chỉ định critic — không ký/finding được.",
                409, "critic_not_assigned")
        if actor != critic:
            raise ScriptDuelError(
                f"Chỉ critic {critic} (hoặc owner) được review brief này.",
                403, "not_critic")

    def _next_action(self, agreed: list[dict], latest: dict | None,
                     rounds_used: int = 0) -> str:
        if agreed:
            return "build_job"
        if not latest:
            return "create_proposal"
        if not (latest.get("validation") or {}).get("ok"):
            return "fix_proposal"
        if rounds_used >= MAX_ROUNDS:
            # Hết quota review: agent không được tự tạo version nữa, phải chờ owner.
            return "owner_required"
        if latest["status"] == "rejected":
            return "create_new_version"
        return "review_or_sign"

    def _row(self, proposal_id: str) -> dict:
        row = self.store.query_one("SELECT * FROM script_proposals WHERE id=?", (proposal_id,))
        if not row:
            raise ScriptDuelError(f"Proposal không tồn tại: {proposal_id}",
                                  404, "not_found")
        return dict(row)

    def _public(self, row: dict) -> dict:
        d = dict(row)
        try:
            content = json.loads(d.pop("content_json") or "{}")
        except (TypeError, ValueError):
            content = {}
        d["shots"] = content.get("shots") or []
        d["script_text"] = content.get("script_text")
        try:
            d["validation"] = json.loads(d.get("validator_json") or "{}")
        except (TypeError, ValueError):
            d["validation"] = {}
        d.pop("validator_json", None)
        return d

    def _with_agreement(self, proposal: dict) -> dict:
        sigs = self.store.query(
            "SELECT signer, verdict, content_hash, signed_at FROM script_signatures "
            "WHERE proposal_id=? ORDER BY signed_at ASC", (proposal["id"],))
        proposal["signatures"] = [dict(s) for s in sigs]
        proposal["agreed"] = proposal.get("status") == "agreed"
        return proposal

    def _event(self, actor: str, type_: str, payload: dict) -> None:
        try:
            self.store.execute(
                "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
                "VALUES (?,?,?,?,?,?,?)",
                (new_id("evt"), None, None, type_, actor,
                 json.dumps(payload, ensure_ascii=False), now()))
        except Exception as exc:  # noqa: BLE001
            logger.warning("script event log failed: %s", exc)
