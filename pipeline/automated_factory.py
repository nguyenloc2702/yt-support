"""Master Automated Video Factory Pipeline.

Implements end-to-end automation for both modes:
1. Mode 1: Smart Reup & Review (Cuts from original footage + Vietnamese TTS voiceover)
2. Mode 2: Full AI Genesis (Generates 100% new 720p/1080p AI video via Gemini Pro accounts + Ken Burns B-roll)

Key architectural features:
- SQLite task queue (`JobQueueManager`) managing multi-job lifecycles.
- Concurrency Semaphore Lock protecting VPS 6GB RAM (max 1 heavy Whisper/FFmpeg process).
- Subtitle extraction fallback (`yt-dlp --write-sub`) to bypass Whisper and save ~1.5GB RAM & 3-5m CPU.
- Telegram approval gate (`/approve <job_id>`, `/reject <job_id>`) with document attachments.
- Output validation before upload to Google Drive.
"""

import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from core.logging_config import get_logger
from pipeline.youtube import download_video
from services.job_queue import JobQueueManager
from services.notifier import TelegramNotifier
from pipeline.output_validator import validate_rendered_output
from services.storage_uploader import StorageUploader

logger = get_logger("automated_factory")


def try_extract_subtitles(project_dir: Path) -> Optional[List[Dict[str, Any]]]:
    """
    Checks if yt-dlp extracted any subtitle files (.vtt or .srt) in project_dir.
    If present, parses them directly into transcript segments, saving Whisper CPU & RAM!
    """
    sub_files = list(project_dir.glob("*.vtt")) + list(project_dir.glob("*.srt"))
    if not sub_files:
        return None

    # Prefer Vietnamese, then English, then any
    sub_file = sub_files[0]
    for sf in sub_files:
        if ".vi." in sf.name.lower():
            sub_file = sf
            break
        elif ".en." in sf.name.lower():
            sub_file = sf

    logger.info("Found pre-existing YouTube subtitles: %s. Bypassing Whisper STT!", sub_file.name)
    segments = []
    try:
        content = sub_file.read_text(encoding="utf-8", errors="ignore")
        # Simple VTT/SRT timestamp parser: 00:01:23.456 --> 00:01:25.789
        time_pattern = re.compile(r"(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{3})")
        lines = content.splitlines()
        current_start = None
        current_end = None
        current_text = []

        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("WEBVTT") or line_str.isdigit():
                continue
            match = time_pattern.search(line_str)
            if match:
                if current_start is not None and current_text:
                    segments.append({
                        "start": current_start,
                        "end": current_end,
                        "text": " ".join(current_text),
                    })
                    current_text = []
                # Parse timestamp
                def to_sec(h, m, s, ms):
                    h_val = int(h.rstrip(":")) if h else 0
                    return h_val * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

                current_start = to_sec(match.group(1), match.group(2), match.group(3), match.group(4))
                current_end = to_sec(match.group(5), match.group(6), match.group(7), match.group(8))
            else:
                # Text line (strip HTML tags like <c>)
                clean_text = re.sub(r"<[^>]+>", "", line_str)
                if clean_text:
                    current_text.append(clean_text)

        if current_start is not None and current_text:
            segments.append({
                "start": current_start,
                "end": current_end,
                "text": " ".join(current_text),
            })
    except Exception as e:
        logger.warning("Failed to parse subtitle file %s: %s", sub_file, e)
        return None

    return segments if segments else None


