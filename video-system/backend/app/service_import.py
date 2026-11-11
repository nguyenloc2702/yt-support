"""Shot import gate — plan v3.2 §3.2, verbatim contract.

Fail-closed everywhere: payload duration never trusted (probe required),
canonical hash per §3.2.5, re-import per §3.2.6 (superseded, never overwrite).
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import struct
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from . import domain, new_id, now
from .config import allowed_hosts, data_dir
from .providers import registry
from .store import Store


class ImportRejected(Exception):
    def __init__(self, report: dict, status_code: int = 422) -> None:
        self.report = report
        self.status_code = status_code
        super().__init__(report.get("error", "import_gate_failed"))


QUOTE_TYPES = {"quote", "diagram"}
ON_SCREEN_TYPES = {"thought_bubble", "caption", "label", "diagram_text"}


# ---------------------------------------------------------------------------
# audio probe — wav via stdlib header, other formats via probe adapter (ffprobe)
# ---------------------------------------------------------------------------

def _probe_wav(path: Path) -> float:
    """Header probe with size cross-check — a spoofed header claiming more
    frames than the file contains is rejected (anti-spoof, §3.2.2)."""
    with wave.open(str(path), "rb") as w:
        nframes = w.getnframes()
        framesize = w.getnchannels() * w.getsampwidth()
        rate = w.getframerate()
    actual = path.stat().st_size - 44  # minus canonical header
    if nframes * framesize > actual:
        raise ValueError("wav header claims more frames than file contains")
    if rate <= 0:
        raise ValueError("invalid sample rate")
    return nframes / float(rate)


def probe_audio_file(path: Path) -> dict:
    try:
        if path.suffix.lower() == ".wav":
            return {"duration": round(_probe_wav(path), 3), "prober": "wave"}
        adapter = registry.require_probe()
        info = adapter.probe_audio(str(path))
        return {"duration": round(float(info["duration"]), 3), "prober": "adapter"}
    except Exception as e:  # noqa: BLE001 — probe failure = 422, never 500
        raise ImportRejected({"error": "audio_probe_failed", "detail": str(e)}, 422)


def _assert_public_host(host: str) -> None:
    """Allowlist + SSRF guard: resolve DNS, block private/loopback/link-local
    unless the host itself is an explicit allowlist entry (§7.3)."""
    if host not in allowed_hosts():
        raise ImportRejected({"error": "url_host_not_allowed", "host": host}, 422)
    if host in {"localhost", "127.0.0.1", "::1"}:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise ImportRejected({"error": "dns_resolve_failed", "host": host,
                              "detail": str(e)}, 422)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ImportRejected({"error": "private_ip_blocked", "host": host,
                                  "ip": str(ip)}, 422)


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-check allowlist on EVERY redirect hop (§7.3)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlsplit(newurl).hostname or ""
        _assert_public_host(host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_GuardedRedirect)


def fetch_url(url: str, dest: Path, max_bytes: int) -> Path:
    """Allowlist-domain + SSRF-guarded fetch with redirect re-check (§7.3)."""
    host = urllib.parse.urlsplit(url).hostname or ""
    _assert_public_host(host)
    dest.parent.mkdir(parents=True, exist_ok=True)
    got = 0
    try:
        with _OPENER.open(url, timeout=30) as r, open(dest, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                got += len(chunk)
                if got > max_bytes:
                    raise ImportRejected({"error": "payload_too_large"}, 413)
                f.write(chunk)
    except ImportRejected:
        raise
    except Exception as e:  # noqa: BLE001 — lỗi fetch luôn là 422, không 500
        raise ImportRejected({"error": "fetch_failed", "detail": str(e)}, 422)
    return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ImportGate:
    def __init__(self, store: Store, items) -> None:
        self.store = store
        self.items = items

    # -- validation ---------------------------------------------------------
    def _validate_shots(self, shots: list[dict]) -> list[dict]:
        limits = _render_limits()
        errors: list[dict] = []
        if not shots:
            raise ImportRejected({"error": "empty_shots"}, 422)
        if len(shots) > limits["max_shots_per_import"]:
            raise ImportRejected(
                {"error": "too_many_shots", "limit": limits["max_shots_per_import"]}, 413)
        required = {"index", "narration", "audio_start_sec", "audio_end_sec",
                    "visual_prompt_clean"}
        seen = set()
        clean = []
        for s in shots:
            missing = required - set(s)
            if missing:
                errors.append({"index": s.get("index"),
                               "reason": f"missing_fields:{sorted(missing)}"})
                continue
            idx = int(s["index"])
            if idx < 1:
                errors.append({"index": idx, "reason": "index_must_be_positive"})
                continue
            if idx in seen:
                errors.append({"index": idx, "reason": "duplicate_index"})
                continue
            seen.add(idx)
            start, end = float(s["audio_start_sec"]), float(s["audio_end_sec"])
            if end <= start:
                errors.append({"index": idx, "reason": "bad_segment"})
                continue
            ost = s.get("on_screen_text")
            st = s.get("shot_type", "scene")
            if ost and st not in QUOTE_TYPES:
                errors.append({"index": idx,
                               "reason": "on_screen_text_only_allowed_for_quote_diagram"})
                continue
            if s.get("on_screen_type") and s.get("on_screen_type") not in ON_SCREEN_TYPES:
                errors.append({"index": idx, "reason": "invalid_on_screen_type"})
                continue
            clean.append({
                "index": idx, "shot_type": st, "narration": s["narration"],
                "audio_start_sec": start, "audio_end_sec": end,
                "visual_prompt_clean": s["visual_prompt_clean"],
                "on_screen_text": ost, "on_screen_type": s.get("on_screen_type"),
                "keyframe_sha256": s.get("keyframe_sha256"),
                "keyframe_asset_url": s.get("keyframe_asset_url"),
                "character_ids": s.get("character_ids") or [],
            })
        if errors:
            raise ImportRejected({"error": "schema_violations", "shots": errors}, 422)
        clean.sort(key=lambda x: x["index"])
        # per-shot adjacency: overlap/gap between consecutive segments ≤ tolerance
        tol = _render_qc()["duration_tolerance_sec"]
        for a, b in zip(clean, clean[1:]):
            delta = b["audio_start_sec"] - a["audio_end_sec"]
            if abs(delta) > tol:
                raise ImportRejected({"error": "import_gate_failed", "global_drift_sec": None,
                                      "shots": [
                                          {"index": b["index"],
                                           "expected_start": a["audio_end_sec"],
                                           "expected_end": b["audio_end_sec"],
                                           "delta_sec": round(delta, 3), "ok": False,
                                           "reason": "SEGMENT_GAP_OVERLAP"}],
                                      "summary": {"total": len(clean), "ok": len(clean) - 1,
                                                  "failed": 1,
                                                  "max_abs_delta_sec": round(abs(delta), 3)}},
                                     422)
        return clean

    # -- main ----------------------------------------------------------------
    def import_shots(self, item_id: str, payload: dict, audio_src_path: Path | None,
                     audio_url: str | None) -> dict:
        item = self.items.get(item_id)
        if not item:
            raise KeyError(item_id)
        script_version = int(payload["script_version"])
        shots_in = payload["shots"]

        # 1. audio: NEVER trust payload duration — probe required (§3.2.2)
        if audio_src_path is None and not audio_url:
            raise ImportRejected({"error": "audio_file_required"}, 422)
        limits = _render_limits()
        if audio_src_path is not None:
            # sandbox: client-supplied file_path must live under data dir,
            # otherwise the gate becomes a hash-oracle for arbitrary files (§7.3)
            resolved = audio_src_path.resolve()
            sandbox = (data_dir()).resolve()
            if sandbox not in resolved.parents and resolved != sandbox:
                raise ImportRejected(
                    {"error": "file_path_outside_data_dir",
                     "hint": "upload the file first or use audio.file_url"}, 422)
        if audio_src_path is None:
            audio_src_path = fetch_url(
                audio_url, data_dir() / "objects" / item_id / "imports" / "audio.bin",
                limits["max_audio_mb"] * 1024 * 1024)
        audio_sha = sha256_file(audio_src_path)
        probed = probe_audio_file(audio_src_path)
        audio_duration = probed["duration"]

        # 2. validate shots
        clean = self._validate_shots(shots_in)
        total = clean[-1]["audio_end_sec"] - clean[0]["audio_start_sec"]
        global_drift = round(total - audio_duration, 3)
        gtol = _render_qc()["global_tolerance_sec"]

        # 3. idempotency / re-import (§3.2.6)
        shots_hash = domain.canonical_shots_hash(clean)
        prior = self.store.query_one(
            "SELECT * FROM scripts WHERE item_id=? AND version=?",
            (item_id, script_version))
        if prior:
            if prior["shots_hash"] == shots_hash:
                return {"replayed": True, "item_id": item_id,
                        "script_version": script_version, "shots_hash": shots_hash}
            raise ImportRejected(
                {"error": "script_version_conflict",
                 "hint": "same script_version with different content — bump version"},
                409)

        # 4. gates
        report = {"error": "import_gate_failed",
                  "audio": {"sha256": audio_sha,
                            "duration_sec_probed": audio_duration,
                            "duration_sec_payload": payload.get("audio", {}).get("duration_sec"),
                            "duration_trusted": False},
                  "shots": [], "summary": {}}
        if abs(global_drift) > gtol:
            report["global_drift_sec"] = global_drift
            report["summary"] = {"total": len(clean), "ok": 0, "failed": len(clean),
                                 "max_abs_delta_sec": abs(global_drift)}
            report["hint"] = ("Tổng duration shots lệch audio probe quá ±"
                              f"{gtol}s. Gen lại audio hoặc cắt script. Không tự kéo giãn.")
            raise ImportRejected(report, 422)

        # 5. keyframes supplied via URL: validate BEFORE trust (§3.2.4) —
        # hash, size, OCR; style check is a style-adapter TODO (fail-closed
        # is impossible until the adapter exists, so style is deferred and
        # re-checked at keyframe QC)
        for s in clean:
            if s.get("keyframe_asset_url"):
                kf_path = fetch_url(
                    s["keyframe_asset_url"],
                    data_dir() / "objects" / item_id / "imports" /
                    f"kf_{s['index']}.bin",
                    limits["max_keyframe_mb"] * 1024 * 1024)
                kf_sha = sha256_file(kf_path)
                declared = s.get("keyframe_sha256")
                if declared and declared != kf_sha:
                    raise ImportRejected(
                        {"error": "import_gate_failed", "shots": [
                            {"index": s["index"], "ok": False,
                             "reason": "KEYFRAME_HASH_MISMATCH",
                             "declared": declared, "actual": kf_sha}]}, 422)
                adapter = registry.require_probe()
                dims = adapter.probe_image(str(kf_path))
                spec = _render_video()
                if dims["width"] != spec["width"] or dims["height"] != spec["height"]:
                    raise ImportRejected(
                        {"error": "import_gate_failed", "shots": [
                            {"index": s["index"], "ok": False,
                             "reason": "KEYFRAME_SIZE_FAIL",
                             "got": f"{dims['width']}x{dims['height']}"}]}, 422)
                if s.get("on_screen_text"):
                    try:
                        ocr = registry.require_ocr()
                    except Exception:  # noqa: BLE001 — fail-closed, not silent
                        raise ImportRejected(
                            {"error": "ocr_unavailable",
                             "detail": "quote/diagram keyframe needs OCR to verify "
                                       "on_screen_text; configure OCR adapter"}, 422)
                    seen = ocr.ocr(str(kf_path)).strip()
                    if seen != s["on_screen_text"].strip():
                        raise ImportRejected(
                            {"error": "import_gate_failed", "shots": [
                                {"index": s["index"], "ok": False,
                                 "reason": "KEYFRAME_TEXT_MISMATCH",
                                 "ocr": seen}]}, 422)
                s["keyframe_sha256"] = kf_sha
                s["_kf_path"] = str(kf_path)
            else:
                s["_kf_path"] = None

        # 6. commit: new script version supersedes old shots (never overwrite)
        with self.store.transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO scripts (id,item_id,version,language,text,scenes_json,"
                    "hash,shots_hash,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (new_id("scr"), item_id, script_version, payload.get("language"),
                     payload.get("text"), json.dumps(clean, ensure_ascii=False),
                     audio_sha, shots_hash, "active", now()))
            except Exception as e:  # noqa: BLE001
                if "UNIQUE" in str(e):
                    raise ImportRejected(
                        {"error": "script_version_conflict",
                         "hint": "concurrent import of same version — retry idempotently"},
                        409)
                raise
            if payload.get("audio"):
                conn.execute(
                    "INSERT INTO audios (id,item_id,version,kind,audio_asset_id,"
                    "duration_sec,hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (new_id("aud"), item_id, script_version, "master", None,
                     audio_duration, audio_sha, now()))
            # keyframe assets: registered in assets table, shots point at ids
            for s in clean:
                if s.get("_kf_path"):
                    aid = new_id("ast")
                    conn.execute(
                        "INSERT INTO assets (id,item_id,kind,storage_key,sha256,"
                        "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?)",
                        (aid, item_id, "image",
                         f"items/{item_id}/shots/{s['index']:04d}/keyframe.png",
                         s["keyframe_sha256"], "imported", now()))
                    s["_kf_asset_id"] = aid
                else:
                    s["_kf_asset_id"] = None
            # supersede old-version shots through the legal transition table,
            # with per-shot events (no raw silent overwrite, §1.6/A14)
            old_rows = conn.execute(
                "SELECT id, status FROM shots WHERE item_id=? AND script_version < ? "
                "AND status != 'superseded'", (item_id, script_version)).fetchall()
            for row in old_rows:
                cur = row["status"]
                domain.assert_shot_transition(cur, "superseded")
            t = now()
            for row in old_rows:
                conn.execute(
                    "UPDATE shots SET status='superseded', updated_at=? WHERE id=? "
                    "AND status=? AND status != 'superseded'", (t, row["id"], row["status"]))
                conn.execute(
                    "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (new_id("evt"), item_id, row["id"], "shot.status", "api",
                     json.dumps({"from": row["status"], "to": "superseded",
                                 "reason": f"re-import v{script_version}"}),
                     t))
            for s in clean:
                conn.execute(
                    "INSERT INTO shots (id,item_id,script_version,index_num,shot_type,"
                    "narration,audio_start_sec,audio_end_sec,visual_prompt,"
                    "on_screen_text,on_screen_type,keyframe_asset_id,keyframe_hash,"
                    "character_ids,status,created_at,"
                    "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (new_id("shot"), item_id, script_version, s["index"], s["shot_type"],
                     s["narration"], s["audio_start_sec"], s["audio_end_sec"],
                     s["visual_prompt_clean"], s["on_screen_text"], s["on_screen_type"],
                     s["_kf_asset_id"], s.get("keyframe_sha256"),
                     json.dumps(s.get("character_ids") or []),
                     domain.ShotStatus.PENDING.value, now(), now()))
            conn.execute("UPDATE items SET total_shots=?, audio_duration_sec=?, updated_at=? WHERE id=?",
                         (self._active_count(item_id), audio_duration, now(), item_id))
        # stale counters after supersede (P2): recompute appended/manifest
        self._recompute_after_supersede(item_id)
        self._transition_item_after_import(item_id, script_version)
        self.items.event(item_id, None, "shots.imported", "api",
                         {"script_version": script_version, "count": len(clean),
                          "audio_sha256": audio_sha, "shots_hash": shots_hash})
        return {"item_id": item_id, "script_version": script_version,
                "shots_hash": shots_hash, "count": len(clean),
                "audio": report["audio"]}

    def _transition_item_after_import(self, item_id: str, script_version: int) -> None:
        item = self.items.get(item_id)
        if item["status"] == domain.ItemStatus.DRAFT.value:
            self.items.transition(item_id, domain.ItemStatus.ANALYZING.value,
                                  actor="api", reason="shots imported")
        if item["status"] in {domain.ItemStatus.DRAFT.value,
                              domain.ItemStatus.ANALYZING.value}:
            self.items.transition(item_id, domain.ItemStatus.AWAITING_REVIEW.value,
                                  actor="api", reason=f"shots imported v{script_version}")

    def _recompute_after_supersede(self, item_id: str) -> None:
        from .service_batches import BatchService  # local import avoids cycle
        appended = self.store.query_one(
            "SELECT COUNT(*) c FROM shots WHERE item_id=? AND status='appended'",
            (item_id,))["c"]
        self.store.execute("UPDATE items SET appended_count=? WHERE id=?",
                           (appended, item_id))

    def _active_count(self, item_id: str) -> int:
        return self.store.query_one(
            "SELECT COUNT(*) c FROM shots WHERE item_id=? AND status != 'superseded'",
            (item_id,))["c"]


# config access without importing config module (avoid double-load in tests)
def _spec() -> dict:
    from .config import RENDER_SPEC
    return RENDER_SPEC


def _render_video() -> dict:
    return _spec()["video"]


def _render_qc() -> dict:
    return _spec()["qc"]


def _render_limits() -> dict:
    return _spec()["limits"]
