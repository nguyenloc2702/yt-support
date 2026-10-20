"""Core Worker Daemon for Automated Video Generation Pipeline.

Orchestrates:
1. SQLite Job & Shot Queue (`JobQueueManager`)
2. Account Pool Manager (`AccountPoolManager`) with 70% quota & kill-switch safety
3. GeminiWebApiProvider (primary, lightweight RPC) & ImageKenBurnsProvider (fallback)
4. Cloud storage uploader (Google Drive via rclone) with preview sent before local delete
5. Telegram progress notifications & Interactive Remote Command Listener
6. Graceful shutdown on SIGTERM / SIGINT for safe zero-damage redeployments.
"""

import logging
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import time

from pipeline.automated_factory import AutomatedFactory
from services.account_pool import AccountPoolManager
from services.gemini_webapi_provider import GeminiWebApiProvider
from services.image_kenburns_provider import ImageKenBurnsProvider
from services.job_queue import JobQueueManager
from services.notifier import TelegramNotifier
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

        # Components
        self.jq = JobQueueManager()
        self.account_pool = AccountPoolManager()
        self.primary_provider = GeminiWebApiProvider()
        self.fallback_provider = ImageKenBurnsProvider(fallback_provider=self.primary_provider)
        self.uploader = StorageUploader()
        self.notifier = TelegramNotifier()
        self.factory = AutomatedFactory(db_path=self.jq.db_path)

        # Remote Telegram Command Listener
        self.bot = TelegramCommandBot(
            on_reup_handler=self.factory.run_smart_reup,
            on_remake_handler=self.factory.run_ai_genesis,
            on_approve_handler=self.factory.execute_approved_reup,
            job_queue=self.jq,
            account_pool=self.account_pool,
        )

        # Register signals for safe restart
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.info("Received shutdown signal (%s). Finishing current step before stopping...", signum)
        self.running = False
        if hasattr(self, "bot"):
            self.bot.stop_polling()

    def run(self):
        logger.info("==================================================")
        logger.info("🚀 KHỞI ĐỘNG VIDEO AUTOMATION WORKER")
        logger.info("==================================================")

        # 1. Reset any interrupted shots on startup (refund attempt counts)
        self.jq.reset_interrupted_shots()

        # 2. Check accounts status
        num_accounts = self.account_pool.reload_accounts()
        pool_status = self.account_pool.get_status_summary()

        logger.info("Số tài khoản Pro sẵn sàng: %d (Available: %d)", num_accounts, pool_status["available"])

        if self.notifier.is_configured:
            self.notifier.send_message(
                f"🤖 <b>Video Worker Đã Khởi Động Thành Công</b>\n"
                f"👤 Tài khoản Pro sẵn sàng: {pool_status['available']}/{num_accounts}\n"
                f"☁️ Google Drive Remote: <code>{self.uploader.remote_name}</code>\n"
                f"📱 Bạn có thể gửi lệnh qua Telegram: <code>/reup &lt;url&gt;</code> hoặc <code>/remake &lt;url&gt;</code>"
            )

        # 3. Start remote Telegram bot listener
        if self.bot.is_configured:
            self.bot.start_polling()

        # 4. Main processing loop
        batch_notified_jobs = set()

        while self.running:
            # Check if kill switch is active
            if self.account_pool.kill_switch_active:
                logger.warning("Kill-switch đang BẬT. Dừng sinh video AI Mode 2. Chờ /resume...")
                time.sleep(30)
                continue

            # Fetch next pending shot across all approved/processing jobs
            shot = self.jq.get_next_pending_shot()
            if not shot:
                # Check if there are any jobs that just finished all shots
                time.sleep(15)
                continue

            job_id = shot["job_id"]
            shot_id = shot["id"]
            shot_idx = shot["index_num"]
            prompt = shot["prompt"]
            attempts = shot.get("attempts", 0)

            # Pick an available account with atomic in-flight reservation
            account = self.account_pool.get_available_account(reserve=True)
            if not account:
                logger.warning("Không có tài khoản Pro nào khả dụng (tất cả đang Cooldown hoặc hết Quota). Chờ 60s...")
                time.sleep(60)
                continue

            acc_id = account.acc_id
            session_file = Path(account.session_file)
            out_filename = f"shot_{shot_idx:03d}.mp4"
            target_out_dir = Path("data/automated_runs") / job_id / "shots"
            target_out_dir.mkdir(parents=True, exist_ok=True)
            target_out_path = target_out_dir / out_filename

            logger.info("▶ Bắt đầu xử lý %s (Cảnh %d, lần %d) bằng tài khoản %s", shot_id, shot_idx, attempts + 1, acc_id)
            self.jq.mark_shot_in_progress(shot_id, acc_id)

            # Quota Optimization: If local video exists from previous attempt where only upload failed, reuse it!
            if target_out_path.exists() and target_out_path.stat().st_size > 1024:
                logger.info("⚡ File video cục bộ đã tồn tại (%s). Bỏ qua sinh AI để tiết kiệm quota tài khoản!", target_out_path.name)
                res = ProviderResult(ok=True, video_path=target_out_path)
                duration = 0.5
            else:
                # Generate: use primary provider (Gemini Veo), or fallback to Ken Burns if attempt >= 2
                start_time = time.time()
                if attempts >= 2:
                    logger.warning("Shot %s đã thất bại 2 lần. Kích hoạt Ken Burns graceful fallback!", shot_id)
                    # Protect VPS RAM & CPU from concurrent FFmpeg zoompan rendering
                    while not self.jq.acquire_heavy_lock(job_id):
                        logger.info("Ken Burns fallback đang chờ heavy lock 10s...")
                        time.sleep(10)
                    try:
                        res = self.fallback_provider.generate(prompt=prompt, out_path=target_out_path, session_file=session_file)
                    finally:
                        self.jq.release_heavy_lock(job_id)
                else:
                    res = self.primary_provider.generate(prompt=prompt, out_path=target_out_path, session_file=session_file)

                duration = time.time() - start_time

            if res.ok and res.video_path and res.video_path.exists():
                logger.info("✅ Shot %s sinh thành công sau %.1fs", shot_id, duration)
                self.account_pool.report_success(acc_id)

                # 1. Send Telegram preview before cloud archival
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

                # 2. Upload to Google Drive (retain local buffer for documentary assembly)
                upload_ok, gdrive_url = self.uploader.upload_and_clean(
                    local_path=res.video_path,
                    remote_filename=out_filename,
                    remote_folder=f"Video_Projects/{job_id}/raw_shots",
                    clean_local=False,
                )

                if upload_ok:
                    self.jq.mark_shot_done(shot_id, acc_id, str(res.video_path), gdrive_url)
                    logger.info("✔ Shot %s đã đánh dấu DONE và lưu Drive: %s", shot_id, gdrive_url)
                else:
                    self.jq.mark_shot_failed(shot_id, "Upload to Google Drive failed")

                # Check if job is now completely done (all shots finished)
                updated_summary = self.jq.get_job_summary(job_id)
                if updated_summary["done"] >= updated_summary["total"] and updated_summary["total"] > 0:
                    if job_id not in batch_notified_jobs:
                        batch_notified_jobs.add(job_id)
                        logger.info("🎉 Tất cả %d cảnh của Job %s đã hoàn thành! Bắt đầu ráp documentary hoàn chỉnh...", updated_summary["total"], job_id)
                        self._assemble_and_finalize_documentary(job_id, updated_summary)

            else:
                logger.error("❌ Shot %s thất bại: %s (%s)", shot_id, res.error_message, res.error_kind)

                # Route error to AccountPool
                if res.error_kind == ErrorKind.RATE_LIMIT:
                    self.account_pool.report_rate_limit(acc_id, cooldown_seconds=3600)
                elif res.error_kind == ErrorKind.QUOTA:
                    self.account_pool.report_quota_exhausted(acc_id)
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

                # Check if all shots for this job have settled (no pending, no in_progress)
                settled_summary = self.jq.get_job_summary(job_id)
                if settled_summary["pending"] == 0 and settled_summary["in_progress"] == 0:
                    if settled_summary["failed"] > 0:
                        logger.error("Job %s kết thúc với %d cảnh thất bại vĩnh viễn.", job_id, settled_summary["failed"])
                        self.jq.update_job_status(
                            job_id,
                            status="FAILED",
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
        """Assembles all generated shots into a final documentary video and uploads to Google Drive."""
        project_dir = Path("data/automated_runs") / job_id
        while not self.jq.acquire_heavy_lock(job_id):
            time.sleep(15)

        try:
            from pipeline.documentary_assembler import assemble_documentary
            logger.info("Assembling full documentary for Job %s...", job_id)
            final_video = assemble_documentary(job_id=job_id, project_dir=project_dir)

            # Upload final assembled documentary to Google Drive
            remote_name = f"documentary_final_{job_id}.mp4"
            upload_ok, drive_url = self.uploader.upload_and_clean(
                local_path=final_video,
                remote_filename=remote_name,
                remote_folder=f"Video_Projects/{job_id}",
                clean_local=True,
            )

            # Clean up local raw shots buffer to free disk space
            shutil.rmtree(project_dir / "shots", ignore_errors=True)

            if upload_ok:
                self.jq.update_job_status(job_id, status="COMPLETED", final_video=str(final_video), drive_url=drive_url)
                if self.notifier.is_configured:
                    self.notifier.send_message(
                        f"🎉 <b>[MODE 2 HOÀN TẤT] PHIM TÀI LIỆU ĐÃ RÁP XONG!</b>\n\n"
                        f"🆔 Job: <code>{job_id}</code>\n"
                        f"🎞 Tổng số cảnh: <b>{summary['done']} / {summary['total']} cảnh</b>\n"
                        f"☁️ Google Drive: <code>{drive_url}</code>\n"
                        f"✨ Video phim tài liệu hoàn chỉnh đã được kiểm định QC và tải lên Google Drive!"
                    )
            else:
                self.jq.update_job_status(job_id, status="FAILED", error_message="Upload final documentary to Google Drive failed")
        except Exception as e:
            logger.error("Failed to assemble documentary for %s: %s", job_id, e, exc_info=True)
            self.jq.update_job_status(job_id, status="FAILED", error_message=f"Documentary assembly failed: {e}")
            if self.notifier.is_configured:
                self.notifier.send_message(f"❌ <b>[LỖI RÁP PHIM TÀI LIỆU - {job_id}]:</b> <code>{str(e)[:300]}</code>")
        finally:
            self.jq.release_heavy_lock(job_id)


if __name__ == "__main__":
    worker = VideoAutomationWorker()
    worker.run()
