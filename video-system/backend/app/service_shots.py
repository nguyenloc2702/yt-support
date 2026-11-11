"""Shot transitions (Appendix A), retry-from-held, generation worker step."""
from __future__ import annotations

import json
from pathlib import Path

from . import domain, new_id, now
from .providers import AdapterNotConfigured, registry
from .service_import import sha256_file
from .service_items import ItemService
from .service_qc import run_keyframe_qc, run_video_qc
from .store import Store


class ShotService:
    def __init__(self, store: Store, items: ItemService) -> None:
        self.store = store
        self.items = items

    # -- queries -------------------------------------------------------------
    def get(self, shot_id: str) -> dict | None:
        return self.store.query_one("SELECT * FROM shots WHERE id=?", (shot_id,))

    def list(self, item_id: str, script_version: int | None = None,
             status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM shots WHERE item_id=?"
        params: list = [item_id]
        if script_version:
            sql += " AND script_version=?"
            params.append(script_version)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY index_num"
        return self.store.query(sql, tuple(params))

    # -- transitions ----------------------------------------------------------
    # valid held_reason groups per SOURCE status (audit fix: no reason from
    # the wrong group — human reasons only apply at human review stages)
    _HOLD_GROUP_RULES = {
        "keyframe_pending": {"keyframe", "workflow"},
        "keyframe_approved": {"keyframe"},
        "generating": {"video", "keyframe"},
        "generated": {"video", "human", "workflow"},
        "qc_passed": {"video", "human", "workflow"},
    }

    def transition(self, shot_id: str, to: str, actor: str = "worker",
                   held_reason: str | None = None, held_detail: str | None = None,
                   extra_set: str = "", extra_params: tuple = ()) -> dict:
        shot = self.get(shot_id)
        if not shot:
            raise KeyError(shot_id)
        domain.assert_shot_transition(shot["status"], to)
        if to == domain.ShotStatus.HELD.value:
            if held_reason is None:
                raise ValueError("held transition requires held_reason")
            hr = domain.HeldReason(held_reason)
            allowed = self._HOLD_GROUP_RULES.get(shot["status"], set())
            if hr.group not in allowed:
                raise domain.TransitionError(
                    f"reason {held_reason} ({hr.group}) invalid when held from "
                    f"{shot['status']}; allowed groups: {sorted(allowed)}")
        sets = "status=?, updated_at=?"
        params: list = [to, now()]
        if to == domain.ShotStatus.HELD.value:
            sets += ", held_reason=?, held_detail=?"
            params += [held_reason, held_detail]
        if extra_set:
            sets += ", " + extra_set
            params += list(extra_params)
        sql = (f"UPDATE shots SET {sets} WHERE id=? AND status=?")
        params += [shot_id, shot["status"]]
        if not self.store.cas_update(sql, tuple(params)):
            raise domain.TransitionError(f"concurrent transition lost for {shot_id}")
        self.items.event(shot["item_id"], shot_id, "shot.status", actor,
                         {"from": shot["status"], "to": to,
                          "held_reason": held_reason})
        if to == "held" or (shot["status"] == "held" and to != "held"):
            # entering OR leaving held must re-sync held_review (audit P2-3)
            self.items.recompute_from_shots(shot["item_id"])
        return self.get(shot_id)

    # -- keyframe step ----------------------------------------------------------
    def approve_keyframe(self, shot_id: str, actor: str = "worker") -> dict:
        shot = self.get(shot_id)
        if shot["status"] != domain.ShotStatus.KEYFRAME_PENDING.value:
            raise domain.TransitionError("keyframe approval requires keyframe_pending")
        # automatic keyframe QC when a real keyframe asset exists (§4.B.3);
        # override by a human reviewer remains possible via admin decision
        if shot.get("keyframe_asset_id"):
            asset = self.store.query_one("SELECT storage_key FROM assets WHERE id=?",
                                         (shot["keyframe_asset_id"],))
            if asset:
                res = run_keyframe_qc(asset["storage_key"], shot)
                if not res.ok():
                    return self.transition(
                        shot_id, domain.ShotStatus.HELD.value, actor=actor,
                        held_reason=res.reason.value, held_detail=res.detail)
        return self.transition(shot_id, domain.ShotStatus.KEYFRAME_APPROVED.value,
                               actor=actor)

    # -- generation step ----------------------------------------------------------
    def generate(self, shot_id: str, actor: str = "worker") -> dict:
        shot = self.get(shot_id)
        if shot["status"] == domain.ShotStatus.RETRY_LATER.value:
            shot = self.transition(shot_id, domain.ShotStatus.GENERATING.value,
                                   actor=actor, extra_set="attempts=attempts+1")
        elif shot["status"] == domain.ShotStatus.GENERATING.value:
            # fencing (minimal, §7.5): a shot already producing with a video
            # asset attached cannot be re-produced by a second worker
            if shot.get("video_asset_id"):
                raise domain.TransitionError(
                    "shot already producing with an attached asset (fencing)")
        else:
            if shot["status"] != domain.ShotStatus.KEYFRAME_APPROVED.value:
                raise domain.TransitionError(
                    "generation requires keyframe_approved (no jump over QC)")
            shot = self.transition(shot_id, domain.ShotStatus.GENERATING.value,
                                   actor=actor, extra_set="attempts=attempts+1")
        # CHARACTER GATE (plan §4.B.2, P0): a shot declaring character_ids
        # requires an APPROVED character sheet reference image, used as
        # image-to-image input. Missing → held REF_IMAGE_MISSING (fail-closed).
        char_ids = json.loads(shot.get("character_ids") or "[]")
        if char_ids:
            refs = []
            for name in char_ids:
                sheet = self.store.query_one(
                    "SELECT * FROM character_sheets WHERE item_id=? AND name=? "
                    "AND status='approved' ORDER BY version DESC LIMIT 1",
                    (shot["item_id"], name))
                if not sheet or not sheet["reference_asset_id"]:
                    return self.transition(
                        shot_id, domain.ShotStatus.HELD.value, actor=actor,
                        held_reason=domain.HeldReason.REF_IMAGE_MISSING.value,
                        held_detail=f"no approved reference image for '{name}'")
                asset = self.store.query_one(
                    "SELECT storage_key FROM assets WHERE id=?",
                    (sheet["reference_asset_id"],))
                refs.append(asset["storage_key"] if asset else "")
            self._last_ref_images = refs
        if not (shot.get("keyframe_asset_id") or shot.get("keyframe_hash")):
            return self.transition(
                shot_id, domain.ShotStatus.HELD.value, actor=actor,
                held_reason=domain.HeldReason.REF_IMAGE_MISSING.value,
                held_detail="shot has no keyframe asset — keyframe-first is mandatory")
        return self._produce(shot_id, actor)

    def _produce(self, shot_id: str, actor: str) -> dict:
        """Run the generation adapter + record assets. Caller guarantees status."""
        gen = None
        try:
            gen = registry.require_generation()
        except AdapterNotConfigured:
            return self.transition(
                shot_id, domain.ShotStatus.HELD.value, actor=actor,
                held_reason=domain.HeldReason.PROVIDER_FATAL.value,
                held_detail="generation adapter not configured")
        shot = self.get(shot_id)
        try:
            video_path = gen.animate(
                shot.get("keyframe_asset_id") or shot.get("keyframe_hash") or "",
                shot["visual_prompt"],
                float(shot["audio_end_sec"]) - float(shot["audio_start_sec"]))
        except AdapterNotConfigured:
            raise
        # sha256 binds the manifest to the real file (audit P1: no null binding)
        video_sha = None
        p = Path(video_path)
        if p.exists():
            video_sha = sha256_file(p)
        asset_id = new_id("ast")
        self.store.execute(
            "INSERT INTO assets (id,item_id,kind,storage_key,sha256,"
            "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?)",
            (asset_id, shot["item_id"], "video",
             f"items/{shot['item_id']}/shots/{shot['index_num']:04d}/video.mp4",
             video_sha, getattr(gen, "engine_name", "veo"), now()))
        # CAS: only the worker holding status=generating may attach/replace
        # the asset (fencing against /generate while producing is enforced at
        # the generate() entry; retry legitimately replaces the old asset)
        changed = self.store.cas_update(
            "UPDATE shots SET video_asset_id=?, render_engine=?, seed=? "
            "WHERE id=? AND status='generating'",
            (asset_id, getattr(gen, "engine_name", "veo"),
             getattr(gen, "last_seed", None), shot_id))
        if not changed:
            raise domain.TransitionError(
                "lost generation race — another worker attached the asset")
        return self.transition(shot_id, domain.ShotStatus.GENERATED.value, actor=actor)

    # -- QC step -----------------------------------------------------------
    def run_qc(self, shot_id: str, actor: str = "worker") -> dict:
        shot = self.get(shot_id)
        if shot["status"] != domain.ShotStatus.GENERATED.value:
            raise domain.TransitionError("QC requires generated")
        if not shot["video_asset_id"]:
            raise ValueError("shot has no video asset")
        asset = self.store.query_one("SELECT * FROM assets WHERE id=?",
                                     (shot["video_asset_id"],))
        res = run_video_qc(asset["storage_key"], shot, {})
        if res.ok():
            return self.transition(shot_id, domain.ShotStatus.QC_PASSED.value,
                                   actor=actor)
        return self.transition(shot_id, domain.ShotStatus.HELD.value, actor=actor,
                               held_reason=res.reason.value, held_detail=res.detail)

    # -- retry from held (A12/A13) ------------------------------------------
    def retry_held(self, shot_id: str, actor: str = "api") -> dict:
        shot = self.get(shot_id)
        if shot["status"] != domain.ShotStatus.HELD.value:
            raise domain.TransitionError("retry requires held")
        reason = shot["held_reason"] or ""
        group = domain.HeldReason(reason).group if reason else "video"
        if group in {"human", "workflow"}:
            # human decisions are resolved via batch review/append, not retry
            raise domain.TransitionError(
                f"held_reason {reason} is resolved by human review, not retry")
        if group == "keyframe":
            return self.transition(shot_id, domain.ShotStatus.KEYFRAME_PENDING.value,
                                   actor=actor, extra_set="attempts=0",
                                   extra_params=())
        # video group: held → generating (A13) then the worker produces again
        self.transition(shot_id, domain.ShotStatus.GENERATING.value, actor=actor,
                        extra_set="attempts=0")
        return self._produce(shot_id, actor)

    def hold_manual(self, shot_id: str, reason: str, detail: str | None,
                    actor: str = "api") -> dict:
        return self.transition(shot_id, domain.ShotStatus.HELD.value, actor=actor,
                               held_reason=reason, held_detail=detail)

    def retry_keyframe_gen(self, shot_id: str, actor: str = "api") -> dict:
        """Explicit 'gen lại keyframe' — ONLY via this endpoint, never automatic."""
        return self.transition(shot_id, domain.ShotStatus.KEYFRAME_PENDING.value,
                               actor=actor)
