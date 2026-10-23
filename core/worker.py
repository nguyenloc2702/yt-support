"""Core Worker Daemon for Automated Video Generation Pipeline.

Orchestrates:
1. SQLite Job & Shot Queue (`JobQueueManager`)
2. Account Pool Manager (`AccountPoolManager`) with 70% quota & kill-switch safety
3. GeminiWebApiProvider (primary, lightweight RPC) & ImageKenBurnsProvider (fallback)
4. Cloud storage uploader (Google Drive via rclone) with preview sent before local delete
5. Telegram progress notifications & Interactive Remote Command Listener
6. Graceful shutdown on SIGTERM / SIGINT for safe zero-damage redeployments.
"""

import json
import logging
import os
from pathlib import Path
import random
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

from pipeline.automated_factory import AutomatedFactory
from services.account_pool import AccountPoolManager
from services.gemini_webapi_provider import GeminiWebApiProvider
from services.image_kenburns_provider import ImageKenBurnsProvider
from services.job_queue import JobQueueManager
from services.notifier import TelegramNotifier
from services.settings_service import is_veo_enabled
from services.storage_uploader import StorageUploader
from services.telegram_bot import TelegramCommandBot
from services.video_provider import ErrorKind, ProviderResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("worker")


class VideoAutomationWorker:
    def __init__(self):
        self.running = True
        self.worker_id = f"{socket.gethostname()}_{os.getpid()}_{uuid.uuid4().hex[:6]}"

        # The worker reaches the encrypted settings store (API keys) on every
        # LLM/TTS call; without the tables those queries crash with
        # "no such table: app_settings" on a fresh DATA_DIR.
        from core.database import init_db
        init_db()

        # Components
        self.jq = JobQueueManager()
        self.account_pool = AccountPoolManager()
        self.primary_provider = GeminiWebApiProvider()
        self.fallback_provider = ImageKenBurnsProvider(fallback_provider=self.primary_provider)
        self.uploader = StorageUploader()
        self.notifier = TelegramNotifier()
        self.factory = AutomatedFactory(db_path=self.jq.db_path, account_pool=self.account_pool)

        # Remote Telegram Command Listener
        self.bot = TelegramCommandBot(
            on_reup_handler=self.factory.run_smart_reup,
            on_remake_handler=self.factory.run_ai_genesis,
            on_approve_handler=self.factory.execute_approved_reup,
            job_queue=self.jq,
            account_pool=self.account_pool,
            factory=self.factory,
        )

        # Register signals for safe restart
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.info("Received shutdown signal (%s). Finishing current step before stopping...", signum)
        self.running = False
        if hasattr(self, "account_pool"):
            self.account_pool.stop_keep_alive()
        if hasattr(self, "bot"):
            self.bot.stop_polling()

    def run(self):
        logger.info("==================================================")
        logger.info("🚀 KHỞI ĐỘNG VIDEO AUTOMATION WORKER (ID: %s)", self.worker_id)
        logger.info("==================================================")

        # 1. Reset any interrupted shots on startup (refund attempt counts)
        self.jq.reset_interrupted_shots(worker_id=self.worker_id)

        # 2. Check accounts status
        num_accounts = self.account_pool.reload_accounts()
        pool_status = self.account_pool.get_status_summary()

        veo_enabled = is_veo_enabled()
        logger.info("Số tài khoản Pro sẵn sàng: %d (Available: %d)", num_accounts, pool_status["available"])
        logger.info(
            "Chế độ sinh video: %s",
            "Veo (tài khoản Google Pro) + Ken Burns fallback" if veo_enabled
            else "Ken Burns (ảnh + camera motion) — Veo TẮT theo cài đặt",
        )

        if self.notifier.is_configured:
            self.notifier.send_message(
                f"🤖 <b>Video Worker Đã Khởi Động Thành Công</b>\n"
                f"👤 Worker ID: <code>{self.worker_id}</code>\n"
                f"🎬 Engine: <b>{'Veo + Ken Burns' if veo_enabled else 'Ken Burns (Veo tắt)'}</b>\n"
                f"👤 Tài khoản Pro sẵn sàng: {pool_status['available']}/{num_accounts}\n"
                f"☁️ Google Drive Remote: <code>{self.uploader.remote_name}</code>\n"
                f"📱 Bạn có thể gửi lệnh qua Telegram: <code>/reup &lt;url&gt;</code> hoặc <code>/remake &lt;url&gt;</code>"
            )

        # 3. Start remote Telegram bot listener
        if self.bot.is_configured:
            self.bot.start_polling()

        # 3b. Start auto keep-alive daemon to renew Google cookies 24/7 — only
        # when Veo actually consumes them, so a Veo-off worker never pings Google.
        if veo_enabled:
            self.account_pool.start_keep_alive(
                interval_seconds=1800,
                provider=self.primary_provider,
                notifier=self.notifier,
            )
        else:
            logger.info("Keep-alive Google không chạy vì Veo đang tắt (bật bằng /veo on).")

        # 4. Main processing loop (Dedup based strictly on DB state, no in-memory sets)
        self._scan_and_assemble_pending_jobs()

        while self.running:
            # Check if kill switch is active
            if self.account_pool.kill_switch_active:
                logger.warning("Kill-switch đang BẬT. Dừng sinh video AI Mode 2. Chờ /resume...")
                time.sleep(30)
                continue

            # Step 4a: Process pending uploads first (P0.2: decouple upload from generation)
            upload_shot = self.jq.get_next_upload_pending_shot()
            if upload_shot:
                u_job_id = upload_shot["job_id"]
                u_shot_id = upload_shot["id"]
                u_shot_idx = upload_shot["index_num"]
                u_vid_path = Path(upload_shot["video_path"]) if upload_shot.get("video_path") else None
                out_filename = f"shot_{u_shot_idx:03d}.mp4"

                if u_vid_path and u_vid_path.exists():
                    logger.info("☁️ Uploading shot %s (video: %s)...", u_shot_id, u_vid_path.name)
                    upload_ok, gdrive_url = self.uploader.upload_and_clean(
                        local_path=u_vid_path,
                        remote_filename=out_filename,
                        remote_folder=f"Video_Projects/{u_job_id}/raw_shots",
                        clean_local=False,
                    )
                    if upload_ok:
                        self.jq.mark_shot_done(
                            shot_id=u_shot_id,
                            account_id=upload_shot.get("account_id") or "local",
                            video_path=str(u_vid_path),
                            drive_url=gdrive_url,
                        )
                        logger.info("✔ Shot %s upload thành công: %s", u_shot_id, gdrive_url)
                        # Check if all shots finished and trigger assembly
                        summ = self.jq.get_job_summary(u_job_id)
                        if summ["total"] > 0 and summ["done"] >= summ["total"]:
                            self._assemble_and_finalize_documentary(u_job_id, summ)
                    else:
                        is_final = self.jq.mark_shot_upload_failed(u_shot_id, "Upload to Google Drive failed")
                        logger.error("Upload shot %s thất bại (is_final=%s)", u_shot_id, is_final)
                else:
                    self.jq.mark_shot_upload_failed(u_shot_id, f"Local file not found: {u_vid_path}")

                time.sleep(2)
                continue

            # Step 4b: Fetch next pending shot for generation across approved/processing jobs
            shot = self.jq.get_next_pending_shot()
            if not shot:
                self._scan_and_assemble_pending_jobs()
                time.sleep(15)
                continue

            job_id = shot["job_id"]
            shot_id = shot["id"]
            shot_idx = shot["index_num"]
            prompt = shot["prompt"]
            attempts = shot.get("attempts", 0)
            is_key_scene = bool(shot.get("is_key_scene", 0))

            # Verify 4-gate manifest integrity if state machine exists (P0.5)
            job_obj = self.jq.get_job(job_id)
            p_dir = Path(job_obj.get("project_dir") or f"data/automated_runs/{job_id}") if job_obj else Path(f"data/automated_runs/{job_id}")
            state_file = p_dir / "approval_state.json"
            ref_images = []
            if state_file.exists():
                from services.approval_state_machine import ApprovalStateMachine, VideoReadinessStatus
                asm = ApprovalStateMachine(p_dir, job_id)
                valid, err = asm.verify_manifest_integrity()
                if not valid:
                    logger.error("❌ Manifest integrity check failed for job %s: %s. Refusing to generate!", job_id, err)
                    self.jq.mark_shot_failed(shot_id, f"Manifest integrity failed: {err}")
                    continue
                if asm.video_status == VideoReadinessStatus.APPROVED:
                    asm.mark_generating()

                # Read prompt and reference images strictly from locked manifest
                if asm.active_manifest:
                    m_shot = next((s for s in asm.active_manifest.shot_manifest if s.get("index") == shot_idx), None)
                    if m_shot:
                        prompt = m_shot.get("prompt") or prompt
                        ref_images = [Path(p) for p in m_shot.get("reference_images", []) if Path(p).is_file()]
                        # Fail-closed check: if shot declares character but image is missing, do not generate!
                        if m_shot.get("character_ids") and not ref_images:
                            err_msg = f"Shot {shot_idx} has character_ids {m_shot.get('character_ids')} but no valid reference images found on disk!"
                            logger.error("❌ Fail-closed violation: %s", err_msg)
                            self.jq.mark_shot_failed(shot_id, err_msg)
                            continue

            # Guarded transition from APPROVED -> PROCESSING upon starting shot (P0.4)
            self.jq.transition_job(job_id, to_state="PROCESSING", from_states=("APPROVED",))

            out_filename = f"shot_{shot_idx:03d}.mp4"
            target_out_dir = p_dir / "shots"
            target_out_dir.mkdir(parents=True, exist_ok=True)
            target_out_path = target_out_dir / out_filename

            # 1. Quota Optimization (P0.5): Check if valid local video exists from previous attempt
            if target_out_path.exists() and target_out_path.stat().st_size > 5000:
                from pipeline.output_validator import validate_rendered_output
                val_res = validate_rendered_output(target_out_path)
                if val_res.is_valid:
                    logger.info("⚡ File video cục bộ đã tồn tại và hợp lệ (%s). Đưa vào UPLOAD_PENDING không cần gọi provider!", target_out_path.name)
                    self.jq.mark_shot_generated(shot_id, str(target_out_path))
                    continue

            # 2. Generation dispatch
            needs_veo = is_veo_enabled() and is_key_scene and attempts < 2
            if needs_veo:
                account = self.account_pool.get_available_account(reserve=True)
                if not account:
                    logger.warning("Không có tài khoản Pro nào khả dụng cho Key Scene %s. Chờ 60s...", shot_id)
                    time.sleep(60)
                    continue

                acc_id = account.acc_id
                session_file = Path(account.session_file)
                logger.info("▶ Bắt đầu xử lý %s (Cảnh %d, lần %d, Key Scene) bằng Veo AI qua tài khoản %s",
                            shot_id, shot_idx, attempts + 1, acc_id)
                marked = self.jq.mark_shot_in_progress(shot_id, acc_id, worker_id=self.worker_id)
                if not marked:
                    self.account_pool.release_reservation(acc_id)
                    continue

                start_time = time.time()
                res = self.primary_provider.generate(prompt=prompt, out_path=target_out_path, session_file=session_file)
                duration = time.time() - start_time
            else:
                acc_id = "ken_burns_local"
                if not is_key_scene:
                    logger.info("Shot %s là cảnh phụ B-roll. Điều phối trực tiếp sang ImageKenBurnsProvider (tiết kiệm quota Veo).", shot_id)
                elif not needs_veo and attempts < 2:
                    logger.info("Shot %s (Key Scene) được render bằng Ken Burns vì Veo đang tắt trong cài đặt.", shot_id)
                else:
                    logger.warning("Shot %s đã thất bại 2 lần. Kích hoạt Ken Burns graceful fallback!", shot_id)

                marked = self.jq.mark_shot_in_progress(shot_id, acc_id, worker_id=self.worker_id)
                if not marked:
                    continue

                session_path = None
                avail = self.account_pool.get_available_account(reserve=False)
                if avail and Path(avail.session_file).exists():
                    session_path = Path(avail.session_file)

                start_time = time.time()
                with self.jq.heavy_lock_lease(job_id, interval=15.0):
                    res = self.fallback_provider.generate(
                        prompt=prompt,
                        out_path=target_out_path,
                        session_file=session_path,
                        ref_image=ref_images[0] if ref_images else None,
                        duration=float(shot.get("duration") or 5.0),
                    )
                duration = time.time() - start_time

            # 3. Handle generation result
            if res.ok and res.video_path and res.video_path.exists():
                logger.info("✅ Shot %s sinh thành công sau %.1fs", shot_id, duration)
                if needs_veo:
                    self.account_pool.report_success(acc_id)

                # Send Telegram preview before cloud archival
                summary = self.jq.get_job_summary(job_id)
                if self.notifier.is_configured:
                    self.notifier.notify_shot_completed(
                        shot_idx=shot_idx,
                        total_shots=summary["total"],
                        shot_id=shot_id,
                        acc_id=acc_id,
                        duration_sec=duration,
                        gdrive_url="Đang upload...",
                        video_path=str(res.video_path),
                    )

                # Transition to UPLOAD_PENDING (P0.2)
                self.jq.mark_shot_generated(shot_id, str(res.video_path))

            else:
                logger.error("❌ Shot %s thất bại: %s (%s)", shot_id, res.error_message, res.error_kind)

                if needs_veo:
                    if res.error_kind == ErrorKind.RATE_LIMIT:
                        self.account_pool.report_rate_limit(acc_id, cooldown_seconds=120)
                        self.jq.mark_shot_retry_later(shot_id, res.error_message or "Rate limited")
                        continue
                    elif res.error_kind == ErrorKind.QUOTA:
                        self.account_pool.report_quota_exhausted(acc_id)
                        self.jq.mark_shot_retry_later(shot_id, res.error_message or "Veo quota exhausted")
                        if self.notifier.is_configured:
                            self.notifier.send_message(
                                f"⚠️ <b>HẾT HẠN NGẠCH VEO ({acc_id})</b>\n"
                                f"Tài khoản <code>{acc_id}</code> đã chạm giới hạn video của Google.\n"
                                f"Đã đánh dấu EXHAUSTED và hoãn cảnh {shot_idx} chờ quota hồi phục hoặc chuyển tài khoản tiếp theo."
                            )
                        continue
                    elif res.error_kind in (ErrorKind.AUTH, ErrorKind.VERIFY):
                        self.account_pool.trigger_kill_switch(acc_id, res.error_message or "Auth/Verification failure")
                        if self.notifier.is_configured:
                            self.notifier.send_message(
                                f"🚨 <b>KILL-SWITCH ĐÃ BẬT!</b>\n"
                                f"Tài khoản <code>{acc_id}</code> gặp lỗi xác thực: {res.error_message}\n"
                                f"Dây chuyền Mode 2 đã tạm dừng để bảo vệ an toàn. Hãy cập nhật cookie và gõ <code>/resume</code>."
                            )
                    else:
                        self.account_pool.release_reservation(acc_id)

                is_final_failure = self.jq.mark_shot_failed(shot_id, res.error_message or "Unknown error")
                summary = self.jq.get_job_summary(job_id)
                if is_final_failure and self.notifier.is_configured:
                    self.notifier.notify_shot_failed(
                        shot_idx=shot_idx,
                        total_shots=summary["total"],
                        shot_id=shot_id,
                        acc_id=acc_id,
                        error_msg=res.error_message or "Max retries exceeded",
                    )

                # Check if all shots for this job have settled
                settled_summary = self.jq.get_job_summary(job_id)
                if settled_summary["pending"] == 0 and settled_summary["in_progress"] == 0 and settled_summary["upload_pending"] == 0:
                    if settled_summary["failed"] > 0:
                        logger.error("Job %s kết thúc với %d cảnh thất bại vĩnh viễn.", job_id, settled_summary["failed"])
                        self.jq.transition_job(
                            job_id,
                            to_state="FAILED",
                            error_message=f"{settled_summary['failed']} / {settled_summary['total']} cảnh thất bại sau 3 lần retry."
                        )
                        if self.notifier.is_configured:
                            self.notifier.send_message(
                                f"❌ <b>[JOB {job_id} THẤT BẠI]</b>\n"
                                f"Có {settled_summary['failed']}/{settled_summary['total']} cảnh không thể sinh video sau 3 lần retry.\n"
                                f"Dùng lệnh <code>/retry {job_id}</code> để thử lại hoặc cập nhật cookie tài khoản."
                            )

            # Jitter delay between requests (15s to 30s)
            delay = random.uniform(15.0, 30.0)
            logger.info("Tạm nghỉ jitter %.1fs trước khi xử lý cảnh tiếp theo...", delay)
            time.sleep(delay)

        logger.info("Worker dừng an toàn (Graceful Shutdown completed).")

    def _assemble_and_finalize_documentary(self, job_id: str, summary: dict):
        """Assembles all generated shots into a final documentary video and uploads to Google Drive with idempotent recovery."""
        job = self.jq.get_job(job_id)
        if not job:
            return
        project_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
        final_video = project_dir / f"documentary_final_{job_id}.mp4"

        current_status = job.get("status")
        if current_status in ("COMPLETED", "CANCELLED"):
            return

        # P0.1 & P0.3: Atomically claim job by conditional transition
        if current_status != "FINAL_UPLOAD_PENDING":
            claimed = self.jq.transition_job(
                job_id,
                to_state="ASSEMBLING",
                from_states=("APPROVED", "PROCESSING", "ASSEMBLY_FAILED"),
            )
            if not claimed:
                logger.info("Job %s could not be claimed for ASSEMBLING (status: %s).", job_id, current_status)
                return

        try:
            # 1. Assembly Phase: Only assemble if final video doesn't exist
            if not final_video.exists():
                # Check cancellation checkpoint before heavy assembly
                fresh_job = self.jq.get_job(job_id)
                if fresh_job and fresh_job.get("status") == "CANCELLED":
                    logger.info("Job %s cancelled before assembly. Aborting.", job_id)
                    return

                with self.jq.heavy_lock_lease(job_id, interval=15.0):
                    from pipeline.documentary_assembler import assemble_documentary
                    logger.info("Assembling full documentary for Job %s...", job_id)
                    final_video = assemble_documentary(job_id=job_id, project_dir=project_dir)

                # Save local manifest before upload attempt
                manifest = {
                    "job_id": job_id,
                    "final_video": str(final_video),
                    "file_size": final_video.stat().st_size if final_video.exists() else 0,
                    "assembled_at": time.time(),
                }
                (project_dir / "assembly_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                self.jq.transition_job(
                    job_id,
                    to_state="FINAL_UPLOAD_PENDING",
                    from_states=("ASSEMBLING",),
                    final_video=str(final_video),
                )

            # Check cancellation checkpoint after assembly
            fresh_job = self.jq.get_job(job_id)
            if fresh_job and fresh_job.get("status") == "CANCELLED":
                logger.info("Job %s cancelled after assembly. Aborting final upload.", job_id)
                return

            # 2. Upload Phase: Idempotently upload final documentary to Google Drive
            logger.info("Uploading final assembled documentary for Job %s...", job_id)
            from datetime import datetime
            from services.prompt_sanitizer import sanitize_filename
            from services.job_queue import extract_youtube_id

            source_title = fresh_job.get("source_title") or ""
            source_id = fresh_job.get("source_id") or extract_youtube_id(fresh_job.get("url") or "")
            date_str = datetime.now().strftime("%Y-%m-%d")
            short_id = job_id.split("_")[-1] if "_" in job_id else job_id[:6]
            safe_title = sanitize_filename(source_title or f"Video_{short_id}")

            final_folder = f"Final_Videos/{date_str}/{safe_title}_[YT-{source_id}]"

            # Check if dual language versions exist in project_dir
            out_vi = project_dir / f"documentary_final_{job_id}_vi.mp4"
            out_en = project_dir / f"documentary_final_{job_id}_en.mp4"

            if out_vi.exists() and out_en.exists():
                name_vi = f"{safe_title}_[YT-{source_id}]_[{short_id}]_VI.mp4"
                name_en = f"{safe_title}_[YT-{source_id}]_[{short_id}]_EN.mp4"
                self.uploader.upload_and_clean(local_path=out_vi, remote_filename=name_vi, remote_folder=final_folder, clean_local=False)
                self.uploader.upload_and_clean(local_path=out_en, remote_filename=name_en, remote_folder=final_folder, clean_local=False)
            else:
                named_video_name = f"{safe_title}_[YT-{source_id}]_[{short_id}].mp4"
                self.uploader.upload_and_clean(local_path=final_video, remote_filename=named_video_name, remote_folder=final_folder, clean_local=False)

            # Also upload metadata & source info if available
            meta_file = project_dir / "metadata.json"
            if meta_file.exists():
                self.uploader.upload_and_clean(local_path=meta_file, remote_folder=final_folder, clean_local=False)
            source_txt = project_dir / "source_info.txt"
            if source_txt.exists():
                self.uploader.upload_and_clean(local_path=source_txt, remote_folder=final_folder, clean_local=False)

            # Upload standard backup to Video_Projects/{job_id}
            remote_name = f"documentary_final_{job_id}.mp4"
            upload_ok, drive_url = self.uploader.upload_and_clean(
                local_path=final_video,
                remote_filename=remote_name,
                remote_folder=f"Video_Projects/{job_id}",
                clean_local=False,
            )

            if upload_ok:
                # Update DB state FIRST before cleaning any local buffers!
                self.jq.transition_job(
                    job_id,
                    to_state="COMPLETED",
                    from_states=("FINAL_UPLOAD_PENDING", "ASSEMBLING"),
                    final_video=str(final_video),
                    drive_url=drive_url,
                )
                if (project_dir / "approval_state.json").exists():
                    try:
                        from services.approval_state_machine import ApprovalStateMachine
                        asm = ApprovalStateMachine(project_dir, job_id)
                        asm.mark_completed()
                    except Exception as e:
                        logger.warning("Failed to mark state machine completed for %s: %s", job_id, e)
                # Safe to clean local shots only after DB state is committed
                shutil.rmtree(project_dir / "shots", ignore_errors=True)
                final_video.unlink(missing_ok=True)
                if out_vi.exists():
                    out_vi.unlink(missing_ok=True)
                if out_en.exists():
                    out_en.unlink(missing_ok=True)
                if self.notifier.is_configured:
                    title_info = f"🎬 <b>Tiêu đề:</b> {source_title}\n" if source_title else ""
                    yt_info = f"🔗 <b>YouTube gốc:</b> https://youtu.be/{source_id}\n" if source_id else ""
                    self.notifier.send_message(
                        f"🎉 <b>[MODE 2 HOÀN TẤT] PHIM TÀI LIỆU ĐÃ RÁP XONG!</b>\n\n"
                        f"{title_info}"
                        f"{yt_info}"
                        f"🆔 Job: <code>{job_id}</code>\n"
                        f"🎞 Tổng số cảnh: <b>{summary['done']} / {summary['total']} cảnh</b>\n"
                        f"📁 Thư mục Google Drive: <code>{final_folder}</code>\n"
                        f"☁️ Backup: <code>{drive_url}</code>\n"
                        f"✨ Video phim tài liệu hoàn chỉnh đã được kiểm định QC và tải lên Google Drive!"
                    )
            else:
                # Calculate backoff for FINAL_UPLOAD_PENDING (P0.1)
                fresh_job = self.jq.get_job(job_id) or {}
                attempts = (fresh_job.get("upload_attempts") or 0) + 1
                backoff = min(3600.0, 60.0 * (2 ** (attempts - 1)))
                next_retry = time.time() + backoff
                self.jq.transition_job(
                    job_id,
                    to_state="FINAL_UPLOAD_PENDING",
                    from_states=("FINAL_UPLOAD_PENDING", "ASSEMBLING"),
                    upload_attempts=attempts,
                    next_retry_at=next_retry,
                    error_message=f"Final upload failed (attempt {attempts})",
                )
                logger.error("Upload phim cuối thất bại cho Job %s (attempt %d). Next retry in %.0fs.", job_id, attempts, backoff)
                if attempts == 10 and self.notifier.is_configured:
                    self.notifier.send_message(
                        f"⚠️ <b>[CẢNH BÁO UPLOAD] Job {job_id}</b>\n"
                        f"Tải file phim tài liệu lên Google Drive đã thất bại 10 lần liên tiếp. Hệ thống vẫn tiếp tục retry ngầm."
                    )
        except Exception as e:
            logger.error("Failed to assemble documentary for %s: %s", job_id, e, exc_info=True)
            # P0.3: Mark ASSEMBLY_FAILED instead of FAILED so retry doesn't re-generate shots!
            self.jq.transition_job(
                job_id,
                to_state="ASSEMBLY_FAILED",
                from_states=("ASSEMBLING", "FINAL_UPLOAD_PENDING"),
                error_message=f"Documentary assembly failed: {e}",
            )
            if self.notifier.is_configured:
                self.notifier.send_message(f"❌ <b>[LỖI RÁP PHIM TÀI LIỆU - {job_id}]:</b> <code>{str(e)[:300]}</code>")

    def _scan_and_assemble_pending_jobs(self):
        """Scans for any completed Mode 2 jobs that haven't been assembled yet or have pending final uploads."""
        now = time.time()
        try:
            with self.jq._get_conn() as conn:
                rows = conn.execute(
                    "SELECT id, mode, status, upload_attempts, next_retry_at, updated_at FROM factory_jobs "
                    "WHERE status IN ('APPROVED', 'PROCESSING', 'ASSEMBLING', 'ASSEMBLY_FAILED', 'FINAL_UPLOAD_PENDING')"
                ).fetchall()
                for r in rows:
                    j_id = r["id"]
                    j_mode = str(r["mode"]).upper()
                    j_status = str(r["status"]).upper()
                    if j_mode not in ("REMAKE", "AI_GENESIS"):
                        continue

                    # Case 1: FINAL_UPLOAD_PENDING with backoff elapsed
                    if j_status == "FINAL_UPLOAD_PENDING":
                        next_retry = r["next_retry_at"]
                        if next_retry is None or next_retry <= now:
                            summ = self.jq.get_job_summary(j_id)
                            logger.info("Retrying final upload for Job %s...", j_id)
                            self._assemble_and_finalize_documentary(j_id, summ)
                        continue

                    # Case 2: Stale ASSEMBLING job (crashed > 1h ago)
                    if j_status == "ASSEMBLING":
                        if r["updated_at"] and (now - r["updated_at"]) > 3600:
                            summ = self.jq.get_job_summary(j_id)
                            logger.warning("Recovering stale ASSEMBLING job %s...", j_id)
                            self._assemble_and_finalize_documentary(j_id, summ)
                        continue

                    # Case 3: All shots done in APPROVED / PROCESSING / ASSEMBLY_FAILED
                    summ = self.jq.get_job_summary(j_id)
                    if summ["total"] > 0 and summ["done"] >= summ["total"]:
                        logger.info("Triggering assembly for Job %s (all %d shots done)...", j_id, summ["total"])
                        self._assemble_and_finalize_documentary(j_id, summ)
        except Exception as e:
            logger.debug("Error during un-assembled jobs scan: %s", e)


if __name__ == "__main__":
    worker = VideoAutomationWorker()
    worker.run()

