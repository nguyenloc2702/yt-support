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
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Callable, Optional
import requests

from services.account_pool import AccountPoolManager
from services.gemini_webapi_provider import GeminiWebApiProvider
from services.job_queue import JobQueueManager
from services.notifier import TelegramNotifier, get_default_keyboard

logger = logging.getLogger("telegram_bot")


def format_vn_time(iso_str: Optional[str]) -> str:
    """Formats an ISO timestamp to Vietnam Time (UTC+7) with human countdown."""
    if not iso_str:
        return "Không có"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        vn_tz = timezone(timedelta(hours=7))
        vn_dt = dt.astimezone(vn_tz)
        diff = dt - datetime.now(timezone.utc)
        if diff.total_seconds() > 0:
            hrs = int(diff.total_seconds() // 3600)
            mins = int((diff.total_seconds() % 3600) // 60)
            secs = int(diff.total_seconds() % 60)
            if hrs > 0:
                remain = f"còn {hrs}h {mins}p"
            elif mins > 0:
                remain = f"còn {mins}p {secs}s"
            else:
                remain = f"còn {secs}s"
            return f"<b>{vn_dt.strftime('%H:%M:%S (%d/%m)')}</b> (⏳ {remain})"
        else:
            return f"<b>{vn_dt.strftime('%H:%M:%S (%d/%m)')}</b> (✅ Đã reset)"
    except Exception:
        return str(iso_str)


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
        self.base_url = f"https://api.telegram.org/bot{self.token}" if self.token else ""
        self.allowed_chat_id = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID", ""))
        self.notifier = TelegramNotifier(self.token, self.allowed_chat_id)
        self.on_reup_handler = on_reup_handler
        self.on_remake_handler = on_remake_handler
        self.on_approve_handler = on_approve_handler
        self.jq = job_queue or JobQueueManager()
        self.pool = account_pool or AccountPoolManager()
        self.provider = GeminiWebApiProvider()
        self.running = False
        stored_offset = self.jq.get_kv("telegram_last_update_id") if self.jq else None
        self.last_update_id = int(stored_offset) if (stored_offset and stored_offset.isdigit()) else 0
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
        logger.info("Starting Telegram Bot long-polling (offset: %d)...", self.last_update_id)

        # Register bot commands menu with Telegram asynchronously
        threading.Thread(target=self.notifier.set_bot_commands, daemon=True).start()

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
                    try:
                        self.jq.set_kv("telegram_last_update_id", str(self.last_update_id))
                    except Exception:
                        pass

                    # 1. Handle Inline Button Clicks (Callback Query)
                    cb = update.get("callback_query")
                    if cb:
                        sender_id = str(cb.get("from", {}).get("id", ""))
                        if not self.allowed_chat_id or sender_id == self.allowed_chat_id:
                            self._handle_callback_query(cb)
                        continue

                    # 2. Handle Messages
                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    sender_id = str(chat.get("id", ""))
                    text = msg.get("text", "").strip()
                    doc = msg.get("document")

                    # Security check: only allow commands from the designated chat_id
                    if self.allowed_chat_id and sender_id != self.allowed_chat_id:
                        logger.warning("Ignored message from unauthorized chat_id: %s", sender_id)
                        continue

                    if doc:
                        self._handle_document(doc, caption=msg.get("caption", ""))
                    elif text:
                        self._handle_command(text)

            except Exception as e:
                logger.error("Error in Telegram polling loop: %s", e)
                time.sleep(5)

    def _handle_callback_query(self, cb: dict):
        """Processes button presses from Telegram inline keyboards."""
        cb_id = cb.get("id")
        data = cb.get("data", "")
        try:
            requests.post(f"{self.base_url}/answerCallbackQuery", json={"callback_query_id": cb_id}, timeout=10)
        except Exception:
            pass

        if not data:
            return

        parts = data.split(":")
        action = parts[0]

        if action == "remake_ai" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/remake_ai {job_id}")

        elif action == "remake_custom" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/custom_script {job_id}")

        elif action == "voice" and len(parts) >= 3:
            job_id = parts[1]
            voice_id = parts[2]
            self._handle_command(f"/voice {job_id} {voice_id}")

        elif action == "approve" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/approve {job_id}")

        elif action == "reject" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/reject {job_id}")

    def _handle_document(self, doc: dict, caption: str = ""):
        """Processes uploaded document files (e.g. .txt custom scripts)."""
        file_name = doc.get("file_name", "")
        file_id = doc.get("file_id")
        if not file_id:
            return

        target_job = None
        if caption:
            for word in caption.split():
                j = self.jq.get_job(word.strip())
                if j:
                    target_job = j
                    break

        if not target_job:
            target_job = self.jq.get_active_awaiting_script_job()

        if not target_job:
            self.notifier.send_message(
                "⚠️ Nhận file nhưng không tìm thấy Job nào đang chờ nhập script (trạng thái <code>AWAITING_CUSTOM_SCRIPT</code>).\n"
                "Vui lòng tạo job bằng <code>/remake &lt;url&gt;</code> trước khi gửi file."
            )
            return

        job_id = target_job["id"]
        try:
            file_info_resp = requests.get(f"{self.base_url}/getFile?file_id={file_id}", timeout=15)
            if file_info_resp.status_code != 200:
                self.notifier.send_message(f"❌ Không thể tải file từ Telegram (mã lỗi {file_info_resp.status_code}).")
                return
            file_path = file_info_resp.json().get("result", {}).get("file_path")
            dl_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            file_content_resp = requests.get(dl_url, timeout=30)
            file_text = file_content_resp.content.decode("utf-8", errors="replace").strip()
        except Exception as e:
            self.notifier.send_message(f"❌ Lỗi khi đọc file đính kèm: <code>{e}</code>")
            return

        self._process_custom_script_for_job(job_id, file_text)

    def _process_custom_script_for_job(self, job_id: str, script_text: str):
        """Audits custom script and dispatches remake pipeline if valid."""
        from services.script_auditor import ScriptAuditor
        auditor = ScriptAuditor()
        audit_res = auditor.audit_script(script_text)

        if not audit_res.is_valid:
            feedback_msg = audit_res.format_telegram_feedback(job_id=job_id)
            self.notifier.send_message(feedback_msg)
            return

        job = self.jq.get_job(job_id)
        if not job:
            self.notifier.send_message(f"❌ Không tìm thấy thông tin Job {job_id}.")
            return

        self.notifier.send_message(
            f"✅ <b>Kịch bản đã đạt chuẩn hoàn hảo! ({audit_res.total_scenes} cảnh, ngôn ngữ: {audit_res.language.upper()})</b>\n"
            f"🚀 Đang khởi động phân tích phân cảnh và tạo mẫu giọng đọc nghe thử..."
        )

        if self.on_remake_handler:
            try:
                import inspect
                sig = inspect.signature(self.on_remake_handler)
                has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                if "custom_script" in sig.parameters or has_kwargs:
                    self._executor.submit(self.on_remake_handler, job["url"], job_id=job_id, custom_script=script_text)
                else:
                    self._executor.submit(self.on_remake_handler, job["url"], job_id=job_id)
            except Exception:
                self._executor.submit(self.on_remake_handler, job["url"], job_id=job_id)

    def get_accounts_report(self, check_realtime: bool = True) -> str:
        """Inspects all accounts in pool and returns a detailed real-time HTML status report."""
        accounts = self.pool.get_all_accounts_state()
        if not accounts:
            self.pool.reload_accounts()
            accounts = self.pool.get_all_accounts_state()

        if not accounts:
            return (
                "📋 <b>DANH SÁCH TÀI KHOẢN GOOGLE GEMINI:</b>\n\n"
                "⚠️ <i>Chưa có tài khoản nào được nạp vào thư mục <code>sessions/</code>.</i>\n\n"
                "👉 Dùng lệnh terminal sau trên VPS để nạp cookie:\n"
                "<code>python scripts/manage_accounts.py add --acc 1</code>"
            )

        report_lines = [
            "📋 <b>BÁO CÁO CHI TIẾT TÀI KHOẢN (REALTIME):</b>\n"
        ]

        now = datetime.now(timezone.utc)
        total_accs = len(accounts)
        ready_count = 0
        sorted_acc_ids = sorted(accounts.keys())

        for idx, acc_id in enumerate(sorted_acc_ids, 1):
            acc = accounts[acc_id]
            session_file = Path(acc.get("session_file", f"sessions/{acc_id}.json"))
            status = acc.get("status", "UNKNOWN")
            shots_today = acc.get("shots_today", 0)
            total_shots = acc.get("total_shots", 0)
            cd_until = acc.get("cooldown_until")

            tier = "UNKNOWN"
            credits_remaining = None
            reset_at = cd_until
            is_valid = True

            if check_realtime and session_file.exists():
                try:
                    val_res = self.provider.validate_session(session_file)
                    is_valid = val_res.valid
                    tier = val_res.tier or "UNKNOWN"
                    if val_res.credits_remaining is not None:
                        credits_remaining = val_res.credits_remaining
                    if val_res.reset_at:
                        reset_at = val_res.reset_at

                    # Auto-recover status if reset time has passed
                    if reset_at:
                        try:
                            rdt = datetime.fromisoformat(reset_at)
                            if rdt.tzinfo is None:
                                rdt = rdt.replace(tzinfo=timezone.utc)
                            if now >= rdt and status in ("EXHAUSTED", "COOLDOWN"):
                                status = "AVAILABLE"
                                self.pool.update_account_status(acc_id, "AVAILABLE", None)
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning("Error checking account %s: %s", acc_id, e)

            # Determine badge & status label
            if not session_file.exists():
                status_icon = "❌"
                status_desc = "MẤT FILE SESSION"
            elif not is_valid:
                status_icon = "🔴"
                status_desc = "HẾT HẠN COOKIE (AUTH_ERROR)"
            elif status == "EXHAUSTED":
                status_icon = "🟡"
                status_desc = "HẾT QUOTA VEO (CHỜ RESET)"
            elif status == "COOLDOWN":
                status_icon = "🟠"
                status_desc = "ĐANG COOLDOWN TẠM THỜI"
            elif tier == "FREE":
                status_icon = "⚠️"
                status_desc = "GÓI FREE (KHÔNG CÓ VEO)"
            else:
                status_icon = "🟢"
                status_desc = "SẴN SÀNG SINH VIDEO"
                ready_count += 1

            tier_badge = "PRO ⭐" if tier == "PRO" else ("FREE ⚠️" if tier == "FREE" else tier)

            report_lines.append(
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>[{idx}/{total_accs}] Tài khoản: <code>{acc_id}</code></b> ({tier_badge})\n"
                f"• Trạng thái: {status_icon} <b>{status_desc}</b>\n"
                f"• Giới hạn mới (Reset lúc): {format_vn_time(reset_at)}\n"
                f"• Credits 5h còn lại: <b>{credits_remaining if credits_remaining is not None else 'Không rõ'}</b>\n"
                f"• Tiến độ hôm nay: <b>{shots_today}</b> shots (Tổng: <b>{total_shots}</b> shots)\n"
                f"• File session: <code>{session_file.name}</code>"
            )

        report_lines.append("━━━━━━━━━━━━━━━━━━━")
        report_lines.append(
            f"📊 <b>Tổng kết:</b> <b>{ready_count}/{total_accs}</b> tài khoản sẵn sàng sinh video Veo.\n\n"
            f"💡 <i>Gợi ý: Nếu hết quota, hãy nạp thêm tài khoản bằng:</i>\n"
            f"<code>python scripts/manage_accounts.py add --acc 2</code>\n"
            f"<i>Sau đó gõ <code>/resume</code> để tiếp tục dây chuyền.</i>"
        )

        return "\n".join(report_lines)

    def _handle_command(self, text: str):
        # Strip button emoji prefixes if user tapped custom reply keyboard
        clean_text = text
        for emoji in ["📋", "📊", "⏸", "▶️", "ℹ️"]:
            clean_text = clean_text.replace(emoji, "")
        clean_text = clean_text.strip()

        parts = clean_text.split()
        if not parts:
            return
        cmd = parts[0].lower()

        # Search for any YouTube URL anywhere in the message text
        import re
        yt_match = re.search(r"https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/[^\s]+", text)
        found_url = yt_match.group(0) if yt_match else None

        # Handle raw YouTube URLs or candidate custom script text sent without a slash command
        if not cmd.startswith("/"):
            if found_url:
                from pipeline.youtube import extract_youtube_video_id
                vid = extract_youtube_video_id(found_url)
                if vid:
                    self.notifier.send_message(
                        f"💡 <b>Nhận diện link YouTube:</b>\n<code>{found_url}</code>\n\n"
                        f"Vui lòng chọn chế độ sản xuất:\n"
                        f"• <code>/reup {found_url}</code> ➔ <i>Mode 1: Tóm tắt & cắt dựng nhanh từ video gốc</i>\n"
                        f"• <code>/remake {found_url}</code> ➔ <i>Mode 2: Sinh 100% video AI mới (Veo/Ken Burns)</i>",
                        reply_markup=get_default_keyboard(),
                    )
                else:
                    self.notifier.send_message(
                        f"⚠️ <b>Link YouTube không hợp lệ hoặc thiếu mã video:</b>\n<code>{found_url}</code>\n\n"
                        "Link bạn gửi chỉ có mã theo dõi (<code>si=...</code>) mà thiếu mã video (<code>v=...</code>).\n"
                        "Vui lòng mở video trên YouTube và bấm <b>Chia sẻ</b> ➔ <b>Sao chép liên kết</b> để lấy link đầy đủ (dạng <code>https://youtu.be/...</code> hoặc <code>https://www.youtube.com/watch?v=...</code>).",
                        reply_markup=get_default_keyboard(),
                    )
            else:
                # Check if there is an active job awaiting custom script input
                active_job = self.jq.get_active_awaiting_script_job()
                if active_job and len(text.split()) >= 10:
                    logger.info("Received candidate custom script (%d words) for waiting job %s", len(text.split()), active_job['id'])
                    self._process_custom_script_for_job(active_job["id"], text)
            return

        if cmd in ["/start", "/help"]:
            help_msg = (
                "🤖 <b>HỆ THỐNG ĐIỀU KHIỂN VIDEO FACTORY TỰ ĐỘNG</b>\n\n"
                "📌 <b>Các lệnh khả dụng:</b>\n"
                "• <code>/accounts</code> | <code>/acc</code>\n"
                "  ➔ <i>Xem danh sách, thông tin chi tiết & quota realtime từng tài khoản.</i>\n\n"
                "• <code>/reup &lt;link_youtube&gt; [duration=180]</code>\n"
                "  ➔ <i>Mode 1: Tóm tắt & cắt dựng nhanh từ video gốc.</i>\n\n"
                "• <code>/remake &lt;link_youtube&gt; [ai|custom]</code>\n"
                "  ➔ <i>Mode 2: Sinh 100% video AI mới (chọn AI Genesis hoặc Tự nhập script).</i>\n\n"
                "• <code>/custom_script &lt;job_id&gt;</code> | <code>/script &lt;job_id&gt; &lt;text&gt;</code>\n"
                "  ➔ <i>Nạp kịch bản tùy biến cho Job để AI thẩm định.</i>\n\n"
                "• <code>/voice &lt;job_id&gt; &lt;voice_id&gt;</code>\n"
                "  ➔ <i>Chọn giọng đọc tài liệu (Nam Minh / Hoài My / Guy US).</i>\n\n"
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
            self.notifier.send_message(help_msg, reply_markup=get_default_keyboard())

        elif cmd in ["/accounts", "/acc"]:
            self.notifier.send_message("🔍 <i>Đang kiểm tra quota realtime từng tài khoản từ Google server...</i>")
            def _async_acc_report():
                report = self.get_accounts_report(check_realtime=True)
                self.notifier.send_message(report, reply_markup=get_default_keyboard())
            self._executor.submit(_async_acc_report)

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
                    up_shots = conn.execute("SELECT COUNT(*) FROM factory_shots WHERE status = 'UPLOAD_PENDING'").fetchone()[0]
                    if up_shots > 0:
                        status_counts["SHOTS_UPLOAD_PENDING"] = up_shots
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
                f"• Cooldown: <b>{pool_summary['cooldown']}</b> | Lỗi xác thực: <b>{pool_summary['auth_error']}</b>\n\n"
                f"👉 <b>Bấm /accounts để xem chi tiết quota realtime từng tài khoản.</b>"
            )
            if pool_summary["kill_switch_reason"]:
                status_msg += f"\n⚠️ Lý do dừng: <code>{pool_summary['kill_switch_reason']}</code>"
            self.notifier.send_message(status_msg, reply_markup=get_default_keyboard())

        elif cmd == "/reup":
            url = found_url or (parts[1].strip() if len(parts) > 1 else "")
            if not url:
                self.notifier.send_message("⚠️ Vui lòng cung cấp link YouTube: <code>/reup &lt;url&gt; [duration=180]</code>")
                return
            from pipeline.youtube import extract_youtube_video_id
            if not extract_youtube_video_id(url):
                self.notifier.send_message(
                    f"⚠️ <b>Link YouTube không hợp lệ hoặc thiếu mã video:</b>\n<code>{url}</code>\n\n"
                    "Link bạn gửi chỉ có mã theo dõi (<code>si=...</code>) mà thiếu mã video (<code>v=...</code>).\n"
                    "Vui lòng lấy lại link đầy đủ từ nút <b>Chia sẻ</b> trên YouTube."
                )
                return

            duration = 180.0
            for p in parts[1:]:
                if p.startswith("duration="):
                    try:
                        duration = float(p.split("=")[1])
                    except ValueError:
                        pass

            # Atomic creation and duplicate check (P1.9)
            job_id = self.jq.create_job_if_no_active("reup", url, target_duration=duration)
            if not job_id:
                self.notifier.send_message(
                    "⚠️ URL này đang được xử lý trong một Job khác. Vui lòng đợi hoàn tất hoặc dùng <code>/status</code>."
                )
                return

            if self.on_reup_handler:
                try:
                    import inspect
                    sig = inspect.signature(self.on_reup_handler)
                    if "job_id" in sig.parameters:
                        self._executor.submit(self.on_reup_handler, url, duration, job_id=job_id)
                    else:
                        self._executor.submit(self.on_reup_handler, url, duration)
                except Exception:
                    self._executor.submit(self.on_reup_handler, url, duration)

        elif cmd == "/remake":
            url = found_url or (parts[1].strip() if len(parts) > 1 else "")
            if not url:
                self.notifier.send_message("⚠️ Vui lòng cung cấp link YouTube: <code>/remake &lt;url&gt;</code>")
                return
            from pipeline.youtube import extract_youtube_video_id
            if not extract_youtube_video_id(url):
                self.notifier.send_message(
                    f"⚠️ <b>Link YouTube không hợp lệ hoặc thiếu mã video:</b>\n<code>{url}</code>\n\n"
                    "Link bạn gửi chỉ có mã theo dõi (<code>si=...</code>) mà thiếu mã video (<code>v=...</code>).\n"
                    "Vui lòng lấy lại link đầy đủ từ nút <b>Chia sẻ</b> trên YouTube."
                )
                return

            submode = ""
            for p in parts[1:]:
                if p.lower() in ("ai", "auto", "custom"):
                    submode = p.lower()
                    break

            # Atomic creation and duplicate check (P1.9)
            job_id = self.jq.create_job_if_no_active("remake", url)
            if not job_id:
                self.notifier.send_message(
                    "⚠️ URL này đang được xử lý trong một Job khác. Vui lòng đợi hoàn tất hoặc dùng <code>/status</code>."
                )
                return

            if submode in ("ai", "auto"):
                if self.on_remake_handler:
                    self._executor.submit(self.on_remake_handler, url, job_id=job_id)
            elif submode == "custom":
                self.jq.transition_job(job_id, to_state="AWAITING_CUSTOM_SCRIPT")
                self.notifier.send_message(
                    f"✍️ <b>[JOB {job_id}: NHẬP SCRIPT TÙY BIẾN]</b>\n\n"
                    f"Vui lòng gửi nội dung kịch bản cho Job này bằng cách:\n"
                    f"1. Gửi file đính kèm <code>.txt</code>\n"
                    f"2. Hoặc paste trực tiếp nội dung văn bản vào khung chat (hoặc gõ: <code>/script {job_id} &lt;nội dung&gt;</code>).\n\n"
                    f"<i>Lưu ý: Kịch bản tài liệu nên có 24 cảnh (mỗi cảnh 18-25 từ, tối đa 28 từ).</i>"
                )
            else:
                inline_options = {
                    "inline_keyboard": [
                        [
                            {"text": "🤖 Option 1: AI Tự Động Biên Kịch", "callback_data": f"remake_ai:{job_id}"},
                        ],
                        [
                            {"text": "✍️ Option 2: Tôi Sẽ Nhập Script", "callback_data": f"remake_custom:{job_id}"},
                        ]
                    ]
                }
                msg = (
                    f"🎬 <b>[MODE 2: REMAKE AI] Nhận diện link video:</b>\n"
                    f"<code>{url}</code>\n"
                    f"🆔 Job ID: <code>{job_id}</code>\n\n"
                    f"Vui lòng chọn 1 trong 2 phương án biên kịch:\n\n"
                    f"<b>1️⃣ Option 1 - AI Tự Biên Kịch:</b>\n"
                    f"AI sẽ tải video, lọc sạch metadata phụ đề và tạo song song 2 kịch bản tiếng Việt & tiếng Anh.\n"
                    f"👉 Bấm nút <b>AI Tự Động Biên Kịch</b> hoặc gõ: <code>/remake_ai {job_id}</code>\n\n"
                    f"<b>2️⃣ Option 2 - Tôi Tự Nhập Script:</b>\n"
                    f"Bạn tự gửi kịch bản (paste text hoặc gửi file .txt). AI sẽ thẩm định độ dài, nhịp đọc 10s và từ an toàn Veo.\n"
                    f"👉 Bấm nút <b>Tôi Sẽ Nhập Script</b> hoặc gõ: <code>/custom_script {job_id}</code>"
                )
                self.notifier.send_message(msg, reply_markup=inline_options)

        elif cmd == "/remake_ai":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job: <code>/remake_ai &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            job = self.jq.get_job(job_id)
            if not job:
                self.notifier.send_message(f"❌ Không tìm thấy Job {job_id}.")
                return
            if self.on_remake_handler:
                self._executor.submit(self.on_remake_handler, job["url"], job_id=job_id)

        elif cmd == "/custom_script":
            if len(parts) < 2:
                active = self.jq.get_active_awaiting_script_job()
                if active:
                    job_id = active["id"]
                else:
                    self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job: <code>/custom_script &lt;job_id&gt;</code>")
                    return
            else:
                job_id = parts[1]

            self.jq.transition_job(job_id, to_state="AWAITING_CUSTOM_SCRIPT")
            self.notifier.send_message(
                f"✍️ <b>[JOB {job_id}: NHẬP SCRIPT TÙY BIẾN]</b>\n\n"
                f"Hệ thống đang chờ bạn gửi kịch bản:\n"
                f"• Gửi file <code>.txt</code> đính kèm\n"
                f"• Hoặc copy & paste trực tiếp văn bản vào chat này (hoặc gõ: <code>/script {job_id} &lt;nội dung&gt;</code>).\n\n"
                f"<i>Quy chuẩn: 24 cảnh tài liệu (18-25 từ/cảnh để đọc vừa vặn 10s).</i>"
            )

        elif cmd == "/script":
            if len(parts) < 3:
                self.notifier.send_message("⚠️ Cú pháp: <code>/script &lt;job_id&gt; &lt;nội dung kịch bản...&gt;</code>")
                return
            job_id = parts[1]
            script_text = clean_text[len(parts[0]) + len(parts[1]) + 2:].strip()
            self._process_custom_script_for_job(job_id, script_text)

        elif cmd == "/voice":
            if len(parts) < 3:
                self.notifier.send_message(
                    "⚠️ Cú pháp: <code>/voice &lt;job_id&gt; &lt;voice_id&gt;</code>\n"
                    "Ví dụ: <code>/voice remake_xxx vi-VN-NamMinhNeural</code>"
                )
                return
            job_id = parts[1]
            voice_id = parts[2].strip()
            job = self.jq.get_job(job_id)
            if not job:
                self.notifier.send_message(f"❌ Không tìm thấy Job {job_id}.")
                return
            project_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            project_dir.mkdir(parents=True, exist_ok=True)
            voice_file = project_dir / "voice.txt"
            voice_file.write_text(voice_id, encoding="utf-8")
            self.notifier.send_message(
                f"🎙 <b>Đã chọn giọng đọc thành công:</b> <code>{voice_id}</code> cho Job <code>{job_id}</code>.\n"
                f"👉 Gõ <code>/approve {job_id}</code> để tiến hành sản xuất video!"
            )


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
            self.notifier.send_message(
                "⏸ <b>Hệ thống đã tạm dừng.</b> Không có tác vụ sinh video AI mới nào được thực hiện.",
                reply_markup=get_default_keyboard(),
            )

        elif cmd == "/resume":
            self.pool.reset_kill_switch()
            self.notifier.send_message(
                "▶️ <b>Hệ thống đã tiếp tục hoạt động!</b> Dây chuyền worker đã sẵn sàng.",
                reply_markup=get_default_keyboard(),
            )

        else:
            self.notifier.send_message(
                f"❓ Không nhận diện được lệnh: <code>{cmd}</code>. Gõ <code>/help</code> hoặc <code>/accounts</code> để xem thông tin.",
                reply_markup=get_default_keyboard(),
            )


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
