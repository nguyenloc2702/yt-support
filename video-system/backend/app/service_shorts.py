"""Short Studio service (Phụ lục G) — điều phối kịch bản → TTS → ảnh → MP4 9:16.

House style: mọi chuyển trạng thái qua `assert_short_transition`, artifacts ghi
bảng `assets` (item_id NULL, storage_key `shorts/{short_id}/...`), events ghi
bảng `events` + dispatch webhook (type `short.*` — subscription chưa map thì bỏ qua).
Fail-closed: thiếu adapter → AdapterNotConfigured nổi lên tận endpoint (503).
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import domain, providers
from .config import SHORT_CONFIG, RENDER_SPEC_SHORT, data_dir
from .pipeline_short import assembler, bank, karaoke, prompts, tts_vi, skill_bundle_short
from .pipeline_short import article_images, stock_media, story_media
from .store import Store, new_id, now

logger = logging.getLogger("video_system.service_shorts")


class ShortfallError(Exception):
    """Agent clips chưa phủ đủ duration — endpoint trả 409 kèm progress."""

    def __init__(self, progress: dict) -> None:
        super().__init__(
            f"Clips thiếu {progress['shortfall_sec']}s so với audio "
            f"({progress['covered_duration_sec']}/{progress['total_duration_sec']}s) "
            f"— scene còn thiếu: {progress['missing_scene_indexes']}")
        self.progress = progress


class ShortService:
    def __init__(self, store: Store, webhooks=None) -> None:
        self.store = store
        self.webhooks = webhooks

    # ------------------------------------------------------------------ events
    def _event(self, short_id: str, type_: str, actor: str, payload: dict) -> None:
        eid = new_id("evt")
        t = now()
        self.store.execute(
            "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
            "VALUES (?,?,?,?,?,?,?)",
            (eid, short_id, None, type_, actor, json.dumps(payload, ensure_ascii=False), t),
        )
        if self.webhooks is not None:
            try:
                self.webhooks.dispatch_event({
                    "id": eid, "item_id": short_id, "type": type_,
                    "payload_json": json.dumps(payload, ensure_ascii=False), "at": t,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("webhook dispatch failed for %s: %s", type_, exc)

    def _transition(self, short_id: str, to_status: str, reason: str = "") -> None:
        row = self.store.query_one("SELECT status FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        cur = row["status"]
        if cur == to_status:
            return
        domain.assert_short_transition(cur, to_status)
        self.store.execute(
            "UPDATE shorts SET status=?, error_detail=?, updated_at=? WHERE id=?",
            (to_status, None if to_status != domain.ShortStatus.FAILED.value
             else reason or None, now(), short_id),
        )

    def cancel(self, short_id: str, *, actor: str = "api", reason: str = "") -> None:
        """Huỷ 1 short ở trạng thái scripted/queued — dùng khi critic bắt lỗi và gen lại."""
        self._transition(short_id, domain.ShortStatus.CANCELLED.value,
                         reason=f"cancelled by {actor}: {reason}"[:180])

    # ------------------------------------------------------------------ create
    def create(self, mode: str, source_text: str, source_note: str = "",
               source_link: str = "", target_seconds: float | None = None,
               title: str | None = None, lang: str = "vi",
               actor: str = "api", extra_script_meta: dict | None = None) -> dict:
        if mode not in prompts.MODES:
            raise ValueError(f"mode không hợp lệ: {mode} (chấp nhận: {', '.join(prompts.MODES)})")
        if lang not in prompts.LANGS:
            raise ValueError(f"lang không hợp lệ: {lang} (chấp nhận: {', '.join(prompts.LANGS)})")
        max_chars = int(SHORT_CONFIG["limits"]["max_source_chars"])
        if not source_text or not source_text.strip():
            raise ValueError("Thiếu nội dung nguồn (source_text) — dán tin vắn hoặc chủ đề.")
        if len(source_text) > max_chars:
            raise ValueError(f"Nội dung nguồn {len(source_text)} ký tự vượt giới hạn {max_chars}.")
        if target_seconds is not None and not (10 <= float(target_seconds) <= 180):
            raise ValueError("target_seconds phải trong khoảng 10–180s.")
        source_link = (source_link or "").strip()
        if source_link and not source_link.lower().startswith(("http://", "https://")):
            raise ValueError("source_link phải là URL http(s) của bài báo.")

        llm = providers.registry.require_llm()  # fail-closed AdapterNotConfigured
        raw = llm.chat_json(
            prompts.system_prompt(mode, lang),
            prompts.user_prompt(mode, source_text, target_seconds, source_note, lang),
            prompts.schema(mode),
        )
        script = prompts.validate_script(mode, raw, source_note_fallback=source_note,
                                         lang=lang)
        if extra_script_meta:
            for k, v in extra_script_meta.items():
                if v is not None and k not in {"hook", "beats", "closing", "word_count", "lang"}:
                    script[k] = v

        short_id = new_id("sho")
        t = now()
        display_title = (title or script["title"]).strip()[:200]
        self.store.execute(
            "INSERT INTO shorts (id, mode, lang, title, status, script_json, source_text, "
            "source_note, source_link, target_seconds, duration_s, word_count, "
            "error_detail, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (short_id, mode, lang, display_title, domain.ShortStatus.DRAFT.value,
             json.dumps(script, ensure_ascii=False), source_text,
             script.get("source_note") or source_note, source_link or None,
             target_seconds, 0.0, int(script.get("word_count") or 0), None, t, t),
        )
        self._sync_scenes(short_id, script, lang=lang, mode=mode)
        self._transition(short_id, domain.ShortStatus.SCRIPTED.value,
                         reason="kịch bản đã tạo")  # draft → scripted (assert DRAFT→SCRIPTED)
        self._event(short_id, "short.created", actor,
                    {"mode": mode, "lang": lang, "title": display_title,
                     "word_count": script.get("word_count")})
        return self.get(short_id)

    # ------------------------------------------------------------------ scenes
    def _sync_scenes(self, short_id: str, script: dict, lang: str = "vi",
                     mode: str = "news") -> None:
        """Scene = 1 cue thoại; image_prompt theo style của mode (news/cinematic)."""
        texts = prompts.narration_texts(script)
        max_scenes = int(SHORT_CONFIG["limits"]["max_scenes"])
        if len(texts) > max_scenes:
            raise ValueError(f"Kịch bản {len(texts)} cảnh vượt giới hạn {max_scenes}.")
        self.store.execute("DELETE FROM short_scenes WHERE short_id=?", (short_id,))
        t = now()
        for i, text in enumerate(texts, start=1):
            self.store.execute(
                "INSERT INTO short_scenes (id, short_id, seq_num, text, start_sec, "
                "end_sec, image_prompt, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id("ssc"), short_id, i, text, 0.0, 0.0,
                 _image_prompt_for(short_id, i, text, lang, mode), t, t),
            )

    # ------------------------------------------------------------------ read
    def list(self, status: str | None = None, q: str | None = None) -> list[dict]:
        sql = "SELECT * FROM shorts"
        conds, params = [], []
        if status:
            conds.append("status=?")
            params.append(status)
        if q:
            conds.append("(title LIKE ? OR id LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC"
        rows = self.store.query(sql, tuple(params))
        return [self._hydrate(r, with_scenes=False) for r in rows]

    def get(self, short_id: str) -> dict:
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        return self._hydrate(row, with_scenes=True)

    def _hydrate(self, row: dict, with_scenes: bool) -> dict:
        short = dict(row)
        try:
            short["script"] = json.loads(row.get("script_json") or "{}")
        except json.JSONDecodeError:
            short["script"] = {}
        short.pop("script_json", None)
        short["status_label"] = domain.SHORT_LABELS_VI.get(row["status"], row["status"])
        yt_row = self.store.query_one(
            "SELECT id, id AS upload_id, COALESCE(target_type,'short') AS target_type, "
            "youtube_video_id, youtube_url, title, privacy_status, publish_at, "
            "upload_status, progress_pct, thumbnail_synced, captions_synced, error_message, updated_at "
            "FROM youtube_uploads WHERE item_id=? AND COALESCE(target_type,'todo')='short' "
            "ORDER BY created_at DESC LIMIT 1",
            (row["id"],),
        )
        short["youtube_upload"] = dict(yt_row) if yt_row else None
        if with_scenes:
            short["scenes"] = self.store.query(
                "SELECT * FROM short_scenes WHERE short_id=? ORDER BY seq_num", (row["id"],))
        return short

    # ------------------------------------------------------------------ script
    def update_script(self, short_id: str, patch: dict, actor: str = "api") -> dict:
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        if (row.get("audio_source") or "tts") == "owner":
            raise ValueError(
                "Short dùng audio owner — sửa kịch bản sẽ mất timing audio owner. "
                "Xoá audio trước rồi mới sửa kịch bản.")
        if row["status"] not in {domain.ShortStatus.SCRIPTED.value,
                                 domain.ShortStatus.FAILED.value}:
            raise ValueError(
                f"Chỉ sửa kịch bản khi ở trạng thái 'scripted' (hiện: {row['status']}).")
        script = json.loads(row["script_json"] or "{}")
        for field in ("title", "hook", "closing", "description", "source_note"):
            if field in patch and str(patch[field]).strip():
                script[field] = str(patch[field]).strip()
        if "beats" in patch:
            beats = [str(b).strip() for b in (patch["beats"] or []) if str(b).strip()]
            if not beats:
                raise ValueError("beats không được rỗng.")
            max_beats = 15 if row["mode"] == "daily_digest" else (3 if row["mode"] == "trailer" else 4)
            script["beats"] = beats[:max_beats]
            if row["mode"] == "daily_digest" and isinstance(script.get("stories"), list):
                for idx_s, st in enumerate(script["stories"]):
                    if idx_s < len(script["beats"]) and isinstance(st, dict):
                        st["narration"] = script["beats"][idx_s]
        mode = row["mode"]
        lang = row.get("lang") or "vi"
        validated = prompts.validate_script(mode, script,
                                            source_note_fallback=row.get("source_note") or "",
                                            lang=lang)
        self.store.execute(
            "UPDATE shorts SET script_json=?, title=?, word_count=?, updated_at=? WHERE id=?",
            (json.dumps(validated, ensure_ascii=False),
             (patch.get("title") or validated["title"]).strip()[:200],
             int(validated.get("word_count") or 0), now(), short_id),
        )
        self._sync_scenes(short_id, validated, lang=lang, mode=mode)
        if row["status"] == domain.ShortStatus.FAILED.value:
            self._transition(short_id, domain.ShortStatus.SCRIPTED.value)  # failed → scripted
        self._event(short_id, "short.script_updated", actor,
                    {"word_count": validated.get("word_count")})
        return self.get(short_id)

    # ------------------------------------------------------------------ audio ingest
    _AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".opus"}

    def _audio_duration(self, path: Path, ext: str) -> float:
        """WAV → đọc header; định dạng khác → ffprobe (format=duration)."""
        dur = 0.0
        if ext == ".wav":
            try:
                dur = float(assembler._wav_duration(path))
            except Exception:  # noqa: BLE001 — wav hỏng thì thử ffprobe
                dur = 0.0
        if dur <= 0:
            dur = self._probe_video_duration(path)
        if dur <= 0:
            raise ValueError("Không đọc được thời lượng audio — kiểm tra lại file.")
        return round(dur, 3)

    def _validate_audio_shots(self, shots: list[dict], audio_dur: float) -> list[dict]:
        """Fail-closed: shot phải liên tục, phủ đúng độ dài audio."""
        if not isinstance(shots, list) or not shots:
            raise ValueError("Danh sách shot rỗng — cần ít nhất 1 shot.")
        max_scenes = int(SHORT_CONFIG["limits"]["max_scenes"])
        if len(shots) > max_scenes:
            raise ValueError(f"Có {len(shots)} shot, vượt giới hạn {max_scenes} shot.")
        clean: list[dict] = []
        for idx, shot in enumerate(shots, start=1):
            if not isinstance(shot, dict):
                raise ValueError(f"Shot #{idx} không phải object.")
            text = str(shot.get("text") or "").strip()
            if not text:
                raise ValueError(f"Shot #{idx}: text rỗng.")
            try:
                start = float(shot.get("start_sec"))
                end = float(shot.get("end_sec"))
            except (TypeError, ValueError):
                raise ValueError(
                    f"Shot #{idx}: start_sec/end_sec phải là số.") from None
            if start < 0:
                raise ValueError(f"Shot #{idx}: start_sec phải >= 0.")
            if end <= start:
                raise ValueError(f"Shot #{idx}: end_sec phải lớn hơn start_sec.")
            clean.append({"text": text, "start_sec": start, "end_sec": end})
        if abs(clean[0]["start_sec"]) > 0.5:
            raise ValueError("Shot đầu tiên phải bắt đầu gần 0s (lệch tối đa 0.5s).")
        for i in range(1, len(clean)):
            prev, cur = clean[i - 1], clean[i]
            if cur["start_sec"] < prev["end_sec"] - 0.05:
                raise ValueError(
                    f"Shot #{i + 1} bắt đầu ({cur['start_sec']:.2f}s) trước khi shot "
                    f"#{i} kết thúc ({prev['end_sec']:.2f}s) — timeline phải liên tục.")
        last_end = clean[-1]["end_sec"]
        tol = max(0.6, 0.02 * audio_dur)
        if abs(last_end - audio_dur) > tol:
            raise ValueError(
                f"Tổng thời lượng shot ({last_end:.2f}s) lệch audio ({audio_dur:.2f}s) "
                f"quá dung sai {tol:.2f}s.")
        return clean

    def ingest_audio(self, short_id: str, *, raw_bytes: bytes | None = None,
                     filename: str = "audio.mp3", file_path: Path | None = None,
                     shots: list[dict], actor: str = "ui") -> dict:
        """Owner nạp audio + danh sách shot sẵn (không TTS, không ASR).

        Ghi audio/words/ass/srt, thay `short_scenes` theo timing owner và đánh
        dấu `audio_source='owner'` để `publish_job` bỏ qua `_ensure_voice_timeline`.
        """
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        allowed = {domain.ShortStatus.SCRIPTED.value, domain.ShortStatus.DRAFT.value}
        if row["status"] not in allowed:
            raise ValueError(
                "Chỉ nạp audio khi short ở trạng thái 'scripted' hoặc 'draft' "
                f"(hiện: {row['status']}).")
        if raw_bytes is None:
            if file_path is None:
                raise ValueError("Thiếu file audio (raw_bytes hoặc file_path).")
            p_in = Path(file_path)
            if not p_in.exists():
                raise ValueError(f"File audio không tồn tại: {p_in}")
            raw_bytes = p_in.read_bytes()
        if not raw_bytes:
            raise ValueError("File audio rỗng.")
        ext = Path(filename or "audio.mp3").suffix.lower() or ".mp3"
        if ext not in self._AUDIO_EXTS:
            raise ValueError(f"Định dạng audio không hỗ trợ ({ext}).")

        lang = row.get("lang") or "vi"
        mode = row.get("mode") or ""
        objects = tts_vi.objects_root(short_id)
        audio_dir = objects / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_file = audio_dir / f"voice_{short_id}{ext}"
        audio_file.write_bytes(raw_bytes)
        try:
            audio_dur = self._audio_duration(audio_file, ext)
            clean = self._validate_audio_shots(shots, audio_dur)
        except Exception:
            audio_file.unlink(missing_ok=True)
            raise

        words = _words_from_shots(clean)
        words_obj = audio_dir / f"words_{short_id}.json"
        words_obj.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        ass_path = objects / f"subs_{short_id}.ass"
        srt_path = objects / f"subs_{short_id}.srt"
        karaoke.build_short_ass(words, ass_path, audio_dur)
        karaoke.build_short_srt(words, srt_path, audio_dur)

        audio_ast = self._put_asset(
            short_id, "audio", f"shorts/{short_id}/audio/voice_{short_id}{ext}",
            audio_file, "audio", duration_s=audio_dur, engine="owner-upload",
            meta={"filename": filename or f"audio{ext}"})
        words_ast = self._put_asset(
            short_id, "json", f"shorts/{short_id}/audio/words_{short_id}.json",
            words_obj, "json", engine="word-timeline")
        ass_ast = self._put_asset(
            short_id, "subtitle", f"shorts/{short_id}/subs_{short_id}.ass",
            ass_path, "subtitle", engine="karaoke-ass")
        srt_ast = self._put_asset(
            short_id, "subtitle", f"shorts/{short_id}/subs_{short_id}.srt",
            srt_path, "subtitle", engine="srt")
        self.store.execute(
            "UPDATE shorts SET audio_asset_id=?, words_asset_id=?, srt_asset_id=?, "
            "ass_asset_id=?, duration_s=?, audio_source='owner', updated_at=? WHERE id=?",
            (audio_ast, words_ast, srt_ast, ass_ast, audio_dur, now(), short_id),
        )

        self.store.execute("DELETE FROM short_scenes WHERE short_id=?", (short_id,))
        t = now()
        for i, shot in enumerate(clean, start=1):
            self.store.execute(
                "INSERT INTO short_scenes (id, short_id, seq_num, text, start_sec, "
                "end_sec, image_prompt, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id("ssc"), short_id, i, shot["text"], shot["start_sec"],
                 shot["end_sec"],
                 _image_prompt_for(short_id, i, shot["text"], lang, mode), t, t),
            )
        self._event(short_id, "short.audio_ingested", actor,
                    {"scenes": len(clean), "duration_sec": audio_dur,
                     "audio_source": "owner"})
        # draft chưa publish được (transition draft→queued không hợp lệ) → đưa lên
        # scripted, đúng luật DRAFT→SCRIPTED sẵn có.
        if row["status"] == domain.ShortStatus.DRAFT.value:
            self._transition(short_id, domain.ShortStatus.SCRIPTED.value,
                             reason="nạp audio owner")
        return self.get(short_id)

    def clear_audio(self, short_id: str, actor: str = "ui") -> dict:
        """Bỏ audio owner, quay lại luồng TTS (để sửa kịch bản hoặc nạp audio khác)."""
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        if row["status"] not in {domain.ShortStatus.SCRIPTED.value,
                                 domain.ShortStatus.DRAFT.value}:
            raise ValueError(
                "Chỉ bỏ audio khi short ở trạng thái 'scripted'/'draft' "
                f"(hiện: {row['status']}).")
        if (row.get("audio_source") or "tts") != "owner":
            raise ValueError("Short này không dùng audio owner — không có gì để bỏ.")

        objects = tts_vi.objects_root(short_id)
        audio_dir = objects / "audio"
        for f in audio_dir.glob(f"voice_{short_id}.*"):
            f.unlink(missing_ok=True)
        (audio_dir / f"words_{short_id}.json").unlink(missing_ok=True)
        for f in objects.glob(f"subs_{short_id}.*"):
            f.unlink(missing_ok=True)

        self.store.execute(
            "UPDATE shorts SET audio_source='tts', audio_asset_id=NULL, "
            "words_asset_id=NULL, srt_asset_id=NULL, ass_asset_id=NULL, "
            "duration_s=0.0, updated_at=? WHERE id=?",
            (now(), short_id),
        )
        # Timing owner không còn đúng → trả scene về 0 để publish_job sinh lại bằng TTS.
        self.store.execute(
            "UPDATE short_scenes SET start_sec=0.0, end_sec=0.0, "
            "generated_duration_sec=0.0, agent_checked=0 WHERE short_id=?",
            (short_id,))
        self._event(short_id, "short.audio_cleared", actor, {})
        return self.get(short_id)

    # ------------------------------------------------------------------ produce
    def _ensure_voice_timeline(self, short_id: str, row: dict) -> tuple[dict, list[dict], Path, Path]:
        """Voice EN/VI + word timeline + scene windows + karaoke subs (G.13).

        Dùng chung cho 2 luồng: auto produce (ảnh tĩnh) và job mode (agent
        ngoài gen video). Idempotent — gọi lại thì overwrite artifacts.
        Trả: (voice, scenes, ass_path, srt_path).
        """
        if (row.get("audio_source") or "tts") == "owner":
            raise ValueError(
                "Short dùng audio owner — không được sinh lại voice timeline "
                "(sẽ xoá scene + audio owner).")
        script = json.loads(row["script_json"] or "{}")
        lang = row.get("lang") or "vi"
        texts = prompts.narration_texts(script)
        objects = tts_vi.objects_root(short_id)

        mode = row.get("mode") or ""
        mode_cfg = (SHORT_CONFIG.get("modes") or {}).get(mode) or {}
        voice_override = row.get("voice") or mode_cfg.get("voice")
        voice = tts_vi.synthesize_short_voice(texts, objects, short_id,
                                              voice=voice_override, lang=lang)
        audio_ast = self._put_asset(short_id, "audio", voice["object_key"],
                                    Path(voice["audio_path"]), "audio",
                                    duration_s=voice["duration"],
                                    engine="edge-tts" if not voice["offline"] else "offline-synth",
                                    meta={"voice": voice["voice"]})
        words_obj = objects / "audio" / f"words_{short_id}.json"
        words_obj.write_text(json.dumps(voice["words"], ensure_ascii=False), encoding="utf-8")
        words_ast = self._put_asset(short_id, "json",
                                    f"shorts/{short_id}/audio/words_{short_id}.json",
                                    words_obj, "json", engine="word-timeline")

        spans = voice["cue_spans"]
        self.store.execute("DELETE FROM short_scenes WHERE short_id=?", (short_id,))
        t = now()
        for i, text in enumerate(texts, start=1):
            span = spans[i - 1] if i - 1 < len(spans) else {"start": 0.0, "end": voice["duration"]}
            self.store.execute(
                "INSERT INTO short_scenes (id, short_id, seq_num, text, start_sec, end_sec, "
                "image_prompt, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id("ssc"), short_id, i, text, float(span["start"]), float(span["end"]),
                 _image_prompt_for(short_id, i, text, lang, mode), t, t),
            )
        scenes = self.store.query(
            "SELECT * FROM short_scenes WHERE short_id=? ORDER BY seq_num", (short_id,))

        ass_path = objects / f"subs_{short_id}.ass"
        srt_path = objects / f"subs_{short_id}.srt"
        karaoke.build_short_ass(voice["words"], ass_path, voice["duration"])
        karaoke.build_short_srt(voice["words"], srt_path, voice["duration"])
        ass_ast = self._put_asset(short_id, "subtitle",
                                  f"shorts/{short_id}/subs_{short_id}.ass", ass_path,
                                  "subtitle", engine="karaoke-ass")
        srt_ast = self._put_asset(short_id, "subtitle",
                                  f"shorts/{short_id}/subs_{short_id}.srt", srt_path,
                                  "subtitle", engine="srt")
        # Ghi cột artifact vào shorts row (audio_path/ass_path resolve từ đây)
        self.store.execute(
            "UPDATE shorts SET audio_asset_id=?, words_asset_id=?, srt_asset_id=?, "
            "ass_asset_id=?, duration_s=?, updated_at=? WHERE id=?",
            (audio_ast, words_ast, srt_ast, ass_ast, voice["duration"], now(), short_id),
        )
        return voice, scenes, ass_path, srt_path

    def produce(self, short_id: str, actor: str = "api") -> dict:
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        if (row.get("audio_source") or "tts") == "owner":
            raise ValueError(
                "Short dùng audio owner — produce ảnh tĩnh sẽ ghi đè audio/scene owner. "
                "Dùng job mode (publish-job → agent nộp clip) thay vì produce.")
        if row["status"] not in {domain.ShortStatus.SCRIPTED.value,
                                 domain.ShortStatus.READY.value,
                                 domain.ShortStatus.FAILED.value}:
            raise ValueError(
                f"Không thể tổng hợp từ trạng thái '{row['status']}' — cần kịch bản đã duyệt.")
        self._transition(short_id, domain.ShortStatus.PRODUCING.value)
        self.store.execute("UPDATE shorts SET error_detail=NULL, updated_at=? WHERE id=?",
                           (now(), short_id))
        self._event(short_id, "short.produce_started", actor, {"mode": row["mode"]})
        try:
            result = self._produce_inner(short_id, row, actor)
        except Exception as exc:  # noqa: BLE001
            msg = f"Produce thất bại: {exc}"
            logger.exception("short %s produce failed", short_id)
            self.store.execute("UPDATE shorts SET error_detail=?, updated_at=? WHERE id=?",
                               (msg, now(), short_id))
            self._transition(short_id, domain.ShortStatus.FAILED.value, reason=msg)
            self._event(short_id, "short.failed", actor, {"error": str(exc)})
            raise
        self._transition(short_id, domain.ShortStatus.READY.value)
        self._event(short_id, "short.ready", actor, {
            "duration_sec": result.get("duration"),
            "scenes": result.get("scenes_count"),
            "video_url": f"/api/v1/shorts/{short_id}/video",
        })
        return self.get(short_id)

    def _produce_inner(self, short_id: str, row: dict, actor: str) -> dict:
        script = json.loads(row["script_json"] or "{}")
        mode = row["mode"]
        lang = row.get("lang") or "vi"
        texts = prompts.narration_texts(script)
        objects = tts_vi.objects_root(short_id)

        # 1-2) Voice + word timeline + scene windows + karaoke subs
        #      (hàm dùng chung — G.13 publish_job cũng gọi để build audio job)
        voice, scenes, ass_path, srt_path = self._ensure_voice_timeline(short_id, row)

        # 3) Nguồn visual mỗi scene — thứ tự (G.11 + mảng quốc tế): ảnh gốc bài báo
        #    → Pexels photo → Pexels video b-roll → bank → AI-gen → thẻ màu.
        #    Không adapter + bank trống + không ảnh bài → lỗi tiếng người (honest).
        img_cfg = SHORT_CONFIG["modes"][mode]
        image_gen = None
        try:
            image_gen = providers.registry.require_image_gen()
        except providers.AdapterNotConfigured:
            image_gen = None
        fast = bool(img_cfg.get("fast_mode"))
        hero_n = int(img_cfg.get("hero_images") or 0) if (fast and image_gen) else 0

        # 3a) Ảnh gốc bài báo + ảnh liên quan chặt chẽ (Wikipedia/Wikimedia) cho từng tin daily_digest;
        #     các mode đơn lẻ lấy ảnh từ source_link.
        article_paths: list[Path] = []
        per_scene_article: dict[int, tuple[Path, str | None]] = {}
        per_scene_multi: dict[int, dict] = {}  # seq -> {"paths": list[Path], "attribution": str, "asset_id": str}
        story_links = script.get("story_links") or {}
        stories_meta = list(script.get("stories") or [])
        # Ảnh chủ động cung cấp qua digest submit ({seq: [path,...]}) — ưu tiên cao nhất
        owner_images = script.get("owner_images") or {}
        if mode == "daily_digest" and (isinstance(story_links, dict) and story_links or stories_meta):
            seen_hashes: set[str] = set()
            for sc in scenes:
                seq = int(sc["seq_num"])
                s_info = story_links.get(str(seq)) or story_links.get(seq) or {}
                st_meta = stories_meta[seq - 2] if 2 <= seq <= len(stories_meta) + 1 else {}
                link_u = str(s_info.get("link") or "").strip() if isinstance(s_info, dict) else ""
                headline_hint = str(
                    st_meta.get("headline")
                    or (s_info.get("title") if isinstance(s_info, dict) else "")
                    or sc["text"][:100]
                ).strip()
                q_list = list(st_meta.get("image_queries") or [])
                if st_meta.get("search_keywords_en"):
                    q_list.append(str(st_meta["search_keywords_en"]))
                if not link_u and not q_list and seq in {1, len(scenes)}:
                    # Hook/Outro lấy đại diện ảnh #1 hoặc tin cuối nếu không có query riêng
                    continue
                owner_files = [Path(p) for p in (owner_images.get(str(seq)) or []) if Path(p).exists()]
                if owner_files:
                    # Owner submit ảnh sẵn → dùng thẳng, không scrape
                    found_items = [{"path": str(p), "source": "owner-submitted", "attribution": ""}
                                   for p in owner_files]
                else:
                    found_items = story_media.collect_story_visuals(
                        article_link=link_u,
                        headline=headline_hint,
                        queries=q_list,
                        out_dir=objects / "article_images" / f"s{seq:02d}",
                        target_images=3 if 2 <= seq <= len(scenes) - 1 else 2,
                        seen_hashes=seen_hashes,
                    )
                if found_items:
                    paths_i: list[Path] = []
                    attrs_i: list[str] = []
                    first_aid: str | None = None
                    for idx_p, item_p in enumerate(found_items):
                        fp = Path(item_p["path"])
                        aid = self._put_asset(
                            short_id, "image",
                            f"shorts/{short_id}/article_images/s{seq:02d}_{idx_p + 1:02d}_{fp.name}",
                            fp, "image", engine=str(item_p.get("source") or "article-original"),
                            meta={"attribution": item_p.get("attribution")},
                        )
                        if first_aid is None:
                            first_aid = aid
                        paths_i.append(fp)
                        if item_p.get("attribution"):
                            attrs_i.append(str(item_p["attribution"]))
                    per_scene_multi[seq] = {
                        "paths": paths_i,
                        "asset_id": first_aid,
                        "attribution": "; ".join(dict.fromkeys(attrs_i))[:280],
                    }
                    per_scene_article[seq] = (paths_i[0], first_aid)
            # Hook (seq=1) & Outro (cuối) mượn ảnh tin đầu/cuối nếu chưa có để giữ đồng bộ tin trong ngày
            if 1 not in per_scene_multi and 2 in per_scene_multi:
                per_scene_multi[1] = {**per_scene_multi[2], "paths": [per_scene_multi[2]["paths"][0]]}
                per_scene_article[1] = per_scene_article[2]
            last_seq = scenes[-1]["seq_num"] if scenes else 0
            if last_seq and last_seq not in per_scene_multi and (last_seq - 1) in per_scene_multi:
                prev_m = per_scene_multi[last_seq - 1]
                per_scene_multi[last_seq] = {**prev_m, "paths": [prev_m["paths"][0]]}
                per_scene_article[last_seq] = per_scene_article[last_seq - 1]

        source_link = (row.get("source_link") or "").strip()
        if source_link and not per_scene_article:
            try:
                article_paths = article_images.collect_article_images(
                    source_link, objects / "article_images",
                    limit=len(texts) + 2,
                    title_hint=str(script.get("title") or row.get("title") or ""))
            except Exception as exc:  # noqa: BLE001
                logger.warning("article images failed (%s): %s", source_link[:80], exc)
                article_paths = []
        for art in article_paths:
            self._put_asset(short_id, "image",
                            f"shorts/{short_id}/article_images/{art.name}",
                            art, "image", engine="article-original")
        # map tên file → asset_id để gán vào scene (UI lấy ảnh qua scene endpoint)
        article_asset_ids = {
            a["storage_key"].rsplit("/", 1)[-1]: a["id"]
            for a in self.store.query(
                "SELECT id, storage_key FROM assets WHERE storage_key LIKE ?",
                (f"shorts/{short_id}/article_images/%",))
        }

        # 3b) Stock Pixabay/Pexels (ảnh + video b-roll) — tắt ở daily_digest để tránh ảnh stock lệch chủ đề tin
        stock_plan: dict[int, dict] = {}  # seq -> {path, kind, attribution, asset_id}
        if stock_media.is_configured() and mode != "daily_digest":
            stock_dir = objects / "stock_media"
            stock_dir.mkdir(parents=True, exist_ok=True)
            kw_en_list = list(script.get("search_keywords_en") or [])
            used_stock_paths: set[str] = set()
            for sc in scenes:
                seq = int(sc["seq_num"])
                if seq in per_scene_article or seq <= len(article_paths):
                    continue  # scene này đã có ảnh gốc bài báo
                q_en = kw_en_list[seq - 1].strip() if seq - 1 < len(kw_en_list) else ""
                q = q_en or stock_media.keywords_for_query(sc["text"])
                if not q:
                    continue
                got = None
                try:
                    got = stock_media.fetch_video(q) or stock_media.fetch_photo(q)
                    if got and str(got["path"]) in used_stock_paths:
                        alt_q = stock_media.keywords_for_query(sc["text"], max_words=3)
                        if alt_q and alt_q != q:
                            got_alt = stock_media.fetch_video(alt_q) or stock_media.fetch_photo(alt_q)
                            if got_alt:
                                got = got_alt
                except Exception as exc:  # noqa: BLE001
                    logger.warning("stock scene %d failed: %s", seq, exc)
                if not got:
                    continue
                used_stock_paths.add(str(got["path"]))
                local = stock_dir / f"scene_{seq:03d}{got['path'].suffix.lower()}"
                if not local.exists():
                    local.write_bytes(got["path"].read_bytes())
                asset_id = self._put_asset(
                    short_id, "image" if got["kind"] == "image" else "video",
                    f"shorts/{short_id}/stock_media/{local.name}",
                    local, got["kind"], engine="pexels",
                    meta={"attribution": got["attribution"]})
                stock_plan[seq] = {"path": local, "kind": got["kind"],
                                   "attribution": got["attribution"],
                                   "asset_id": asset_id}

        bank_pool = bank.list_bank_images()
        bank_plan = {}  # seq -> Path|None (chỉ scene KHÔNG dùng ảnh bài/stock)
        ai_plan = {}    # seq -> cần gen AI
        ai_count = 0
        for sc in scenes:
            seq = int(sc["seq_num"])
            if seq in per_scene_article or seq <= len(article_paths) or seq in stock_plan:
                continue  # scene này đã có visual từ nguồn ưu tiên cao hơn
            if mode == "daily_digest":
                gen_here = image_gen is not None and ((fast and ai_count < hero_n) or not fast)
            else:
                gen_here = image_gen is not None and ((fast and seq <= hero_n) or not fast)
            if gen_here:
                ai_plan[seq] = True
                ai_count += 1
            else:
                if mode == "daily_digest":
                    # Luật: bắt buộc liên quan mới đưa dô — KHÔNG lấy random bank nếu lệch chủ đề tin
                    continue
                if not bank_pool:
                    if not per_scene_article and not article_paths and not stock_plan and not ai_plan:
                        raise ValueError(
                            "Không có nguồn ảnh: không lấy được ảnh gốc từ bài báo "
                            "(kiểm tra source_link), chưa cấu hình adapter image-gen "
                            "(VIDEO_XKIRO_API_KEY), thiếu VIDEO_PEXELS_API_KEY và bank "
                            "ảnh trống tại data/shorts_bank/.")
                    if image_gen is None:
                        ai_plan.pop(seq, None)
                else:
                    bank_plan[seq] = bank.assign_for(short_id, seq)

        images_dir = objects / "images"
        image_paths: dict[int, str | None] = {}
        clip_paths: dict[int, str | None] = {}
        for sc in scenes:
            seq = int(sc["seq_num"])
            image_asset_id: str | None = None
            asset_kind, attribution = "image", None
            img_path, used_bank = None, 0
            if seq in per_scene_multi:
                pm = per_scene_multi[seq]
                img_path, image_asset_id = pm["paths"][0], pm["asset_id"]
                attribution = pm.get("attribution") or None
                used_bank = 0
            elif seq in per_scene_article:
                img_path, image_asset_id = per_scene_article[seq]
                used_bank = 0
            elif seq <= len(article_paths):
                img_path, used_bank = article_paths[seq - 1], 0
                image_asset_id = article_asset_ids.get(img_path.name)
            elif seq in stock_plan:
                sp = stock_plan[seq]
                img_path, used_bank = sp["path"], 0
                image_asset_id, asset_kind = sp["asset_id"], sp["kind"]
                attribution = sp["attribution"]
                if sp["kind"] == "video_clip":
                    img_path = None  # clip không phải ảnh — scene_image_path không trỏ vào
            elif seq in bank_plan:
                img_path, used_bank = bank_plan[seq], 1
            elif seq in ai_plan:
                out = images_dir / f"scene_{seq:03d}.png"
                res = image_gen.generate_image(sc["image_prompt"], str(out), size="1080x1920")
                img_path, used_bank = Path(res["path"]), 0
                image_asset_id = self._put_asset(
                    short_id, "image",
                    f"shorts/{short_id}/images/scene_{seq:03d}{img_path.suffix.lower()}",
                    img_path, "image", engine="xkiro-image")
            # else: thẻ màu — honest
            image_paths[seq] = str(img_path) if img_path else None
            clip_paths[seq] = (str(stock_plan[seq]["path"])
                               if seq in stock_plan and stock_plan[seq]["kind"] == "video_clip"
                               else None)
            self.store.execute(
                "UPDATE short_scenes SET image_asset_id=?, bank_used=?, asset_kind=?, "
                "attribution=?, updated_at=? WHERE short_id=? AND seq_num=?",
                (image_asset_id, used_bank, asset_kind, attribution,
                 now(), short_id, seq),
            )

        # 4) Karaoke subtitles đã build trong _ensure_voice_timeline

        # 5) Assemble + QC fail-closed
        scene_render = [
            {"seq": int(sc["seq_num"]), "text": sc["text"],
             "start_sec": float(sc["start_sec"]), "end_sec": float(sc["end_sec"]),
             "image_path": image_paths.get(int(sc["seq_num"])),
             "image_paths": (
                 [str(p) for p in per_scene_multi[int(sc["seq_num"])]["paths"]]
                 if int(sc["seq_num"]) in per_scene_multi
                 else ([image_paths[int(sc["seq_num"])]] if image_paths.get(int(sc["seq_num"])) else [])
             ),
             "clip_path": clip_paths.get(int(sc["seq_num"]))}
            for sc in scenes
        ]
        out_dir = objects / "exports"
        preset = _render_preset(mode, lang)
        overlay_spec: dict[str, Any] = {
            "preset": preset,
            "hook_headline": str(script.get("title") or row.get("title") or "").strip(),
            "source_note": str(script.get("source_note") or row.get("source_note") or "").strip(),
            "mood": "energetic" if (lang == "en" or mode in {"decode", "daily_digest"}) else "ominous",
        }
        if mode == "daily_digest":
            stories_meta = list(script.get("stories") or [])
            digest_scenes: list[dict] = []
            for sc in scenes:
                seq = int(sc["seq_num"])
                if seq == 1:
                    s_idx = 0
                    meta = {}
                elif 2 <= seq <= len(stories_meta) + 1:
                    s_idx = seq - 1
                    meta = stories_meta[s_idx - 1]
                else:
                    s_idx = len(stories_meta) + 1
                    meta = {}
                digest_scenes.append({
                    "seq": seq,
                    "story_idx": s_idx,
                    "start_sec": float(sc["start_sec"]),
                    "end_sec": float(sc["end_sec"]),
                    "category": meta.get("category", "WORLD"),
                    "headline": meta.get("headline", ""),
                    "hero_stat": meta.get("hero_stat", ""),
                    "hero_sub": meta.get("hero_sub", ""),
                })
            overlay_spec["digest_spec"] = {
                "brand_header": str(script.get("brand_header") or "@STICKSTO · DAILY BRIEF"),
                "signature": str(script.get("signature") or "@StickSto"),
                "total_stories": len(stories_meta) or max(1, len(scenes) - 2),
                "scenes": digest_scenes,
            }
            overlay_spec["watermark"] = {
                "text": str(script.get("signature") or "@StickSto"),
                "opacity": 0.22,
                "font_size": 36,
            }
            overlay_spec["whoosh_times"] = [
                float(sc["start_sec"]) for sc in scenes[1:] if float(sc["start_sec"]) > 0.3
            ]
        result = assembler.assemble_short(short_id, scene_render, voice["audio_path"],
                                          voice["words"], str(ass_path), out_dir,
                                          overlay_spec=overlay_spec)
        video_path = Path(result["video_path"])
        video_ast = self._put_asset(short_id, "final",
                                    f"shorts/{short_id}/exports/short_{short_id}.mp4",
                                    video_path, "video", duration_s=result["duration"],
                                    engine="short-assembler",
                                    meta={"probe": result.get("probe"),
                                          "bgm_used": result.get("bgm_used", False)})

        # 6) Publish pack (gói đăng bài)
        publish = _publish_markdown(script, voice["duration"], lang)
        publish_path = objects / f"publish_{short_id}.md"
        publish_path.write_text(publish, encoding="utf-8")
        pub_ast = self._put_asset(short_id, "json",
                                  f"shorts/{short_id}/publish_{short_id}.md", publish_path,
                                  "json", engine="publish-pack")

        # audio/words/ass/srt asset ids đã ghi bởi _ensure_voice_timeline
        self.store.execute(
            "UPDATE shorts SET video_asset_id=?, publish_md_asset_id=?, duration_s=?, "
            "updated_at=? WHERE id=?",
            (video_ast, pub_ast, result["duration"], now(), short_id),
        )
        return {"duration": result["duration"], "scenes_count": len(scenes),
                "video_path": str(video_path)}

    # ------------------------------------------------------------------ job (G.13/G.14)
    def publish_job(self, short_id: str, actor: str = "api") -> dict:
        """Phát hành job cho agent video ngoài (cả VI & EN): TTS + timeline
        + karaoke subs + prompt chi tiết từng shot → status queued."""
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        if row["status"] != domain.ShortStatus.SCRIPTED.value:
            raise ValueError(
                f"Chỉ phát hành job từ trạng thái 'scripted' (hiện: {row['status']}).")
        if (row.get("audio_source") or "tts") == "owner":
            # Audio + scene owner đã nạp sẵn — KHÔNG gọi _ensure_voice_timeline
            # (hàm đó DELETE short_scenes và gọi TTS). Chỉ kiểm coverage.
            scenes = self.store.query(
                "SELECT * FROM short_scenes WHERE short_id=? ORDER BY seq_num",
                (short_id,))
            if not scenes:
                raise ValueError("Short dùng audio owner nhưng chưa có scene — nạp audio trước.")
            bad = [int(sc["seq_num"]) for sc in scenes
                   if float(sc["end_sec"]) <= float(sc["start_sec"])]
            if bad:
                raise ValueError(
                    f"Scene {bad} có timing không hợp lệ (end_sec <= start_sec) — "
                    "nạp lại audio + shot.")
            duration = float(row.get("duration_s") or 0.0)
            self._transition(short_id, domain.ShortStatus.QUEUED.value,
                             reason="job phát hành từ audio owner")
            self._event(short_id, "short.job_ready", actor,
                        {"scenes": len(scenes), "duration_sec": round(duration, 3),
                         "audio_source": "owner"})
            return self.get_job(short_id)
        voice, scenes, _ass, _srt = self._ensure_voice_timeline(short_id, row)
        self._transition(short_id, domain.ShortStatus.QUEUED.value,
                         reason="job đã phát hành cho agent video ngoài")
        self._event(short_id, "short.job_ready", actor,
                    {"scenes": len(scenes), "duration_sec": round(voice["duration"], 3)})
        return self.get_job(short_id)

    def _job_progress(self, short_id: str, scenes: list[dict]) -> dict:
        clips = self.store.query(
            "SELECT * FROM short_clips WHERE short_id=? ORDER BY seq_num", (short_id,))
        covered: set[int] = set()
        covered_dur = 0.0
        for c in clips:
            try:
                idxs = json.loads(c["covered_scene_indexes"] or "[]")
            except json.JSONDecodeError:
                idxs = []
            for ix in idxs:
                covered.add(int(ix))
        audio_dur = max((float(sc["end_sec"]) for sc in scenes), default=0.0)
        for sc in scenes:
            if int(sc["seq_num"]) in covered:
                covered_dur += float(sc["generated_duration_sec"] or 0.0)
        missing = [int(sc["seq_num"]) for sc in scenes if int(sc["seq_num"]) not in covered]
        # Tolerance cộng dồn ±0.25s/scene (cùng luật QC per-shot của video dài) —
        # làm tròn frame encoder khiến mỗi clip ngắn hơn window một chút.
        tol = 0.25 * max(1, len(scenes))
        return {
            "clips_uploaded": len(clips),
            "scenes_total": len(scenes),
            "scenes_covered": len(scenes) - len(missing),
            "missing_scene_indexes": missing,
            "total_duration_sec": round(audio_dur, 3),
            "covered_duration_sec": round(covered_dur, 3),
            "shortfall_sec": round(max(0.0, audio_dur - covered_dur), 3),
            "is_duration_sufficient": bool(scenes) and not missing
                                      and covered_dur >= audio_dur - tol,
        }

    def get_job(self, short_id: str) -> dict:
        """Package đầy đủ cho agent video ngoài (audio + timing + template)."""
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        script = json.loads(row["script_json"] or "{}")
        scenes = self.store.query(
            "SELECT * FROM short_scenes WHERE short_id=? ORDER BY seq_num", (short_id,))
        clips = self.store.query(
            "SELECT * FROM short_clips WHERE short_id=? ORDER BY seq_num", (short_id,))
        lang = row.get("lang") or "vi"
        style = "cinematic" if row["mode"] == "cinematic" else "news"
        kw_en_list = list(script.get("search_keywords_en") or [])
        headline_hint = str(script.get("title") or row.get("title") or "").strip()
        scene_rows = []
        for sc in scenes:
            seq = int(sc["seq_num"])
            dur_s = round(float(sc["end_sec"]) - float(sc["start_sec"]), 3)
            kw = (kw_en_list[seq - 1].strip() if seq - 1 < len(kw_en_list) else "") or stock_media.keywords_for_query(sc["text"])
            shot_type = "hook" if seq == 1 else ("closing" if seq == len(scenes) else "beat")
            detailed_prompt = skill_bundle_short.build_detailed_shot_prompt(
                shot_type, seq, sc["text"], dur_s,
                lang=lang, headline_hint=headline_hint, stock_keywords=kw,
                style=style,
            )
            scene_rows.append({
                "seq": seq,
                "narration": sc["text"],
                "audio_start_sec": float(sc["start_sec"]),
                "audio_end_sec": float(sc["end_sec"]),
                "duration_sec": dur_s,
                "visual_prompt": detailed_prompt,
                "stock_search_keywords": kw,
                "sub_shots": skill_bundle_short.slice_sub_shots(
                    seq, sc["text"], float(sc["start_sec"]), float(sc["end_sec"]),
                    lang=lang, headline_hint=headline_hint, stock_keywords=kw,
                    style=style,
                ),
                "agent_checked": bool(sc["agent_checked"]),
                "generated_duration_sec": float(sc["generated_duration_sec"] or 0.0),
            })
        clip_rows = []
        for c in clips:
            clip_rows.append({
                "seq_num": int(c["seq_num"]),
                "duration_sec": float(c["duration_sec"] or 0.0),
                "width": int(c["width"] or 0), "height": int(c["height"] or 0),
                "fps": float(c["fps"] or 0.0),
                "covered_scene_indexes": json.loads(c["covered_scene_indexes"] or "[]"),
                "note": c["note"],
                "file_url": f"/api/v1/shorts/{short_id}/clips/{int(c['seq_num'])}/file",
            })
        base = f"/api/v1/shorts/{short_id}/job"
        return {
            "short_id": short_id,
            "mode": row["mode"],
            "lang": row.get("lang") or "vi",
            "title": row["title"],
            "status": row["status"],
            "template": skill_bundle_short.get_short_skill_summary(style=style),
            "script": script,
            "scenes": scene_rows,
            "clips": clip_rows,
            "progress": self._job_progress(short_id, scenes),
            "audio_url": f"{base}/audio",
            "subtitles_ass_url": f"/api/v1/shorts/{short_id}/subtitles.ass",
            "subtitles_srt_url": f"/api/v1/shorts/{short_id}/subtitles.srt",
            "guide_url": "/api/v1/short-agent-guide",
            "submit_url": f"/api/v1/shorts/{short_id}/clips",
            "assemble_url": f"/api/v1/shorts/{short_id}/assemble",
        }

    def _probe_clip(self, path: Path) -> dict:
        """Probe video submit: registry.probe (test) else ffprobe thật."""
        try:
            probe = providers.registry.require_probe()
        except providers.AdapterNotConfigured:
            probe = None
        if probe is not None:
            info = probe.probe_video(str(path))
            dur = info.get("duration", info.get("duration_sec", 0.0))
        else:
            info = assembler._probe_video(path)
            dur = info.get("duration", 0.0)
        return {
            "duration_sec": float(dur or 0.0),
            "width": int(info.get("width") or 0),
            "height": int(info.get("height") or 0),
            "fps": float(info.get("fps") or 0.0),
        }

    def submit_clip(
        self,
        short_id: str,
        seq_num: int | None = None,
        file_path: Path | None = None,
        file_url: str | None = None,
        raw_bytes: bytes | None = None,
        filename: str | None = None,
        covered_scene_indexes: list[int] | None = None,
        note: str | None = None,
        actor: str = "external_api",
    ) -> dict:
        """Agent submit 1 clip cho scene seq_num (hoặc gộp nhiều scene)."""
        from .service_import import fetch_url, sha256_file
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        if row["status"] not in {domain.ShortStatus.QUEUED.value,
                                 domain.ShortStatus.RENDERING.value}:
            raise ValueError(
                f"Short chưa phát hành job (status: {row['status']}) — gọi publish-job trước.")
        if raw_bytes is None and file_path is None and not file_url:
            raise ValueError("Cần file upload, file_path hoặc file_url.")

        scenes = self.store.query(
            "SELECT seq_num FROM short_scenes WHERE short_id=? ORDER BY seq_num",
            (short_id,))
        total_scenes = len(scenes)
        if seq_num is None:
            row_max = self.store.query_one(
                "SELECT MAX(seq_num) m FROM short_clips WHERE short_id=?", (short_id,))
            seq_num = int(row_max["m"] or 0) + 1
        seq_num = int(seq_num)
        if not 1 <= seq_num <= max(1, total_scenes):
            raise ValueError(f"seq_num phải trong 1..{max(1, total_scenes)}.")

        clips_dir = data_dir() / "objects" / "shorts" / short_id / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        dest_path = clips_dir / f"seq_{seq_num:04d}.mp4"
        max_mb = 512
        if raw_bytes is not None:
            if len(raw_bytes) > max_mb * 1024 * 1024:
                raise ValueError(f"Clip vượt giới hạn {max_mb}MB.")
            dest_path.write_bytes(raw_bytes)
        elif file_path is not None:
            resolved = Path(file_path).resolve()
            sandbox = data_dir().resolve()
            if sandbox not in resolved.parents and resolved != sandbox:
                raise ValueError("file_path phải nằm trong VIDEO_DATA_DIR.")
            if not resolved.exists():
                raise ValueError(f"file_path không tồn tại: {file_path}")
            if resolved != dest_path.resolve():
                shutil.copy2(resolved, dest_path)
        else:
            fetch_url(file_url, dest_path, max_mb * 1024 * 1024)

        info = self._probe_clip(dest_path)
        dur_sec = info["duration_sec"]
        if dur_sec <= 0:
            dest_path.unlink(missing_ok=True)
            raise ValueError("Clip không đọc được duration (file hỏng?)")

        target_scenes = [int(x) for x in (covered_scene_indexes or []) if int(x) >= 1]
        if not target_scenes:
            target_scenes = [seq_num]
        scene_durs = {
            int(sc["seq_num"]):
                max(0.0, float(sc["end_sec"]) - float(sc["start_sec"]))
            for sc in self.store.query(
                "SELECT seq_num, start_sec, end_sec FROM short_scenes WHERE short_id=?",
                (short_id,))
        }
        matched = [ix for ix in target_scenes if ix in scene_durs]
        if not matched:
            dest_path.unlink(missing_ok=True)
            raise ValueError(
                f"covered_scene_indexes không khớp scene nào: {target_scenes}.")

        t = now()
        ast_id = new_id("ast")
        storage_key = f"shorts/{short_id}/clips/seq_{seq_num:04d}.mp4"
        v_sha = sha256_file(dest_path)
        self.store.execute(
            "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,duration_s,"
            "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (ast_id, None, "video", storage_key, v_sha,
             dest_path.stat().st_size, dur_sec, "external_video", t),
        )
        exists = self.store.query_one(
            "SELECT id FROM short_clips WHERE short_id=? AND seq_num=?",
            (short_id, seq_num))
        if exists:
            self.store.execute(
                "UPDATE short_clips SET video_asset_id=?, filename=?, sha256=?, "
                "duration_sec=?, width=?, height=?, fps=?, covered_scene_indexes=?, "
                "note=?, updated_at=? WHERE short_id=? AND seq_num=?",
                (ast_id, filename or dest_path.name, v_sha, dur_sec,
                 info["width"], info["height"], info["fps"],
                 json.dumps(target_scenes), note, t, short_id, seq_num),
            )
        else:
            self.store.execute(
                "INSERT INTO short_clips (id, short_id, seq_num, video_asset_id, filename, "
                "sha256, duration_sec, width, height, fps, covered_scene_indexes, note, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id("scl"), short_id, seq_num, ast_id, filename or dest_path.name,
                 v_sha, dur_sec, info["width"], info["height"], info["fps"],
                 json.dumps(target_scenes), note, t, t),
            )

        # Gán agent_checked + generated_duration_sec pro-rata cho scene được phủ
        grp_exp = sum(scene_durs[ix] for ix in matched)
        for ix in matched:
            s_dur = (round(dur_sec, 3) if len(matched) == 1
                     else round(dur_sec * (scene_durs[ix] / grp_exp), 3) if grp_exp > 0
                     else round(dur_sec / len(matched), 3))
            self.store.execute(
                "UPDATE short_scenes SET agent_checked=1, generated_duration_sec=?, "
                "updated_at=? WHERE short_id=? AND seq_num=?",
                (s_dur, t, short_id, ix),
            )

        if row["status"] == domain.ShortStatus.QUEUED.value:
            self._transition(short_id, domain.ShortStatus.RENDERING.value,
                             reason="agent bắt đầu submit clip")
        self._event(short_id, "short.clip_submitted", actor,
                    {"seq_num": seq_num, "duration_sec": dur_sec,
                     "covered_scene_indexes": matched})
        progress = self._job_progress(short_id, self.store.query(
            "SELECT * FROM short_scenes WHERE short_id=? ORDER BY seq_num", (short_id,)))
        return {"short_id": short_id, "seq_num": seq_num, "progress": progress,
                "covered_scene_indexes": matched}

    def delete_clip(self, short_id: str, seq_num: int, actor: str = "api") -> None:
        row = self.store.query_one("SELECT * FROM short_clips WHERE short_id=? AND seq_num=?",
                                   (short_id, seq_num))
        if not row:
            raise KeyError(short_id)
        covered = [int(x) for x in json.loads(row["covered_scene_indexes"] or "[]")]
        self.store.execute("DELETE FROM short_clips WHERE short_id=? AND seq_num=?",
                           (short_id, seq_num))
        for ix in covered:
            self.store.execute(
                "UPDATE short_scenes SET agent_checked=0, generated_duration_sec=0.0, "
                "updated_at=? WHERE short_id=? AND seq_num=?",
                (now(), short_id, ix),
            )
        self._event(short_id, "short.clip_deleted", actor, {"seq_num": seq_num})

    def assemble_job(self, short_id: str, actor: str = "api") -> dict:
        """Mux clips của agent + voice + karaoke + BGM → final MP4 (QC fail-closed)."""
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        if row["status"] not in {domain.ShortStatus.RENDERING.value,
                                 domain.ShortStatus.QUEUED.value}:
            raise ValueError(
                f"Chỉ assemble từ job đã phát hành (hiện: {row['status']}).")
        if row["status"] == domain.ShortStatus.QUEUED.value:
            # đã có đủ coverage (submit_clip cuối chuyển QUEUED→RENDERING rồi,
            # nhánh này cho submit lập tức sau publish) — chuẩn hóa trước ready
            self._transition(short_id, domain.ShortStatus.RENDERING.value)
        script = json.loads(row["script_json"] or "{}")
        lang = row.get("lang") or "vi"
        scenes = self.store.query(
            "SELECT * FROM short_scenes WHERE short_id=? ORDER BY seq_num", (short_id,))
        clips = self.store.query(
            "SELECT * FROM short_clips WHERE short_id=? ORDER BY seq_num", (short_id,))
        progress = self._job_progress(short_id, scenes)
        if not progress["is_duration_sufficient"]:
            raise ShortfallError(progress)

        objects = tts_vi.objects_root(short_id)
        audio_path = self.audio_path(short_id)
        ass_path = self.ass_path(short_id)
        audio_dur = assembler._wav_duration(audio_path)

        spec = RENDER_SPEC_SHORT["video"]
        width, height, fps = int(spec["width"]), int(spec["height"]), int(spec["fps"])
        crf = str(spec.get("crf", 20))
        tol = float(RENDER_SPEC_SHORT["qc"]["duration_tolerance_sec"])

        out_dir = objects / "exports"
        final_path = out_dir / f"short_{short_id}.mp4"
        tmp = out_dir / "_tmp_job"
        tmp.mkdir(parents=True, exist_ok=True)

        if not assembler._has_ffmpeg():
            header = (
                f"SHORT_MP4_CONTAINER|short={short_id}|scenes={len(scenes)}|"
                f"dur={audio_dur:.3f}|geometry={width}x{height}@{fps}|burn_subs=True|"
                f"source=external-agent\n"
            ).encode("utf-8")
            final_path.write_bytes(header + b"MP4_STREAM_DATA" * 200)
            duration = round(audio_dur, 3)
        else:
            # 1) Normalize từng clip về scene window (trim + scale 9:16 + mute).
            #    Clip gộp nhiều scene: chia duration pro-rata theo scene window.
            norm_clips: list[Path] = []
            for sc in scenes:
                seq = int(sc["seq_num"])
                dur = max(0.5, float(sc["end_sec"]) - float(sc["start_sec"]))
                clip_row = None
                for c in clips:  # clip phủ scene này (ưu tiên seq cao nhất = mới nhất)
                    idxs = json.loads(c["covered_scene_indexes"] or "[]")
                    if seq in [int(x) for x in idxs]:
                        clip_row = c
                if clip_row is None:
                    raise ValueError(f"Scene #{seq} không có clip nào phủ (coverage lệch).")
                covered = sorted(int(x) for x in json.loads(
                    clip_row["covered_scene_indexes"] or "[]"))
                clip_dur = float(clip_row["duration_sec"] or 0.0)
                cov_durs = []
                for ix in covered:
                    s = next(s for s in scenes if int(s["seq_num"]) == ix)
                    cov_durs.append(max(0.5, float(s["end_sec"]) - float(s["start_sec"])))
                grp = sum(cov_durs) or clip_dur
                # offset trong clip = tổng duration (pro-rata) các scene TRƯỚC scene này
                pos = covered.index(seq)
                seek = sum(cov_durs[:pos]) * (clip_dur / grp) if grp > 0 else 0.0
                alloc = clip_dur * (cov_durs[pos] / grp) if grp > 0 else clip_dur
                alloc = min(alloc, clip_dur - seek) if clip_dur > 0 else dur
                layer = tmp / f"layer_{seq:03d}.mp4"
                src = data_dir() / "objects" / (
                    self.store.query_one("SELECT storage_key FROM assets WHERE id=?",
                                         (clip_row["video_asset_id"],))["storage_key"])
                cmd = ["ffmpeg", "-y", "-v", "error",
                       "-stream_loop", "-1",  # clip ngắn hơn window → loop pad đủ
                       "-ss", f"{max(0.0, seek):.3f}",
                       "-t", f"{max(0.5, min(alloc, dur)):.3f}", "-i", str(src),
                       "-vf", (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                               f"crop={width}:{height},setsar=1,fps={fps}"),
                       "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                       "-pix_fmt", "yuv420p", str(layer)]
                subprocess.run(cmd, check=True, timeout=300)
                norm_clips.append(layer)
            # 2) Concat
            concat_list = tmp / "concat_list.txt"
            concat_list.write_text("\n".join(
                f"file '{p.resolve().as_posix()}'" for p in norm_clips), encoding="utf-8")
            joined = tmp / "joined.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                            "-i", str(concat_list), "-c", "copy", str(joined)],
                           check=True, timeout=300)
            # 3) Mix voice + BGM theo mood + Impact/Whoosh SFX
            mixed = tmp / "mixed.wav"
            preset = _render_preset(row["mode"], lang)
            mood = "energetic" if (lang == "en" or row["mode"] == "decode") else "ominous"
            whoosh_times = ([float(scenes[0]["end_sec"]), max(0.4, float(scenes[-1]["start_sec"]) - 0.1)]
                            if len(scenes) >= 2 else [])
            bgm_used = assembler._mix_bgm_bed(Path(audio_path), mixed, audio_dur, short_id,
                                              mood=mood, whoosh_times=whoosh_times)
            # 4) Burn TikTok Overlay (Pillow RGBA safe-zone) hoặc fallback ASS + mux
            words_ast_row = self.store.query_one("SELECT storage_key FROM assets WHERE id=?", (row["words_asset_id"],)) if row.get("words_asset_id") else None
            words_list = []
            if words_ast_row:
                wp = data_dir() / "objects" / words_ast_row["storage_key"]
                if wp.exists():
                    words_list = json.loads(wp.read_text(encoding="utf-8"))
            from .pipeline_short import overlay_renderer
            used_overlay = False
            try:
                used_overlay = overlay_renderer.render_overlay_and_mux(
                    joined, mixed, audio_dur, words_list, final_path, tmp,
                    preset=preset,
                    hook_headline=str(script.get("title") or row.get("title") or "").strip(),
                    source_note=str(script.get("source_note") or row.get("source_note") or "").strip(),
                    crf=crf,
                    audio_rate=int(RENDER_SPEC_SHORT["audio"]["rate"]),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("job overlay_renderer fallback ASS (%s): %s", short_id, exc)
                used_overlay = False
            if not used_overlay:
                ass_escaped = Path(ass_path).resolve().as_posix().replace(":", r"\:")
                cmd_final = [
                    "ffmpeg", "-y", "-v", "error",
                    "-i", str(joined), "-i", str(mixed),
                    "-vf", f"ass='{ass_escaped}'",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    "-ar", str(RENDER_SPEC_SHORT["audio"]["rate"]),
                    "-movflags", "+faststart",
                    "-t", f"{audio_dur:.3f}",
                    str(final_path),
                ]
                subprocess.run(cmd_final, check=True, timeout=600)
            probe = assembler._probe_video(final_path)
            problems = []
            if int(probe["width"]) != width or int(probe["height"]) != height:
                problems.append(f"sai độ phân giải {probe['width']}x{probe['height']}")
            if abs(probe["fps"] - fps) > 0.5:
                problems.append(f"sai fps {probe['fps']}")
            if probe["codec"] != spec["codec"]:
                problems.append(f"sai codec {probe['codec']}")
            if abs(probe["duration"] - audio_dur) > tol:
                problems.append(
                    f"thời lượng {probe['duration']:.2f}s lệch audio {audio_dur:.2f}s "
                    f"quá ±{tol}s")
            if problems:
                raise ValueError("QC Short job thất bại: " + "; ".join(problems))
            duration = round(probe["duration"], 3)
            shutil.rmtree(tmp, ignore_errors=True)

        video_ast = self._put_asset(short_id, "final",
                                    f"shorts/{short_id}/exports/short_{short_id}.mp4",
                                    final_path, "video", duration_s=duration,
                                    engine="external-agent-mux",
                                    meta={"source": "agent_clips"})
        publish = _publish_markdown(script, duration, lang)
        publish_path = objects / f"publish_{short_id}.md"
        publish_path.write_text(publish, encoding="utf-8")
        pub_ast = self._put_asset(short_id, "json",
                                  f"shorts/{short_id}/publish_{short_id}.md",
                                  publish_path, "json", engine="publish-pack")
        self.store.execute(
            "UPDATE shorts SET video_asset_id=?, publish_md_asset_id=?, duration_s=?, "
            "updated_at=? WHERE id=?",
            (video_ast, pub_ast, duration, now(), short_id),
        )
        self._transition(short_id, domain.ShortStatus.READY.value)
        self._event(short_id, "short.ready", actor,
                    {"duration_sec": duration, "source": "external-agent",
                     "video_url": f"/api/v1/shorts/{short_id}/video"})
        return {"short_id": short_id, "duration_sec": duration,
                "video_url": f"/api/v1/shorts/{short_id}/video"}

    # ------------------------------------------------------------------ manual upload
    @staticmethod
    def _probe_video_duration(path: Path) -> float:
        try:
            res = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True, text=True, timeout=15, check=True,
            )
            val = float((res.stdout or "").strip() or 0.0)
            return round(max(0.0, val), 2)
        except Exception:  # noqa: BLE001
            return 0.0

    def upload_final_video(self, short_id: str, raw_bytes: bytes,
                           filename: str = "short.mp4",
                           actor: str = "ui") -> dict:
        """Upload trực tiếp file MP4 thành phẩm cho một Short đã tồn tại."""
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
        if not raw_bytes:
            raise ValueError("File video rỗng.")
        ext = Path(filename or "short.mp4").suffix.lower()
        if ext and ext not in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
            raise ValueError(f"Định dạng không hỗ trợ ({ext}). Chỉ nhận MP4/MOV/WEBM/MKV.")

        objects = data_dir() / "objects" / "shorts" / short_id
        objects.mkdir(parents=True, exist_ok=True)
        final_path = objects / f"final_{short_id}.mp4"
        final_path.write_bytes(raw_bytes)

        duration = self._probe_video_duration(final_path)
        video_ast = self._put_asset(
            short_id, "video", f"shorts/{short_id}/final_{short_id}.mp4",
            final_path, "video", duration_s=duration,
            engine="manual-upload", meta={"filename": filename or "short.mp4"},
        )

        pub_ast = row["publish_md_asset_id"]
        if not pub_ast:
            script = json.loads(row["script_json"] or "{}")
            lang = row["lang"] or "vi"
            publish = _publish_markdown(script, duration, lang)
            publish_path = objects / f"publish_{short_id}.md"
            publish_path.write_text(publish, encoding="utf-8")
            pub_ast = self._put_asset(
                short_id, "json", f"shorts/{short_id}/publish_{short_id}.md",
                publish_path, "json", engine="publish-pack",
            )

        self.store.execute(
            "UPDATE shorts SET video_asset_id=?, publish_md_asset_id=?, duration_s=?, "
            "error_detail=NULL, updated_at=? WHERE id=?",
            (video_ast, pub_ast, duration, now(), short_id),
        )
        self._transition(short_id, domain.ShortStatus.READY.value)
        self._event(short_id, "short.ready", actor, {
            "duration_sec": duration,
            "source": "manual-upload",
            "filename": filename or "short.mp4",
            "video_url": f"/api/v1/shorts/{short_id}/video",
        })
        return self.get(short_id)

    def create_from_script(self, *, hook: str, beats: list[str], closing: str = "",
                           title: str = "", mode: str = "cinematic", lang: str = "en",
                           description: str = "", source_note: str = "",
                           search_keywords_en: list[str] | None = None,
                           actor: str = "api") -> dict:
        """Tạo short từ kịch bản owner đã viết — KHÔNG gọi LLM, KHÔNG áp word budget.

        Khác `create()`: không qua `prompts.validate_script` (hàm đó cắt beat còn 4
        và chặn theo ngân sách từ), nên giữ được kịch bản dài nhiều shot như file
        markdown owner tự soạn. Chỉ kiểm tra cấu trúc + giới hạn `max_scenes`.
        """
        if mode not in prompts.MODES:
            raise ValueError(f"mode không hợp lệ: {mode} (chấp nhận: {', '.join(prompts.MODES)})")
        if lang not in prompts.LANGS:
            raise ValueError(f"lang không hợp lệ: {lang} (chấp nhận: {', '.join(prompts.LANGS)})")
        hook = (hook or "").strip()
        closing = (closing or "").strip()
        clean_beats = [str(b).strip() for b in (beats or []) if str(b).strip()]
        if not hook:
            raise ValueError("Thiếu 'hook' (câu mở đầu).")
        if not clean_beats and not closing:
            raise ValueError("Cần ít nhất 1 beat hoặc closing.")
        total = 1 + len(clean_beats) + (1 if closing else 0)
        max_scenes = int(SHORT_CONFIG["limits"]["max_scenes"])
        if total > max_scenes:
            raise ValueError(
                f"Kịch bản {total} đoạn vượt giới hạn {max_scenes} scene/short.")

        script = {
            "title": (title or "").strip()[:200] or hook[:120],
            "hook": hook,
            "beats": clean_beats,
            "closing": closing,
            "description": (description or "").strip(),
            "source_note": (source_note or "").strip(),
            "search_keywords_en": [str(k).strip() for k in (search_keywords_en or [])
                                   if str(k).strip()],
            "word_count": sum(len(t.split()) for t in [hook, *clean_beats, closing] if t),
        }
        short_id = new_id("sho")
        t = now()
        self.store.execute(
            "INSERT INTO shorts (id, mode, lang, title, status, script_json, source_text, "
            "source_note, source_link, target_seconds, duration_s, word_count, "
            "error_detail, audio_source, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (short_id, mode, lang, script["title"], domain.ShortStatus.SCRIPTED.value,
             json.dumps(script, ensure_ascii=False), hook, script["source_note"] or None,
             None, None, 0.0, int(script["word_count"]), None, "tts", t, t),
        )
        self._sync_scenes(short_id, script, lang=lang)
        self._event(short_id, "short.created", actor,
                    {"mode": mode, "lang": lang, "title": script["title"],
                     "source": "owner-script", "segments": total})
        return self.get(short_id)

    def create_from_audio(self, *, raw_bytes: bytes, filename: str = "audio.mp3",
                          shots: list[dict], title: str = "", mode: str = "cinematic",
                          lang: str = "vi", actor: str = "ui") -> dict:
        """Tạo short trực tiếp từ audio + shot list của owner (KHÔNG gọi LLM).

        Kịch bản dựng từ chính text của shot (hook = shot đầu, closing = shot cuối)
        để các đường đọc `script_json` (publish markdown, danh sách) không vỡ.
        """
        if mode not in prompts.MODES:
            raise ValueError(f"mode không hợp lệ: {mode} (chấp nhận: {', '.join(prompts.MODES)})")
        if lang not in prompts.LANGS:
            raise ValueError(f"lang không hợp lệ: {lang} (chấp nhận: {', '.join(prompts.LANGS)})")
        texts = [str(s.get("text") or "").strip() for s in (shots or [])]
        texts = [t for t in texts if t]
        if not texts:
            raise ValueError("Danh sách shot rỗng — cần ít nhất 1 shot có nội dung.")

        clean_title = (title or "").strip()[:200] or texts[0][:120]
        script = {
            "title": clean_title,
            "hook": texts[0],
            "beats": texts[1:-1] if len(texts) > 2 else texts[1:],
            "closing": texts[-1] if len(texts) > 1 else "",
            "description": "",
            "source_note": "",
            "word_count": sum(len(t.split()) for t in texts),
        }
        short_id = new_id("sho")
        t = now()
        self.store.execute(
            "INSERT INTO shorts (id, mode, lang, title, status, script_json, source_text, "
            "source_note, source_link, target_seconds, duration_s, word_count, "
            "error_detail, audio_source, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (short_id, mode, lang, clean_title, domain.ShortStatus.SCRIPTED.value,
             json.dumps(script, ensure_ascii=False), clean_title, None, None,
             None, 0.0, int(script["word_count"]), None, "tts", t, t),
        )
        self._event(short_id, "short.created", actor,
                    {"mode": mode, "lang": lang, "title": clean_title,
                     "source": "audio-upload"})
        return self.ingest_audio(short_id, raw_bytes=raw_bytes, filename=filename,
                                 shots=shots, actor=actor)

    def create_from_upload(self, title: str, mode: str = "news", lang: str = "vi",
                           raw_bytes: bytes = b"", filename: str = "short.mp4",
                           source_note: str = "", description: str = "",
                           actor: str = "ui") -> dict:
        """Tạo Short mới trực tiếp từ file MP4 thành phẩm (không cần gọi LLM)."""
        if mode not in prompts.MODES:
            raise ValueError(f"mode không hợp lệ: {mode} (chấp nhận: {', '.join(prompts.MODES)})")
        if lang not in prompts.LANGS:
            raise ValueError(f"lang không hợp lệ: {lang} (chấp nhận: {', '.join(prompts.LANGS)})")
        if not raw_bytes:
            raise ValueError("Thiếu file video (.mp4) để upload.")

        clean_title = (title or "").strip()[:200] or Path(filename or "Short Video").stem[:200]
        short_id = new_id("sho")
        t = now()
        script = {
            "title": clean_title,
            "hook": "",
            "beats": [],
            "closing": "",
            "description": (description or "").strip(),
            "source_note": (source_note or "").strip(),
            "word_count": 0,
        }
        self.store.execute(
            "INSERT INTO shorts (id, mode, lang, title, status, script_json, source_text, "
            "source_note, source_link, target_seconds, duration_s, word_count, "
            "error_detail, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (short_id, mode, lang, clean_title, domain.ShortStatus.SCRIPTED.value,
             json.dumps(script, ensure_ascii=False), clean_title,
             (source_note or "").strip(), None, None, 0.0, 0, None, t, t),
        )
        self._event(short_id, "short.created", actor, {
            "mode": mode, "lang": lang, "title": clean_title, "source": "manual-upload",
        })
        return self.upload_final_video(short_id, raw_bytes=raw_bytes, filename=filename, actor=actor)

    # ------------------------------------------------------------------ misc
    def _put_asset(self, short_id: str, kind: str, storage_key: str, path: Path,
                   assets_kind: str, duration_s: float | None = None,
                   engine: str = "", meta: dict | None = None) -> str:
        from .service_import import sha256_file
        ast_id = new_id("ast")
        t = now()
        self.store.execute(
            "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,duration_s,"
            "provenance_engine,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ast_id, None, assets_kind, storage_key,
             sha256_file(path) if path.exists() else None,
             path.stat().st_size if path.exists() else 0,
             duration_s, engine,
             json.dumps(meta, ensure_ascii=False) if meta else None, t),
        )
        return ast_id

    def cancel(self, short_id: str, actor: str = "api") -> dict:
        self._transition(short_id, domain.ShortStatus.CANCELLED.value,
                         reason="cancelled by admin")
        self._event(short_id, "short.cancelled", actor, {})
        return self.get(short_id)

    # ------------------------------------------------------------------ files
    def _ensure_local(self, p: Path) -> Path:
        """Kéo file từ Drive nếu unit đã archive; không đổi gì khi file có sẵn."""
        try:
            from .service_archive import ensure_local
            ensure_local(self.store, p)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ensure_local lỗi cho %s: %s", p, exc)
        return p

    def _resolve(self, short_id: str, asset_col: str) -> Path:
        row = self.store.query_one(f"SELECT {asset_col} FROM shorts WHERE id=?", (short_id,))
        if not row or not row[asset_col]:
            raise FileNotFoundError(f"Short chưa có artifact cho {asset_col}.")
        ast = self.store.query_one("SELECT storage_key FROM assets WHERE id=?",
                                   (row[asset_col],))
        if not ast:
            raise FileNotFoundError("Asset row không tồn tại.")
        p = self._ensure_local(data_dir() / "objects" / ast["storage_key"])
        if not p.exists():
            raise FileNotFoundError(f"File artifact không tồn tại: {p}")
        return p

    def audio_path(self, short_id: str) -> Path:
        return self._resolve(short_id, "audio_asset_id")

    def video_path(self, short_id: str) -> Path:
        return self._resolve(short_id, "video_asset_id")

    def ass_path(self, short_id: str) -> Path:
        return self._resolve(short_id, "ass_asset_id")

    def srt_path(self, short_id: str) -> Path:
        return self._resolve(short_id, "srt_asset_id")

    def publish_path(self, short_id: str) -> Path:
        return self._resolve(short_id, "publish_md_asset_id")

    def words_path(self, short_id: str) -> Path:
        return self._resolve(short_id, "words_asset_id")

    def clip_file_path(self, short_id: str, seq_num: int) -> Path:
        """File clip agent submit (G.13) — resolve qua assets."""
        row = self.store.query_one(
            "SELECT video_asset_id FROM short_clips WHERE short_id=? AND seq_num=?",
            (short_id, seq_num))
        if not row or not row["video_asset_id"]:
            raise KeyError(short_id)
        ast = self.store.query_one("SELECT storage_key FROM assets WHERE id=?",
                                   (row["video_asset_id"],))
        if not ast:
            raise KeyError(short_id)
        p = data_dir() / "objects" / ast["storage_key"]
        self._ensure_local(p)
        if not p.exists():
            raise FileNotFoundError(f"File clip không tồn tại: {p}")
        return p

    def scene_image_path(self, short_id: str, seq: int) -> Path:
        """Đường dẫn ảnh scene: ảnh gen qua assets; ảnh bank resolve lại từ bank."""
        row = self.store.query_one(
            "SELECT image_asset_id, bank_used FROM short_scenes WHERE short_id=? AND seq_num=?",
            (short_id, seq),
        )
        if not row:
            raise FileNotFoundError(f"Scene #{seq} không tồn tại.")
        if row["image_asset_id"]:
            ast = self.store.query_one("SELECT storage_key FROM assets WHERE id=?",
                                       (row["image_asset_id"],))
            if ast:
                p = data_dir() / "objects" / ast["storage_key"]
                self._ensure_local(p)
                if p.exists():
                    return p
        if row["bank_used"]:
            from .pipeline_short import bank as _bank
            cand = _bank.assign_for(short_id, seq)
            if cand and cand.exists():
                return cand
        raise FileNotFoundError(f"Scene #{seq} chưa có ảnh (chưa produce hoặc thiếu bank).")


# --------------------------------------------------------------------- helpers
def _render_preset(mode: str, lang: str) -> str:
    """Preset overlay theo mode — cinematic dùng karaoke-only (không badge/headline)."""
    presets = SHORT_CONFIG.get("render_presets") or {}
    if mode == "cinematic":
        return presets.get("cinematic", "clean_karaoke")
    if mode == "daily_digest":
        return "daily_digest"
    return (presets.get("en_default", "split_news_60") if lang == "en"
            else presets.get("vi_default", "viral_redpill"))


def _words_from_shots(shots: list[dict]) -> list[dict]:
    """Word timeline cho audio owner: chia đều từ trong từng shot window.

    Dùng lại công thức offline của TTS (`_cue_words_offline`) — cùng semantics:
    mỗi từ nhận `duration / len(words)`. Clamp `cue_start` về sau từ cuối của
    shot trước để `start` tăng dần tuyệt đối kể cả khi window chồng lấn ≤ 0.05s.
    """
    words: list[dict] = []
    for shot in shots:
        prev_end = words[-1]["end"] if words else 0.0
        cue_start = max(float(shot["start_sec"]), prev_end)
        dur = float(shot["end_sec"]) - cue_start
        if dur <= 0:
            continue
        for w in tts_vi._cue_words_offline(str(shot["text"]), cue_start, dur):
            if w["end"] <= w["start"]:
                continue
            words.append(w)
    return words


def _image_prompt_for(short_id: str, seq: int, text: str, lang: str = "vi",
                      mode: str = "news") -> str:
    """Prompt fallback khi không có ảnh gốc/bank (G.11: KHÔNG dùng style crayon
    của bên video dài — news: phong cách tin tức trung tính; cinematic: editorial
    điện ảnh kể chuyện)."""
    snippet = text.strip()[:140].replace("\n", " ")
    if mode == "cinematic":
        base = (
            "Cinematic editorial visual for a vertical storytelling short, film-like "
            "grade, shallow depth of field, volumetric light, muted palette with one "
            "focal accent colour, natural atmosphere, no text in image, no watermark, "
            "no logo. "
        )
        if lang == "en":
            return base + "Scene: " + snippet
        return base + "Ý cảnh: " + snippet
    base = (
        "Professional news editorial visual, photojournalistic or clean modern "
        "flat illustration, muted realistic palette, suitable for a business "
        "news short video, no text in image, no watermark, no logo. "
    )
    if lang == "en":
        # EN: giữ toàn bộ prompt tiếng Anh — keyword tìm stock/AI-gen chuẩn quốc tế.
        return base + "Scene: " + snippet
    return base + "Ý cảnh: " + snippet


def _publish_markdown(script: dict, duration: float, lang: str = "vi") -> str:
    tags = script.get("hashtags") or {}
    if lang == "en":
        lines = [
            f"# {script.get('title', '')}",
            "",
            script.get("description") or "",
            "",
            f"Duration: {duration:.1f}s",
        ]
        source_prefixes = ("source",)
        source_label = "Source"
    else:
        lines = [
            f"# {script.get('title', '')}",
            "",
            script.get("description") or "",
            "",
            f"Thời lượng: {duration:.1f}s",
        ]
        source_prefixes = ("nguồn", "nguon")
        source_label = "Nguồn"
    if script.get("source_note"):
        note = str(script["source_note"]).strip()
        # tránh lặp "Nguồn: Nguồn: ..." khi source_note đã tự mang tiền tố
        if not note.lower().startswith(source_prefixes):
            note = f"{source_label}: {note}"
        lines += ["", note]
    if tags.get("tiktok"):
        lines += ["", "## TikTok", " ".join(tags["tiktok"])]
    if tags.get("facebook"):
        lines += ["", "## Facebook", " ".join(tags["facebook"])]
    if tags.get("youtube"):
        lines += ["", "## YouTube Shorts", " ".join(tags["youtube"])]
    return "\n".join(lines) + "\n"