class AutomatedFactory:
    def __init__(self, output_dir: Optional[Path] = None, db_path: Optional[Path] = None):
        self.output_dir = Path(output_dir or "data/automated_runs")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.notifier = TelegramNotifier()
        self.uploader = StorageUploader()
        self.jq = JobQueueManager(db_path=db_path)

    # =========================================================================
    # MODE 1: SMART REUP & SUMMARY
    # =========================================================================

    def run_smart_reup(self, youtube_url: str, target_duration: float = 180.0) -> str:
        """
        Executes Mode 1: Smart Reup & Summary.
        Clones YouTube video, extracts highlights, writes Vietnamese review script (LLM),
        prepares beat timeline, requests Telegram approval, and prepares for TTS + BGM render.
        """
        job_id = self.jq.create_job(mode="reup", url=youtube_url, target_duration=target_duration)
        project_dir = self.output_dir / job_id
        project_dir.mkdir(parents=True, exist_ok=True)
        self.jq.update_job_status(job_id, status="ANALYZING")

        logger.info("=== STARTING SMART REUP PIPELINE: %s ===", job_id)
        if self.notifier.is_configured:
            self.notifier.send_message(
                f"🎬 <b>[MODE 1: SMART REUP] Nhận job: {job_id}</b>\n"
                f"🔗 Link: {youtube_url}\n"
                f"⏱ Thời lượng mục tiêu: {target_duration}s\n"
                f"<i>Đang tải video & trích xuất phụ đề/audio...</i>"
            )

        try:
            # 1. Download YouTube video
            logger.info("Downloading YouTube video for %s...", job_id)
            raw_video = download_video(youtube_url, project_dir, max_height=1080)

            # 2. Extract Transcript (Try subtitles first to save Whisper CPU/RAM)
            raw_sub_segments = try_extract_subtitles(project_dir)
            if not raw_sub_segments:
                # Concurrency Semaphore check before running heavy Whisper
                while not self.jq.acquire_heavy_lock(job_id):
                    logger.info("VPS đang bận xử lý 1 tác vụ nặng khác. Job %s chờ 15s...", job_id)
                    time.sleep(15)

                try:
                    if self.notifier.is_configured:
                        self.notifier.send_message(f"🎙 <i>[Job {job_id}] Đang bóc băng Whisper lời thoại gốc...</i>")
                    from pipeline.transcribe import transcribe_audio
                    stt_res = transcribe_audio(raw_video, model_size="small", compute_type="int8")
                    raw_sub_segments = stt_res.get("segments", [])
                finally:
                    self.jq.release_heavy_lock(job_id)

            # Standardize segments with 1-based sequential IDs
            segments = []
            for i, seg in enumerate(raw_sub_segments or []):
                text = str(seg.get("text", "")).strip()
                if text:
                    segments.append({
                        "id": i + 1,
                        "start": float(seg.get("start", 0.0)),
                        "end": float(seg.get("end", 0.0)),
                        "text": text,
                    })

            if not segments:
                # Fallback synthetic segments if video is completely silent
                segments = [
                    {"id": 1, "start": 0.0, "end": min(30.0, target_duration / 3), "text": "Đoạn mở đầu video"},
                    {"id": 2, "start": 30.0, "end": min(90.0, target_duration * 2 / 3), "text": "Diễn biến trung tâm"},
                    {"id": 3, "start": 90.0, "end": target_duration, "text": "Phần cao trào kết thúc"},
                ]

            transcript_file = project_dir / "transcript.json"
            transcript_data = {"segments": segments}
            transcript_file.write_text(json.dumps(transcript_data, indent=2, ensure_ascii=False), encoding="utf-8")

            # 3. Rewrite & Paraphrase Script to Vietnamese (LLM with structured beat fallback)
            logger.info("Generating Vietnamese review script for %s...", job_id)
            script_data = None
            try:
                from pipeline.script_rewrite import rewrite_script
                script_data = rewrite_script(transcript_data, target_duration_seconds=target_duration)
                logger.info("LLM rewrite succeeded with %d beats.", len(script_data.get("beats", [])))
            except Exception as e:
                logger.warning("LLM rewrite unavailable (%s). Synthesizing structured beats from transcript.", e)

            if not script_data or not script_data.get("beats"):
                # Deterministic highlight decomposition matching target duration
                num_beats = max(3, min(8, int(target_duration // 30)))
                step = max(1, len(segments) // num_beats)
                beats = []
                emotions = ["hype", "bua", "hoang_mang", "xuc_dong", "hype"]
                for b_idx in range(num_beats):
                    seg_idx = min(b_idx * step, len(segments) - 1)
                    target_seg = segments[seg_idx]
                    emo = emotions[b_idx % len(emotions)]
                    if b_idx == 0:
                        b_text = f"Chào mừng các bạn đến với bản tóm tắt siêu cuốn! Mở đầu câu chuyện: {target_seg['text'][:80]}."
                    elif b_idx == num_beats - 1:
                        b_text = f"Và cái kết thực sự bất ngờ ngoài dự đoán! {target_seg['text'][:80]}! 10 điểm không có nhưng!"
                    else:
                        b_text = f"Diễn biến tiếp theo khiến người xem ngỡ ngàng: {target_seg['text'][:90]}."

                    beats.append({
                        "id": b_idx,
                        "text": b_text,
                        "source_segment_ids": [target_seg["id"]],
                        "emotion": emo,
                    })

                script_data = {
                    "title": f"Review Video {job_id}",
                    "tone": "bựa bựa, hài hước, review cuốn",
                    "beats": beats,
                    "status": "approved",
                    "target_duration_seconds": target_duration,
                    "duration_tolerance_seconds": 2.0,
                }

            script_file = project_dir / "script.json"
            script_file.write_text(json.dumps(script_data, indent=2, ensure_ascii=False), encoding="utf-8")

            # Format markdown summary for reading and Telegram preview
            md_lines = [
                f"# Kịch bản Review: {script_data.get('title', job_id)}",
                f"**Thời lượng mục tiêu:** {target_duration}s | **Số beat:** {len(script_data.get('beats', []))}",
                "",
                "---",
            ]
            for b in script_data.get("beats", []):
                md_lines.append(f"### Beat {b['id'] + 1} [{b.get('emotion', 'neutral')}]:")
                md_lines.append(f"> {b.get('text', '')}")
                md_lines.append("")
            script_md = project_dir / "script.md"
            script_md.write_text("\n".join(md_lines), encoding="utf-8")

            # 4. Set job to WAITING_APPROVAL
            summary_preview = "\n".join(
                f"• Beat {b['id'] + 1} ({b.get('emotion', 'hype')}): {b.get('text', '')[:65]}..."
                for b in script_data.get("beats", [])[:4]
            )
            self.jq.update_job_status(
                job_id,
                status="WAITING_APPROVAL",
                script_summary=summary_preview[:400],
                script_file=str(script_md),
            )

            # 5. Notify Telegram for Approval
            if self.notifier.is_configured:
                approval_msg = (
                    f"📝 <b>[MODE 1: CHỜ DUYỆT KỊCH BẢN] Job: {job_id}</b>\n\n"
                    f"📄 <b>Kịch bản voiceover ({len(script_data.get('beats', []))} beats, {target_duration}s):</b>\n"
                    f"<i>{summary_preview}</i>\n\n"
                    f"👉 <b>Hành động duyệt:</b>\n"
                    f"• Gõ: <code>/approve {job_id}</code> để cắt ghép cảnh khớp kịch bản, lồng tiếng Edge-TTS & BGM.\n"
                    f"• Gõ: <code>/reject {job_id}</code> để hủy bỏ job này."
                )
                self.notifier.send_message(approval_msg)

            return job_id

        except Exception as e:
            logger.error("Smart Reup pipeline failed for %s: %s", job_id, e, exc_info=True)
            self.jq.update_job_status(job_id, status="FAILED", error_message=str(e))
            self.jq.release_heavy_lock(job_id)
            if self.notifier.is_configured:
                self.notifier.send_message(f"❌ <b>[LỖI SMART REUP - {job_id}]:</b> <code>{str(e)[:300]}</code>")
            return job_id

    def execute_approved_reup(self, job_id: str):
        """Executes the rendering phase for an approved Mode 1 job."""
        job = self.jq.get_job(job_id)
        if not job or job["status"] != "APPROVED":
            return

        self.jq.update_job_status(job_id, status="RENDERING")
        project_dir = Path(job["project_dir"] or (self.output_dir / job_id))
        output_final = project_dir / f"final_{job_id}.mp4"

        # Concurrency Semaphore check before heavy FFmpeg render
        while not self.jq.acquire_heavy_lock(job_id):
            time.sleep(15)

        temp_render_dir = project_dir / "temp_reup_render"
        temp_tts_dir = project_dir / "temp_tts"

        try:
            logger.info("Executing approved Smart Reup render for %s...", job_id)
            raw_video = next(project_dir.glob("download.*"), None)
            if not raw_video:
                raise FileNotFoundError(f"Source video not found in {project_dir}")

            # 1. Load script and transcript
            script_file = project_dir / "script.json"
            transcript_file = project_dir / "transcript.json"
            if not script_file.exists() or not transcript_file.exists():
                raise FileNotFoundError(f"Missing script.json or transcript.json in {project_dir}")

            script_data = json.loads(script_file.read_text(encoding="utf-8"))
            transcript_data = json.loads(transcript_file.read_text(encoding="utf-8"))

            # 2. Match video scenes to script beats using script-led planner
            from pipeline.script_timeline_planner import plan_script_timeline
            timeline = plan_script_timeline(script_data, transcript_data)
            timeline_file = project_dir / "timeline.json"
            timeline_file.write_text(json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")

            # 3. Cut matched source video clips
            temp_render_dir.mkdir(parents=True, exist_ok=True)
            clip_files = []
            clips = [c for c in timeline.get("clips", []) if c.get("enabled", True)]
            if not clips:
                raise ValueError("Timeline contains no enabled video clips.")

            logger.info("Cutting %d video clips according to script timeline...", len(clips))
            for i, clip in enumerate(clips):
                c_start = float(clip.get("source_start", 0.0))
                c_end = float(clip.get("source_end", c_start + 5.0))
                c_dur = max(0.5, c_end - c_start)
                clip_out = temp_render_dir / f"clip_{i:03d}.mp4"

                cmd_cut = [
                    "ffmpeg", "-y",
                    "-ss", f"{c_start:.3f}",
                    "-i", str(raw_video),
                    "-t", f"{c_dur:.3f}",
                    "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
                    "-r", "30",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    str(clip_out),
                ]
                subprocess.run(cmd_cut, check=True, capture_output=True)
                clip_files.append(clip_out)

            # Concatenate cut clips into joined video
            concat_list = temp_render_dir / "concat.txt"
            concat_lines = [f"file '{cf.resolve().as_posix()}'" for cf in clip_files]
            concat_list.write_text("\n".join(concat_lines), encoding="utf-8")
            joined_video = temp_render_dir / "joined.mp4"

            cmd_join = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                str(joined_video),
            ]
            subprocess.run(cmd_join, check=True, capture_output=True)

            # 4. Generate Vietnamese Edge-TTS voiceover and mix with auto-ducking BGM
            from pipeline.tts import generate_and_overlay_narration
            temp_tts_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Synthesizing Edge-TTS voiceover with auto-ducked original audio...")

            narrated_video = generate_and_overlay_narration(
                video_path=joined_video,
                timeline=timeline,
                output_path=output_final,
                voice="vi-VN-HoaiMyNeural",
                gain_db=1.5,
                duck_gain_db=-12.0,
                temp_dir=temp_tts_dir,
            )

            if not narrated_video or not output_final.exists() or output_final.stat().st_size == 0:
                logger.warning("TTS overlay did not produce output; copying joined video directly.")
                import shutil
                shutil.copy2(joined_video, output_final)

            # 5. Quality Control validation
            val_res = validate_rendered_output(output_final)
            if not val_res.is_valid:
                raise ValueError(f"Output video QC failed: {val_res.error_message}")

            # 6. Upload to Google Drive and clean up local video buffer
            remote_name = f"reup_{job_id}.mp4"
            upload_ok, drive_url = self.uploader.upload_and_clean(
                local_path=output_final,
                remote_filename=remote_name,
                remote_folder="Video_Projects/reup_videos",
                clean_local=True,
            )

            if upload_ok:
                duration_val = timeline.get("actual_duration", job.get("target_duration", 180.0))
                self.jq.update_job_status(job_id, status="COMPLETED", final_video=str(output_final), drive_url=drive_url)
                if self.notifier.is_configured:
                    self.notifier.send_message(
                        f"🎉 <b>[SMART REUP HOÀN TẤT] Job: {job_id}</b>\n"
                        f"✅ Video đã dựng khớp kịch bản, lồng tiếng Edge-TTS & BGM auto-ducking thành công!\n"
                        f"☁️ Google Drive: <code>{drive_url}</code>\n"
                        f"⏱ Thời lượng: {duration_val:.1f}s"
                    )
            else:
                self.jq.update_job_status(job_id, status="FAILED", error_message="Upload to Google Drive failed")

        except Exception as e:
            logger.error("Render failed for %s: %s", job_id, e, exc_info=True)
            self.jq.update_job_status(job_id, status="FAILED", error_message=str(e))
            if self.notifier.is_configured:
                self.notifier.send_message(f"❌ <b>[LỖI RENDER - {job_id}]:</b> <code>{str(e)[:300]}</code>")
        finally:
            import shutil
            shutil.rmtree(temp_render_dir, ignore_errors=True)
            shutil.rmtree(temp_tts_dir, ignore_errors=True)
            self.jq.release_heavy_lock(job_id)

    # =========================================================================
    # MODE 2: FULL AI GENESIS
    # =========================================================================

    def run_ai_genesis(self, youtube_url: str) -> str:
        """
        Executes Mode 2: Full AI Genesis.
        Extracts narrative structure, decomposes into dynamic granular 10-15s shots,
        identifies dramatic key scenes, and requests approval for autonomous multi-account rendering.
        """
        job_id = self.jq.create_job(mode="remake", url=youtube_url)
        project_dir = self.output_dir / job_id
        project_dir.mkdir(parents=True, exist_ok=True)
        self.jq.update_job_status(job_id, status="ANALYZING")

        logger.info("=== STARTING FULL AI GENESIS PIPELINE: %s ===", job_id)
        if self.notifier.is_configured:
            self.notifier.send_message(
                f"🎬 <b>[MODE 2: AI GENESIS] Nhận job: {job_id}</b>\n"
                f"🔗 Link: {youtube_url}\n"
                f"<i>Đang phân tích cốt truyện & lập danh sách shot điện ảnh...</i>"
            )

        try:
            # 1. Download YouTube video
            raw_video = download_video(youtube_url, project_dir, max_height=1080)

            # 2. Extract Transcript (Subtitles first, then Whisper)
            sub_segments = try_extract_subtitles(project_dir)
            if not sub_segments:
                while not self.jq.acquire_heavy_lock(job_id):
                    time.sleep(15)
                try:
                    from pipeline.transcribe import transcribe_audio
                    stt_res = transcribe_audio(raw_video, model_size="small", compute_type="int8")
                    sub_segments = stt_res.get("segments", [])
                finally:
                    self.jq.release_heavy_lock(job_id)

            # 3. Dynamic Narrative Decomposition (Granular shots across documentary storyline)
            total_dur = max((float(s.get("end", 0)) for s in (sub_segments or [])), default=180.0)
            target_shots = max(6, min(24, int(total_dur // 15)))

            dramatic_cues = {
                "đột ngột", "quyết định", "khủng hoảng", "bước ngoặt", "thành công",
                "thất bại", "chiến thắng", "sụp đổ", "ra mắt", "bắt đầu", "shock",
                "war", "battle", "turning point", "founded", "crisis", "breakthrough", "launch"
            }

            step = max(1, len(sub_segments) // target_shots) if sub_segments else 1
            shots = []
            for idx in range(1, target_shots + 1):
                seg_idx = min((idx - 1) * step, len(sub_segments) - 1) if sub_segments else None
                seg_text = sub_segments[seg_idx].get("text", "") if (seg_idx is not None and seg_idx < len(sub_segments)) else f"Historical milestone scene {idx}"
                clean_seg_text = seg_text.replace("\n", " ").strip()

                is_key = any(cue in clean_seg_text.lower() for cue in dramatic_cues) or (idx % 2 != 0)

                shots.append({
                    "index": idx,
                    "prompt": (
                        f"Cinematic 4K, professional documentary cinematography. Visual representation of: "
                        f"{clean_seg_text[:140]}. 35mm master lens, photorealistic atmospheric depth, "
                        f"dramatic cinematic lighting, silent, no audio."
                    ),
                    "duration": 10.0,
                    "is_key_scene": is_key,
                })

            # Save shots plan
            shots_file = project_dir / "shots_plan.json"
            shots_file.write_text(json.dumps(shots, indent=2, ensure_ascii=False), encoding="utf-8")

            # Store in database
            self.jq.add_shots_for_job(job_id, shots)
            key_count = sum(1 for s in shots if s["is_key_scene"])
            broll_count = len(shots) - key_count
            self.jq.update_job_status(
                job_id,
                status="WAITING_APPROVAL",
                script_summary=f"Tạo {len(shots)} cảnh AI (10s/cảnh: {key_count} Veo Key Scenes + {broll_count} B-roll) tái hiện toàn bộ câu chuyện.",
                script_file=str(shots_file),
            )

            # Notify Telegram
            if self.notifier.is_configured:
                approval_msg = (
                    f"🎬 <b>[MODE 2: CHỜ DUYỆT SHOTS AI] Job: {job_id}</b>\n\n"
                    f"📊 Tổng số cảnh: <b>{len(shots)} cảnh (10s/cảnh)</b>\n"
                    f"🌟 <b>{key_count} cảnh Veo điện ảnh</b> + 🖼 <b>{broll_count} cảnh Ken Burns</b>\n"
                    f"👤 Sẽ điều phối pool tài khoản Pro sinh video tuần tự & tự động ráp phim cuối.\n\n"
                    f"👉 <b>Hành động:</b>\n"
                    f"• Gõ <code>/approve {job_id}</code> để kích hoạt sinh video.\n"
                    f"• Gõ <code>/reject {job_id}</code> để hủy job."
                )
                self.notifier.send_message(approval_msg)

            return job_id

        except Exception as e:
            logger.error("AI Genesis pipeline failed for %s: %s", job_id, e, exc_info=True)
            self.jq.update_job_status(job_id, status="FAILED", error_message=str(e))
            self.jq.release_heavy_lock(job_id)
            if self.notifier.is_configured:
                self.notifier.send_message(f"❌ <b>[LỖI AI GENESIS - {job_id}]:</b> <code>{str(e)[:300]}</code>")
            return job_id
