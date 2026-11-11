"""Batches + cumulative append-only assembly (plan §4.B.6, §4.C).

Append rule (verbatim): contiguous prefix only — starting at last appended
index + 1, extend while shots are qc_passed; the first non-qc_passed shot
(including HELD) stops the run and shots after it are NEVER appended.
"""
from __future__ import annotations

import hashlib
import json

from . import domain, new_id, now
from .config import RENDER_SPEC
from .service_items import ItemService
from .service_shots import ShotService
from .store import Store

MAX_BATCH = RENDER_SPEC["limits"]["batch_max_shots"]


class BatchService:
    def __init__(self, store: Store, items: ItemService, shots: ShotService) -> None:
        self.store = store
        self.items = items
        self.shots = shots

    def create(self, item_id: str, size: int = MAX_BATCH) -> dict:
        item = self.items.get(item_id)
        if not item:
            raise KeyError(item_id)
        if item["status"] not in {domain.ItemStatus.AWAITING_REVIEW.value,
                                  domain.ItemStatus.GENERATING.value,
                                  domain.ItemStatus.HELD_REVIEW.value}:
            raise domain.TransitionError(
                f"batch creation requires awaiting_review/generating, got {item['status']}")
        rows = self.shots.list(item_id)
        next_expected = self._next_append_index(item_id)
        # Recovery for the contiguous-prefix deadlock (audit P0): a shot that
        # already passed QC but missed its batch window (its predecessor was
        # held at approval time) is batched again so it can be appended.
        candidates = [
            r for r in rows
            if r["status"] == domain.ShotStatus.PENDING.value
            or (r["status"] == domain.ShotStatus.QC_PASSED.value
                and r["index_num"] == next_expected)
        ]
        if not candidates:
            raise domain.TransitionError("no pending shots left to batch")
        chosen = candidates[:min(size, MAX_BATCH)]
        batch_id = new_id("bat")
        self.store.execute(
            "INSERT INTO batches (id,item_id,shot_indexes,status,created_at) "
            "VALUES (?,?,?,?,?)",
            (batch_id, item_id, json.dumps([c["index_num"] for c in chosen]),
             "open", now()))
        for c in chosen:
            # only PENDING shots enter the keyframe pipeline; a recovered
            # qc_passed shot keeps its status (it already passed QC)
            if c["status"] == domain.ShotStatus.PENDING.value:
                self.shots.transition(c["id"], domain.ShotStatus.KEYFRAME_PENDING.value,
                                      actor="api")
        if item["status"] == domain.ItemStatus.AWAITING_REVIEW.value:
            self.items.transition(item_id, domain.ItemStatus.GENERATING.value,
                                  actor="api", reason="first batch created")
        return self.store.query_one("SELECT * FROM batches WHERE id=?", (batch_id,))

    def get(self, batch_id: str) -> dict | None:
        return self.store.query_one("SELECT * FROM batches WHERE id=?", (batch_id,))

    def approve(self, batch_id: str, actor: str = "api",
                reason: str = "") -> dict:
        """Approve batch → append contiguous qc_passed prefix (§4.B.6/§4.C.1)."""
        batch = self.get(batch_id)
        if not batch:
            raise KeyError(batch_id)
        if batch["status"] != "open":
            raise domain.TransitionError("batch already closed")
        item_id = batch["item_id"]
        indexes = json.loads(batch["shot_indexes"])
        rows = {r["index_num"]: r for r in self.shots.list(item_id)}

        # Rule: cannot jump over a held/unresolved shot.
        next_expected = self._next_append_index(item_id)
        ordered = sorted(indexes)
        if ordered[0] != next_expected:
            raise domain.TransitionError(
                f"cannot append batch starting at {ordered[0]}: contiguous prefix "
                f"requires index {next_expected} (a held/unresolved shot blocks)")
        appended, stopped_at = [], None
        for idx in ordered:
            shot = rows.get(idx)
            if shot is None:
                stopped_at = idx
                break
            if shot["status"] != domain.ShotStatus.QC_PASSED.value:
                stopped_at = idx
                break
            # render_engine enforcement (§2.2/§1.4 — no silent downgrade):
            engine = shot.get("render_engine")
            if engine not in domain.ALLOWED_DONE_ENGINES:
                raise domain.TransitionError(
                    f"shot {idx} render_engine={engine!r} not allowed on append")
            if engine == "ken_burns_downgrade" and not shot.get("downgrade_approved"):
                raise domain.TransitionError(
                    f"shot {idx} ken_burns_downgrade without downgrade_approved")
            # manifest binding must be real (audit P1: never hash a null video)
            asset = self.store.query_one(
                "SELECT sha256 FROM assets WHERE id=?", (shot["video_asset_id"],))
            if not asset or not asset["sha256"]:
                raise domain.TransitionError(
                    f"shot {idx} video asset has no sha256 — manifest would be unbound")
            self.shots.transition(shot["id"], domain.ShotStatus.APPENDED.value,
                                  actor=actor)
            appended.append(shot)

        if not appended:
            # keep batch open — nothing was appended, approving would burn it
            raise domain.TransitionError(
                f"nothing appended (first shot at {stopped_at} not qc_passed); "
                "batch stays open")

        self.store.execute(
            "UPDATE batches SET status='approved', approved_at=? WHERE id=?",
            (now(), batch_id))
        self.items.approve(item_id, "keyframe_batch", "approved",
                           reason or f"batch {batch_id} approved", actor,
                           version=None)
        manifest = self.rebuild_cumulative(item_id, appended)
        self.items.event(item_id, None, "batch.approved", actor,
                         {"batch_id": batch_id, "appended": [s["index_num"] for s in appended],
                          "stopped_at": stopped_at, "manifest_hash": manifest["hash"]})
        return {"batch_id": batch_id, "appended": [s["index_num"] for s in appended],
                "stopped_at": stopped_at, **manifest}

    def _next_append_index(self, item_id: str) -> int:
        row = self.store.query_one(
            "SELECT MAX(index_num) m FROM shots WHERE item_id=? AND status='appended'",
            (item_id,))
        return (row["m"] or 0) + 1

    # -- cumulative manifest (append-only, §4.C.2) -----------------------------
    def rebuild_cumulative(self, item_id: str, just_appended: list[dict]) -> dict:
        rows = self.store.query(
            "SELECT s.index_num, s.keyframe_hash, s.audio_start_sec, s.audio_end_sec, "
            "a.sha256 AS video_sha256, s.render_engine "
            "FROM shots s LEFT JOIN assets a ON a.id = s.video_asset_id "
            "WHERE s.item_id=? AND s.status='appended' ORDER BY s.index_num", (item_id,))
        audio = self.store.query_one(
            "SELECT hash FROM audios WHERE item_id=? AND kind='master' "
            "ORDER BY version DESC LIMIT 1", (item_id,))
        item = self.items.get(item_id)
        payload = {
            "shots": [
                {"index": r["index_num"], "keyframe_hash": r["keyframe_hash"],
                 "video_sha256": r["video_sha256"],
                 "audio_start_sec": round(r["audio_start_sec"], 3),
                 "audio_end_sec": round(r["audio_end_sec"], 3),
                 "render_engine": r["render_engine"]}
                for r in rows],
            "audio_hash": audio["hash"] if audio else None,
            "style_lock_version": item["style_lock_version"] if item else None,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
        manifest_hash = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        duration = sum(r["audio_end_sec"] - r["audio_start_sec"] for r in rows)
        self.store.execute(
            "UPDATE items SET appended_count=?, cumulative_duration_sec=?, "
            "cumulative_manifest_hash=?, updated_at=? WHERE id=?",
            (len(rows), round(duration, 3), manifest_hash, now(), item_id))
        held = self.store.query_one(
            "SELECT COUNT(*) c FROM shots WHERE item_id=? AND status='held'",
            (item_id,))["c"]
        self.store.execute("UPDATE items SET held_count=? WHERE id=?",
                           (held, item_id))
        return {"hash": manifest_hash, "appended_count": len(rows),
                "duration_sec": round(duration, 3)}

    def cumulative(self, item_id: str) -> dict:
        item = self.items.get(item_id)
        if not item:
            raise KeyError(item_id)
        rows = self.store.query(
            "SELECT index_num, status, audio_start_sec, audio_end_sec, held_reason "
            "FROM shots WHERE item_id=? AND status IN ('appended','held','qc_passed') "
            "ORDER BY index_num", (item_id,))
        return {"manifest_hash": item["cumulative_manifest_hash"],
                "appended_count": item["appended_count"],
                "duration_sec": item["cumulative_duration_sec"],
                "total_shots": item["total_shots"],
                "segments": rows}
