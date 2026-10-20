"""Telegram Remote Command Bot for Automated Video Factory.

Allows controlling the pipeline directly from a smartphone via Telegram:
- /reup <youtube_url> [duration=180]  -> Trigger Mode 1 (Smart Reup)
- /remake <youtube_url>              -> Trigger Mode 2 (Full AI Genesis)
- /approve <job_id>                  -> Approve script and proceed with generation/render
- /reject <job_id>                   -> Reject script and cancel job
- /status                             -> Query VPS resources, disk, accounts & queue progress
- /pause                              -> Manually pause generation
- /resume                             -> Resume generation after kill-switch or manual pause
- /help                               -> Show command reference
"""

from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Callable, Optional
import requests

from services.account_pool import AccountPoolManager
from services.job_queue import JobQueueManager
from services.notifier import TelegramNotifier

logger = logging.getLogger("telegram_bot")


class TelegramCommandBot:
    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        on_reup_handler: Optional[Callable] = None,
        on_remake_handler: Optional[Callable] = None,
        on_approve_handler: Optional[Callable] = None,
        job_queue: Optional[JobQueueManager] = None,
        account_pool: Optional[AccountPoolManager] = None,
        max_workers: int = 2,
    ):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.allowed_chat_id = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID", ""))
        self.notifier = TelegramNotifier(self.token, self.allowed_chat_id)
        self.on_reup_handler = on_reup_handler
        self.on_remake_handler = on_remake_handler
        self.on_approve_handler = on_approve_handler
        self.jq = job_queue or JobQueueManager()
        self.pool = account_pool or AccountPoolManager()
        self.running = False
        self.last_update_id = 0
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tg_worker")

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.allowed_chat_id)

    def start_polling(self):
        """Start listening for incoming Telegram commands in a background thread."""
        if not self.is_configured:
            logger.warning("Telegram Bot is not fully configured. Polling disabled.")
            return

        self.running = True
        logger.info("Starting Telegram Bot long-polling...")
        thread = threading.Thread(target=self._poll_loop, daemon=True)
        thread.start()

    def stop_polling(self):
        self.running = False

    def _poll_loop(self):
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        while self.running:
            try:
                params = {"offset": self.last_update_id + 1, "timeout": 20}
                resp = requests.get(url, params=params, timeout=25)
                if resp.status_code != 200:
                    time.sleep(5)
                    continue

                data = resp.json()
                for update in data.get("result", []):
                    self.last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    sender_id = str(chat.get("id", ""))
                    text = msg.get("text", "").strip()

                    # Security check: only allow commands from the designated chat_id
                    if self.allowed_chat_id and sender_id != self.allowed_chat_id:
                        logger.warning("Ignored message from unauthorized chat_id: %s", sender_id)
                        continue

                    if text:
                        self._handle_command(text)

            except Exception as e:
                logger.error("Error in Telegram polling loop: %s", e)
                time.sleep(5)

    def _handle_command(self, text: str):
        parts = text.split()
        cmd = parts[0].lower()

        if cmd in ["/start", "/help"]:
            help_msg = (
                "🤖 <b>HỆ THỐNG ĐIỀU KHIỂN VIDEO FACTORY TỰ ĐỘNG</b>\n\n"
                "📌 <b>Các lệnh khả dụng:</b>\n"
                "• <code>/reup &lt;link_youtube&gt; [duration=180]</code>\n"
                "  ➔ <i>Mode 1: Tóm tắt & cắt dựng nhanh từ video gốc.</i>\n\n"
                "• <code>/remake &lt;link_youtube&gt;</code>\n"
                "  ➔ <i>Mode 2: Sinh 100% video AI mới bằng 10 tài khoản Pro.</i>\n\n"
                "• <code>/approve &lt;job_id&gt;</code>\n"
                "  ➔ <i>Duyệt kịch bản và tiến hành sản xuất video.</i>\n\n"
                "• <code>/reject &lt;job_id&gt;</code>\n"
                "  ➔ <i>Từ chối kịch bản và hủy job.</i>\n\n"
                "• <code>/cancel &lt;job_id&gt;</code>\n"
                "  ➔ <i>Hủy job đang chạy và dừng các cảnh còn lại.</i>\n\n"
                "• <code>/retry &lt;job_id&gt;</code>\n"
                "  ➔ <i>Khởi động lại job bị lỗi hoặc đã hủy.</i>\n\n"
                "• <code>/status</code>\n"
                "  ➔ <i>Kiểm tra tài nguyên VPS, quota tài khoản & tiến độ.</i>\n\n"
                "• <code>/pause</code> | <code>/resume</code>\n"
                "  ➔ <i>Tạm dừng hoặc kích hoạt lại sau kill-switch.</i>"
            )
            self.notifier.send_message(help_msg)

        elif cmd == "/status":
            pool_summary = self.pool.get_status_summary()
            
            # Disk space check
            try:
                disk = shutil.disk_usage(".")
                free_gb = round(disk.free / (1024**3), 1)
                total_gb = round(disk.total / (1024**3), 1)
                disk_str = f"{free_gb} GB / {total_gb} GB"
            except Exception:
                disk_str = "Không rõ"

            # Active queue stats
            try:
                with self.jq._get_conn() as conn:
                    rows = conn.execute("""
                        SELECT status, COUNT(*) as cnt FROM factory_jobs
                        WHERE status NOT IN ('COMPLETED', 'CANCELLED')
                        GROUP BY status
                    """).fetchall()
                    status_counts = {r["status"]: r["cnt"] for r in rows}
                queue_str = ", ".join(f"{k}: {v}" for k, v in status_counts.items()) if status_counts else "Trống"
            except Exception:
                queue_str = "Không rõ"

            kill_status = "🔴 ĐANG TẠM DỪNG (KILL-SWITCH)" if pool_summary["kill_switch_active"] else "🟢 HOẠT ĐỘNG BÌNH THƯỜNG"

            status_msg = (
                f"📊 <b>BÁO CÁO TRẠNG THÁI HỆ THỐNG:</b>\n"
                f"• Trạng thái: <b>{kill_status}</b>\n"
                f"• Đĩa trống VPS: <b>{disk_str}</b>\n"
                f"• Hàng đợi tác vụ: <code>{queue_str}</code>\n\n"
                f"👤 <b>Tài khoản Gemini Pro ({pool_summary['total_accounts']}):</b>\n"
                f"• Sẵn sàng: <b>{pool_summary['available']}</b> | Đang chờ reset: <b>{pool_summary['exhausted']}</b>\n"
                f"• Cooldown: <b>{pool_summary['cooldown']}</b> | Lỗi xác thực: <b>{pool_summary['auth_error']}</b>"
            )
            if pool_summary["kill_switch_reason"]:
                status_msg += f"\n⚠️ Lý do dừng: <code>{pool_summary['kill_switch_reason']}</code>"
            self.notifier.send_message(status_msg)

        elif cmd == "/reup":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng cung cấp link YouTube: <code>/reup &lt;url&gt; [duration=180]</code>")
                return
            url = parts[1].strip()

            # Prevent duplicate submissions of currently active URL
            try:
                with self.jq._get_conn() as conn:
                    existing = conn.execute(
                        "SELECT id, status FROM factory_jobs WHERE url = ? AND status NOT IN ('COMPLETED', 'CANCELLED', 'FAILED')",
                        (url,)
                    ).fetchone()
                    if existing:
                        self.notifier.send_message(
                            f"⚠️ URL này đang được xử lý trong Job <code>{existing['id']}</code> (Trạng thái: <b>{existing['status']}</b>).\n"
                            f"Vui lòng đợi job hoàn tất hoặc dùng <code>/status</code>."
                        )
                        return
            except Exception as e:
                logger.debug("Duplicate URL check error: %s", e)

            duration = 180.0
            for p in parts[2:]:
                if p.startswith("duration="):
                    try:
                        duration = float(p.split("=")[1])
                    except ValueError:
                        pass

            if self.on_reup_handler:
                self._executor.submit(self.on_reup_handler, url, duration)

        elif cmd == "/remake":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng cung cấp link YouTube: <code>/remake &lt;url&gt;</code>")
                return
            url = parts[1].strip()

            # Prevent duplicate submissions of currently active URL
            try:
                with self.jq._get_conn() as conn:
                    existing = conn.execute(
                        "SELECT id, status FROM factory_jobs WHERE url = ? AND status NOT IN ('COMPLETED', 'CANCELLED', 'FAILED')",
                        (url,)
                    ).fetchone()
                    if existing:
                        self.notifier.send_message(
                            f"⚠️ URL này đang được xử lý trong Job <code>{existing['id']}</code> (Trạng thái: <b>{existing['status']}</b>).\n"
                            f"Vui lòng đợi job hoàn tất hoặc dùng <code>/status</code>."
                        )
                        return
            except Exception as e:
                logger.debug("Duplicate URL check error: %s", e)

            if self.on_remake_handler:
                self._executor.submit(self.on_remake_handler, url)

        elif cmd == "/approve":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job: <code>/approve &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            job = self.jq.get_job(job_id)
            if not job or job.get("status") != "WAITING_APPROVAL":
                self.notifier.send_message(f"⚠️ Không thể duyệt Job <code>{job_id}</code> (Job không ở trạng thái CHỜ DUYỆT hoặc không tồn tại).")
                return

            ok = self.jq.approve_job(job_id)
            if ok:
                mode = str(job.get("mode", "")).upper()
                if mode in ("SMART_REUP", "REUP"):
                    self.notifier.send_message(f"✅ <b>Đã duyệt Job Reup: {job_id}!</b>\n🚀 Đang kích hoạt render video hoàn chỉnh...")
                    if self.on_approve_handler:
                        self._executor.submit(self.on_approve_handler, job_id)
                else:
                    self.notifier.send_message(
                        f"✅ <b>Đã duyệt Job Mode 2: {job_id}!</b>\n"
                        f"🚀 Dây chuyền worker đang tiến hành sinh và ráp các cảnh tự động..."
                    )
            else:
                self.notifier.send_message(f"⚠️ Không thể duyệt Job <code>{job_id}</code>.")

        elif cmd == "/reject":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job: <code>/reject &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            ok = self.jq.reject_job(job_id)
            if ok:
                self.notifier.send_message(f"🚫 <b>Đã hủy Job: {job_id}.</b>")
            else:
                self.notifier.send_message(f"⚠️ Không thể hủy Job <code>{job_id}</code>.")

        elif cmd == "/cancel":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job: <code>/cancel &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            ok = self.jq.cancel_job(job_id, reason="Cancelled via Telegram /cancel")
            if ok:
                self.notifier.send_message(f"🛑 <b>Đã hủy Job: {job_id}</b> và dừng các cảnh còn lại.")
            else:
                self.notifier.send_message(f"⚠️ Không thể hủy Job <code>{job_id}</code> (Job không tồn tại hoặc đã hoàn thành).")

        elif cmd == "/retry":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job: <code>/retry &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            ok = self.jq.retry_job(job_id)
            if ok:
                self.notifier.send_message(f"🔄 <b>Đã reset Job: {job_id}!</b> Hệ thống sẽ tiếp tục xử lý các cảnh còn dở.")
            else:
                self.notifier.send_message(f"⚠️ Không thể chạy lại Job <code>{job_id}</code> (Job không ở trạng thái LỖI hoặc ĐÃ HỦY).")

        elif cmd == "/pause":
            self.pool.trigger_kill_switch("ADMIN", "Manual pause requested via Telegram /pause")
            self.notifier.send_message("⏸ <b>Hệ thống đã tạm dừng.</b> Không có tác vụ sinh video AI mới nào được thực hiện.")

        elif cmd == "/resume":
            self.pool.reset_kill_switch()
            self.notifier.send_message("▶️ <b>Hệ thống đã tiếp tục hoạt động!</b> Dây chuyền worker đã sẵn sàng.")

        else:
            self.notifier.send_message(f"❓ Không nhận diện được lệnh: <code>{cmd}</code>. Gõ <code>/help</code> để xem hướng dẫn.")


if __name__ == "__main__":
    bot = TelegramCommandBot()
    if bot.is_configured:
        print("Starting test polling... Press Ctrl+C to stop.")
        bot.start_polling()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            bot.stop_polling()
            print("Stopped.")
    else:
        print("Telegram bot is not configured with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
