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
from typing import Any, Dict, List, Optional, Tuple

from core.logging_config import get_logger
from pipeline.youtube import download_video, probe_youtube_title
from services.job_queue import JobQueueManager
from services.notifier import TelegramNotifier
from pipeline.output_validator import validate_rendered_output
from services.storage_uploader import StorageUploader
from services.prompt_sanitizer import clean_narration_for_tts, sanitize_veo_prompt

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
            if not line_str or line_str.startswith("WEBVTT") or line_str.isdigit() or line_str.lower().startswith(("kind:", "language:", "align:", "position:", "note")):
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

        segments = deduplicate_vtt_segments(segments)

    except Exception as e:
        logger.warning("Failed to parse subtitle file %s: %s", sub_file, e)
        return None

    return segments if segments else None


def deduplicate_vtt_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicates rolling-window subtitles common in YouTube auto-generated VTT."""
    deduped = []
    prev_clean = ""
    for seg in segments:
        txt = re.sub(r"\s+", " ", seg.get("text", "")).strip()
        if not txt:
            continue
        clean_lower = txt.lower()
        if clean_lower == prev_clean:
            continue
        if prev_clean and prev_clean in clean_lower and len(clean_lower) > len(prev_clean):
            if deduped:
                deduped[-1]["end"] = seg["end"]
                deduped[-1]["text"] = txt
                prev_clean = clean_lower
                continue
        if prev_clean and clean_lower in prev_clean:
            continue
        start_t = float(seg.get("start", 0.0))
        end_t = float(seg.get("end", start_t + 1.0))
        if end_t <= start_t:
            end_t = start_t + 1.0
        deduped.append({"start": start_t, "end": end_t, "text": txt})
        prev_clean = clean_lower
    return deduped


def send_voice_previews(notifier: TelegramNotifier, job_id: str, project_dir: Path, target_lang: str = "vi") -> None:
    """Generates 3 short voice samples (Nam Minh, Hoai My, Guy) and sends them to Telegram."""
    if not notifier or not notifier.is_configured:
        return
    samples_dir = project_dir / "voice_samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    voices_to_preview = [
        ("vi-VN-NamMinhNeural", "Nam Minh (Nam Việt - Đĩnh đạc / Phim tài liệu)", "Xin chào, đây là giọng đọc tài liệu điện ảnh của Nam Minh."),
        ("vi-VN-HoaiMyNeural", "Hoài My (Nữ Việt - Truyền cảm / Nhẹ nhàng)", "Xin chào, đây là giọng đọc mẫu của Hoài My."),
        ("en-US-GuyNeural", "Guy (Nam Mỹ - Chuẩn Documentary English)", "Hello, this is a cinematic documentary voice preview by Guy."),
    ]

    from services.voice_service import preview_voice
    for voice_id, label, text in voices_to_preview:
        sample_path = samples_dir / f"{voice_id}.mp3"
        try:
            if not sample_path.exists() or sample_path.stat().st_size == 0:
                preview_voice(voice_id, sample_path, sample_text=text)
            notifier.send_voice(str(sample_path), caption=f"🔊 <b>Mẫu giọng:</b> {label}\n👉 Chọn: <code>/voice {job_id} {voice_id}</code>")
        except Exception as e:
            logger.warning("Failed to generate voice sample for %s: %s", voice_id, e)


class AutomatedFactory:
    def __init__(
        self,
        output_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
        account_pool=None,
    ):
        self.output_dir = Path(output_dir or "data/automated_runs")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.notifier = TelegramNotifier()
        self.uploader = StorageUploader()
        self.jq = JobQueueManager(db_path=db_path)
        if account_pool is None:
            from services.account_pool import AccountPoolManager

            account_pool = AccountPoolManager()
        self.account_pool = account_pool

    # =========================================================================
    # MODE 1: SMART REUP & SUMMARY
    # =========================================================================

    def run_smart_reup(self, youtube_url: str, target_duration: float = 180.0, job_id: Optional[str] = None) -> str:
        """
        Executes Mode 1: Smart Reup & Summary.
        Clones YouTube video, extracts highlights, writes Vietnamese review script (LLM),
        prepares beat timeline, requests Telegram approval, and prepares for TTS + BGM render.
        """
        source_title = probe_youtube_title(youtube_url)
        if not job_id:
            job_id = self.jq.create_job(mode="reup", url=youtube_url, target_duration=target_duration, source_title=source_title)
        elif source_title:
            with self.jq._get_conn() as conn:
                conn.execute("UPDATE factory_jobs SET source_title = ? WHERE id = ? AND (source_title IS NULL OR source_title = '')", (source_title, job_id))
                conn.commit()
        project_dir = self.output_dir / job_id
        project_dir.mkdir(parents=True, exist_ok=True)
        self.jq.transition_job(job_id, to_state="ANALYZING", project_dir=str(project_dir))

        logger.info("=== STARTING SMART REUP PIPELINE: %s ===", job_id)
        if self.notifier.is_configured:
            title_line = f"📺 Tiêu đề: <b>{source_title}</b>\n" if source_title else ""
            self.notifier.send_message(
                f"🎬 <b>[MODE 1: SMART REUP] Nhận job: {job_id}</b>\n"
                f"{title_line}"
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
            transcript_source = "youtube_subtitles"
            if not raw_sub_segments:
                transcript_source = "whisper_stt"
                if self.notifier.is_configured:
                    self.notifier.send_message(f"🎙 <i>[Job {job_id}] Đang bóc băng Whisper lời thoại gốc...</i>")

                # Concurrency Semaphore check with active heartbeat during heavy Whisper
                with self.jq.heavy_lock_lease(job_id, interval=15.0):
                    from pipeline.transcribe import transcribe_audio
                    stt_res = transcribe_audio(raw_video, model_size="small", compute_type="int8")
                    raw_sub_segments = stt_res.get("segments", [])

            logger.info("Transcript acquired from source: %s (%d segments)", transcript_source, len(raw_sub_segments or []))

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
                seg_dur = target_duration / 3.0
                segments = [
                    {"id": 1, "start": 0.0, "end": seg_dur, "text": "Đoạn mở đầu video"},
                    {"id": 2, "start": seg_dur, "end": seg_dur * 2, "text": "Diễn biến trung tâm"},
                    {"id": 3, "start": seg_dur * 2, "end": target_duration, "text": "Phần cao trào kết thúc"},
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
                    "status": "pending_approval",
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

            # Also generate clean human-readable .txt script file for final inspection
            txt_lines = [
                "=" * 80,
                f"🎬 KỊCH BẢN REVIEW VOICE-OVER TIẾNG VIỆT",
                f"Job ID: {job_id}",
                f"Tiêu đề: {source_title or script_data.get('title', 'Video')}",
                f"Thời lượng mục tiêu: {target_duration}s | Số beat: {len(script_data.get('beats', []))}",
                "=" * 80,
                "",
            ]
            for b in script_data.get("beats", []):
                txt_lines.append(f"[BEAT {b['id'] + 1:02d}] ({b.get('emotion', 'neutral')}):")
                txt_lines.append(f"Lời thoại: {b.get('text', '')}")
                txt_lines.append("-" * 40)
            txt_lines.extend([
                "",
                "=" * 80,
                "👉 DUYỆT HOẶC CHỈNH SỬA:",
                f"- Để chốt kịch bản và tiến hành render: /approve {job_id}",
                f"- Để hủy bỏ job này: /reject {job_id}",
                "=" * 80,
            ])
            script_txt = project_dir / f"kich_ban_VI_{job_id}.txt"
            script_txt.write_text("\n".join(txt_lines), encoding="utf-8")

            # 4. Set job to WAITING_APPROVAL
            summary_preview = "\n".join(
                f"• Beat {b['id'] + 1} ({b.get('emotion', 'hype')}): {b.get('text', '')[:65]}..."
                for b in script_data.get("beats", [])[:4]
            )
            self.jq.update_job_status(
                job_id,
                status="WAITING_APPROVAL",
                script_summary=summary_preview[:400],
                script_file=str(script_txt),
            )

            # 5. Notify Telegram for Approval & Send Script Document
            if self.notifier.is_configured:
                self.notifier.send_document(
                    str(script_txt),
                    caption=(
                        f"📜 <b>KỊCH BẢN REVIEW TIẾNG VIỆT TOÀN VĂN - Job {job_id}</b>\n"
                        f"⏱ Thời lượng: ~{target_duration:.0f}s ({len(script_data.get('beats', []))} beats)\n"
                        f"👉 <i>Tải về để tra cứu phát cuối trước khi duyệt!</i>"
                    ),
                )
                approval_msg = (
                    f"📝 <b>[MODE 1: CHỜ DUYỆT KỊCH BẢN] Job: {job_id}</b>\n\n"
                    f"📄 <b>Kịch bản voiceover ({len(script_data.get('beats', []))} beats, {target_duration}s):</b>\n"
                    f"<i>{summary_preview}</i>\n\n"
                    f"📑 <i>File kịch bản toàn văn <code>.txt</code> đã được gửi kèm ở trên.</i>\n\n"
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
        if not job or job.get("status") != "APPROVED" or str(job.get("mode", "")).upper() not in ("SMART_REUP", "REUP"):
            logger.info("Job %s is not an APPROVED Smart Reup job. Skipping reup execution.", job_id)
            return False

        self.jq.update_job_status(job_id, status="RENDERING")
        project_dir = Path(job["project_dir"] or (self.output_dir / job_id))
        output_final = project_dir / f"final_{job_id}.mp4"

        temp_render_dir = project_dir / "temp_reup_render"
        temp_tts_dir = project_dir / "temp_tts"

        try:
            with self.jq.heavy_lock_lease(job_id, interval=15.0):
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
                    raise RuntimeError(f"TTS narration overlay failed to generate voiceover video for job {job_id}")

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
                    return True
                else:
                    self.jq.update_job_status(job_id, status="FAILED", error_message="Upload to Google Drive failed")
                    return False

        except Exception as e:
            logger.error("Render failed for %s: %s", job_id, e, exc_info=True)
            self.jq.update_job_status(job_id, status="FAILED", error_message=str(e))
            if self.notifier.is_configured:
                self.notifier.send_message(f"❌ <b>[LỖI RENDER - {job_id}]:</b> <code>{str(e)[:300]}</code>")
            return False
        finally:
            import shutil
            shutil.rmtree(temp_render_dir, ignore_errors=True)
            shutil.rmtree(temp_tts_dir, ignore_errors=True)

    # =========================================================================
    # MODE 2: FULL AI GENESIS
    # =========================================================================

    def extract_and_export_original_script(
        self,
        youtube_url: str,
        job_id: str,
        target_scenes: int = 24,
    ) -> Tuple[Path, Path, str, int]:
        """
        Quickly extracts subtitles/transcript from a YouTube URL and exports:
        1. job_{job_id}_{safe_title}_subtitles.srt
        2. job_{job_id}_{safe_title}_kich_ban_goc.txt
        Returns (srt_path, txt_path, source_title, segment_count).
        """
        source_title = probe_youtube_title(youtube_url) or "YouTube_Video"
        safe_title = re.sub(r'[^a-zA-Z0-9_\-\u00C0-\u024F\u1EA0-\u1EF9]+', '_', source_title).strip('_')[:40] or "video"
        project_dir = self.output_dir / job_id
        project_dir.mkdir(parents=True, exist_ok=True)

        with self.jq._get_conn() as conn:
            conn.execute(
                "UPDATE factory_jobs SET source_title = ?, project_dir = ? WHERE id = ?",
                (source_title, str(project_dir), job_id)
            )
            conn.commit()

        # 1. Try fast subtitle extraction via yt-dlp --skip-download (2-5s)
        from pipeline.youtube import download_subtitles_fast, download_audio_fast
        sub_segments = download_subtitles_fast(youtube_url, project_dir)

        # 2. Fallback to Whisper audio transcription if no captions on YouTube
        if not sub_segments:
            logger.info("No subtitles on YouTube for %s. Transcribing audio with Whisper...", job_id)
            if self.notifier.is_configured:
                self.notifier.send_message(f"🎙 <i>[Job {job_id}] Video không có phụ đề sẵn. Đang bóc băng bằng Whisper AI...</i>")
            with self.jq.heavy_lock_lease(job_id, interval=15.0):
                audio_file = download_audio_fast(youtube_url, project_dir)
                from pipeline.transcribe import transcribe_audio
                stt_res = transcribe_audio(audio_file, model_size="small", compute_type="int8")
                sub_segments = stt_res.get("segments", [])

        if not sub_segments:
            sub_segments = [
                {"start": 0.0, "end": 10.0, "text": "Đoạn mở đầu video."},
                {"start": 10.0, "end": 20.0, "text": "Diễn biến trung tâm câu chuyện."},
                {"start": 20.0, "end": 30.0, "text": "Phần cao trào và kết thúc."},
            ]

        # 3. Export to SRT & TXT formats
        from services.script_auditor import export_segments_to_srt, export_segments_to_txt
        srt_path = project_dir / f"job_{job_id}_{safe_title}_subtitles.srt"
        txt_path = project_dir / f"job_{job_id}_{safe_title}_kich_ban_goc.txt"

        export_segments_to_srt(sub_segments, srt_path)
        export_segments_to_txt(sub_segments, txt_path, title=source_title, url=youtube_url, target_scenes=target_scenes)

        # Cache transcript.json
        (project_dir / "transcript.json").write_text(
            json.dumps({"segments": sub_segments}, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        return srt_path, txt_path, source_title, len(sub_segments)

    def run_ai_genesis(
        self,
        youtube_url: str,
        job_id: Optional[str] = None,
        target_duration: Optional[float] = None,
        custom_script: Optional[str] = None,
        chosen_voice: Optional[str] = None,
        preview_voice_samples: bool = True,
    ) -> str:
        """
        Executes Mode 2: Full AI Genesis.
        Supports:
        - Option 1 (AI Genesis): Generates dual scripts (Vietnamese & English) from YouTube video.
        - Option 2 (Custom Script): Audits custom script, verifies pacing & safety, and decomposes into shots.
        - Dynamic Voice Audition: Sends 3-5s voice samples to Telegram for user selection.
        """
        source_title = probe_youtube_title(youtube_url)
        if not job_id:
            job_id = self.jq.create_job(mode="remake", url=youtube_url, target_duration=target_duration or 180.0, source_title=source_title)
        elif source_title:
            with self.jq._get_conn() as conn:
                conn.execute("UPDATE factory_jobs SET source_title = ? WHERE id = ? AND (source_title IS NULL OR source_title = '')", (source_title, job_id))
                conn.commit()
        project_dir = self.output_dir / job_id
        project_dir.mkdir(parents=True, exist_ok=True)
        self.jq.transition_job(job_id, to_state="ANALYZING", project_dir=str(project_dir))

        from services.script_auditor import ScriptAuditor, detect_language, VOICE_MAP

        logger.info("=== STARTING FULL AI GENESIS PIPELINE: %s ===", job_id)
        if self.notifier.is_configured:
            script_mode_desc = "Tự cung cấp kịch bản" if custom_script else "AI tự động biên kịch song ngữ"
            title_line = f"📺 Tiêu đề: <b>{source_title}</b>\n" if source_title else ""
            self.notifier.send_message(
                f"🎬 <b>[MODE 2: AI GENESIS] Nhận job: {job_id}</b>\n"
                f"{title_line}"
                f"🔗 Link: {youtube_url}\n"
                f"⚙️ Chế độ kịch bản: <i>{script_mode_desc}</i>\n"
                f"<i>Đang phân tích cốt truyện & lập danh sách shot điện ảnh...</i>"
            )

        try:
            shots = []
            vi_scenes = []
            en_scenes = []
            default_shot_duration = 5.0
            audit_res = None

            if custom_script:
                # Option 2: Custom script flow
                from services.script_auditor import ScriptAuditor, detect_language
                auditor = ScriptAuditor(algorithm_mode=True)
                audit_res = auditor.audit_script(custom_script)
                if not audit_res.is_valid:
                    logger.warning("Custom script for job %s failed audit: %s", job_id, audit_res.issues)
                    self.jq.transition_job(job_id, to_state="AWAITING_CUSTOM_SCRIPT")
                    if self.notifier.is_configured:
                        self.notifier.send_message(audit_res.format_telegram_feedback(job_id))
                    return job_id

                parsed_scenes = auditor.parse_raw_script(custom_script)
                target_shots = len(parsed_scenes)
                detected_lang = detect_language(custom_script)
                for idx, text in enumerate(parsed_scenes, 1):
                    clean_text = clean_narration_for_tts(text)
                    prompt = sanitize_veo_prompt(clean_text, scene_idx=idx, total_scenes=target_shots)
                    scene_item = {
                        "index": idx,
                        "text": clean_text,
                        "prompt": prompt,
                        "duration": default_shot_duration,
                        "is_key_scene": True,
                        "character_ids": [],
                    }
                    if detected_lang == "vi":
                        vi_scenes.append(scene_item)
                    else:
                        en_scenes.append(scene_item)

                shots = vi_scenes if vi_scenes else en_scenes
            else:
                # Option 1: AI Dual-Script Genesis from YouTube video
                sub_segments = None
                transcript_file = project_dir / "transcript.json"
                if transcript_file.exists():
                    try:
                        sub_segments = json.loads(transcript_file.read_text(encoding="utf-8")).get("segments", [])
                    except Exception:
                        pass

                if not sub_segments:
                    from pipeline.youtube import download_subtitles_fast
                    sub_segments = download_subtitles_fast(youtube_url, project_dir)

                if not sub_segments:
                    raw_video = download_video(youtube_url, project_dir, max_height=1080)
                    with self.jq.heavy_lock_lease(job_id, interval=15.0):
                        from pipeline.transcribe import transcribe_audio
                        stt_res = transcribe_audio(raw_video, model_size="small", compute_type="int8")
                        sub_segments = stt_res.get("segments", [])

                total_dur = max((float(s.get("end", 0)) for s in (sub_segments or [])), default=180.0)
                # Determine target duration: if user specified duration, use it;
                # otherwise match original video duration!
                effective_duration = float(target_duration) if (target_duration and target_duration > 0) else total_dur
                # Proportional shot count (5.0s per shot, allowing full narrative coverage up to 180 shots / 15 mins)
                target_shots = max(6, min(180, int(round(effective_duration / default_shot_duration))))

                # Phase 2: best-of-N grounded rewrite with LLM judging.
                # Fails loudly (ScriptFactoryError) when the xKiro gateway is unusable.
                from services.script_factory import ScriptFactory
                vi_scenes, en_scenes, algo_result = ScriptFactory().run(
                    sub_segments or [], target_shots=target_shots, project_dir=project_dir
                )
                # Record the user-facing target so the QA scorecard can score drift
                algo_result["target_duration_sec"] = round(effective_duration, 1)
                (project_dir / "algorithm_result.json").write_text(
                    json.dumps(algo_result, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                logger.info(
                    "Script factory chose angle '%s' (score %.1f/%.1f, %d rounds, metrics: %s)",
                    algo_result["chosen_angle"],
                    algo_result["chosen_score"],
                    algo_result["min_score"],
                    algo_result["total_rounds"],
                    {
                        k: algo_result["mechanical_metrics"][k]
                        for k in ("short_sentence_pct", "estimated_wpm", "total_duration_sec")
                    },
                )

                if chosen_voice and "vietnamese" not in chosen_voice:
                    shots = en_scenes
                else:
                    shots = vi_scenes

                # Persist both scripts for downstream tooling
                shots_vi_file = project_dir / "shots_plan_vi.json"
                shots_vi_file.write_text(json.dumps(vi_scenes, indent=2, ensure_ascii=False), encoding="utf-8")
                shots_en_file = project_dir / "shots_plan_en.json"
                shots_en_file.write_text(json.dumps(en_scenes, indent=2, ensure_ascii=False), encoding="utf-8")

                from services.script_auditor import ScriptAuditor
                audit_res = ScriptAuditor(algorithm_mode=True).audit_script("\n".join(s.get("text", "") for s in shots))

            # Character detection across scripts — LLM-based (replaces regex that
            # flooded fake characters). Raises on gateway failure (no silent fakes).
            from pipeline.character_manager import detect_characters_in_text_llm
            combined_script_text = " ".join(s.get("text", "") for s in vi_scenes) + " " + " ".join(s.get("text", "") for s in en_scenes)
            detected_chars = detect_characters_in_text_llm(combined_script_text)
            required_characters = [d["name"] for d in detected_chars]

            # Assign character_ids to shots
            for s in shots:
                s_txt = s.get("text", "").lower()
                s["character_ids"] = [
                    d["name"].lower().replace(" ", "_")
                    for d in detected_chars
                    if d["name"].lower() in s_txt
                ]

            # Save active shots plan
            shots_file = project_dir / "shots_plan.json"
            shots_file.write_text(json.dumps(shots, indent=2, ensure_ascii=False), encoding="utf-8")

            # Determine target voice and save voice.txt
            first_few_texts = " ".join(s.get("text", "") for s in shots[:4])
            detected_lang = detect_language(first_few_texts)
            target_voice = chosen_voice or VOICE_MAP.get(detected_lang, VOICE_MAP["vi"])["male"]
            (project_dir / "voice.txt").write_text(target_voice, encoding="utf-8")

            # Initialize 4-Gate Approval State Machine
            from services.approval_state_machine import ApprovalStateMachine, BeatIntent
            asm = ApprovalStateMachine(project_dir, job_id)
            beats_list = []
            for i in range(1, len(shots) + 1):
                vi_txt = vi_scenes[i - 1].get("text", "") if (vi_scenes and i - 1 < len(vi_scenes)) else ""
                en_txt = en_scenes[i - 1].get("text", "") if (en_scenes and i - 1 < len(en_scenes)) else ""
                shot_chars = shots[i - 1].get("character_ids", [])
                beats_list.append(BeatIntent(
                    id=i,
                    text_vi=vi_txt,
                    text_en=en_txt,
                    duration=default_shot_duration,
                    character_ids=shot_chars,
                ))

            vi_full = "\n".join(b.text_vi for b in beats_list if b.text_vi)
            en_full = "\n".join(b.text_en for b in beats_list if b.text_en)

            # Audit report check
            is_valid_audit = bool(audit_res and audit_res.is_valid) and bool(vi_scenes) and bool(en_scenes)
            audit_issues = list(audit_res.issues) if audit_res else []
            if not vi_scenes:
                audit_issues.append("Kịch bản thiếu bản tiếng Việt (VI).")
            if not en_scenes:
                audit_issues.append("Kịch bản thiếu bản tiếng Anh (EN).")

            audit_report = {
                "is_valid": is_valid_audit,
                "issues": audit_issues,
                "total_scenes": len(shots),
            }

            # Register characters in state machine
            for d in detected_chars:
                cid = d["name"].lower().replace(" ", "_")
                char_shots = [s["index"] for s in shots if cid in s.get("character_ids", [])]
                descriptor = d.get("descriptor", "")
                description = d.get("context", f"Nhân vật trong kịch bản: {d['name']}")
                if descriptor:
                    description = f"{description}. Ngoại hình: {descriptor}"
                asm.register_character(
                    char_id=cid,
                    name=d["name"],
                    description=description,
                    shot_ids=char_shots,
                )

            asm.set_script(
                script_vi=vi_full,
                script_en=en_full,
                beats=beats_list,
                required_characters=required_characters,
                audit_report=audit_report,
            )
            asm.set_shots_plan(shots)

            # Store in database
            self.jq.add_shots_for_job(job_id, shots)
            key_count = sum(1 for s in shots if s["is_key_scene"])
            broll_count = len(shots) - key_count
            self.jq.transition_job(
                job_id,
                to_state="WAITING_APPROVAL",
                script_summary=f"Tạo {len(shots)} cảnh AI (ngôn ngữ: {detected_lang.upper()}, giọng: {target_voice}).",
                script_file=str(shots_file),
            )

            # Generate human-readable .txt script files for final user review ("tra phát cuối")
            est_total_duration = sum(float(s.get("duration") or default_shot_duration) for s in shots)
            total_words_vi = sum(len(str(s.get("text", "")).split()) for s in (vi_scenes or shots))

            vi_txt_lines = [
                "=" * 80,
                "🎬 KỊCH BẢN PHÂN CẢNH VOICE-OVER TIẾNG VIỆT",
                f"Job ID: {job_id}",
                f"Tiêu đề: {source_title or 'Video Documentary'}",
                f"Tổng số cảnh: {len(shots)} cảnh | Thời lượng dự kiến: ~{est_total_duration:.0f}s (~{est_total_duration / 60:.1f} phút)",
                f"Tổng số từ: {total_words_vi} từ | Tốc độ đọc: ~2.5 - 3.0 từ/giây",
                f"Giọng đọc đề xuất: {target_voice}",
                "=" * 80,
                "",
            ]
            cum_t = 0.0
            for s in (vi_scenes or shots):
                idx = s.get("index", 1)
                sc_dur = float(s.get("duration") or default_shot_duration)
                t_start, t_end = cum_t, cum_t + sc_dur
                cum_t += sc_dur
                tag = " [HOOK]" if idx <= 2 else (" [OUTRO/KẾT]" if idx == len(shots) else "")
                vi_txt_lines.append(f"[CẢNH {idx:03d}] ({t_start:.1f}s - {t_end:.1f}s){tag}")
                vi_txt_lines.append(f"Lời thoại: {s.get('text', '')}")
                vi_txt_lines.append(f"Mô tả hình ảnh (AI Prompt): {s.get('prompt', '')}")
                if s.get("character_ids"):
                    vi_txt_lines.append(f"Nhân vật: {', '.join(s['character_ids'])}")
                vi_txt_lines.append("-" * 40)

            vi_txt_lines.extend([
                "",
                "=" * 80,
                "👉 HƯỚNG DẪN KIỂM TRA & DUYỆT (GATE 1):",
                f"- Muốn sửa câu chữ từng cảnh: /edit_beat {job_id} <cảnh_số> <nội dung mới>",
                f"- Hoặc tải file .txt này về máy, chỉnh sửa rồi gửi lại file lên chat bot.",
                f"- Khi đã hoàn toàn ưng ý: bấm [Duyệt Kịch Bản (G1)] hoặc gõ /approve_script {job_id}",
                "=" * 80,
            ])
            kich_ban_vi_file = project_dir / f"kich_ban_VI_{job_id}.txt"
            kich_ban_vi_file.write_text("\n".join(vi_txt_lines), encoding="utf-8")

            kich_ban_en_file = project_dir / f"kich_ban_EN_{job_id}.txt"
            if en_scenes:
                en_txt_lines = [
                    "=" * 80,
                    "🎬 ENGLISH VOICE-OVER SCRIPT & SHOT PLAN",
                    f"Job ID: {job_id}",
                    f"Title: {source_title or 'Video'}",
                    f"Total scenes: {len(en_scenes)} scenes | Duration: ~{sum(float(s.get('duration') or 5.0) for s in en_scenes):.0f}s",
                    "=" * 80,
                    "",
                ]
                cum_t_en = 0.0
                for s in en_scenes:
                    idx = s.get("index", 1)
                    sc_dur = float(s.get("duration") or default_shot_duration)
                    t_start, t_end = cum_t_en, cum_t_en + sc_dur
                    cum_t_en += sc_dur
                    en_txt_lines.append(f"[SCENE {idx:03d}] ({t_start:.1f}s - {t_end:.1f}s)")
                    en_txt_lines.append(f"Narration: {s.get('text', '')}")
                    en_txt_lines.append(f"Visual Prompt: {s.get('prompt', '')}")
                    en_txt_lines.append("-" * 40)
                kich_ban_en_file.write_text("\n".join(en_txt_lines), encoding="utf-8")

            # 1. Send clean script documents directly to Telegram for user inspection ("tra phát cuối")
            if self.notifier.is_configured:
                self.notifier.send_document(
                    str(kich_ban_vi_file),
                    caption=(
                        f"📜 <b>KỊCH BẢN TIẾNG VIỆT TOÀN VĂN - Job {job_id}</b>\n"
                        f"⏱ Thời lượng: <b>~{est_total_duration:.0f}s (~{est_total_duration / 60:.1f} phút)</b> ({len(shots)} cảnh) | {total_words_vi} từ\n"
                        f"👉 <i>Tải về để tra cứu phát cuối trước khi duyệt Gate 1!</i>"
                    ),
                )
                if kich_ban_en_file.exists():
                    self.notifier.send_document(
                        str(kich_ban_en_file),
                        caption=f"📜 <b>KỊCH BẢN TIẾNG ANH (EN) - Job {job_id}</b>",
                    )

            # 2. Send voice auditions directly to Telegram
            if preview_voice_samples and self.notifier.is_configured:
                send_voice_previews(self.notifier, job_id, project_dir, target_lang=detected_lang)

            # 3. Notify Telegram with interactive buttons and rich preview
            if self.notifier.is_configured:
                inline_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "🎙 Nam Dõng Dạc (xKiro VN)", "callback_data": f"voice:{job_id}:confident-male-vietnamese"},
                            {"text": "🎙 Nữ Ngọt Ngào (xKiro VN)", "callback_data": f"voice:{job_id}:sweet-female-vietnamese"},
                        ],
                        [
                            {"text": "🎙 Nam Documentary (xKiro EN)", "callback_data": f"voice:{job_id}:male-voice"},
                            {"text": "🎙 Nam Minh (edge VN)", "callback_data": f"voice:{job_id}:vi-VN-NamMinhNeural"},
                        ],
                        [
                            {"text": "📝 Duyệt Kịch Bản (G1)", "callback_data": f"approve_script:{job_id}"},
                            {"text": "📊 Tiến Độ 4 Gate", "callback_data": f"gates:{job_id}"},
                        ],
                        [
                            {"text": "🚀 Duyệt Video (G4)", "callback_data": f"approve_video:{job_id}"},
                            {"text": "🚫 Hủy Job", "callback_data": f"reject:{job_id}"},
                        ],
                    ]
                }
                char_info = f"👤 Nhân vật phát hiện: <b>{', '.join(required_characters) if required_characters else 'Không có (Documentary chung)'}</b>\n"

                # Extract Hook preview (first 2-3 scenes)
                hook_preview = "\n".join(f"• <b>Cảnh {s['index']}:</b> <i>{s.get('text', '')}</i>" for s in shots[:3])
                # Extract mid-point milestones
                mid_preview = ""
                if len(shots) > 6:
                    mid_idx_1 = len(shots) // 3
                    mid_idx_2 = (2 * len(shots)) // 3
                    mid_shots = [shots[mid_idx_1], shots[mid_idx_2]]
                    mid_preview = "\n".join(f"• <b>Cảnh {s['index']}:</b> <i>{s.get('text', '')}</i>" for s in mid_shots)
                outro_preview = f"• <b>Cảnh {shots[-1]['index']} (Kết):</b> <i>{shots[-1].get('text', '')}</i>" if shots else ""

                approval_msg = (
                    f"🎬 <b>[MODE 2: KỊCH BẢN ĐÃ XONG - CHỜ TRA PHÁT CUỐI & DUYỆT G1]</b>\n"
                    f"🆔 Job: <code>{job_id}</code>\n"
                    f"📺 Tiêu đề: <b>{source_title or 'Video'}</b>\n\n"
                    f"⏱ Thời lượng: <b>~{est_total_duration:.0f}s (~{est_total_duration / 60:.1f} phút)</b> (dài tương xứng video gốc!)\n"
                    f"📊 Tổng số cảnh: <b>{len(shots)} cảnh</b> ({default_shot_duration}s/cảnh) | {total_words_vi} từ\n"
                    f"🌟 <b>{key_count} cảnh Veo điện ảnh</b> + 🖼 <b>{broll_count} cảnh Ken Burns</b>\n"
                    f"{char_info}"
                    f"🌐 Ngôn ngữ: <b>{detected_lang.upper()}</b> | Giọng đề xuất: <b>{target_voice}</b>\n\n"
                    f"🎯 <b>TRÍCH ĐOẠN MỞ ĐẦU (HOOK):</b>\n{hook_preview}\n\n"
                )
                if mid_preview:
                    approval_msg += f"📖 <b>CÁC MỐC CHUYỂN BIẾN CHÍNH:</b>\n{mid_preview}\n\n"
                if outro_preview:
                    approval_msg += f"🎬 <b>CẢNH KẾT (OUTRO):</b>\n{outro_preview}\n\n"

                approval_msg += (
                    f"📑 <i>File kịch bản toàn văn <code>.txt</code> đã được gửi kèm ở trên để bạn tra phát cuối!</i>\n\n"
                    f"👉 <b>Bạn có thể:</b>\n"
                    f"• Sửa từng cảnh: <code>/edit_beat {job_id} &lt;cảnh_số&gt; &lt;nội dung mới&gt;</code>\n"
                    f"• Tải file <code>.txt</code> về máy sửa rồi gửi lại file lên bot.\n"
                    f"• Bấm <b>[📝 Duyệt Kịch Bản (G1)]</b> hoặc gõ <code>/approve_script {job_id}</code> để chốt kịch bản."
                )
                self.notifier.send_message(approval_msg, reply_markup=inline_markup)

            return job_id

        except Exception as e:
            logger.error("AI Genesis pipeline failed for %s: %s", job_id, e, exc_info=True)
            self.jq.transition_job(job_id, to_state="FAILED", error_message=str(e))
            if self.notifier.is_configured:
                self.notifier.send_message(f"❌ <b>[LỖI AI GENESIS - {job_id}]:</b> <code>{str(e)[:300]}</code>")
            return job_id

    def generate_gate2_and_gate3_artifacts(self, job_id: str, project_dir: Path):
        """
        Runs in background after Gate 1 (Script) approval:
        1. Parallel synthesis of Audio VI and Audio EN voiceovers via edge-tts.
        2. Generation / establishment of canonical reference images for required characters.
        3. Updates state machine and notifies user on Telegram with audio & character sheet previews.
        """
        from concurrent.futures import ThreadPoolExecutor
        from services.approval_state_machine import ApprovalStateMachine
        from pipeline.tts import _probe_duration
        project_dir = Path(project_dir)
        asm = ApprovalStateMachine(project_dir, job_id)
        if not asm.is_script_gate_passed():
            logger.warning("generate_gate2_and_gate3_artifacts called for %s but Gate 1 not passed", job_id)
            return

        logger.info("Starting parallel generation of Gate 2 (Audio VI/EN) & Gate 3 (Characters) for %s", job_id)

        def _generate_character_image(prompt: str, ref_path: Path) -> None:
            """Generate a real crayon-style character reference image.

            Chain: Imagen (gemini-webapi via account pool session) -> DALL-E 3
            (OPENAI_API_KEY). Raises when no provider succeeds — no placeholder
            images are ever written (zero-fake policy).
            """
            session_file: Optional[Path] = None
            try:
                account = self.account_pool.get_available_account(reserve=False)
                if account and Path(account.session_file).exists():
                    session_file = Path(account.session_file)
            except Exception as exc:
                logger.warning("Account pool unavailable for character image: %s", exc)

            if session_file:
                try:
                    import asyncio

                    from gemini_webapi import GeminiClient
                    from services.gemini_webapi_provider import extract_cookies_from_file

                    psid, psidts = extract_cookies_from_file(session_file)
                    if psid:
                        async def _gen_img():
                            c = GeminiClient(secure_1psid=psid, secure_1psidts=psidts)
                            await c.init(timeout=60, auto_refresh=True, verbose=False)
                            out = await c.generate_content(prompt)
                            if out.images:
                                await out.images[0].save(path=str(ref_path.parent), filename=ref_path.name)
                            await c.close()

                        asyncio.run(asyncio.wait_for(_gen_img(), timeout=120.0))
                        if ref_path.exists() and ref_path.stat().st_size > 1000:
                            logger.info("Character reference generated via Imagen: %s", ref_path)
                            return
                except Exception as exc:
                    logger.warning("Imagen character image failed (%s). Trying DALL-E.", exc)

            import os

            openai_key = os.environ.get("OPENAI_API_KEY")
            if openai_key:
                try:
                    import base64

                    from openai import OpenAI

                    client = OpenAI(api_key=openai_key, timeout=90.0)
                    resp = client.images.generate(
                        model="dall-e-3",
                        prompt=prompt[:300],
                        n=1,
                        size="1024x1024",
                        response_format="b64_json",
                    )
                    if resp.data and resp.data[0].b64_json:
                        ref_path.parent.mkdir(parents=True, exist_ok=True)
                        ref_path.write_bytes(base64.b64decode(resp.data[0].b64_json))
                        logger.info("Character reference generated via DALL-E: %s", ref_path)
                        return
                except Exception as exc:
                    logger.warning("DALL-E character image failed: %s", exc)

            raise RuntimeError(
                "No image provider available for character reference "
                "(Imagen session + OPENAI_API_KEY both unavailable)"
            )

        def _generate_audio_vi():
            try:
                from pipeline.tts import _probe_duration, synthesize_one_xkiro_with_fallback
                from services.script_auditor import VOICE_MAP

                voice_file = project_dir / "voice.txt"
                voice_vi = voice_file.read_text(encoding="utf-8").strip() if voice_file.exists() else ""
                if not voice_vi:
                    voice_vi = VOICE_MAP["vi"]["default"]
                out_vi = project_dir / "audio_vi.mp3"
                txt_vi = asm.script_vi_text
                if txt_vi:
                    used_voice = synthesize_one_xkiro_with_fallback(txt_vi, voice_vi, out_vi)
                    dur = _probe_duration(out_vi)
                    asm.set_audio_generated(language="vi", file_path=out_vi, duration=dur, voice_id=used_voice)
                    if self.notifier.is_configured:
                        markup = {
                            "inline_keyboard": [[
                                {"text": "🎙 Duyệt Audio VI", "callback_data": f"approve_audio_vi:{job_id}"}
                            ]]
                        }
                        self.notifier.send_message(
                            f"🎙 <b>[GATE 2 - AUDIO VI XONG]:</b> Job <code>{job_id}</code>\n"
                            f"• Thời lượng: <b>{dur:.1f}s</b> | Giọng: <code>{used_voice}</code>\n"
                            f"👉 Gõ <code>/approve_audio_vi {job_id}</code> hoặc bấm nút bên dưới:",
                            reply_markup=markup
                        )
            except Exception as e:
                logger.error("Failed to generate audio VI for %s: %s", job_id, e)
                if self.notifier.is_configured:
                    self.notifier.send_message(
                        f"⚠️ <b>[GATE 2 - LỖI TẠO AUDIO VI - {job_id}]:</b> <code>{str(e)[:200]}</code>"
                    )

        def _generate_audio_en():
            try:
                from pipeline.tts import _probe_duration, synthesize_one_xkiro_with_fallback
                from services.script_auditor import VOICE_MAP

                voice_en = VOICE_MAP["en"]["default"]
                out_en = project_dir / "audio_en.mp3"
                txt_en = asm.script_en_text
                if txt_en:
                    used_voice = synthesize_one_xkiro_with_fallback(txt_en, voice_en, out_en)
                    dur = _probe_duration(out_en)
                    asm.set_audio_generated(language="en", file_path=out_en, duration=dur, voice_id=used_voice)
                    if self.notifier.is_configured:
                        markup = {
                            "inline_keyboard": [[
                                {"text": "🎙 Duyệt Audio EN", "callback_data": f"approve_audio_en:{job_id}"}
                            ]]
                        }
                        self.notifier.send_message(
                            f"🎙 <b>[GATE 2 - AUDIO EN XONG]:</b> Job <code>{job_id}</code>\n"
                            f"• Duration: <b>{dur:.1f}s</b> | Voice: <code>{used_voice}</code>\n"
                            f"👉 Gõ <code>/approve_audio_en {job_id}</code> hoặc bấm nút bên dưới:",
                            reply_markup=markup
                        )
            except Exception as e:
                logger.error("Failed to generate audio EN for %s: %s", job_id, e)
                if self.notifier.is_configured:
                    self.notifier.send_message(
                        f"⚠️ <b>[GATE 2 - LỖI TẠO AUDIO EN - {job_id}]:</b> <code>{str(e)[:200]}</code>"
                    )

        def _generate_character_sheets():
            try:
                asm.load()
                char_prompt_style = (
                    "Whimsical hand-drawn editorial cartoon character reference, "
                    "rough wax crayon and colored pencil sketch strokes, grainy paper texture, "
                    "flat 2D minimalist composition, generous negative space, "
                    "plain off-white textured sketchbook paper background, "
                    "no gradients, no 3D shading, no photorealism. Character: "
                )
                for cid, char in asm.characters.items():
                    char_dir = project_dir / "characters" / cid
                    char_dir.mkdir(parents=True, exist_ok=True)
                    ref_path = char_dir / "reference.png"
                    style_verdict = None
                    if not ref_path.exists() or ref_path.stat().st_size < 100:
                        prompt = f"{char_prompt_style}{char.name}. {char.description}"

                        def _gen_style_checked(gen_prompt: str, out_path: Path) -> None:
                            _generate_character_image(gen_prompt, out_path)

                        from services.style_checker import (
                            generate_with_style_check,
                            save_style_report,
                        )

                        # Phase 3: AI verifies its own art — regenerate once with
                        # judge issues folded back before showing the human gate.
                        style_verdict = generate_with_style_check(
                            _gen_style_checked,
                            prompt,
                            ref_path,
                            subject=f"{char.name}. Ngoại hình: {char.description}",
                            max_attempts=2,
                        )
                        save_style_report(char_dir, char.name, style_verdict)
                        if not style_verdict["ok"]:
                            logger.warning(
                                "Character %s style still below bar after retry (score %.1f): %s",
                                cid, style_verdict["score"], style_verdict["issues"],
                            )

                    asm.set_character_reference(cid, ref_path)
                    if self.notifier.is_configured:
                        style_note = ""
                        if style_verdict is not None and not style_verdict["ok"]:
                            style_note = (
                                f"\n⚠️ AI tự chấm style: {style_verdict['score']:.1f}/10 — "
                                f"{'; '.join(style_verdict['issues'][:2])}"
                            )
                        markup = {
                            "inline_keyboard": [[
                                {"text": f"👤 Duyệt {char.name}", "callback_data": f"approve_char:{job_id}:{cid}"}
                            ]]
                        }
                        self.notifier.send_message(
                            f"👤 <b>[GATE 3 - CHARACTER SHEET XONG]:</b> <b>{char.name}</b> (ID: <code>{cid}</code>)\n"
                            f"• Xuất hiện ở cảnh: {char.shot_ids}"
                            f"{style_note}\n"
                            f"👉 Gõ <code>/approve_character {job_id} {cid}</code> hoặc duyệt tất cả: <code>/approve_character {job_id} all</code>",
                            reply_markup=markup
                        )
            except Exception as e:
                logger.error("Failed to generate character sheets for %s: %s", job_id, e)
                if self.notifier.is_configured:
                    self.notifier.send_message(
                        f"⚠️ <b>[GATE 3 - LỖI TẠO CHARACTER SHEET - {job_id}]:</b> "
                        f"<code>{str(e)[:200]}</code>\n"
                        f"Không tạo ảnh placeholder — cần session Gemini hoạt động hoặc OPENAI_API_KEY rồi bấm 📊 Tiến Độ để thử lại."
                    )

        with ThreadPoolExecutor(max_workers=3) as pool:
            f_vi = pool.submit(_generate_audio_vi)
            f_en = pool.submit(_generate_audio_en)
            f_char = pool.submit(_generate_character_sheets)
            f_vi.result()
            f_en.result()
            f_char.result()

        logger.info("Finished parallel generation of Gate 2 & Gate 3 artifacts for %s", job_id)
        if self.notifier.is_configured:
            summary_markup = {
                "inline_keyboard": [
                    [
                        {"text": "📊 Tiến Độ 4 Gate", "callback_data": f"gates:{job_id}"},
                        {"text": "🚀 Duyệt Video (G4)", "callback_data": f"approve_video:{job_id}"},
                    ]
                ]
            }
            self.notifier.send_message(
                f"🎉 <b>[GATE 2 & GATE 3 ĐÃ TẠO XONG]:</b> Job <code>{job_id}</code>\n"
                f"Các bản Audio VI, Audio EN và Character Sheets đã được chuẩn bị.\n"
                f"👉 Hãy nghe duyệt audio và duyệt nhân vật để mở Gate 4 (Video)!",
                reply_markup=summary_markup
            )

