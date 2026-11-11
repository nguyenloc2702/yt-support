"""Short Studio service (Phụ lục G) — điều phối kịch bản → TTS → ảnh → MP4 9:16.

House style: mọi chuyển trạng thái qua `assert_short_transition`, artifacts ghi
bảng `assets` (item_id NULL, storage_key `shorts/{short_id}/...`), events ghi
bảng `events` + dispatch webhook (type `short.*` — subscription chưa map thì bỏ qua).
Fail-closed: thiếu adapter → AdapterNotConfigured nổi lên tận endpoint (503).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import domain, providers
from .config import SHORT_CONFIG, data_dir
from .pipeline_short import assembler, bank, karaoke, prompts, tts_vi
from .pipeline_short import article_images
from .store import Store, new_id, now

logger = logging.getLogger("video_system.service_shorts")


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

    # ------------------------------------------------------------------ create
    def create(self, mode: str, source_text: str, source_note: str = "",
               source_link: str = "", target_seconds: float | None = None,
               title: str | None = None,
               actor: str = "api") -> dict:
        if mode not in prompts.MODES:
            raise ValueError(f"mode không hợp lệ: {mode} (chấp nhận: {', '.join(prompts.MODES)})")
        max_chars = int(SHORT_CONFIG["limits"]["max_source_chars"])
        if not source_text or not source_text.strip():
            raise ValueError("Thiếu nội dung nguồn (source_text) — dán tin vắn hoặc chủ đề.")
        if len(source_text) > max_chars:
            raise ValueError(f"Nội dung nguồn {len(source_text)} ký tự vượt giới hạn {max_chars}.")
        if target_seconds is not None and not (10 <= float(target_seconds) <= 90):
            raise ValueError("target_seconds phải trong khoảng 10–90s.")
        source_link = (source_link or "").strip()
        if source_link and not source_link.lower().startswith(("http://", "https://")):
            raise ValueError("source_link phải là URL http(s) của bài báo.")

        llm = providers.registry.require_llm()  # fail-closed AdapterNotConfigured
        raw = llm.chat_json(
            prompts.system_prompt(mode),
            prompts.user_prompt(mode, source_text, target_seconds, source_note),
            prompts.schema(),
        )
        script = prompts.validate_script(mode, raw, source_note_fallback=source_note)

        short_id = new_id("sho")
        t = now()
        display_title = (title or script["title"]).strip()[:200]
        self.store.execute(
            "INSERT INTO shorts (id, mode, title, status, script_json, source_text, "
            "source_note, source_link, target_seconds, duration_s, word_count, "
            "error_detail, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (short_id, mode, display_title, domain.ShortStatus.DRAFT.value,
             json.dumps(script, ensure_ascii=False), source_text,
             script.get("source_note") or source_note, source_link or None,
             target_seconds, 0.0, int(script.get("word_count") or 0), None, t, t),
        )
        self._sync_scenes(short_id, script)
        self._transition(short_id, domain.ShortStatus.SCRIPTED.value,
                         reason="kịch bản đã tạo")  # draft → scripted (assert DRAFT→SCRIPTED)
        self._event(short_id, "short.created", actor,
                    {"mode": mode, "title": display_title,
                     "word_count": script.get("word_count")})
        return self.get(short_id)

    # ------------------------------------------------------------------ scenes
    def _sync_scenes(self, short_id: str, script: dict) -> None:
        """Scene = 1 cue thoại; image_prompt phong cách Vector/Crayon dọc 9:16."""
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
                 _image_prompt_for(short_id, i, text), t, t),
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
        if with_scenes:
            short["scenes"] = self.store.query(
                "SELECT * FROM short_scenes WHERE short_id=? ORDER BY seq_num", (row["id"],))
        return short

    # ------------------------------------------------------------------ script
    def update_script(self, short_id: str, patch: dict, actor: str = "api") -> dict:
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
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
            script["beats"] = beats[:4]
        mode = row["mode"]
        validated = prompts.validate_script(mode, script,
                                            source_note_fallback=row.get("source_note") or "")
        self.store.execute(
            "UPDATE shorts SET script_json=?, title=?, word_count=?, updated_at=? WHERE id=?",
            (json.dumps(validated, ensure_ascii=False),
             (patch.get("title") or validated["title"]).strip()[:200],
             int(validated.get("word_count") or 0), now(), short_id),
        )
        self._sync_scenes(short_id, validated)
        if row["status"] == domain.ShortStatus.FAILED.value:
            self._transition(short_id, domain.ShortStatus.SCRIPTED.value)  # failed → scripted
        self._event(short_id, "short.script_updated", actor,
                    {"word_count": validated.get("word_count")})
        return self.get(short_id)

    # ------------------------------------------------------------------ produce
    def produce(self, short_id: str, actor: str = "api") -> dict:
        row = self.store.query_one("SELECT * FROM shorts WHERE id=?", (short_id,))
        if not row:
            raise KeyError(short_id)
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
        texts = prompts.narration_texts(script)
        objects = tts_vi.objects_root(short_id)

        # 1) Voice + word timeline
        voice = tts_vi.synthesize_short_voice(texts, objects, short_id)
        audio_ast = self._put_asset(short_id, "audio", voice["object_key"],
                                    Path(voice["audio_path"]), "audio",
                                    duration_s=voice["duration"],
                                    engine="edge-tts" if not voice["offline"] else "offline-synth",
                                    meta={"voice": voice["voice"]})
        words_obj = objects / "audio" / f"words_{short_id}.json"
        words_obj.write_text(json.dumps(voice["words"], ensure_ascii=False), encoding="utf-8")
        words_ast = self._put_asset(short_id, "json", f"shorts/{short_id}/audio/words_{short_id}.json",
                                    words_obj, "json", engine="word-timeline")

        # 2) Scene windows từ cue thực tế
        spans = voice["cue_spans"]
        self.store.execute("DELETE FROM short_scenes WHERE short_id=?", (short_id,))
        t = now()
        for i, text in enumerate(texts, start=1):
            span = spans[i - 1] if i - 1 < len(spans) else {"start": 0.0, "end": voice["duration"]}
            self.store.execute(
                "INSERT INTO short_scenes (id, short_id, seq_num, text, start_sec, end_sec, "
                "image_prompt, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id("ssc"), short_id, i, text, float(span["start"]), float(span["end"]),
                 _image_prompt_for(short_id, i, text), t, t),
            )
        scenes = self.store.query(
            "SELECT * FROM short_scenes WHERE short_id=? ORDER BY seq_num", (short_id,))

        # 3) Nguồn ảnh mỗi scene — thứ tự (G.11): ảnh gốc bài báo → bank → AI-gen
        #    → thẻ màu. Không adapter + bank trống + không có ảnh bài → lỗi
        #    tiếng người (không placeholder).
        img_cfg = SHORT_CONFIG["modes"][mode]
        image_gen = None
        try:
            image_gen = providers.registry.require_image_gen()
        except providers.AdapterNotConfigured:
            image_gen = None
        fast = bool(img_cfg.get("fast_mode"))
        hero_n = int(img_cfg.get("hero_images") or 0) if (fast and image_gen) else 0

        # 3a) Ảnh gốc bài báo (nếu có link) — ưu tiên cao nhất
        article_paths: list[Path] = []
        source_link = (row.get("source_link") or "").strip()
        if source_link:
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

        bank_pool = bank.list_bank_images()
        bank_plan = {}  # seq -> Path|None (chỉ scene KHÔNG dùng ảnh bài)
        ai_plan = {}    # seq -> cần gen AI
        for sc in scenes:
            seq = int(sc["seq_num"])
            if seq <= len(article_paths):
                continue  # scene này dùng ảnh gốc bài báo
            gen_here = image_gen is not None and ((fast and seq <= hero_n) or not fast)
            if gen_here:
                ai_plan[seq] = True
            else:
                if not bank_pool:
                    if not article_paths:
                        raise ValueError(
                            "Không có nguồn ảnh: không lấy được ảnh gốc từ bài báo "
                            "(kiểm tra source_link), chưa cấu hình adapter image-gen "
                            "(VIDEO_XKIRO_API_KEY) và bank ảnh trống tại data/shorts_bank/.")
                    if image_gen is None:
                        # có ảnh bài nhưng không đủ phủ, không thể gen → thẻ màu
                        ai_plan.pop(seq, None)
                else:
                    bank_plan[seq] = bank.assign_for(short_id, seq)

        images_dir = objects / "images"
        image_paths: dict[int, str | None] = {}
        for sc in scenes:
            seq = int(sc["seq_num"])
            image_asset_id: str | None = None
            if seq <= len(article_paths):
                img_path, used_bank = article_paths[seq - 1], 0
                image_asset_id = article_asset_ids.get(img_path.name)
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
            else:
                img_path, used_bank = None, 0  # thẻ màu — honest
            image_paths[seq] = str(img_path) if img_path else None
            self.store.execute(
                "UPDATE short_scenes SET image_asset_id=?, bank_used=?, updated_at=? "
                "WHERE short_id=? AND seq_num=?",
                (image_asset_id, used_bank, now(), short_id, seq),
            )

        # 4) Karaoke subtitles
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

        # 5) Assemble + QC fail-closed
        scene_render = [
            {"seq": int(sc["seq_num"]), "text": sc["text"],
             "start_sec": float(sc["start_sec"]), "end_sec": float(sc["end_sec"]),
             "image_path": image_paths.get(int(sc["seq_num"]))}
            for sc in scenes
        ]
        out_dir = objects / "exports"
        result = assembler.assemble_short(short_id, scene_render, voice["audio_path"],
                                          voice["words"], str(ass_path), out_dir)
        video_path = Path(result["video_path"])
        video_ast = self._put_asset(short_id, "final",
                                    f"shorts/{short_id}/exports/short_{short_id}.mp4",
                                    video_path, "video", duration_s=result["duration"],
                                    engine="short-assembler",
                                    meta={"probe": result.get("probe"),
                                          "bgm_used": result.get("bgm_used", False)})

        # 6) Publish pack (gói đăng bài)
        publish = _publish_markdown(script, voice["duration"])
        publish_path = objects / f"publish_{short_id}.md"
        publish_path.write_text(publish, encoding="utf-8")
        pub_ast = self._put_asset(short_id, "json",
                                  f"shorts/{short_id}/publish_{short_id}.md", publish_path,
                                  "json", engine="publish-pack")

        self.store.execute(
            "UPDATE shorts SET audio_asset_id=?, words_asset_id=?, srt_asset_id=?, "
            "ass_asset_id=?, video_asset_id=?, publish_md_asset_id=?, duration_s=?, "
            "updated_at=? WHERE id=?",
            (audio_ast, words_ast, srt_ast, ass_ast, video_ast, pub_ast,
             result["duration"], now(), short_id),
        )
        return {"duration": result["duration"], "scenes_count": len(scenes),
                "video_path": str(video_path)}

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
    def _resolve(self, short_id: str, asset_col: str) -> Path:
        row = self.store.query_one(f"SELECT {asset_col} FROM shorts WHERE id=?", (short_id,))
        if not row or not row[asset_col]:
            raise FileNotFoundError(f"Short chưa có artifact cho {asset_col}.")
        ast = self.store.query_one("SELECT storage_key FROM assets WHERE id=?",
                                   (row[asset_col],))
        if not ast:
            raise FileNotFoundError("Asset row không tồn tại.")
        p = data_dir() / "objects" / ast["storage_key"]
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
                if p.exists():
                    return p
        if row["bank_used"]:
            from .pipeline_short import bank as _bank
            cand = _bank.assign_for(short_id, seq)
            if cand and cand.exists():
                return cand
        raise FileNotFoundError(f"Scene #{seq} chưa có ảnh (chưa produce hoặc thiếu bank).")


# --------------------------------------------------------------------- helpers
def _image_prompt_for(short_id: str, seq: int, text: str) -> str:
    """Prompt fallback khi không có ảnh gốc/bank (G.11: KHÔNG dùng style crayon
    của bên video dài — phong cách tin tức trung tính)."""
    snippet = text.strip()[:140].replace("\n", " ")
    return (
        "Professional news editorial visual, photojournalistic or clean modern "
        "flat illustration, muted realistic palette, suitable for a business "
        "news short video, no text in image, no watermark, no logo. "
        "Ý cảnh: " + snippet
    )


def _publish_markdown(script: dict, duration: float) -> str:
    tags = script.get("hashtags") or {}
    lines = [
        f"# {script.get('title', '')}",
        "",
        script.get("description") or "",
        "",
        f"Thời lượng: {duration:.1f}s",
    ]
    if script.get("source_note"):
        note = str(script["source_note"]).strip()
        # tránh lặp "Nguồn: Nguồn: ..." khi source_note đã tự mang tiền tố
        if not note.lower().startswith("nguồn") and not note.lower().startswith("nguon"):
            note = f"Nguồn: {note}"
        lines += ["", note]
    if tags.get("tiktok"):
        lines += ["", "## TikTok", " ".join(tags["tiktok"])]
    if tags.get("facebook"):
        lines += ["", "## Facebook", " ".join(tags["facebook"])]
    if tags.get("youtube"):
        lines += ["", "## YouTube Shorts", " ".join(tags["youtube"])]
    return "\n".join(lines) + "\n"
