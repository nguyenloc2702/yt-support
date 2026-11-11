"""Item lifecycle: create, list, detail, gates, soft delete, transitions."""
from __future__ import annotations

import json

from . import domain, new_id, now
from .store import Store


class ItemService:
    def __init__(self, store: Store) -> None:
        self.store = store

    # -- CRUD ---------------------------------------------------------------
    def create(self, title: str, type_: str, language: str | None,
               style_lock_version: str) -> dict:
        if type_ not in {"distill", "remake", "prebuilt_import", "upload",
                         "script_en", "youtube_en", "todo"}:
            raise ValueError(f"invalid item type: {type_}")
        item_id = new_id("itm")
        t = now()
        self.store.execute(
            "INSERT INTO items (id,title,type,status,language,style_lock_version,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (item_id, title, type_, domain.ItemStatus.DRAFT.value, language,
             style_lock_version, t, t))
        self.event(item_id, None, "item.created", "api", {"title": title, "type": type_})
        return self.get(item_id)

    def get(self, item_id: str) -> dict | None:
        return self.store.query_one("SELECT * FROM items WHERE id=?", (item_id,))

    def list(self, status: str | None = None, q: str | None = None,
             page: int = 1, size: int = 50) -> list[dict]:
        sql = "SELECT * FROM items WHERE status != 'deleted'"
        params: list = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if q:
            sql += " AND title LIKE ?"
            params.append(f"%{q}%")
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params += [size, (page - 1) * size]
        return self.store.query(sql, tuple(params))

    # -- status -------------------------------------------------------------
    def transition(self, item_id: str, to: str, actor: str = "api",
                   reason: str | None = None) -> dict:
        item = self.get(item_id)
        if not item:
            raise KeyError(item_id)
        domain.assert_item_transition(item["status"], to)
        t = now()
        changed = self.store.cas_update(
            "UPDATE items SET status=?, updated_at=? WHERE id=? AND status=?",
            (to, t, item_id, item["status"]))
        if not changed:
            raise domain.TransitionError(f"concurrent transition lost for {item_id}")
        self.event(item_id, None, "item.status", actor,
                   {"from": item["status"], "to": to, "reason": reason})
        return self.get(item_id)

    def recompute_from_shots(self, item_id: str) -> dict:
        """Đồng bộ held_review/generating + đếm held sau khi shot đổi trạng thái."""
        item = self.get(item_id)
        if not item or item["status"] not in {"generating", "held_review"}:
            return item
        held = self.store.query_one(
            "SELECT COUNT(*) c FROM shots WHERE item_id=? AND status='held'",
            (item_id,))["c"]
        # held_count phải cập nhật tại đây, không chỉ khi batch approve
        # (bug: UI hiện '—' dù item đang held_review)
        if held != item.get("held_count"):
            self.store.execute("UPDATE items SET held_count=? WHERE id=?",
                               (held, item_id))
        target = domain.ItemStatus.HELD_REVIEW.value if held else domain.ItemStatus.GENERATING.value
        if item["status"] != target:
            return self.transition(item_id, target, actor="worker")
        return self.get(item_id)

    # -- gates --------------------------------------------------------------
    def approve(self, item_id: str, gate: str, decision: str, reason: str,
                approver: str, version: int | None = None) -> dict:
        if gate not in {"script", "audio", "character", "keyframe_batch", "final"}:
            raise ValueError(f"unknown gate: {gate}")
        if decision not in {"approved", "rejected", "override"}:
            raise ValueError(f"unknown decision: {decision}")
        if decision == "override" and gate != "script":
            raise ValueError("override decision only valid on script gate (unverified flags)")
        item = self.get(item_id)
        if not item:
            raise KeyError(item_id)
        self.store.execute(
            "INSERT INTO approvals (id,item_id,gate,version,decision,reason,approver,"
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            (new_id("apr"), item_id, gate, version, decision, reason, approver, now()))
        if gate == "character" and decision == "approved":
            # approving the character gate approves the latest sheet per name
            self.store.execute(
                "UPDATE character_sheets SET status='approved', approved_by=?, "
                "approved_at=? WHERE item_id=? AND status='draft'", (approver, now(), item_id))
        self.event(item_id, None, "gate.decision", approver,
                   {"gate": gate, "decision": decision, "reason": reason,
                    "version": version})
        return self.gates(item_id)

    def gates(self, item_id: str) -> dict:
        rows = self.store.query(
            "SELECT gate, decision, version, reason, approver, created_at "
            "FROM approvals WHERE item_id=? ORDER BY created_at", (item_id,))
        latest: dict[str, dict] = {}
        for r in rows:
            latest[r["gate"]] = r
        return latest

    def approved(self, item_id: str, gate: str) -> bool:
        return self.gates(item_id).get(gate, {}).get("decision") == "approved"

    # -- delete (§7.6) --------------------------------------------------------
    def soft_delete(self, item_id: str, actor: str = "api") -> dict:
        item = self.get(item_id)
        if not item:
            raise KeyError(item_id)
        domain.assert_item_transition(item["status"], domain.ItemStatus.DELETED.value)
        # Fail-closed: refuse while any job-like activity is in flight.
        busy = self.store.query_one(
            "SELECT COUNT(*) c FROM shots WHERE item_id=? AND status IN "
            "('generating','retry_later','generated')", (item_id,))["c"]
        if busy:
            raise domain.TransitionError(
                f"cannot delete: {busy} shot(s) in flight")
        self.transition(item_id, domain.ItemStatus.DELETED.value, actor=actor,
                        reason="soft delete; audit trail retained")
        return self.get(item_id)

    # -- events ---------------------------------------------------------------
    def event(self, item_id: str, shot_id: str | None, type_: str, actor: str,
              payload: dict) -> str:
        eid = new_id("evt")
        self.store.execute(
            "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
            "VALUES (?,?,?,?,?,?,?)",
            (eid, item_id, shot_id, type_, actor, json.dumps(payload, ensure_ascii=False),
             now()))
        return eid

    def events(self, item_id: str, limit: int = 200) -> list[dict]:
        return self.store.query(
            "SELECT * FROM events WHERE item_id=? ORDER BY at DESC LIMIT ?",
            (item_id, limit))
