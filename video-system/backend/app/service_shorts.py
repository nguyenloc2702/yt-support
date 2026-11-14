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
from .pipeline_short import article_images, stock_media
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

    # ------------------------------------------------------------------ create
    def create(self, mode: str, source_text: str, source_note: str = "",
               source_link: str = "", target_seconds: float | None = None,
               title: str | None = None, lang: str = "vi",
               actor: str = "api") -> dict:
        if mode not in prompts.MODES:
            raise ValueError(f"mode không hợp lệ: {mode} (chấp nhận: {', '.join(prompts.MODES)})")
        if lang not in prompts.LANGS:
            raise ValueError(f"lang không hợp lệ: {lang} (chấp nhận: {', '.join(prompts.LANGS)})")
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
            prompts.system_prompt(mode, lang),
            prompts.user_prompt(mode, source_text, target_seconds, source_note, lang),
            prompts.schema(),
        )
        script = prompts.validate_script(mode, raw, source_note_fallback=source_note,
                                         lang=lang)

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
        self._sync_scenes(short_id, script, lang=lang)
        self._transition(short_id, domain.ShortStatus.SCRIPTED.value,
                         reason="kịch bản đã tạo")  # draft → scripted (assert DRAFT→SCRIPTED)
        self._event(short_id, "short.created", actor,
                    {"mode": mode, "lang": lang, "title": display_title,
                     "word_count": script.get("word_count")})
        return self.get(short_id)

    # ------------------------------------------------------------------ scenes
    def _sync_scenes(self, short_id: str, script: dict, lang: str = "vi") -> None:
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
                 _image_prompt_for(short_id, i, text, lang), t, t),
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
        self._sync_scenes(short_id, validated, lang=lang)
        if row["status"] == domain.ShortStatus.FAILED.value:
            self._transition(short_id, domain.ShortStatus.SCRIPTED.value)  # failed → scripted
        self._event(short_id, "short.script_updated", actor,
                    {"word_count": validated.get("word_count")})
        return self.get(short_id)

    # ------------------------------------------------------------------ produce
    def _ensure_voice_timeline(self, short_id: str, row: dict) -> tuple[dict, list[dict], Path, Path]:
        """Voice EN/VI + word timeline + scene windows + karaoke subs (G.13).

        Dùng chung cho 2 luồng: auto produce (ảnh tĩnh) và job mode (agent
        ngoài gen video). Idempotent — gọi lại thì overwrite artifacts.
        Trả: (voice, scenes, ass_path, srt_path).
        """
        script = json.loads(row["script_json"] or "{}")
        lang = row.get("lang") or "vi"
        texts = prompts.narration_texts(script)
        objects = tts_vi.objects_root(short_id)

        voice = tts_vi.synthesize_short_voice(texts, objects, short_id, lang=lang)
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
                 _image_prompt_for(short_id, i, text, lang), t, t),
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

        # 3b) Stock Pexels (ảnh + video b-roll) — cần VIDEO_PEXELS_API_KEY.
        #     File cache dùng chung (sha1(url)) → copy vào objects của short
        #     để asset model nhất quán (scene_image_path resolve được).
        stock_plan: dict[int, dict] = {}  # seq -> {path, kind, attribution, asset_id}
        if stock_media.is_configured():
            stock_dir = objects / "stock_media"
            stock_dir.mkdir(parents=True, exist_ok=True)
            kw_en_list = list(script.get("search_keywords_en") or [])
            used_stock_paths: set[str] = set()
            for sc in scenes:
                seq = int(sc["seq_num"])
                if seq <= len(article_paths):
                    continue  # scene này đã có ảnh gốc bài báo
                q_en = kw_en_list[seq - 1].strip() if seq - 1 < len(kw_en_list) else ""
                q = q_en or stock_media.keywords_for_query(sc["text"])
                if not q:
                    continue
                got = None
                try:
                    # Ưu tiên video b-roll động trước, sau đó mới fallback ảnh tĩnh
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
        for sc in scenes:
            seq = int(sc["seq_num"])
            if seq <= len(article_paths) or seq in stock_plan:
                continue  # scene này đã có visual từ nguồn ưu tiên cao hơn
            gen_here = image_gen is not None and ((fast and seq <= hero_n) or not fast)
            if gen_here:
                ai_plan[seq] = True
            else:
                if not bank_pool:
                    if not article_paths and not stock_plan:
                        raise ValueError(
                            "Không có nguồn ảnh: không lấy được ảnh gốc từ bài báo "
                            "(kiểm tra source_link), chưa cấu hình adapter image-gen "
                            "(VIDEO_XKIRO_API_KEY), thiếu VIDEO_PEXELS_API_KEY và bank "
                            "ảnh trống tại data/shorts_bank/.")
                    if image_gen is None:
                        # có nguồn thấp hơn nhưng không thể gen → thẻ màu
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
            if seq <= len(article_paths):
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
             "clip_path": clip_paths.get(int(sc["seq_num"]))}
            for sc in scenes
        ]
        out_dir = objects / "exports"
        presets = SHORT_CONFIG.get("render_presets") or {}
        preset = presets.get("en_default", "split_news_60") if lang == "en" else presets.get("vi_default", "viral_redpill")
        overlay_spec = {
            "preset": preset,
            "hook_headline": str(script.get("title") or row.get("title") or "").strip(),
            "source_note": str(script.get("source_note") or row.get("source_note") or "").strip(),
            "mood": "energetic" if (lang == "en" or mode == "decode") else "ominous",
        }
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
            "template": skill_bundle_short.get_short_skill_summary(),
            "script": script,
            "scenes": scene_rows,
            "clips": clip_rows,
            "progress": self._job_progress(short_id, scenes),
            "audio_url": f"{base}/audio",
            "subtitles_ass_url": f"{base}/subtitles.ass",
            "subtitles_srt_url": f"{base}/subtitles.srt",
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
            presets = SHORT_CONFIG.get("render_presets") or {}
            preset = presets.get("en_default", "split_news_60") if lang == "en" else presets.get("vi_default", "viral_redpill")
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
                if p.exists():
                    return p
        if row["bank_used"]:
            from .pipeline_short import bank as _bank
            cand = _bank.assign_for(short_id, seq)
            if cand and cand.exists():
                return cand
        raise FileNotFoundError(f"Scene #{seq} chưa có ảnh (chưa produce hoặc thiếu bank).")


# --------------------------------------------------------------------- helpers
def _image_prompt_for(short_id: str, seq: int, text: str, lang: str = "vi") -> str:
    """Prompt fallback khi không có ảnh gốc/bank (G.11: KHÔNG dùng style crayon
    của bên video dài — phong cách tin tức trung tính)."""
    snippet = text.strip()[:140].replace("\n", " ")
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
