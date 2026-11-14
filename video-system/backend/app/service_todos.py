"""TODO Queue & External Video Submission Service (100% EN Pipeline).

Workflow:
1. Pre-production generates standardized English Audio (`audio_en.wav`) +
   standardized English Script (`script_en.json` with exact per-shot timing)
   -> Item lands in `todo` status.
2. External video generation system pulls the TODO package (`GET /api/v1/todos`
   or `GET /api/v1/todos/{item_id}`) to obtain the script shots and audio file.
3. External system pushes generated videos back in sequential order (`seq_num` = 1, 2, 3...).
   Multiple script shots may be merged into a single video clip.
4. System computes cumulative video duration vs English Audio duration and emits
   an explicit warning (`shortfall_warning`) if all uploaded videos combined are
   still shorter than the English Audio.
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("service_todos")

from . import domain
from .config import RENDER_SPEC, data_dir
from .pipeline_en.audio_generator_en import (
    probe_media_duration,
    probe_video_info,
    synthesize_en_todo_package,
    write_subtitles_en,
)
from .pipeline_en.script_generator_en import (
    build_en_shots_from_transcript_segments,
    normalize_english_text,
    split_script_text_into_shots,
)
from .pipeline_en.skill_bundle import get_full_skill_bundle, get_skill_summary
from .pipeline_en.youtube_ingest import download_youtube_transcript_en
from .providers import registry
from .service_import import fetch_url, sha256_file
from .service_items import ItemService
from .store import Store, new_id, now


class TodoService:
    def __init__(self, store: Store, items: ItemService) -> None:
        self.store = store
        self.items = items

    # -- Pre-production: Create TODO from Script or YouTube ------------------

    def create_from_script(
        self,
        title: str,
        script_text: str | None = None,
        shots: list[dict[str, Any]] | None = None,
        shots_input: list[dict[str, Any]] | None = None,
        bgm_enabled: bool = True,
        voice: str | None = None,
        collect_references: bool = True,
        actor: str = "api",
    ) -> dict[str, Any]:
        """Generate EN Script + EN Audio from English text or shots array and push to TODO."""
        shots_list = shots if shots is not None else shots_input
        if not (script_text and script_text.strip()) and not shots_list:
            raise ValueError("Either script_text or shots must be provided")

        item = self.items.create(
            title=title.strip(),
            type_="script_en",
            language="en",
            style_lock_version="v3",
        )
        item_id = item["id"]
        self.store.execute(
            "UPDATE items SET bgm_enabled=?, updated_at=? WHERE id=?",
            (1 if bgm_enabled else 0, now(), item_id),
        )
        self.items.transition(item_id, domain.ItemStatus.PROCESSING_INPUT.value, actor=actor,
                              reason="synthesizing EN script and audio")

        try:
            if shots_list:
                raw_shots: list[dict[str, Any]] = []
                total = len(shots_list)
                for i, s in enumerate(shots_list, start=1):
                    narr = normalize_english_text(str(s.get("narration") or s.get("text") or ""))
                    if not narr:
                        continue
                    vp = str(s.get("visual_prompt") or s.get("visual_prompt_clean") or "").strip()
                    if not vp:
                        vp = f"Clean 2D flat vector documentary illustration (1280x720) for scene #{i}/{total}: {narr}"
                    raw_shots.append({
                        "index": int(s.get("index") or i),
                        "shot_type": str(s.get("shot_type") or "scene"),
                        "narration": narr,
                        "visual_prompt": vp,
                        "on_screen_text": s.get("on_screen_text"),
                        "on_screen_type": s.get("on_screen_type"),
                    })
                if not raw_shots:
                    raise ValueError("No valid English narration found in shots")
            else:
                raw_shots = split_script_text_into_shots(script_text or "")

            return self._finalize_preproduction_to_todo(
                item_id=item_id,
                raw_shots=raw_shots,
                bgm_enabled=bgm_enabled,
                voice=voice,
                collect_references=collect_references,
                actor=actor,
            )
        except Exception as exc:
            self.store.execute(
                "UPDATE items SET error_detail=?, updated_at=? WHERE id=?",
                (str(exc), now(), item_id),
            )
            self.items.transition(item_id, domain.ItemStatus.FAILED.value, actor=actor,
                                  reason=str(exc))
            raise

    def create_from_youtube(
        self,
        title: str | None,
        youtube_url: str,
        target_duration_sec: float | None = None,
        bgm_enabled: bool = True,
        voice: str | None = None,
        transcript_segments: list[dict[str, Any]] | None = None,
        collect_references: bool = True,
        actor: str = "api",
    ) -> dict[str, Any]:
        """Extract EN transcript from YouTube -> generate EN Script + EN Audio -> push to TODO."""
        if not youtube_url or not youtube_url.strip():
            raise ValueError("youtube_url is required")

        item = self.items.create(
            title=(title or "YouTube EN Documentary").strip(),
            type_="youtube_en",
            language="en",
            style_lock_version="v3",
        )
        item_id = item["id"]
        self.store.execute(
            "UPDATE items SET source_url=?, bgm_enabled=?, updated_at=? WHERE id=?",
            (youtube_url.strip(), 1 if bgm_enabled else 0, now(), item_id),
        )
        self.items.transition(item_id, domain.ItemStatus.PROCESSING_INPUT.value, actor=actor,
                              reason="extracting YouTube transcript & generating EN audio")

        try:
            work_dir = data_dir() / "objects" / item_id / "ingest"
            if transcript_segments is None:
                yt_info = download_youtube_transcript_en(youtube_url.strip(), work_dir)
                segments = yt_info["segments"]
                if not title and yt_info.get("title"):
                    self.store.execute(
                        "UPDATE items SET title=?, updated_at=? WHERE id=?",
                        (yt_info["title"], now(), item_id),
                    )
            else:
                segments = transcript_segments

            raw_shots = build_en_shots_from_transcript_segments(
                segments, target_duration_sec=target_duration_sec
            )
            return self._finalize_preproduction_to_todo(
                item_id=item_id,
                raw_shots=raw_shots,
                bgm_enabled=bgm_enabled,
                voice=voice,
                collect_references=collect_references,
                actor=actor,
            )
        except Exception as exc:
            self.store.execute(
                "UPDATE items SET error_detail=?, updated_at=? WHERE id=?",
                (str(exc), now(), item_id),
            )
            self.items.transition(item_id, domain.ItemStatus.FAILED.value, actor=actor,
                                  reason=str(exc))
            raise

    def _finalize_preproduction_to_todo(
        self,
        item_id: str,
        raw_shots: list[dict[str, Any]],
        bgm_enabled: bool,
        voice: str | None,
        collect_references: bool = True,
        actor: str = "api",
    ) -> dict[str, Any]:
        todo_dir = data_dir() / "objects" / item_id / "todo"
        pkg = synthesize_en_todo_package(
            shots=raw_shots,
            out_dir=todo_dir,
            bgm_enabled=bgm_enabled,
            voice=voice,
        )
        audio_path: Path = pkg["audio_path"]
        script_path: Path = pkg["script_path"]
        srt_path: Path = pkg["srt_path"]
        ass_path: Path = pkg["ass_path"]
        audio_dur: float = pkg["audio_duration_sec"]
        timed_shots: list[dict[str, Any]] = pkg["shots"]

        audio_sha = sha256_file(audio_path)
        script_sha = sha256_file(script_path)
        srt_sha = sha256_file(srt_path)
        ass_sha = sha256_file(ass_path)
        shots_hash = domain.canonical_shots_hash(timed_shots)
        t = now()

        audio_ast_id = new_id("ast")
        script_ast_id = new_id("ast")
        srt_ast_id = new_id("ast")
        ass_ast_id = new_id("ast")

        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,duration_s,"
                "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    audio_ast_id, item_id, "audio",
                    f"items/{item_id}/todo/audio_en.wav",
                    audio_sha, audio_path.stat().st_size, audio_dur,
                    "edge-tts-en", t,
                ),
            )
            conn.execute(
                "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,"
                "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    script_ast_id, item_id, "script",
                    f"items/{item_id}/todo/script_en.json",
                    script_sha, script_path.stat().st_size,
                    "script-generator-en", t,
                ),
            )
            conn.execute(
                "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,"
                "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    srt_ast_id, item_id, "subtitle",
                    f"items/{item_id}/todo/subtitles_en.srt",
                    srt_sha, srt_path.stat().st_size,
                    "subtitle-builder-en", t,
                ),
            )
            conn.execute(
                "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,"
                "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    ass_ast_id, item_id, "subtitle",
                    f"items/{item_id}/todo/subtitles_en.ass",
                    ass_sha, ass_path.stat().st_size,
                    "subtitle-builder-en", t,
                ),
            )
            conn.execute(
                "INSERT INTO scripts (id,item_id,version,language,text,scenes_json,"
                "hash,shots_hash,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    new_id("scr"), item_id, 1, "en",
                    "\n".join(s["narration"] for s in timed_shots),
                    json.dumps(timed_shots, ensure_ascii=False),
                    script_sha, shots_hash, "active", t,
                ),
            )
            conn.execute(
                "INSERT INTO audios (id,item_id,version,kind,audio_asset_id,"
                "duration_sec,voice_id,hash,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    new_id("aud"), item_id, 1, "master", audio_ast_id,
                    audio_dur, pkg["voice"], audio_sha, t,
                ),
            )
            for s in timed_shots:
                conn.execute(
                    "INSERT INTO shots (id,item_id,script_version,index_num,shot_type,"
                    "narration,audio_start_sec,audio_end_sec,visual_prompt,"
                    "on_screen_text,on_screen_type,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_id("shot"), item_id, 1, s["index"], s["shot_type"],
                        s["narration"], s["audio_start_sec"], s["audio_end_sec"],
                        s["visual_prompt"], s.get("on_screen_text"), s.get("on_screen_type"),
                        domain.ShotStatus.PENDING.value, t, t,
                    ),
                )
            conn.execute(
                "UPDATE items SET total_shots=?, audio_duration_sec=?, "
                "audio_asset_id=?, script_asset_id=?, subtitle_srt_asset_id=?, "
                "subtitle_ass_asset_id=?, error_detail=NULL, updated_at=? WHERE id=?",
                (
                    len(timed_shots), audio_dur,
                    audio_ast_id, script_ast_id, srt_ast_id, ass_ast_id,
                    t, item_id,
                ),
            )

        self.items.transition(
            item_id, domain.ItemStatus.TODO.value, actor=actor,
            reason="EN audio and timed EN script ready in TODO queue",
        )
        self.items.event(
            item_id, None, "todo.ready", actor,
            {
                "total_shots": len(timed_shots),
                "audio_duration_sec": audio_dur,
                "shots_hash": shots_hash,
            },
        )
        if collect_references:
            try:
                self.collect_script_references(item_id=item_id, count=15, actor=actor)
            except Exception as exc:
                logger.warning("Auto-collect script references failed for %s: %s", item_id, exc)

        return self.get_todo(item_id)

    def import_existing_package(
        self,
        title: str,
        audio_src_path: Path,
        shots: list[dict[str, Any]],
        source_url: str | None = None,
        bgm_enabled: bool = True,
        voice: str = "en-US-AndrewMultilingualNeural",
        actor: str = "api",
        clean_voice_src_path: Path | None = None,
    ) -> dict[str, Any]:
        """Import a pre-existing EN job (already having audio + timed shots) directly into `todo`."""
        if not audio_src_path.exists():
            raise ValueError(f"audio_src_path does not exist: {audio_src_path}")
        if not shots:
            raise ValueError("shots list cannot be empty")

        item = self.items.create(
            title=title.strip(),
            type_="todo",
            language="en",
            style_lock_version="v3",
        )
        item_id = item["id"]
        self.store.execute(
            "UPDATE items SET source_url=?, bgm_enabled=?, updated_at=? WHERE id=?",
            (source_url, 1 if bgm_enabled else 0, now(), item_id),
        )
        self.items.transition(
            item_id, domain.ItemStatus.PROCESSING_INPUT.value, actor=actor,
            reason="importing existing EN audio + script package into TODO",
        )

        todo_dir = data_dir() / "objects" / item_id / "todo"
        todo_dir.mkdir(parents=True, exist_ok=True)
        audio_path = todo_dir / "audio_en.wav"
        voice_clean_path = todo_dir / "voice_clean.wav"
        script_path = todo_dir / "script_en.json"
        srt_path = todo_dir / "subtitles_en.srt"
        ass_path = todo_dir / "subtitles_en.ass"

        if clean_voice_src_path is None:
            for cand_name in (
                audio_src_path.name.replace("_master_with_bgm", "_narration_only"),
                audio_src_path.name.replace("_with_bgm", "_full"),
                audio_src_path.name.replace("_with_bgm", ""),
                "audio_en_full.mp3",
                "blackrock_15min_narration_only.mp3",
            ):
                cand = audio_src_path.parent / cand_name
                if cand != audio_src_path and cand.exists():
                    clean_voice_src_path = cand
                    break

        try:
            if audio_src_path.suffix.lower() == ".wav":
                shutil.copy2(audio_src_path, audio_path)
            elif shutil.which("ffmpeg"):
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-i", str(audio_src_path),
                        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                        str(audio_path),
                    ],
                    check=True,
                    timeout=180,
                )
            else:
                shutil.copy2(audio_src_path, audio_path)

            if clean_voice_src_path and clean_voice_src_path.exists():
                if clean_voice_src_path.suffix.lower() == ".wav":
                    shutil.copy2(clean_voice_src_path, voice_clean_path)
                elif shutil.which("ffmpeg"):
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-v", "error",
                            "-i", str(clean_voice_src_path),
                            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                            str(voice_clean_path),
                        ],
                        check=True,
                        timeout=180,
                    )

            audio_dur = probe_media_duration(audio_path)
            timed_shots: list[dict[str, Any]] = []
            cursor = 0.0
            total = len(shots)
            for idx, s in enumerate(shots, start=1):
                narr = normalize_english_text(
                    str(s.get("narration") or s.get("narration_en") or s.get("text") or f"Shot {idx}.")
                )
                vp = str(
                    s.get("visual_prompt")
                    or s.get("visual_prompt_clean")
                    or s.get("prompt")
                    or s.get("visual_idea")
                    or ""
                ).strip()
                if not vp:
                    vp = f"Clean 2D flat vector documentary illustration (1280x720) for shot #{idx}/{total}: {narr}"

                start_raw = s.get("audio_start_sec", s.get("output_start_sec", s.get("audio_start", s.get("start_sec"))))
                end_raw = s.get("audio_end_sec", s.get("output_end_sec", s.get("audio_end", s.get("end_sec"))))
                dur_raw = s.get("duration_sec", s.get("total_shot_duration_sec", s.get("duration")))

                start_s = round(float(start_raw), 3) if start_raw is not None else round(cursor, 3)
                if end_raw is not None:
                    end_s = round(float(end_raw), 3)
                elif dur_raw is not None:
                    end_s = round(start_s + float(dur_raw), 3)
                else:
                    end_s = round(start_s + max(2.0, audio_dur / total), 3)
                if idx == total:
                    end_s = round(audio_dur, 3)
                cursor = end_s

                st = str(s.get("shot_type") or s.get("visual_type") or "scene").lower()
                if st not in {"scene", "quote", "crowd", "diagram"}:
                    st = "scene"
                ost = s.get("on_screen_text") or s.get("on_screen")

                timed_shots.append({
                    "index": idx,
                    "shot_type": st,
                    "narration": narr,
                    "visual_prompt": vp,
                    "visual_prompt_clean": vp,
                    "on_screen_text": str(ost) if ost else None,
                    "on_screen_type": s.get("on_screen_type"),
                    "audio_start_sec": start_s,
                    "audio_end_sec": end_s,
                    "duration_sec": round(max(0.05, end_s - start_s), 3),
                })

            if voice_clean_path.exists():
                try:
                    import wave as _wave
                    from .pipeline_en.audio_generator_en import _snap_shot_boundaries_to_silence

                    with _wave.open(str(voice_clean_path), "rb") as _vw:
                        if _vw.getsampwidth() == 2 and _vw.getnchannels() == 1:
                            _pcm = _vw.readframes(_vw.getnframes())
                            _raw_bounds = [(ts["audio_start_sec"], ts["audio_end_sec"]) for ts in timed_shots]
                            _snapped = _snap_shot_boundaries_to_silence(_pcm, _vw.getframerate(), _raw_bounds)
                            for ts, (_st, _en) in zip(timed_shots, _snapped):
                                ts["audio_start_sec"] = _st
                                ts["audio_end_sec"] = _en
                                ts["duration_sec"] = round(max(0.05, _en - _st), 3)

                    (todo_dir / "voice_clean_shots.json").write_text(
                        json.dumps(
                            [
                                {
                                    "index": ts["index"],
                                    "audio_start_sec": ts["audio_start_sec"],
                                    "audio_end_sec": ts["audio_end_sec"],
                                }
                                for ts in timed_shots
                            ],
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except Exception:
                    pass

            write_subtitles_en(timed_shots, srt_path, ass_path)
            script_doc = {
                "item_id": item_id,
                "title": title.strip(),
                "language": "en",
                "voice": voice,
                "bgm_enabled": bgm_enabled,
                "audio_duration_sec": audio_dur,
                "total_shots": len(timed_shots),
                "skill_bundle": get_skill_summary(item_id),
                "shots": timed_shots,
            }
            script_path.write_text(json.dumps(script_doc, indent=2, ensure_ascii=False), encoding="utf-8")

            audio_sha = sha256_file(audio_path)
            script_sha = sha256_file(script_path)
            srt_sha = sha256_file(srt_path)
            ass_sha = sha256_file(ass_path)
            shots_hash = domain.canonical_shots_hash(timed_shots)
            t = now()

            audio_ast_id = new_id("ast")
            script_ast_id = new_id("ast")
            srt_ast_id = new_id("ast")
            ass_ast_id = new_id("ast")

            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,duration_s,"
                    "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (audio_ast_id, item_id, "audio", f"items/{item_id}/todo/audio_en.wav",
                     audio_sha, audio_path.stat().st_size, audio_dur, "imported-en", t),
                )
                conn.execute(
                    "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,"
                    "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (script_ast_id, item_id, "script", f"items/{item_id}/todo/script_en.json",
                     script_sha, script_path.stat().st_size, "imported-en", t),
                )
                conn.execute(
                    "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,"
                    "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (srt_ast_id, item_id, "subtitle", f"items/{item_id}/todo/subtitles_en.srt",
                     srt_sha, srt_path.stat().st_size, "subtitle-builder-en", t),
                )
                conn.execute(
                    "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,"
                    "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (ass_ast_id, item_id, "subtitle", f"items/{item_id}/todo/subtitles_en.ass",
                     ass_sha, ass_path.stat().st_size, "subtitle-builder-en", t),
                )
                conn.execute(
                    "INSERT INTO scripts (id,item_id,version,language,text,scenes_json,"
                    "hash,shots_hash,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (new_id("scr"), item_id, 1, "en",
                     "\n".join(s["narration"] for s in timed_shots),
                     json.dumps(timed_shots, ensure_ascii=False),
                     script_sha, shots_hash, "active", t),
                )
                conn.execute(
                    "INSERT INTO audios (id,item_id,version,kind,audio_asset_id,"
                    "duration_sec,voice_id,hash,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (new_id("aud"), item_id, 1, "master", audio_ast_id,
                     audio_dur, voice, audio_sha, t),
                )
                for s in timed_shots:
                    conn.execute(
                        "INSERT INTO shots (id,item_id,script_version,index_num,shot_type,"
                        "narration,audio_start_sec,audio_end_sec,visual_prompt,"
                        "on_screen_text,on_screen_type,status,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (new_id("shot"), item_id, 1, s["index"], s["shot_type"],
                         s["narration"], s["audio_start_sec"], s["audio_end_sec"],
                         s["visual_prompt"], s.get("on_screen_text"), s.get("on_screen_type"),
                         domain.ShotStatus.PENDING.value, t, t),
                    )
                conn.execute(
                    "UPDATE items SET total_shots=?, audio_duration_sec=?, "
                    "audio_asset_id=?, script_asset_id=?, subtitle_srt_asset_id=?, "
                    "subtitle_ass_asset_id=?, error_detail=NULL, updated_at=? WHERE id=?",
                    (len(timed_shots), audio_dur,
                     audio_ast_id, script_ast_id, srt_ast_id, ass_ast_id, t, item_id),
                )

            self.items.transition(
                item_id, domain.ItemStatus.TODO.value, actor=actor,
                reason="Imported EN audio + timed EN script + skill bundle into TODO queue",
            )
            self.items.event(
                item_id, None, "todo.ready", actor,
                {"total_shots": len(timed_shots), "audio_duration_sec": audio_dur, "shots_hash": shots_hash},
            )
            return self.get_todo(item_id)
        except Exception as exc:
            self.store.execute(
                "UPDATE items SET error_detail=?, updated_at=? WHERE id=?",
                (str(exc), now(), item_id),
            )
            self.items.transition(item_id, domain.ItemStatus.FAILED.value, actor=actor, reason=str(exc))
            raise

    # -- Querying TODOs & Duration Progress ----------------------------------

    def compute_video_progress(
        self,
        item: dict[str, Any],
        videos: list[dict[str, Any]] | None = None,
        shots_rows: list[dict[str, Any]] | None = None,
        images: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Calculate total uploaded video duration vs EN audio duration and thumbnail readiness."""
        item_id = item["id"]
        if videos is None:
            videos = self.list_videos(item_id)
        if images is None:
            images = self.list_images(item_id)

        thumbs = [i for i in images if i.get("kind") == "thumbnail"]
        has_thumbnail = len(thumbs) > 0
        thumbnail_url = thumbs[-1]["file_url"] if thumbs else None

        audio_dur = round(float(item.get("audio_duration_sec") or 0.0), 3)
        if audio_dur <= 0:
            aud_row = self.store.query_one(
                "SELECT duration_sec FROM audios WHERE item_id=? ORDER BY version DESC LIMIT 1",
                (item_id,),
            )
            if aud_row and aud_row.get("duration_sec"):
                audio_dur = round(float(aud_row["duration_sec"]), 3)

        if shots_rows is None:
            shots_rows = self.store.query(
                "SELECT index_num, audio_start_sec, audio_end_sec "
                "FROM shots WHERE item_id=? AND status != 'superseded' ORDER BY index_num ASC",
                (item_id,),
            )

        vid_count = len(videos)
        vid_dur = round(sum(float(v.get("duration_sec") or 0.0) for v in videos), 3)
        tol = float(RENDER_SPEC.get("qc", {}).get("shortfall_warn_tolerance_sec", 0.5))

        raw_shortfall = round(max(0.0, audio_dur - vid_dur), 3)
        surplus = round(max(0.0, vid_dur - audio_dur), 3)

        planned_shortfall = 0.0
        covered_planned_sec = vid_dur
        missing_shots_count = 0
        if shots_rows and vid_count > 0:
            valid_indexes = {int(s["index_num"]) for s in shots_rows}
            covered_indexes: set[int] = set()
            for v in videos:
                raw_cov = v.get("covered_shot_indexes") or []
                cov = [int(x) for x in raw_cov if int(x) in valid_indexes]
                if not raw_cov and int(v["seq_num"]) in valid_indexes:
                    cov = [int(v["seq_num"])]
                covered_indexes.update(cov)
            covered_planned_ms = sum(
                max(1, round(float(s["audio_end_sec"] or 0.0) * 1000) - max(0, round(float(s["audio_start_sec"] or 0.0) * 1000)))
                for s in shots_rows
                if int(s["index_num"]) in covered_indexes
            )
            covered_planned_sec = round(covered_planned_ms / 1000.0, 3)
            missing_shots_count = len(valid_indexes) - len(covered_indexes)
            planned_shortfall = round(max(0.0, audio_dur - covered_planned_sec), 3)

        shortfall = max(raw_shortfall, planned_shortfall)
        is_sufficient = (vid_count > 0) and (shortfall <= tol)

        warning: str | None = None
        is_shortfall = False
        if vid_count == 0:
            warning = (
                f"Chưa có video nào được gửi vào. Cần tổng thời lượng video tối thiểu "
                f"{audio_dur:.2f}s để khớp với Audio EN chuẩn."
            )
        elif raw_shortfall > tol:
            is_shortfall = True
            warning = (
                f"CẢNH BÁO THIẾU THỜI LƯỢNG VIDEO: Đã ghép hết {vid_count} video "
                f"(tổng {vid_dur:.2f}s) nhưng vẫn KHÔNG ĐỦ thời lượng so với Audio EN chuẩn "
                f"({audio_dur:.2f}s) — còn thiếu {raw_shortfall:.2f}s!"
            )
        elif planned_shortfall > tol:
            is_shortfall = True
            warning = (
                f"CẢNH BÁO THIẾU THỜI LƯỢNG VIDEO THEO KỊCH BẢN: Sau khi cắt chuẩn theo thời lượng kịch bản, "
                f"tổng thời lượng các shot đã có video là {covered_planned_sec:.2f}s / {audio_dur:.2f}s "
                f"— còn thiếu {planned_shortfall:.2f}s ở {missing_shots_count} shot chưa có video!"
            )

        return {
            "uploaded_count": vid_count,
            "uploaded_videos_count": vid_count,
            "sum_video_duration_sec": vid_dur,
            "uploaded_videos_duration_sec": vid_dur,
            "covered_planned_duration_sec": covered_planned_sec if shots_rows else vid_dur,
            "missing_shots_count": missing_shots_count,
            "audio_duration_sec": audio_dur,
            "shortfall_sec": shortfall,
            "surplus_sec": surplus,
            "tolerance_sec": tol,
            "is_shortfall": is_shortfall,
            "ready_to_assemble": is_sufficient,
            "is_duration_sufficient": is_sufficient,
            "has_thumbnail": has_thumbnail,
            "thumbnail_count": len(thumbs),
            "thumbnail_url": thumbnail_url,
            "ready_to_complete": bool(is_sufficient and has_thumbnail),
            "completion_warning": (
                None if (not is_sufficient or has_thumbnail)
                else "Đã đủ thời lượng video nhưng CHƯA CÓ ẢNH THUMBNAIL (kind=thumbnail) để hoàn thành job."
            ),
            "shortfall_warning": warning,
        }

    def list_todos(
        self,
        status: str | None = None,
        q: str | None = None,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """Return items in the TODO pipeline enriched with video_progress and download URLs."""
        sql = "SELECT * FROM items WHERE status != 'deleted'"
        params: list[Any] = []
        norm_status = (status or "").strip().lower()
        if norm_status in {"assembled", "completed"}:
            sql += " AND status IN ('completed', 'ready', 'exported')"
        elif norm_status == "ready_to_assemble":
            sql += " AND status = 'todo'"
        elif norm_status:
            sql += " AND status=?"
            params.append(norm_status)
        if q:
            sql += " AND title LIKE ?"
            params.append(f"%{q}%")
        sql += " ORDER BY updated_at DESC LIMIT 50 OFFSET ?"
        params.append(max(0, (page - 1) * 50))
        rows = self.store.query(sql, tuple(params))
        if not rows:
            return []

        item_ids = [r["id"] for r in rows]
        ph = ",".join("?" for _ in item_ids)

        shots_by_item: dict[str, list[dict[str, Any]]] = {iid: [] for iid in item_ids}
        for sr in self.store.query(
            f"SELECT item_id, index_num, shot_type, narration, visual_prompt, on_screen_text, "
            f"on_screen_type, audio_start_sec, audio_end_sec, "
            f"agent_checked, generated_duration_sec, agent_note "
            f"FROM shots WHERE item_id IN ({ph}) AND status != 'superseded' ORDER BY index_num ASC",
            tuple(item_ids),
        ):
            shots_by_item[sr["item_id"]].append(sr)

        images_by_item: dict[str, list[dict[str, Any]]] = {iid: [] for iid in item_ids}
        for ir in self.store.query(
            f"SELECT * FROM todo_images WHERE item_id IN ({ph}) ORDER BY kind ASC, seq_num ASC",
            tuple(item_ids),
        ):
            iid = ir["item_id"]
            covered = []
            if ir.get("covered_shot_indexes"):
                try:
                    covered = json.loads(ir["covered_shot_indexes"])
                except Exception:
                    covered = []
            f_url = f"/api/v1/todos/{iid}/images/{ir['seq_num']}/file?kind={ir['kind']}"
            images_by_item[iid].append({
                **ir,
                "covered_shot_indexes": covered,
                "file_url": f_url,
                "download_url": f_url,
            })

        videos_by_item: dict[str, list[dict[str, Any]]] = {iid: [] for iid in item_ids}
        for vr in self.store.query(
            f"SELECT * FROM todo_videos WHERE item_id IN ({ph}) ORDER BY seq_num ASC",
            tuple(item_ids),
        ):
            iid = vr["item_id"]
            covered = []
            if vr.get("covered_shot_indexes"):
                try:
                    covered = json.loads(vr["covered_shot_indexes"])
                except Exception:
                    covered = []
            f_url = f"/api/v1/todos/{iid}/videos/{vr['seq_num']}/file"
            videos_by_item[iid].append({
                **vr,
                "covered_shot_indexes": covered,
                "file_url": f_url,
                "download_url": f_url,
            })

        feedbacks_by_item: dict[str, list[dict[str, Any]]] = {iid: [] for iid in item_ids}
        for fr in self.store.query(
            f"SELECT * FROM todo_feedbacks WHERE item_id IN ({ph}) ORDER BY created_at DESC",
            tuple(item_ids),
        ):
            feedbacks_by_item[fr["item_id"]].append(fr)

        out: list[dict[str, Any]] = []
        for r in rows:
            iid = r["id"]
            fb_list = feedbacks_by_item.get(iid, [])
            fb_summary = {"total": len(fb_list), "open": 0, "resolved": 0, "ignored": 0}
            for fb in fb_list:
                st = str(fb.get("status") or "").lower()
                if st in fb_summary:
                    fb_summary[st] += 1
            todo_pkg = self.get_todo(
                iid,
                item=r,
                preloaded={
                    "shots_rows": shots_by_item.get(iid, []),
                    "images": images_by_item.get(iid, []),
                    "videos": videos_by_item.get(iid, []),
                    "feedbacks": fb_list,
                    "feedback_summary": fb_summary,
                },
            )
            prog = todo_pkg["video_progress"]
            if norm_status == "ready_to_assemble" and not prog.get("ready_to_assemble"):
                continue
            out.append({
                **todo_pkg,
                "uploaded_images_count": todo_pkg["image_progress"]["total_images_count"],
                "references_count": todo_pkg["image_progress"]["references_count"],
            })

        return out

    def get_todo(
        self,
        item_id: str,
        item: dict[str, Any] | None = None,
        preloaded: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return complete TODO package for external system or UI detail view."""
        if item is None:
            item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)

        if preloaded is not None and "shots_rows" in preloaded:
            shots_rows = preloaded["shots_rows"]
        else:
            shots_rows = self.store.query(
                "SELECT index_num, shot_type, narration, visual_prompt, on_screen_text, "
                "on_screen_type, audio_start_sec, audio_end_sec, "
                "agent_checked, generated_duration_sec, agent_note "
                "FROM shots WHERE item_id=? AND status != 'superseded' ORDER BY index_num ASC",
                (item_id,),
            )
        shot_start_lookup = {
            int(s["index_num"]): round(float(s["audio_start_sec"] or 0.0), 3)
            for s in shots_rows
        }
        images = preloaded["images"] if (preloaded is not None and "images" in preloaded) else self.list_images(item_id)
        shot_img_map: dict[int, str] = {}
        shot_ref_map: dict[int, dict[str, Any]] = {}
        for img in images:
            k = img.get("kind")
            if k == "keyframe":
                if img.get("shot_index"):
                    shot_img_map[int(img["shot_index"])] = img["file_url"]
                for c_idx in img.get("covered_shot_indexes") or []:
                    shot_img_map.setdefault(int(c_idx), img["file_url"])
            elif k == "reference":
                s_idx_ref = int(img["shot_index"]) if img.get("shot_index") else None
                img["timestamp_sec"] = shot_start_lookup.get(s_idx_ref, 0.0) if s_idx_ref else 0.0
                img["search_query"] = img.get("prompt_used") or img.get("name") or ""
                img["title"] = img.get("name") or img.get("prompt_used") or ""
                ref_obj = {
                    "seq_num": int(img["seq_num"]),
                    "file_url": img["file_url"],
                    "name": img.get("name"),
                    "prompt_used": img.get("prompt_used"),
                    "search_query": img["search_query"],
                    "title": img["title"],
                    "timestamp_sec": img["timestamp_sec"],
                    "note": img.get("note"),
                }
                if s_idx_ref:
                    shot_ref_map[s_idx_ref] = ref_obj
                for c_idx in img.get("covered_shot_indexes") or []:
                    shot_ref_map.setdefault(int(c_idx), ref_obj)

        videos = preloaded["videos"] if (preloaded is not None and "videos" in preloaded) else self.list_videos(item_id)
        valid_indexes = {int(s["index_num"]) for s in shots_rows}
        shot_dur_lookup = {
            int(s["index_num"]): round(
                max(0.0, float(s["audio_end_sec"] or 0.0) - float(s["audio_start_sec"] or 0.0)), 3
            )
            for s in shots_rows
        }

        for v in videos:
            raw_cov = v.get("covered_shot_indexes") or []
            cov = [int(x) for x in raw_cov if int(x) in valid_indexes]
            if not raw_cov and int(v["seq_num"]) in valid_indexes:
                cov = [int(v["seq_num"])]
            v["effective_shot_indexes"] = cov
            if cov:
                grp_exp = round(sum(shot_dur_lookup.get(ix, 0.0) for ix in cov), 3)
                v["expected_duration_sec"] = grp_exp
                v["delta_duration_sec"] = round(float(v.get("duration_sec") or 0.0) - grp_exp, 3)
            else:
                v["expected_duration_sec"] = None
                v["delta_duration_sec"] = None

        # Prioritize most recently uploaded/updated video when multiple clips cover the same shot index
        shot_video_map: dict[int, dict[str, Any]] = {}
        videos_by_recency = sorted(
            videos,
            key=lambda x: (str(x.get("updated_at") or x.get("created_at") or ""), int(x.get("seq_num") or 0)),
        )
        for v in videos_by_recency:
            for ix in v.get("effective_shot_indexes") or []:
                shot_video_map[ix] = v

        # Compute active shot group owned by each video after any per-shot overrides
        shot_group_map: dict[int, list[int]] = {}
        for ix, v in shot_video_map.items():
            owned = [
                c_ix for c_ix in (v.get("effective_shot_indexes") or [])
                if shot_video_map.get(c_ix) is v
            ]
            shot_group_map[ix] = owned if owned else [ix]

        tol = float(RENDER_SPEC.get("qc", {}).get("duration_tolerance_sec", 0.25))
        shots_formatted = []
        for s in shots_rows:
            idx_num = int(s["index_num"])
            start_s = round(float(s["audio_start_sec"] or 0.0), 3)
            end_s = round(float(s["audio_end_sec"] or 0.0), 3)
            expected_dur = round(max(0.0, end_s - start_s), 3)
            ref_item = shot_ref_map.get(idx_num)

            v = shot_video_map.get(idx_num)
            group = shot_group_map.get(idx_num, [])
            if v is not None:
                orig_group = v.get("effective_shot_indexes") or group
                clip_dur = round(float(v.get("duration_sec") or 0.0), 3)
                orig_grp_exp = round(sum(shot_dur_lookup.get(ix, 0.0) for ix in orig_group), 3)
                grp_exp = round(sum(shot_dur_lookup.get(ix, 0.0) for ix in group), 3)
                clip_delta = round(clip_dur - orig_grp_exp, 3)
                if len(orig_group) <= 1:
                    video_actual_dur = clip_dur
                elif orig_grp_exp > 0:
                    video_actual_dur = round(clip_dur * (expected_dur / orig_grp_exp), 3)
                else:
                    video_actual_dur = round(clip_dur / len(orig_group), 3)
            else:
                clip_dur = None
                grp_exp = expected_dur
                clip_delta = None
                video_actual_dur = 0.0

            raw_note = s.get("agent_note")
            manual_unchecked = raw_note == "__manual_unchecked__"
            clean_note = None if manual_unchecked else raw_note
            db_checked = int(s.get("agent_checked") or 0) == 1
            db_gen_dur = round(float(s.get("generated_duration_sec") or 0.0), 3)

            if db_checked:
                checked = True
                gen_dur = db_gen_dur if db_gen_dur > 0.0 else (video_actual_dur if v is not None else expected_dur)
            elif v is not None and not manual_unchecked:
                checked = True
                gen_dur = db_gen_dur if db_gen_dur > 0.0 else video_actual_dur
            else:
                checked = False
                gen_dur = 0.0

            actual_dur = round(gen_dur if checked else (video_actual_dur if v is not None else 0.0), 3)
            delta_sec = round(actual_dur - expected_dur, 3)
            remaining_sec = round(max(0.0, expected_dur - (actual_dur if checked else 0.0)), 3)

            if not checked and v is None:
                dur_status = "missing"
            elif delta_sec < -tol:
                dur_status = "shortfall"
            elif delta_sec > tol:
                dur_status = "overrun"
            else:
                dur_status = "ok"

            shots_formatted.append({
                "index": idx_num,
                "shot_type": s["shot_type"],
                "narration": s["narration"],
                "visual_prompt": s["visual_prompt"],
                "on_screen_text": s.get("on_screen_text"),
                "on_screen_type": s.get("on_screen_type"),
                "audio_start_sec": start_s,
                "audio_end_sec": end_s,
                "duration_sec": expected_dur,
                "expected_duration_sec": expected_dur,
                "checked": checked,
                "is_generated": checked,
                "generated_duration_sec": round(gen_dur, 3),
                "actual_duration_sec": actual_dur,
                "delta_duration_sec": delta_sec,
                "remaining_duration_sec": remaining_sec,
                "duration_status": dur_status,
                "agent_note": clean_note,
                "has_video": v is not None,
                "video_seq_num": int(v["seq_num"]) if v is not None else None,
                "video_url": v["file_url"] if v is not None else None,
                "video_width": v.get("width") if v is not None else None,
                "video_height": v.get("height") if v is not None else None,
                "video_note": v.get("note") if v is not None else None,
                "covered_shot_indexes": group,
                "clip_duration_sec": clip_dur,
                "clip_expected_duration_sec": grp_exp if v is not None else None,
                "clip_delta_duration_sec": clip_delta,
                "keyframe_url": shot_img_map.get(idx_num),
                "reference_image_url": ref_item["file_url"] if ref_item else None,
                "reference_image": ref_item,
            })

        progress = self.compute_video_progress(item, videos, shots_rows=shots_rows)
        checked_shots = [s for s in shots_formatted if s["checked"]]
        unchecked_shots = [s for s in shots_formatted if not s["checked"]]
        expected_total = round(
            float(progress.get("audio_duration_sec") or sum(s["duration_sec"] for s in shots_formatted)), 3
        )
        generated_total = round(sum(float(s["generated_duration_sec"] or 0.0) for s in checked_shots), 3)
        delta_total = round(generated_total - expected_total, 3)
        remaining_total = round(max(0.0, expected_total - generated_total), 3)
        shortfall_tol = float(RENDER_SPEC.get("qc", {}).get("shortfall_warn_tolerance_sec", 0.5))

        shot_checklist = {
            "item_id": item_id,
            "title": item.get("title"),
            "total_shots": len(shots_formatted),
            "checked_shots_count": len(checked_shots),
            "unchecked_shots_count": len(unchecked_shots),
            "missing_videos_count": sum(1 for s in shots_formatted if not s["has_video"]),
            "checked_shot_indexes": [s["index"] for s in checked_shots],
            "missing_shot_indexes": [s["index"] for s in unchecked_shots],
            "shortfall_shot_indexes": [s["index"] for s in shots_formatted if s["duration_status"] == "shortfall"],
            "overrun_shot_indexes": [s["index"] for s in shots_formatted if s["duration_status"] == "overrun"],
            "expected_total_duration_sec": expected_total,
            "generated_total_duration_sec": generated_total,
            "uploaded_videos_duration_sec": progress["uploaded_videos_duration_sec"],
            "delta_total_duration_sec": delta_total,
            "total_delta_duration_sec": delta_total,
            "remaining_duration_to_generate_sec": remaining_total,
            "is_all_checked": len(unchecked_shots) == 0 and len(shots_formatted) > 0,
            "is_duration_sufficient": (remaining_total <= shortfall_tol) and (len(checked_shots) > 0),
            "shots": [
                {
                    "index": s["index"],
                    "shot_type": s["shot_type"],
                    "audio_start_sec": s["audio_start_sec"],
                    "audio_end_sec": s["audio_end_sec"],
                    "duration_sec": s["duration_sec"],
                    "checked": s["checked"],
                    "is_generated": s["is_generated"],
                    "has_video": s["has_video"],
                    "duration_status": s["duration_status"],
                    "expected_duration_sec": s["expected_duration_sec"],
                    "generated_duration_sec": s["generated_duration_sec"],
                    "actual_duration_sec": s["actual_duration_sec"],
                    "delta_duration_sec": s["delta_duration_sec"],
                    "remaining_duration_sec": s["remaining_duration_sec"],
                    "video_seq_num": s["video_seq_num"],
                    "video_url": s["video_url"],
                    "covered_shot_indexes": s["covered_shot_indexes"],
                    "agent_note": s["agent_note"],
                    "narration": s["narration"],
                    "visual_prompt": s["visual_prompt"],
                    "on_screen_text": s["on_screen_text"],
                    "on_screen_type": s["on_screen_type"],
                    "keyframe_url": s["keyframe_url"],
                    "reference_image_url": s["reference_image_url"],
                }
                for s in shots_formatted
            ],
        }

        keyframes_cnt = sum(1 for i in images if i.get("kind") == "keyframe")
        chars_cnt = sum(1 for i in images if i.get("kind") == "character")
        refs_cnt = sum(1 for i in images if i.get("kind") == "reference")
        thumbs = [i for i in images if i.get("kind") == "thumbnail"]
        has_thumbnail = len(thumbs) > 0
        thumbnail_url = thumbs[-1]["file_url"] if thumbs else None
        hook_hint = (shots_formatted[0]["narration"] if shots_formatted else "")[:140]
        thumbnail_prompt_hint = (
            f"16:9 YouTube Thumbnail (1280x720): high-contrast focal visual for '{item.get('title', '')}', "
            f"hook concept: '{hook_hint}', bold minimal 2-3 word text overlay, Crayon Capital editorial contrast."
        )
        reference_images = [i for i in images if i.get("kind") == "reference"]
        uploaded_images = [i for i in images if i.get("kind") != "reference"]
        feedbacks = (
            preloaded["feedbacks"]
            if (preloaded is not None and "feedbacks" in preloaded)
            else self.list_feedbacks(item_id)
        )
        feedback_summary = (
            preloaded["feedback_summary"]
            if (preloaded is not None and "feedback_summary" in preloaded)
            else self.get_feedback_summary(item_id)
        )

        return {
            **item,
            "item_id": item_id,
            "audio_url": f"/api/v1/todos/{item_id}/audio",
            "script_url": f"/api/v1/todos/{item_id}/script",
            "skills_url": f"/api/v1/todos/{item_id}/skills",
            "agent_guide_url": "/api/v1/agent-guide",
            "subtitles_srt_url": f"/api/v1/todos/{item_id}/subtitles.srt",
            "subtitles_ass_url": f"/api/v1/todos/{item_id}/subtitles.ass",
            "final_video_url": f"/api/v1/todos/{item_id}/final-video" if item.get("final_video_asset_id") else None,
            "skill_bundle": get_skill_summary(item_id),
            "shots": shots_formatted,
            "shot_checklist": shot_checklist,
            "uploaded_images": uploaded_images,
            "reference_images": reference_images,
            "agent_feedbacks": feedbacks,
            "audio_feedbacks": feedbacks,
            "feedback_summary": feedback_summary,
            "open_feedbacks_count": feedback_summary["open"],
            "has_thumbnail": has_thumbnail,
            "thumbnail_url": thumbnail_url,
            "thumbnail_prompt_hint": thumbnail_prompt_hint,
            "image_progress": {
                "uploaded_images_count": len(uploaded_images),
                "keyframes_count": keyframes_cnt,
                "characters_count": chars_cnt,
                "references_count": refs_cnt,
                "thumbnail_count": len(thumbs),
                "has_thumbnail": has_thumbnail,
                "total_images_count": len(images),
                "total_shots": len(shots_formatted),
            },
            "uploaded_videos": videos,
            "video_progress": progress,
        }


    def get_skills(self, item_id: str | None = None) -> dict[str, Any]:
        """Return the full shot-generation v2 + video-production v1 skill bundle."""
        if item_id is not None:
            item = self.items.get(item_id)
            if not item or item["status"] == "deleted":
                raise KeyError(item_id)
        return get_full_skill_bundle(item_id)

    # -- External Image / Keyframe / Character Sheet Submission --------------

    def list_images(self, item_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM todo_images WHERE item_id=?"
        params: list[Any] = [item_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY kind ASC, seq_num ASC"
        rows = self.store.query(sql, tuple(params))
        out: list[dict[str, Any]] = []
        for r in rows:
            covered = []
            if r.get("covered_shot_indexes"):
                try:
                    covered = json.loads(r["covered_shot_indexes"])
                except Exception:
                    covered = []
            f_url = f"/api/v1/todos/{item_id}/images/{r['seq_num']}/file?kind={r['kind']}"
            out.append({
                **r,
                "covered_shot_indexes": covered,
                "file_url": f_url,
                "download_url": f_url,
            })
        return out

    def _probe_image_dims(self, path: Path) -> tuple[int, int]:
        """Fast header probe for PNG/JPEG or ffprobe fallback."""
        try:
            data = path.read_bytes()[:64]
            if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
                w, h = struct.unpack(">II", data[16:24])
                if w > 0 and h > 0:
                    return int(w), int(h)
        except Exception:
            pass
        if shutil.which("ffprobe"):
            try:
                res = subprocess.run(
                    [
                        "ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "json",
                        str(path),
                    ],
                    capture_output=True, text=True, timeout=10,
                )
                if res.returncode == 0 and res.stdout.strip():
                    streams = json.loads(res.stdout).get("streams") or [{}]
                    w = int(streams[0].get("width") or 1280)
                    h = int(streams[0].get("height") or 720)
                    return w, h
            except Exception:
                pass
        return 1280, 720

    def submit_image(
        self,
        item_id: str,
        kind: str = "keyframe",
        seq_num: int | None = None,
        shot_index: int | None = None,
        name: str | None = None,
        src_path: Path | None = None,
        file_path: Path | None = None,
        file_url: str | None = None,
        base64_data: str | None = None,
        raw_bytes: bytes | None = None,
        file_bytes: bytes | None = None,
        filename: str | None = None,
        prompt_used: str | None = None,
        covered_shot_indexes: list[int] | None = None,
        note: str | None = None,
        actor: str = "external_image_agent",
    ) -> dict[str, Any]:
        """Upload/register a generated keyframe or character image back onto the TODO item."""
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)

        kind = (kind or "keyframe").strip().lower()
        kind_aliases = {
            "character_sheet": "character",
            "char": "character",
            "style_ref": "reference",
            "ref": "reference",
            "thumb": "thumbnail",
            "cover": "thumbnail",
        }
        kind = kind_aliases.get(kind, kind)
        if kind not in {"keyframe", "character", "scene", "reference", "thumbnail"}:
            raise ValueError("kind must be 'keyframe', 'character', 'scene', 'reference', or 'thumbnail'")

        if raw_bytes is None and file_bytes is not None:
            raw_bytes = file_bytes
        if raw_bytes is None and base64_data:
            b64_clean = base64_data.split(",", 1)[-1].strip()
            raw_bytes = base64.b64decode(b64_clean)
        if src_path is None and file_path is not None:
            src_path = file_path

        if seq_num is None:
            if kind == "thumbnail":
                seq_num = 1
            elif shot_index is not None and shot_index >= 1:
                seq_num = int(shot_index)
            else:
                row = self.store.query_one(
                    "SELECT MAX(seq_num) m FROM todo_images WHERE item_id=? AND kind=?",
                    (item_id, kind),
                )
                seq_num = int(row["m"] or 0) + 1
        else:
            seq_num = int(seq_num)
            if seq_num < 1:
                raise ValueError("seq_num must be >= 1")

        if shot_index is None and kind == "keyframe":
            shot_index = seq_num

        ext = ".png"
        if filename:
            suf = Path(filename).suffix.lower()
            if suf in {".png", ".jpg", ".jpeg", ".webp"}:
                ext = suf
        elif src_path:
            suf = Path(src_path).suffix.lower()
            if suf in {".png", ".jpg", ".jpeg", ".webp"}:
                ext = suf

        images_dir = data_dir() / "objects" / item_id / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        dest_path = images_dir / f"{kind}_{seq_num:04d}{ext}"

        max_mb = int(RENDER_SPEC.get("limits", {}).get("max_keyframe_mb", 25))
        if raw_bytes is not None:
            if len(raw_bytes) > max_mb * 1024 * 1024:
                raise ValueError("Uploaded image exceeds max_keyframe_mb")
            dest_path.write_bytes(raw_bytes)
        elif src_path is not None:
            resolved = Path(src_path).resolve()
            sandbox = data_dir().resolve()
            if sandbox not in resolved.parents and resolved != sandbox:
                raise ValueError("file_path must live inside VIDEO_DATA_DIR")
            if not resolved.exists():
                raise ValueError(f"file_path does not exist: {src_path}")
            if resolved != dest_path.resolve():
                shutil.copy2(resolved, dest_path)
        elif file_url:
            fetch_url(file_url, dest_path, max_mb * 1024 * 1024)
        else:
            raise ValueError("Must provide image file upload, base64_data, file_path, or file_url")

        width, height = self._probe_image_dims(dest_path)
        img_sha = sha256_file(dest_path)
        t = now()
        ast_id = new_id("ast")
        storage_key = f"items/{item_id}/images/{dest_path.name}"

        target_shots = list(covered_shot_indexes or [])
        if shot_index and shot_index not in target_shots:
            target_shots.insert(0, int(shot_index))

        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,"
                "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    ast_id, item_id, "image", storage_key,
                    img_sha, dest_path.stat().st_size,
                    "external_image_agent", t,
                ),
            )
            existing = conn.execute(
                "SELECT id FROM todo_images WHERE item_id=? AND kind=? AND seq_num=?",
                (item_id, kind, seq_num),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE todo_images SET shot_index=?, name=?, image_asset_id=?, filename=?, "
                    "sha256=?, width=?, height=?, prompt_used=?, covered_shot_indexes=?, "
                    "note=?, updated_at=? WHERE item_id=? AND kind=? AND seq_num=?",
                    (
                        shot_index, name, ast_id, filename or dest_path.name,
                        img_sha, width, height, prompt_used,
                        json.dumps(target_shots), note, t,
                        item_id, kind, seq_num,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO todo_images (id,item_id,kind,seq_num,shot_index,name,"
                    "image_asset_id,filename,sha256,width,height,prompt_used,"
                    "covered_shot_indexes,note,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_id("timg"), item_id, kind, seq_num, shot_index, name,
                        ast_id, filename or dest_path.name, img_sha, width, height,
                        prompt_used, json.dumps(target_shots), note, t, t,
                    ),
                )
            if kind == "keyframe" and target_shots:
                for s_idx in target_shots:
                    conn.execute(
                        "UPDATE shots SET keyframe_asset_id=?, keyframe_hash=?, updated_at=? "
                        "WHERE item_id=? AND index_num=? AND status != 'superseded'",
                        (ast_id, img_sha, t, item_id, int(s_idx)),
                    )

        img_cnt = self.store.query_one(
            "SELECT COUNT(*) c FROM todo_images WHERE item_id=?", (item_id,)
        )["c"]
        self.store.execute(
            "UPDATE items SET uploaded_images_count=?, updated_at=? WHERE id=?",
            (int(img_cnt or 0), now(), item_id),
        )
        images = self.list_images(item_id)
        img_obj = next(
            i for i in images if i["kind"] == kind and int(i["seq_num"]) == seq_num
        )
        self.items.event(
            item_id, None, "todo.image_submitted", actor,
            {
                "kind": kind,
                "seq_num": seq_num,
                "shot_index": shot_index,
                "width": width,
                "height": height,
                "uploaded_images_count": len(images),
            },
        )
        thumb_cnt = sum(1 for i in images if i.get("kind") == "thumbnail")
        vp = self.compute_video_progress(item, images=images)
        return {
            "item_id": item_id,
            "kind": kind,
            "seq_num": seq_num,
            "image": img_obj,
            "image_progress": {
                "uploaded_images_count": len(images),
                "keyframes_count": sum(1 for i in images if i.get("kind") == "keyframe"),
                "characters_count": sum(1 for i in images if i.get("kind") == "character"),
                "references_count": sum(1 for i in images if i.get("kind") == "reference"),
                "thumbnail_count": thumb_cnt,
                "has_thumbnail": thumb_cnt > 0,
            },
            "video_progress": vp,
        }

    def delete_image(
        self, item_id: str, seq_num: int, kind: str = "keyframe", actor: str = "api"
    ) -> dict[str, Any]:
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)
        row = self.store.query_one(
            "SELECT * FROM todo_images WHERE item_id=? AND kind=? AND seq_num=?",
            (item_id, kind, int(seq_num)),
        )
        if not row:
            raise KeyError(f"image {kind} #{seq_num} not found")
        self.store.execute(
            "DELETE FROM todo_images WHERE item_id=? AND kind=? AND seq_num=?",
            (item_id, kind, int(seq_num)),
        )
        images = self.list_images(item_id)
        self.store.execute(
            "UPDATE items SET uploaded_images_count=?, updated_at=? WHERE id=?",
            (len(images), now(), item_id),
        )
        self.items.event(
            item_id, None, "todo.image_deleted", actor,
            {"kind": kind, "seq_num": int(seq_num)},
        )
        return {"item_id": item_id, "deleted_seq_num": int(seq_num), "kind": kind}

    def get_uploaded_image_path(
        self, item_id: str, seq_num: int, kind: str = "keyframe"
    ) -> Path:
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)
        row = self.store.query_one(
            "SELECT * FROM todo_images WHERE item_id=? AND kind=? AND seq_num=?",
            (item_id, kind, int(seq_num)),
        )
        if not row:
            raise KeyError(f"image {kind} #{seq_num} not found")
        images_dir = data_dir() / "objects" / item_id / "images"
        for ext in [".png", ".jpg", ".jpeg", ".webp"]:
            cand = images_dir / f"{kind}_{int(seq_num):04d}{ext}"
            if cand.exists():
                return cand
        raise FileNotFoundError(f"Image file not found on disk for {kind} #{seq_num}")

    def collect_script_references(
        self,
        item_id: str,
        count: int = 15,
        force: bool = False,
        actor: str = "reference_collector",
    ) -> dict[str, Any]:
        """Harvest 10-20 authentic reference photos across the entire script and attach to TODO."""
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)

        if not force:
            existing_refs = self.store.query_one(
                "SELECT COUNT(*) c FROM todo_images WHERE item_id=? AND kind='reference'",
                (item_id,),
            )
            if existing_refs and int(existing_refs.get("c") or 0) > 0:
                return self.get_todo(item_id)

        shots_rows = self.store.query(
            "SELECT index_num, shot_type, narration, visual_prompt FROM shots "
            "WHERE item_id=? AND status != 'superseded' ORDER BY index_num ASC",
            (item_id,),
        )
        if not shots_rows:
            return self.get_todo(item_id)

        raw_shots = [
            {
                "index": int(r["index_num"]),
                "shot_type": r["shot_type"],
                "narration": r["narration"],
                "visual_prompt": r["visual_prompt"],
            }
            for r in shots_rows
        ]

        from .pipeline_en.reference_image_collector import collect_references_for_shots

        refs_dir = data_dir() / "objects" / item_id / "images"
        collected = collect_references_for_shots(
            shots=raw_shots,
            out_dir=refs_dir,
            target_count=count,
            item_title=item.get("title", ""),
        )

        for ref in collected:
            seq = int(ref["seq_num"])
            shot_idx = int(ref["shot_index"])
            name = str(ref.get("query") or f"Reference #{seq}")
            f_path = Path(ref["file_path"])
            src_engine = ref.get("source_engine", "web")
            src_title = ref.get("source_title") or name
            note = f"Reference image for shot #{shot_idx}: {src_title} (via {src_engine})"

            self.submit_image(
                item_id=item_id,
                kind="reference",
                seq_num=seq,
                shot_index=shot_idx,
                name=name,
                file_path=f_path,
                prompt_used=ref.get("query"),
                covered_shot_indexes=[shot_idx],
                note=note,
                actor=actor,
            )

        # Sync reference metadata into script_en.json if it exists and update hashes
        script_file = data_dir() / "objects" / item_id / "todo" / "script_en.json"
        if script_file.exists():
            try:
                doc = json.loads(script_file.read_text(encoding="utf-8"))
                todo_now = self.get_todo(item_id)
                doc["reference_images"] = todo_now.get("reference_images", [])
                doc["shots"] = todo_now.get("shots", doc.get("shots", []))
                script_file.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

                new_sha = sha256_file(script_file)
                new_size = script_file.stat().st_size
                self.store.execute(
                    "UPDATE assets SET sha256=?, size=? WHERE item_id=? AND kind='script'",
                    (new_sha, new_size, item_id),
                )
                self.store.execute(
                    "UPDATE scripts SET hash=? WHERE item_id=? AND status='active'",
                    (new_sha, item_id),
                )
            except Exception as exc:
                logger.warning("Could not sync reference_images to script_en.json: %s", exc)

        self.items.event(
            item_id, None, "todo.references_collected", actor,
            {"count": len(collected), "target_count": count},
        )
        return self.get_todo(item_id)

    def list_videos(self, item_id: str) -> list[dict[str, Any]]:
        rows = self.store.query(
            "SELECT * FROM todo_videos WHERE item_id=? ORDER BY seq_num ASC",
            (item_id,),
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            covered = []
            if r.get("covered_shot_indexes"):
                try:
                    covered = json.loads(r["covered_shot_indexes"])
                except Exception:
                    covered = []
            f_url = f"/api/v1/todos/{item_id}/videos/{r['seq_num']}/file"
            out.append({
                **r,
                "covered_shot_indexes": covered,
                "file_url": f_url,
                "download_url": f_url,
            })
        return out

    # -- External Video Submission (Sequential, Supports Merged Shots) -------

    def _probe_submitted_video(self, path: Path) -> dict[str, Any]:
        """Probe video duration & geometry using real ffprobe or test probe adapter."""
        if registry.probe is not None:
            try:
                info = registry.probe.probe_video(str(path))
                return {
                    "duration_sec": round(float(info.get("duration", 0.0)), 3),
                    "width": int(info.get("width", 1280)),
                    "height": int(info.get("height", 720)),
                    "fps": float(info.get("fps", 24.0)),
                }
            except Exception:
                pass
        return probe_video_info(path)

    def submit_video(
        self,
        item_id: str,
        seq_num: int | None = None,
        src_path: Path | None = None,
        file_path: Path | None = None,
        file_url: str | None = None,
        raw_bytes: bytes | None = None,
        file_bytes: bytes | None = None,
        filename: str | None = None,
        covered_shot_indexes: list[int] | None = None,
        note: str | None = None,
        actor: str = "external_api",
    ) -> dict[str, Any]:
        """Register an externally generated video clip at sequential position `seq_num`."""
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)

        if raw_bytes is None and file_bytes is not None:
            raw_bytes = file_bytes
        if src_path is None and file_path is not None:
            src_path = file_path

        if seq_num is None:
            row = self.store.query_one(
                "SELECT MAX(seq_num) m FROM todo_videos WHERE item_id=?",
                (item_id,),
            )
            seq_num = int(row["m"] or 0) + 1
        else:
            seq_num = int(seq_num)
            if seq_num < 1:
                raise ValueError("seq_num must be >= 1")

        videos_dir = data_dir() / "objects" / item_id / "videos"
        videos_dir.mkdir(parents=True, exist_ok=True)
        dest_path = videos_dir / f"seq_{seq_num:04d}.mp4"

        max_mb = int(RENDER_SPEC.get("limits", {}).get("max_video_mb", 2048))
        if raw_bytes is not None:
            if len(raw_bytes) > max_mb * 1024 * 1024:
                raise ValueError("Uploaded video exceeds max_video_mb")
            dest_path.write_bytes(raw_bytes)
        elif src_path is not None:
            resolved = Path(src_path).resolve()
            sandbox = data_dir().resolve()
            if sandbox not in resolved.parents and resolved != sandbox:
                raise ValueError("file_path must live inside VIDEO_DATA_DIR")
            if not resolved.exists():
                raise ValueError(f"file_path does not exist: {src_path}")
            if resolved != dest_path.resolve():
                shutil.copy2(resolved, dest_path)
        elif file_url:
            fetch_url(file_url, dest_path, max_mb * 1024 * 1024)
        else:
            raise ValueError("Must provide video file upload, file_path, or file_url")

        info = self._probe_submitted_video(dest_path)
        dur_sec = float(info["duration_sec"])
        if dur_sec <= 0:
            dest_path.unlink(missing_ok=True)
            raise ValueError("Submitted video has invalid duration <= 0s")

        v_sha = sha256_file(dest_path)
        t = now()
        ast_id = new_id("ast")
        storage_key = f"items/{item_id}/videos/seq_{seq_num:04d}.mp4"

        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO assets (id,item_id,kind,storage_key,sha256,size,duration_s,"
                "provenance_engine,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    ast_id, item_id, "video", storage_key,
                    v_sha, dest_path.stat().st_size, dur_sec,
                    "external_video", t,
                ),
            )
            existing = conn.execute(
                "SELECT id FROM todo_videos WHERE item_id=? AND seq_num=?",
                (item_id, seq_num),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE todo_videos SET video_asset_id=?, filename=?, sha256=?, "
                    "duration_sec=?, width=?, height=?, fps=?, covered_shot_indexes=?, "
                    "note=?, updated_at=? WHERE item_id=? AND seq_num=?",
                    (
                        ast_id, filename or dest_path.name, v_sha,
                        dur_sec, info.get("width", 1280), info.get("height", 720),
                        info.get("fps", 24.0),
                        json.dumps(covered_shot_indexes or []),
                        note, t, item_id, seq_num,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO todo_videos (id,item_id,seq_num,video_asset_id,filename,"
                    "sha256,duration_sec,width,height,fps,covered_shot_indexes,note,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_id("tvid"), item_id, seq_num, ast_id,
                        filename or dest_path.name, v_sha,
                        dur_sec, info.get("width", 1280), info.get("height", 720),
                        info.get("fps", 24.0),
                        json.dumps(covered_shot_indexes or []),
                        note, t, t,
                    ),
                )

        # Sync agent_checked and generated_duration_sec on covered shots
        target_shots = [int(x) for x in (covered_shot_indexes or []) if int(x) >= 1]
        if not target_shots:
            target_shots = [seq_num]
        shot_rows = self.store.query(
            "SELECT index_num, audio_start_sec, audio_end_sec, agent_note FROM shots "
            "WHERE item_id=? AND status != 'superseded'",
            (item_id,),
        )
        valid_shot_durs = {
            int(sr["index_num"]): max(0.0, float(sr["audio_end_sec"] or 0.0) - float(sr["audio_start_sec"] or 0.0))
            for sr in shot_rows
        }
        matched_shots = [ix for ix in target_shots if ix in valid_shot_durs]
        if matched_shots:
            grp_exp = sum(valid_shot_durs[ix] for ix in matched_shots)
            with self.store.transaction() as conn:
                for ix in matched_shots:
                    if len(matched_shots) == 1:
                        s_dur = round(dur_sec, 3)
                    elif grp_exp > 0:
                        s_dur = round(dur_sec * (valid_shot_durs[ix] / grp_exp), 3)
                    else:
                        s_dur = round(dur_sec / len(matched_shots), 3)
                    conn.execute(
                        "UPDATE shots SET agent_checked=1, generated_duration_sec=?, "
                        "agent_note=CASE WHEN agent_note='__manual_unchecked__' THEN NULL ELSE agent_note END, "
                        "updated_at=? WHERE item_id=? AND index_num=? AND status != 'superseded'",
                        (s_dur, t, item_id, ix),
                    )

        self._sync_item_video_totals(item_id)
        if item["status"] in {domain.ItemStatus.AWAITING_REVIEW.value, domain.ItemStatus.DRAFT.value}:
            self.items.transition(item_id, domain.ItemStatus.TODO.value, actor=actor,
                                  reason="external video submitted")

        todo_pkg = self.get_todo(item_id)
        videos = todo_pkg["uploaded_videos"]
        progress = todo_pkg["video_progress"]
        checklist = todo_pkg["shot_checklist"]

        self.items.event(
            item_id, None, "todo.video_submitted", actor,
            {
                "seq_num": seq_num,
                "duration_sec": dur_sec,
                "covered_shot_indexes": matched_shots,
                "uploaded_videos_count": progress["uploaded_videos_count"],
                "uploaded_videos_duration_sec": progress["uploaded_videos_duration_sec"],
                "audio_duration_sec": progress["audio_duration_sec"],
                "shortfall_sec": progress["shortfall_sec"],
                "is_duration_sufficient": progress["is_duration_sufficient"],
            },
        )
        return {
            "item_id": item_id,
            "seq_num": seq_num,
            "video": next(v for v in videos if int(v["seq_num"]) == seq_num),
            "video_progress": progress,
            "shot_checklist": checklist,
        }

    def delete_video(self, item_id: str, seq_num: int, actor: str = "api") -> dict[str, Any]:
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)
        row = self.store.query_one(
            "SELECT * FROM todo_videos WHERE item_id=? AND seq_num=?",
            (item_id, int(seq_num)),
        )
        if not row:
            raise KeyError(f"seq_num {seq_num} not found")
        cov: list[int] = []
        if row.get("covered_shot_indexes"):
            try:
                cov = [int(x) for x in json.loads(row["covered_shot_indexes"])]
            except Exception:
                cov = []
        if not cov:
            cov = [int(seq_num)]

        t = now()
        self.store.execute(
            "DELETE FROM todo_videos WHERE item_id=? AND seq_num=?",
            (item_id, int(seq_num)),
        )

        # Re-evaluate remaining videos so shots still covered by another merged clip stay checked
        rem_videos = self.list_videos(item_id)
        shot_rows = self.store.query(
            "SELECT index_num, audio_start_sec, audio_end_sec FROM shots "
            "WHERE item_id=? AND status != 'superseded'",
            (item_id,),
        )
        valid_shot_durs = {
            int(sr["index_num"]): max(0.0, float(sr["audio_end_sec"] or 0.0) - float(sr["audio_start_sec"] or 0.0))
            for sr in shot_rows
        }
        rem_by_recency = sorted(
            rem_videos,
            key=lambda x: (str(x.get("updated_at") or x.get("created_at") or ""), int(x.get("seq_num") or 0)),
        )
        rem_shot_video_map: dict[int, tuple[dict[str, Any], list[int]]] = {}
        for rv in rem_by_recency:
            r_cov = [int(x) for x in (rv.get("covered_shot_indexes") or []) if int(x) in valid_shot_durs]
            if not r_cov and int(rv["seq_num"]) in valid_shot_durs:
                r_cov = [int(rv["seq_num"])]
            for r_ix in r_cov:
                rem_shot_video_map[r_ix] = (rv, r_cov)

        with self.store.transaction() as conn:
            for ix in cov:
                if ix in rem_shot_video_map:
                    rv, r_cov = rem_shot_video_map[ix]
                    r_dur = float(rv.get("duration_sec") or 0.0)
                    grp_exp = sum(valid_shot_durs.get(c_ix, 0.0) for c_ix in r_cov)
                    if len(r_cov) <= 1:
                        s_dur = round(r_dur, 3)
                    elif grp_exp > 0:
                        s_dur = round(r_dur * (valid_shot_durs.get(ix, 0.0) / grp_exp), 3)
                    else:
                        s_dur = round(r_dur / len(r_cov), 3)
                    conn.execute(
                        "UPDATE shots SET agent_checked=1, generated_duration_sec=?, updated_at=? "
                        "WHERE item_id=? AND index_num=? AND status != 'superseded'",
                        (s_dur, t, item_id, int(ix)),
                    )
                else:
                    conn.execute(
                        "UPDATE shots SET agent_checked=0, generated_duration_sec=0.0, updated_at=? "
                        "WHERE item_id=? AND index_num=? AND status != 'superseded'",
                        (t, item_id, int(ix)),
                    )

        self._sync_item_video_totals(item_id)
        todo_pkg = self.get_todo(item_id)
        progress = todo_pkg["video_progress"]
        checklist = todo_pkg["shot_checklist"]
        self.items.event(item_id, None, "todo.video_deleted", actor, {"seq_num": int(seq_num)})
        return {
            "item_id": item_id,
            "deleted_seq_num": int(seq_num),
            "video_progress": progress,
            "shot_checklist": checklist,
        }

    def get_shot_checklist(self, item_id: str) -> dict[str, Any]:
        """Return per-shot checklist status, generated durations, deltas, and remaining seconds for Agent."""
        todo_pkg = self.get_todo(item_id)
        return todo_pkg["shot_checklist"]

    def update_shot_checklist(
        self,
        item_id: str,
        updates: list[dict[str, Any]] | None = None,
        check_all_with_video: bool = False,
        uncheck_missing: bool = False,
        actor: str = "user",
    ) -> dict[str, Any]:
        """Tick/untick shots and update generated_duration_sec for Agent & UI coordination."""
        todo_pkg = self.get_todo(item_id)
        shots_by_idx = {int(s["index"]): s for s in todo_pkg["shots"]}
        t = now()

        with self.store.transaction() as conn:
            if check_all_with_video:
                for idx, s in shots_by_idx.items():
                    if s["has_video"]:
                        dur = round(float(s["actual_duration_sec"] or s["expected_duration_sec"]), 3)
                        conn.execute(
                            "UPDATE shots SET agent_checked=1, generated_duration_sec=?, "
                            "agent_note=CASE WHEN agent_note='__manual_unchecked__' THEN NULL ELSE agent_note END, "
                            "updated_at=? WHERE item_id=? AND index_num=? AND status != 'superseded'",
                            (dur, t, item_id, idx),
                        )
            if uncheck_missing:
                for idx, s in shots_by_idx.items():
                    if not s["has_video"]:
                        conn.execute(
                            "UPDATE shots SET agent_checked=0, generated_duration_sec=0.0, "
                            "updated_at=? WHERE item_id=? AND index_num=? AND status != 'superseded'",
                            (t, item_id, idx),
                        )

            for u in updates or []:
                if u.get("shot_index") is None:
                    raise ValueError("shot_index is required in each shot checklist update")
                s_idx = int(u["shot_index"])
                if s_idx not in shots_by_idx:
                    raise ValueError(f"Shot #{s_idx} does not exist in item {item_id}")
                cur = shots_by_idx[s_idx]
                exp_dur = float(cur["expected_duration_sec"])

                if u.get("checked") is not None:
                    new_checked = bool(u["checked"])
                elif u.get("is_generated") is not None:
                    new_checked = bool(u["is_generated"])
                elif u.get("generated_duration_sec") is not None:
                    new_checked = float(u["generated_duration_sec"]) > 0.0
                else:
                    new_checked = bool(cur["checked"])

                if u.get("generated_duration_sec") is not None:
                    new_dur = round(max(0.0, float(u["generated_duration_sec"])), 3)
                elif new_checked:
                    if float(cur.get("generated_duration_sec") or 0.0) > 0.0:
                        new_dur = round(float(cur["generated_duration_sec"]), 3)
                    elif cur.get("has_video") and float(cur.get("actual_duration_sec") or 0.0) > 0.0:
                        new_dur = round(float(cur["actual_duration_sec"]), 3)
                    else:
                        new_dur = round(exp_dur, 3)
                else:
                    new_dur = 0.0

                if u.get("note") is not None:
                    new_note = str(u["note"]).strip() or None
                elif not new_checked and cur.get("has_video"):
                    new_note = "__manual_unchecked__"
                else:
                    new_note = cur.get("agent_note")

                conn.execute(
                    "UPDATE shots SET agent_checked=?, generated_duration_sec=?, agent_note=?, "
                    "updated_at=? WHERE item_id=? AND index_num=? AND status != 'superseded'",
                    (1 if new_checked else 0, new_dur, new_note, t, item_id, s_idx),
                )

        updated_pkg = self.get_todo(item_id)
        checklist = updated_pkg["shot_checklist"]
        self.items.event(
            item_id, None, "todo.shot_checklist_updated", actor,
            {
                "checked_shots_count": checklist["checked_shots_count"],
                "unchecked_shots_count": checklist["unchecked_shots_count"],
                "generated_total_duration_sec": checklist["generated_total_duration_sec"],
                "remaining_duration_to_generate_sec": checklist["remaining_duration_to_generate_sec"],
            },
        )
        return checklist

    def _sync_item_video_totals(self, item_id: str) -> None:
        agg = self.store.query_one(
            "SELECT COUNT(*) c, COALESCE(SUM(duration_sec), 0.0) s FROM todo_videos WHERE item_id=?",
            (item_id,),
        )
        count = int(agg["c"] or 0)
        total_s = round(float(agg["s"] or 0.0), 3)
        self.store.execute(
            "UPDATE items SET uploaded_videos_count=?, uploaded_videos_duration_sec=?, "
            "appended_count=?, cumulative_duration_sec=?, updated_at=? WHERE id=?",
            (count, total_s, count, total_s, now(), item_id),
        )

    # -- Asset File Path Resolution ------------------------------------------

    def resolve_audio_path(self, item_id: str) -> Path:
        item = self.items.get(item_id)
        if not item:
            raise KeyError(item_id)
        if item.get("audio_asset_id"):
            ast = self.store.query_one("SELECT storage_key FROM assets WHERE id=?", (item["audio_asset_id"],))
            if ast:
                p = data_dir() / "objects" / ast["storage_key"].removeprefix("items/")
                if p.exists():
                    return p
        # Fallback paths
        for cand in [
            data_dir() / "objects" / item_id / "todo" / "audio_en.wav",
            data_dir() / "objects" / item_id / "imports" / "audio.bin",
            data_dir() / "objects" / item_id / "seed_audio.wav",
        ]:
            if cand.exists():
                return cand
        raise FileNotFoundError(f"Audio file not found for item {item_id}")

    def resolve_script_path(self, item_id: str) -> Path:
        p = data_dir() / "objects" / item_id / "todo" / "script_en.json"
        if p.exists():
            return p
        # Dynamically materialize from DB if item was created via legacy import
        todo_data = self.get_todo(item_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "item_id": item_id,
            "title": todo_data["title"],
            "language": todo_data.get("language") or "en",
            "audio_duration_sec": todo_data["video_progress"]["audio_duration_sec"],
            "total_shots": len(todo_data["shots"]),
            "shots": todo_data["shots"],
        }
        p.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    def resolve_subtitle_path(self, item_id: str, fmt: str = "srt") -> Path:
        ext = "ass" if fmt == "ass" else "srt"
        p = data_dir() / "objects" / item_id / "todo" / f"subtitles_en.{ext}"
        if p.exists():
            return p
        from .pipeline_en.audio_generator_en import write_subtitles_en
        todo_data = self.get_todo(item_id)
        srt_p = data_dir() / "objects" / item_id / "todo" / "subtitles_en.srt"
        ass_p = data_dir() / "objects" / item_id / "todo" / "subtitles_en.ass"
        write_subtitles_en(todo_data["shots"], srt_p, ass_p)
        return ass_p if ext == "ass" else srt_p

    get_audio_path = resolve_audio_path
    get_script_path = resolve_script_path
    get_subtitle_path = resolve_subtitle_path

    def get_uploaded_video_path(self, item_id: str, seq_num: int) -> Path:
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)
        row = self.store.query_one(
            "SELECT * FROM todo_videos WHERE item_id=? AND seq_num=?",
            (item_id, int(seq_num)),
        )
        if not row:
            raise KeyError(f"seq_num {seq_num} not found")
        p = data_dir() / "objects" / item_id / "videos" / f"seq_{int(seq_num):04d}.mp4"
        if not p.exists():
            raise FileNotFoundError(f"Video file not found on disk: {p}")
        return p

    def get_final_video_path(self, item_id: str) -> Path:
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)
        p = data_dir() / "objects" / item_id / "exports" / "final_en.mp4"
        if not p.exists():
            raise FileNotFoundError(f"Final video not yet assembled for item {item_id}")
        return p

    # -- Agent Feedbacks (Audio / Proper Nouns / Years / Pacing) -------------

    def submit_feedback(
        self,
        item_id: str,
        feedback_data: dict[str, Any] | list[dict[str, Any]],
        actor: str = "external_video_agent",
    ) -> dict[str, Any]:
        """Submit feedback from external video agent about audio, pronunciation, or script."""
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)

        raw_list: list[dict[str, Any]]
        if isinstance(feedback_data, list):
            raw_list = feedback_data
        elif isinstance(feedback_data, dict):
            if "feedbacks" in feedback_data and isinstance(feedback_data["feedbacks"], list):
                raw_list = feedback_data["feedbacks"]
            else:
                raw_list = [feedback_data]
        else:
            raise ValueError("Invalid feedback payload format")

        if not raw_list:
            raise ValueError("Feedbacks list cannot be empty")

        created: list[dict[str, Any]] = []
        t = now()
        valid_categories = {
            "pronunciation", "proper_noun", "year_number", "pacing",
            "script_mismatch", "audio_quality", "bgm", "other",
        }
        valid_severities = {"info", "warning", "critical"}

        with self.store.transaction() as conn:
            for f in raw_list:
                fb_id = new_id("fbk")
                category = str(f.get("category") or "pronunciation").strip().lower()
                if category not in valid_categories:
                    raise ValueError(f"Invalid category '{category}'; must be one of {sorted(valid_categories)}")

                severity = str(f.get("severity") or "warning").strip().lower()
                if severity not in valid_severities:
                    raise ValueError(f"Invalid severity '{severity}'; must be one of {sorted(valid_severities)}")

                shot_idx = int(f["shot_index"]) if f.get("shot_index") is not None else None
                if shot_idx is not None and shot_idx < 1:
                    raise ValueError("shot_index must be >= 1")

                ts_sec = float(f["timestamp_sec"]) if f.get("timestamp_sec") is not None else None
                if ts_sec is not None and ts_sec < 0.0:
                    raise ValueError("timestamp_sec must be >= 0.0")

                orig_txt = str(f.get("original_text") or "").strip() or None
                sugg_txt = str(f.get("suggested_text") or "").strip() or None
                hint_txt = str(f.get("pronunciation_hint") or "").strip() or None
                msg = str(f.get("message") or "").strip()
                if not msg:
                    raise ValueError("Feedback 'message' is required")
                agent_name = str(f.get("agent_name") or actor or "external_video_agent").strip()

                conn.execute(
                    "INSERT INTO todo_feedbacks (id, item_id, shot_index, timestamp_sec, "
                    "category, severity, original_text, suggested_text, pronunciation_hint, "
                    "message, agent_name, status, resolution_note, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        fb_id, item_id, shot_idx, ts_sec,
                        category, severity, orig_txt, sugg_txt, hint_txt,
                        msg, agent_name, "open", None, t, t,
                    ),
                )
                created.append({
                    "id": fb_id,
                    "item_id": item_id,
                    "shot_index": shot_idx,
                    "timestamp_sec": ts_sec,
                    "category": category,
                    "severity": severity,
                    "original_text": orig_txt,
                    "suggested_text": sugg_txt,
                    "pronunciation_hint": hint_txt,
                    "message": msg,
                    "agent_name": agent_name,
                    "status": "open",
                    "resolution_note": None,
                    "created_at": t,
                    "updated_at": t,
                })

            open_c = conn.execute(
                "SELECT COUNT(*) FROM todo_feedbacks WHERE item_id=? AND status='open'", (item_id,)
            ).fetchone()[0]
            total_c = conn.execute(
                "SELECT COUNT(*) FROM todo_feedbacks WHERE item_id=?", (item_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE items SET feedbacks_count=?, updated_at=? WHERE id=?",
                (open_c, t, item_id),
            )

        self.items.event(
            item_id, None, "todo.feedback_submitted", actor,
            {
                "submitted_count": len(created),
                "open_feedbacks": open_c,
                "total_feedbacks": total_c,
                "feedback_ids": [c["id"] for c in created],
            },
        )

        return {
            "item_id": item_id,
            "created_feedbacks": created,
            "total_feedbacks": total_c,
            "open_feedbacks": open_c,
        }

    def list_feedbacks(
        self,
        item_id: str,
        status: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM todo_feedbacks WHERE item_id=?"
        params: list[Any] = [item_id]
        if status:
            sql += " AND status=?"
            params.append(status.strip().lower())
        if category:
            sql += " AND category=?"
            params.append(category.strip().lower())
        sql += " ORDER BY created_at DESC"
        return self.store.query(sql, tuple(params))

    def get_feedback_summary(self, item_id: str) -> dict[str, int]:
        rows = self.store.query(
            "SELECT status, COUNT(*) as c FROM todo_feedbacks WHERE item_id=? GROUP BY status",
            (item_id,),
        )
        res = {"total": 0, "open": 0, "resolved": 0, "ignored": 0}
        for r in rows:
            st = str(r["status"]).lower()
            cnt = int(r["c"])
            res["total"] += cnt
            if st in res:
                res[st] = cnt
        return res

    def resolve_feedback(
        self,
        item_id: str,
        feedback_id: str,
        status: str = "resolved",
        resolution_note: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)
        row = self.store.query_one(
            "SELECT * FROM todo_feedbacks WHERE id=? AND item_id=?",
            (feedback_id, item_id),
        )
        if not row:
            raise KeyError(f"Feedback {feedback_id} not found in item {item_id}")

        valid_statuses = {"resolved", "ignored", "open"}
        norm_status = status.strip().lower()
        if norm_status not in valid_statuses:
            raise ValueError(f"Invalid status '{status}'; must be one of {valid_statuses}")

        t = now()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE todo_feedbacks SET status=?, resolution_note=?, updated_at=? WHERE id=?",
                (norm_status, resolution_note, t, feedback_id),
            )
            open_c = conn.execute(
                "SELECT COUNT(*) FROM todo_feedbacks WHERE item_id=? AND status='open'", (item_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE items SET feedbacks_count=?, updated_at=? WHERE id=?",
                (open_c, t, item_id),
            )

        self.items.event(
            item_id, None, "todo.feedback_resolved", actor,
            {"feedback_id": feedback_id, "status": norm_status, "resolution_note": resolution_note},
        )
        return self.store.query_one("SELECT * FROM todo_feedbacks WHERE id=?", (feedback_id,))

    def delete_feedback(
        self,
        item_id: str,
        feedback_id: str,
        actor: str = "user",
    ) -> dict[str, Any]:
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)
        row = self.store.query_one(
            "SELECT * FROM todo_feedbacks WHERE id=? AND item_id=?",
            (feedback_id, item_id),
        )
        if not row:
            raise KeyError(f"Feedback {feedback_id} not found in item {item_id}")
        t = now()
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM todo_feedbacks WHERE id=?", (feedback_id,))
            open_c = conn.execute(
                "SELECT COUNT(*) FROM todo_feedbacks WHERE item_id=? AND status='open'", (item_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE items SET feedbacks_count=?, updated_at=? WHERE id=?",
                (open_c, t, item_id),
            )
        self.items.event(item_id, None, "todo.feedback_deleted", actor, {"feedback_id": feedback_id})
        return {"deleted": True, "id": feedback_id, "item_id": item_id}

    # -- Audio Regeneration (Slower speech + Longer pause + Warm BGM) --------

    def regenerate_item_audio(
        self,
        item_id: str,
        slow_factor: float = 0.96,
        extra_pause_sec: float = 0.12,
        bgm_enabled: bool = True,
        bgm_duck_db: float = -20.0,
        actor: str = "api",
    ) -> dict[str, Any]:
        """Retime voiceover (atempo=slow_factor) + extra inter-shot pause + mix multi-track BGM."""
        item = self.items.get(item_id)
        if not item or item["status"] == "deleted":
            raise KeyError(item_id)

        audio_path = self.resolve_audio_path(item_id)
        if not audio_path.exists():
            raise FileNotFoundError(f"audio_en.wav not found for {item_id}")

        shots_rows = self.store.query(
            "SELECT index_num, shot_type, narration, visual_prompt, on_screen_text, on_screen_type, "
            "audio_start_sec, audio_end_sec "
            "FROM shots WHERE item_id=? AND status != 'superseded' ORDER BY index_num ASC",
            (item_id,),
        )
        if not shots_rows:
            raise ValueError(f"No shots found for item {item_id}")

        shots_list = [
            {
                "index": int(s["index_num"]),
                "shot_type": s["shot_type"],
                "narration": s["narration"],
                "visual_prompt": s["visual_prompt"],
                "visual_prompt_clean": s["visual_prompt"],
                "on_screen_text": s.get("on_screen_text"),
                "on_screen_type": s.get("on_screen_type"),
                "audio_start_sec": float(s["audio_start_sec"] or 0.0),
                "audio_end_sec": float(s["audio_end_sec"] or 0.0),
            }
            for s in shots_rows
        ]


        out_dir = data_dir() / "objects" / item_id / "todo"
        from .pipeline_en.audio_generator_en import retime_and_mix_existing_audio
        res = retime_and_mix_existing_audio(
            audio_path=audio_path,
            shots=shots_list,
            out_dir=out_dir,
            slow_factor=slow_factor,
            extra_pause_sec=extra_pause_sec,
            bgm_enabled=bgm_enabled,
            bgm_duck_db=bgm_duck_db,
            rotation_seed=item_id,
        )

        new_dur = res["audio_duration_sec"]
        new_shots = res["shots"]
        new_audio_sha = sha256_file(res["audio_path"])
        new_script_sha = sha256_file(res["script_path"])
        new_srt_sha = sha256_file(res["srt_path"])
        new_ass_sha = sha256_file(res["ass_path"])
        new_shots_hash = domain.canonical_shots_hash(new_shots)
        t = now()

        # Update SQLite database
        with self.store.transaction() as conn:
            for s in new_shots:
                conn.execute(
                    "UPDATE shots SET audio_start_sec=?, audio_end_sec=?, updated_at=? "
                    "WHERE item_id=? AND index_num=?",
                    (s["audio_start_sec"], s["audio_end_sec"], t, item_id, s["index"]),
                )
            conn.execute(
                "UPDATE items SET audio_duration_sec=?, bgm_enabled=?, updated_at=? WHERE id=?",
                (new_dur, 1 if bgm_enabled else 0, t, item_id),
            )
            conn.execute(
                "UPDATE audios SET duration_sec=?, hash=? WHERE item_id=?",
                (new_dur, new_audio_sha, item_id),
            )
            conn.execute(
                "UPDATE assets SET sha256=?, size=?, duration_s=? WHERE item_id=? AND kind='audio'",
                (new_audio_sha, res["audio_path"].stat().st_size, new_dur, item_id),
            )
            conn.execute(
                "UPDATE assets SET sha256=?, size=? WHERE item_id=? AND kind='script'",
                (new_script_sha, res["script_path"].stat().st_size, item_id),
            )
            conn.execute(
                "UPDATE assets SET sha256=?, size=? WHERE item_id=? AND storage_key LIKE '%subtitles_en.srt'",
                (new_srt_sha, res["srt_path"].stat().st_size, item_id),
            )
            conn.execute(
                "UPDATE assets SET sha256=?, size=? WHERE item_id=? AND storage_key LIKE '%subtitles_en.ass'",
                (new_ass_sha, res["ass_path"].stat().st_size, item_id),
            )
            conn.execute(
                "UPDATE scripts SET scenes_json=?, hash=?, shots_hash=? WHERE item_id=?",
                (json.dumps(new_shots, ensure_ascii=False), new_script_sha, new_shots_hash, item_id),
            )

        self.items.event(
            item_id, None, "todo.audio_regenerated", actor,
            {
                "audio_duration_sec": new_dur,
                "slow_factor": slow_factor,
                "extra_pause_sec": extra_pause_sec,
                "bgm_enabled": bgm_enabled,
                "audio_sha256": new_audio_sha,
                "total_shots": len(new_shots),
            },
        )

        return self.get_todo(item_id)

