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
import json
import logging
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Callable, Optional
import requests

from services.account_pool import SESSION_STALE_WARN_HOURS, AccountPoolManager
from services.gemini_webapi_provider import GeminiWebApiProvider
from services.job_queue import JobQueueManager
from services.notifier import TelegramNotifier, get_default_keyboard
from services.settings_service import is_veo_enabled, set_veo_enabled

logger = logging.getLogger("telegram_bot")


def _fingerprint(secret: Optional[str]) -> str:
    """Short, non-reversible preview so a pasted cookie can be identified without echoing it."""
    if not secret:
        return "(trống)"
    import hashlib

    return f"sha256:{hashlib.sha256(secret.encode('utf-8')).hexdigest()[:12]} (độ dài {len(secret)})"


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
        factory: Optional[Any] = None,
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
        self._factory = factory
        self.running = False
        stored_offset = self.jq.get_kv("telegram_last_update_id") if self.jq else None
        self.last_update_id = int(stored_offset) if (stored_offset and stored_offset.isdigit()) else 0
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tg_worker")

    @property
    def automated_factory(self):
        """Returns the AutomatedFactory instance, instantiating if needed."""
        if self._factory is None:
            from pipeline.automated_factory import AutomatedFactory
            self._factory = AutomatedFactory(db_path=self.jq.db_path)
        return self._factory

    @property
    def factory(self):
        """Alias for automated_factory."""
        return self.automated_factory

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
                        sender_user_id = str(cb.get("from", {}).get("id", ""))
                        sender_chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                        if not self.allowed_chat_id or sender_user_id == self.allowed_chat_id or sender_chat_id == self.allowed_chat_id:
                            self._handle_callback_query(cb)
                        else:
                            logger.warning("Ignored callback query from unauthorized sender: user=%s, chat=%s", sender_user_id, sender_chat_id)
                        continue

                    # 2. Handle Messages
                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    sender_chat_id = str(chat.get("id", ""))
                    sender_user_id = str(msg.get("from", {}).get("id", ""))
                    text = msg.get("text", "").strip()
                    doc = msg.get("document")

                    # Security check: only allow commands from the designated chat_id or user_id
                    if self.allowed_chat_id and (sender_chat_id != self.allowed_chat_id and sender_user_id != self.allowed_chat_id):
                        logger.warning("Ignored message from unauthorized chat_id/user_id: chat=%s, user=%s", sender_chat_id, sender_user_id)
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

        elif action == "approve_script" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/approve_script {job_id}")

        elif action == "gates" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/gates {job_id}")

        elif action == "approve_audio_vi" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/approve_audio_vi {job_id}")

        elif action == "approve_audio_en" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/approve_audio_en {job_id}")

        elif action == "approve_char" and len(parts) >= 2:
            job_id = parts[1]
            char_id = parts[2] if len(parts) >= 3 else "all"
            self._handle_command(f"/approve_character {job_id} {char_id}")

        elif action == "approve_video" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/approve_video {job_id}")

        elif action == "approve" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/approve {job_id}")

        elif action == "reject" and len(parts) >= 2:
            job_id = parts[1]
            self._handle_command(f"/reject {job_id}")

    def _handle_document(self, doc: dict, caption: str = ""):
        """Processes uploaded document files (e.g. .txt or .srt custom scripts)."""
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

        # Check if file_name itself contains the job_id (e.g. job_remake_1234_...)
        if not target_job and file_name:
            clean_fn = file_name.replace(".", "_").replace("-", "_")
            for part in clean_fn.split("_"):
                if part.startswith("remake_") or part.startswith("reup_"):
                    j = self.jq.get_job(part)
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

    def _async_extract_and_send_script(self, url: str, job_id: str):
        """Extracts original subtitles/transcript and sends .srt & .txt to Telegram."""
        try:
            srt_path, txt_path, title, count = self.automated_factory.extract_and_export_original_script(url, job_id)

            # Send both files directly to Telegram
            self.notifier.send_document(
                str(srt_path),
                caption=f"📄 <b>File phụ đề gốc (.srt)</b>\n🆔 Job: <code>{job_id}</code>\n📺 Video: <i>{title}</i>"
            )
            self.notifier.send_document(
                str(txt_path),
                caption=f"📝 <b>File kịch bản gốc & phân cảnh (.txt)</b>\n🆔 Job: <code>{job_id}</code>\n📺 Video: <i>{title}</i>"
            )

            inline_markup = {
                "inline_keyboard": [
                    [
                        {"text": "🤖 Để AI Tự Động Biên Kịch Luôn", "callback_data": f"remake_ai:{job_id}"},
                    ]
                ]
            }

            instruction_msg = (
                f"📥 <b>ĐÃ GỬI FILE KỊCH BẢN & PHỤ ĐỀ GỐC!</b>\n\n"
                f"🆔 Job ID: <code>{job_id}</code>\n"
                f"📺 Tiêu đề: <b>{title}</b>\n\n"
                f"👉 <b>BÂY GIỜ BẠN CÓ THỂ:</b>\n"
                f"1️⃣ Tải file <code>.srt</code> hoặc <code>.txt</code> ở trên về máy để xem và <b>'xào chẻ'</b> (chỉnh sửa lời kể theo ý muốn).\n"
                f"2️⃣ Sau khi sửa xong, bạn chỉ cần:\n"
                f"   • Gửi lại file <code>.txt</code> hoặc <code>.srt</code> đã sửa lên đây\n"
                f"   • Hoặc copy dán trực tiếp nội dung kịch bản vào khung chat (hoặc gõ: <code>/script {job_id} &lt;nội dung&gt;</code>).\n\n"
                f"<i>💡 (Nếu bạn đổi ý muốn AI tự động viết kịch bản 24 cảnh song ngữ ngay lập tức mà không cần sửa tay, hãy bấm nút dưới đây):</i>"
            )
            self.notifier.send_message(instruction_msg, reply_markup=inline_markup)

        except Exception as e:
            logger.error("Failed to extract and send script for job %s: %s", job_id, e)
            self.notifier.send_message(
                f"⚠️ <b>Lỗi khi trích xuất phụ đề tự động cho Job {job_id}:</b>\n<code>{e}</code>\n\n"
                f"👉 Bạn vẫn có thể tự dán kịch bản của bạn vào đây bằng lệnh: <code>/script {job_id} &lt;nội dung&gt;</code> "
                f"hoặc bấm <code>/remake_ai {job_id}</code> để AI tự động tạo."
            )

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

    def get_accounts_report(self, check_realtime: bool = False) -> str:
        """Inspects all accounts in pool and returns a detailed HTML status report.

        Read-only by default: validate_session() burns a Google request per
        account and fails noisily once a cookie is dead, so the normal /accounts
        view just shows the pool state plus cookie age. Pass check_realtime=True
        (/accounts live) when the owner explicitly wants a fresh auth probe.
        """
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
            "📋 <b>BÁO CÁO TÀI KHOẢN</b>"
            + (" <i>(probe Google trực tiếp)</i>:\n" if check_realtime else ":\n")
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
            fail_streak = int(acc.get("ping_fail_streak", 0) or 0)
            last_success = acc.get("last_success_at")

            tier = acc.get("tier", "UNKNOWN") or "UNKNOWN"
            credits_remaining = None
            reset_at = cd_until
            is_valid = status not in ("AUTH_ERROR",)

            age_hours = self.pool.session_age_hours(acc_id)
            if age_hours is None:
                age_text = "không rõ (file thiếu updated_at)"
            elif age_hours < 1:
                age_text = f"{int(age_hours * 60)} phút"
            else:
                age_text = f"{age_hours:.1f} giờ"

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
                    is_valid = False

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
            stale = age_hours is not None and age_hours >= SESSION_STALE_WARN_HOURS

            report_lines.append(
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>[{idx}/{total_accs}] Tài khoản: <code>{acc_id}</code></b> ({tier_badge})\n"
                f"• Trạng thái: {status_icon} <b>{status_desc}</b>\n"
                f"• Cookie cũ: <b>{age_text}</b>"
                + (" ⚠️ <i>cookie lâu ngày, dễ hết hạn</i>" if stale else "")
                + "\n"
                f"• Ping Google lỗi liên tiếp: <b>{fail_streak}</b>"
                + ("" if last_success is None else f" • Ping OK gần nhất: {format_vn_time(last_success)}")
                + "\n"
                f"• Giới hạn mới (Reset lúc): {format_vn_time(reset_at)}\n"
                f"• Credits 5h còn lại: <b>{credits_remaining if credits_remaining is not None else 'Không rõ'}</b>\n"
                f"• Tiến độ hôm nay: <b>{shots_today}</b> shots (Tổng: <b>{total_shots}</b> shots)\n"
                f"• File session: <code>{session_file.name}</code>"
            )

        report_lines.append("━━━━━━━━━━━━━━━━━━━")
        report_lines.append(
            f"📊 <b>Tổng kết:</b> <b>{ready_count}/{total_accs}</b> tài khoản sẵn sàng sinh video Veo.\n\n"
            f"🩺 Probe Google thật (mỗi tài khoản 1 request): <code>/accounts live</code>\n"
            f"🔑 Cookie chết thì ping không cứu được — dán cookie mới, mất ~2 phút:\n"
            f"<code>/setcookie acc_01 &lt;__Secure-1PSID&gt; &lt;__Secure-1PSIDTS&gt;</code>\n"
            f"🎬 Bật/tắt engine Veo: <code>/veo on</code> | <code>/veo off</code>"
        )

        return "\n".join(report_lines)

    def _handle_setcookie(self, parts: list[str]):
        """Paste fresh Google cookies from the phone: /setcookie <acc_id> <PSID> [PSIDTS].

        Google rotates __Secure-1PSIDTS on every request and a dead cookie cannot
        be pinged back to life, so the only reliable revival path is a fresh paste.
        """
        if len(parts) < 3:
            self.notifier.send_message(
                "⚠️ Thiếu tham số.\n"
                "<code>/setcookie acc_01 &lt;__Secure-1PSID&gt; &lt;__Secure-1PSIDTS&gt;</code>\n\n"
                "Lấy cookie: đăng nhập <code>gemini.google.com</code> bằng đúng tài khoản đó "
                "→ F12 → Application → Cookies → copy 2 giá trị <code>__Secure-1PSID</code> "
                "và <code>__Secure-1PSIDTS</code>."
            )
            return

        acc_id = parts[1].strip()
        if acc_id.endswith(".json"):
            acc_id = acc_id[: -len(".json")]
        psid = parts[2].strip()
        psidts = parts[3].strip() if len(parts) > 3 else None

        def _apply():
            res = self.pool.apply_session_cookies(
                acc_id, psid, psidts, provider=self.provider
            )
            if not res.get("ok"):
                self.notifier.send_message(f"❌ Không lưu được cookie: {res.get('message')}")
                return

            valid = res.get("valid")
            tier = res.get("tier") or "UNKNOWN"
            if valid and tier == "FREE":
                verdict = (
                    "⚠️ Cookie <b>HỢP LỆ</b> nhưng tài khoản này là gói <b>FREE</b> — "
                    "Google không cho Veo. Dùng được cho ảnh Ken Burns, không dùng được cho video Veo."
                )
            elif valid:
                verdict = (
                    f"✅ Cookie <b>HỢP LỆ</b> — tier <b>{tier}</b>, credits 5h còn lại: "
                    f"<b>{res.get('credits_remaining') if res.get('credits_remaining') is not None else 'Không rõ'}</b>.\n"
                    f"{'🟢 Veo đã sẵn sàng. Gõ /resume để dây chuyền chạy tiếp.' if is_veo_enabled() else 'ℹ️ Veo đang TẮT theo cài đặt — gõ <code>/veo on</code> nếu muốn dùng cookie này sinh video.'}"
                )
            else:
                verdict = (
                    f"🔴 Cookie <b>KHÔNG hợp lệ</b>: {res.get('message')}\n"
                    f"Hãy lấy lại cookie từ trình duyệt đang đăng nhập đúng tài khoản này."
                )

            self.notifier.send_message(
                f"🔑 <b>ĐÃ LƯU COOKIE cho {acc_id}</b>\n"
                f"• File: <code>{Path(res.get('session_file', acc_id)).name}</code>\n"
                f"• PSID: <code>{_fingerprint(psid)}</code> (đã lưu, không hiển thị nội dung)\n"
                f"{verdict}"
            )

        self.notifier.send_message(f"⏳ Đang lưu và kiểm tra cookie cho <code>{acc_id}</code>...")
        self._executor.submit(_apply)

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
                "• <code>/accounts</code> | <code>/acc</code> | <code>/accounts live</code>\n"
                "  ➔ <i>Xem tình trạng cookie/quota (chế độ thường chỉ đọc state; <code>live</code> mới probe Google).</i>\n\n"
                "• <code>/setcookie &lt;acc_id&gt; &lt;PSID&gt; [PSIDTS]</code>\n"
                "  ➔ <i>Dán cookie Google mới, hệ thống tự kiểm tra tier + credits ngay lập tức.</i>\n\n"
                "• <code>/veo on</code> | <code>/veo off</code>\n"
                "  ➔ <i>Tắt/bật engine Veo. Tắt = mọi scene dùng Ken Burns, không cần cookie, không hết quota.</i>\n\n"
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
                "  ➔ <i>Tạm dừng hoặc kích hoạt lại sau kill-switch.</i>\n\n"
                "• <code>/feedback &lt;job_id&gt; &lt;điểm 0-10&gt; [ghi chú]</code>\n"
                "  ➔ <i>Chấm retention video đã đăng; AI học bài cho các video sau.</i>"
            )
            self.notifier.send_message(help_msg, reply_markup=get_default_keyboard())

        elif cmd in ["/accounts", "/acc"]:
            live = len(parts) > 1 and parts[1].lower() == "live"
            if live:
                self.notifier.send_message("🔍 <i>Đang probe Google thật cho từng tài khoản (mỗi tài khoản 1 request)...</i>")
            def _async_acc_report():
                report = self.get_accounts_report(check_realtime=live)
                self.notifier.send_message(report, reply_markup=get_default_keyboard())
            self._executor.submit(_async_acc_report)

        elif cmd == "/setcookie":
            self._handle_setcookie(parts)

        elif cmd == "/veo":
            arg = parts[1].lower() if len(parts) > 1 else ""
            if arg in ("on", "bật", "1", "true"):
                set_veo_enabled(True)
                self.notifier.send_message(
                    "🎬 <b>VEO = BẬT</b>\nKey Scene sẽ gọi Veo qua tài khoản Google Pro (cần cookie sống).\n"
                    "⚠️ Cần <code>/restart</code> hoặc khởi động lại worker để keep-alive Google chạy."
                )
            elif arg in ("off", "tắt", "0", "false"):
                set_veo_enabled(False)
                self.notifier.send_message(
                    "🎨 <b>VEO = TẮT</b>\nToàn bộ scene render bằng Ken Burns (ảnh + camera motion), "
                    "không đụng quota Google và không cần cookie.\nDây chuyền đang chạy sẽ dùng chế độ này ở cảnh kế tiếp."
                )
            else:
                self.notifier.send_message(
                    f"ℹ️ Veo đang <b>{'BẬT' if is_veo_enabled() else 'TẮT'}</b>.\n"
                    f"Dùng: <code>/veo on</code> hoặc <code>/veo off</code>"
                )

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
            duration = None
            for p in parts[1:]:
                p_lower = p.lower()
                if p_lower in ("ai", "auto", "custom"):
                    submode = p_lower
                elif p_lower.startswith("duration="):
                    try:
                        val = p_lower.split("=")[1].strip()
                        if val != "full":
                            duration = float(val)
                    except ValueError:
                        pass

            # Atomic creation and duplicate check (P1.9)
            job_id = self.jq.create_job_if_no_active("remake", url, target_duration=duration or 180.0)
            if not job_id:
                self.notifier.send_message(
                    "⚠️ URL này đang được xử lý trong một Job khác. Vui lòng đợi hoàn tất hoặc dùng <code>/status</code>."
                )
                return

            if submode in ("ai", "auto"):
                if self.on_remake_handler:
                    kwargs = {"job_id": job_id}
                    if duration is not None:
                        kwargs["target_duration"] = duration
                    self._executor.submit(self.on_remake_handler, url, **kwargs)
            else:
                # Default Remake Flow (User request: send .srt and .txt files to Telegram for remixing/xào chẻ)
                self.jq.transition_job(job_id, to_state="AWAITING_CUSTOM_SCRIPT")
                self.notifier.send_message(
                    f"🎬 <b>[MODE 2: REMAKE AI] Nhận job: <code>{job_id}</code></b>\n"
                    f"🔗 Link: <code>{url}</code>\n\n"
                    f"⏳ <i>Đang bóc tách kịch bản gốc và phụ đề từ YouTube...</i>\n"
                    f"<i>Hệ thống sẽ gửi file <code>.srt</code> và <code>.txt</code> cho bạn ngay sau đây để bạn tải về 'xào chẻ'!</i>"
                )
                self._executor.submit(self._async_extract_and_send_script, url, job_id)

        elif cmd == "/remake_ai":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job hoặc link YouTube: <code>/remake_ai &lt;job_id_hoặc_url&gt; [duration=...]</code>")
                return
            target = parts[1].strip()
            duration = None
            for p in parts[2:]:
                p_lower = p.lower()
                if p_lower.startswith("duration="):
                    try:
                        val = p_lower.split("=")[1].strip()
                        if val != "full":
                            duration = float(val)
                    except ValueError:
                        pass

            from pipeline.youtube import extract_youtube_video_id
            if extract_youtube_video_id(target):
                job_id = self.jq.create_job_if_no_active("remake", target, target_duration=duration or 180.0)
                if not job_id:
                    self.notifier.send_message("⚠️ URL này đang được xử lý trong một Job khác.")
                    return
                url = target
            else:
                job_id = target
                job = self.jq.get_job(job_id)
                if not job:
                    self.notifier.send_message(f"❌ Không tìm thấy Job {job_id}.")
                    return
                url = job["url"]

            if self.on_remake_handler:
                kwargs = {"job_id": job_id}
                if duration is not None:
                    kwargs["target_duration"] = duration
                self._executor.submit(self.on_remake_handler, url, **kwargs)

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


        elif cmd == "/gates":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job: <code>/gates &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            job = self.jq.get_job(job_id)
            if not job:
                self.notifier.send_message(f"❌ Không tìm thấy Job <code>{job_id}</code>.")
                return
            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            from services.approval_state_machine import ApprovalStateMachine
            asm = ApprovalStateMachine(p_dir, job_id)
            self.notifier.send_message(asm.format_telegram_status())

        elif cmd == "/feedback":
            if len(parts) < 3:
                self.notifier.send_message(
                    "⚠️ Cú pháp: <code>/feedback &lt;job_id&gt; &lt;điểm 0-10&gt; [ghi chú]</code>\n"
                    "Ví dụ: <code>/feedback abc123 8</code> hoặc <code>/feedback abc123 4 hook yếu, góc kể hơi khô</code>"
                )
                return
            job_id = parts[1]
            try:
                rating = float(parts[2])
            except ValueError:
                self.notifier.send_message("⚠️ Điểm phải là số trong khoảng 0-10.")
                return
            note = " ".join(parts[3:]).strip()
            job = self.jq.get_job(job_id)
            p_dir = str(Path(job.get("project_dir") or f"data/automated_runs/{job_id}")) if job else ""
            angle = ""
            if p_dir:
                try:
                    algo = json.loads((Path(p_dir) / "algorithm_result.json").read_text(encoding="utf-8"))
                    angle = str(algo.get("chosen_angle", ""))
                except Exception:
                    angle = ""
            from services.feedback_store import add_feedback
            try:
                rec = add_feedback(job_id, rating, note=note, angle=angle, project_dir=p_dir)
            except ValueError as e:
                self.notifier.send_message(f"⚠️ {e}")
                return
            self.notifier.send_message(
                f"📝 <b>[RETENTION FEEDBACK ĐÃ LƯU]:</b> Job <code>{job_id}</code>\n"
                f"• Điểm: <b>{rec['rating']:.1f}/10</b>\n"
                f"• Góc kể: <code>{rec['angle'] or 'unknown'}</code>\n"
                f"• Ghi chú: {rec['note'] or '(không)'}\n"
                f"Bài học này sẽ được fold vào các lần sinh kịch bản tiếp theo."
            )

        elif cmd == "/approve_script":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job: <code>/approve_script &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            job = self.jq.get_job(job_id)
            if not job:
                self.notifier.send_message(f"❌ Không tìm thấy Job <code>{job_id}</code>.")
                return
            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            from services.approval_state_machine import ApprovalStateMachine
            asm = ApprovalStateMachine(p_dir, job_id)
            ok, errs = asm.approve_script(approver="telegram")
            if ok:
                self.notifier.send_message(
                    f"✅ <b>[GATE 1 - KỊCH BẢN ĐÃ DUYỆT]:</b> Job <code>{job_id}</code> (v{asm.script_version})!\n"
                    f"🚀 Đang tiến hành tạo Audio VI/EN và Character Sheets song song...\n"
                    f"👉 Gõ <code>/gates {job_id}</code> để theo dõi tiến độ."
                )
                if self.factory:
                    self._executor.submit(self.factory.generate_gate2_and_gate3_artifacts, job_id, p_dir)
            else:
                self.notifier.send_message(f"⚠️ <b>[GATE 1 LỖI]:</b>\n" + "\n".join(f"• {e}" for e in errs))

        elif cmd == "/reject_script":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job: <code>/reject_script &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            job = self.jq.get_job(job_id)
            if not job or job.get("status") not in ("WAITING_APPROVAL", "QUEUED"):
                self.notifier.send_message(f"⚠️ Không thể từ chối kịch bản: Job <code>{job_id}</code> không tồn tại hoặc không ở trạng thái chờ duyệt.")
                return
            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            from services.approval_state_machine import ApprovalStateMachine
            asm = ApprovalStateMachine(p_dir, job_id)
            asm.reject_script(reason="Rejected via Telegram", approver="telegram")
            self.notifier.send_message(f"🚫 <b>[GATE 1]:</b> Đã từ chối Kịch bản Job <code>{job_id}</code>.")

        elif cmd in ("/approve_audio_vi", "/approve_audio_en"):
            if len(parts) < 2:
                self.notifier.send_message(f"⚠️ Vui lòng chỉ định mã Job: <code>{cmd} &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            job = self.jq.get_job(job_id)
            if not job:
                self.notifier.send_message(f"❌ Không tìm thấy Job <code>{job_id}</code>.")
                return
            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            lang = "vi" if "vi" in cmd else "en"
            from services.approval_state_machine import ApprovalStateMachine
            asm = ApprovalStateMachine(p_dir, job_id)
            ok, errs = asm.approve_audio(language=lang, approver="telegram")
            if ok:
                status_note = "🎉 Cả hai bản VI và EN đã sẵn sàng!" if asm.is_audio_gate_passed() else "⏳ Còn chờ bản ngôn ngữ còn lại."
                self.notifier.send_message(
                    f"🎙 <b>[GATE 2 - AUDIO {lang.upper()} ĐÃ DUYỆT]:</b> Job <code>{job_id}</code>!\n"
                    f"{status_note}\n"
                    f"👉 Gõ <code>/gates {job_id}</code> để kiểm tra tổng thể."
                )
            else:
                self.notifier.send_message(f"⚠️ <b>[GATE 2 LỖI AUDIO {lang.upper()}]:</b>\n" + "\n".join(f"• {e}" for e in errs))

        elif cmd in ("/reject_audio_vi", "/reject_audio_en"):
            if len(parts) < 2:
                self.notifier.send_message(f"⚠️ Vui lòng chỉ định mã Job: <code>{cmd} &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            job = self.jq.get_job(job_id)
            if not job or job.get("status") not in ("WAITING_APPROVAL", "QUEUED"):
                self.notifier.send_message(f"⚠️ Không thể từ chối audio: Job <code>{job_id}</code> không tồn tại hoặc không ở trạng thái chờ duyệt.")
                return
            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            lang = "vi" if "vi" in cmd else "en"
            from services.approval_state_machine import ApprovalStateMachine
            asm = ApprovalStateMachine(p_dir, job_id)
            asm.reject_audio(language=lang, reason="Rejected via Telegram", approver="telegram")
            self.notifier.send_message(f"🚫 <b>[GATE 2]:</b> Đã từ chối Audio {lang.upper()} cho Job <code>{job_id}</code>.")

        elif cmd in ("/approve_character", "/approve_char"):
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Cú pháp: <code>/approve_character &lt;job_id&gt; [char_id|all]</code>")
                return
            job_id = parts[1]
            target_char = parts[2].strip() if len(parts) > 2 else "all"
            job = self.jq.get_job(job_id)
            if not job:
                self.notifier.send_message(f"❌ Không tìm thấy Job <code>{job_id}</code>.")
                return
            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            from services.approval_state_machine import ApprovalStateMachine
            asm = ApprovalStateMachine(p_dir, job_id)
            if target_char.lower() == "all":
                ok, errs = asm.approve_all_characters(approver="telegram")
            else:
                ok, errs = asm.approve_character(target_char, approver="telegram")
            if ok:
                self.notifier.send_message(f"👤 <b>[GATE 3 - CHARACTER ĐÃ DUYỆT]:</b> Job <code>{job_id}</code> ({target_char})!")
            else:
                self.notifier.send_message(f"⚠️ <b>[GATE 3 LỖI NHÂN VẬT]:</b>\n" + "\n".join(f"• {e}" for e in errs))

        elif cmd in ("/reject_character", "/reject_char"):
            if len(parts) < 3:
                self.notifier.send_message("⚠️ Cú pháp: <code>/reject_character &lt;job_id&gt; &lt;char_id&gt; [reason]</code>")
                return
            job_id = parts[1]
            char_id = parts[2]
            reason = " ".join(parts[3:]) if len(parts) > 3 else "Rejected via Telegram"
            job = self.jq.get_job(job_id)
            if not job or job.get("status") not in ("WAITING_APPROVAL", "QUEUED"):
                self.notifier.send_message(f"⚠️ Không thể từ chối nhân vật: Job <code>{job_id}</code> không ở trạng thái chờ duyệt.")
                return
            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            from services.approval_state_machine import ApprovalStateMachine
            asm = ApprovalStateMachine(p_dir, job_id)
            asm.reject_character(char_id, reason=reason, approver="telegram")
            self.notifier.send_message(f"🚫 <b>[GATE 3]:</b> Đã từ chối nhân vật <code>{char_id}</code> cho Job <code>{job_id}</code>.")

        elif cmd == "/replace_character_ref":
            if len(parts) < 4:
                self.notifier.send_message("⚠️ Cú pháp: <code>/replace_character_ref &lt;job_id&gt; &lt;char_id&gt; &lt;file_path&gt;</code>")
                return
            job_id = parts[1]
            char_id = parts[2]
            ref_path = Path(parts[3])
            job = self.jq.get_job(job_id)
            if not job or not ref_path.exists():
                self.notifier.send_message("❌ Job hoặc file ảnh tham chiếu không tồn tại.")
                return
            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            from services.approval_state_machine import ApprovalStateMachine
            asm = ApprovalStateMachine(p_dir, job_id)
            try:
                asm.set_character_reference(char_id, ref_path)
                self.notifier.send_message(f"🔄 <b>[GATE 3]:</b> Đã cập nhật ảnh tham chiếu mới cho <code>{char_id}</code>. Trạng thái đã reset về chờ duyệt.")
            except Exception as e:
                self.notifier.send_message(f"❌ Lỗi: {e}")

        elif cmd == "/edit_beat":
            if len(parts) < 4:
                self.notifier.send_message("⚠️ Cú pháp: <code>/edit_beat &lt;job_id&gt; &lt;beat_id&gt; &lt;text_vi&gt; [| &lt;text_en&gt;]</code>")
                return
            job_id = parts[1]
            try:
                beat_id = int(parts[2])
            except ValueError:
                self.notifier.send_message("❌ Beat ID phải là số nguyên.")
                return
            content = " ".join(parts[3:])
            if "|" in content:
                t_vi, t_en = content.split("|", 1)
                text_vi = t_vi.strip()
                text_en = t_en.strip()
            else:
                text_vi = content.strip()
                text_en = None
            job = self.jq.get_job(job_id)
            if not job:
                self.notifier.send_message(f"❌ Không tìm thấy Job <code>{job_id}</code>.")
                return
            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            from services.approval_state_machine import ApprovalStateMachine
            asm = ApprovalStateMachine(p_dir, job_id)
            try:
                asm.edit_beat(beat_id, new_text_vi=text_vi, new_text_en=text_en)
                self.notifier.send_message(
                    f"✏️ <b>[GATE 1]:</b> Đã cập nhật beat {beat_id} cho Job <code>{job_id}</code> (v{asm.script_version}).\n"
                    f"Gate 1, Audio và Video Readiness đã được reset theo quy tắc fail-closed."
                )
            except Exception as e:
                self.notifier.send_message(f"❌ Lỗi khi sửa beat: {e}")

        elif cmd in ("/approve_video", "/approve_video_generation"):
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Cú pháp: <code>/approve_video &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            job = self.jq.get_job(job_id)
            if not job:
                self.notifier.send_message(f"❌ Không tìm thấy Job <code>{job_id}</code>.")
                return
            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            from services.approval_state_machine import ApprovalStateMachine
            asm = ApprovalStateMachine(p_dir, job_id)
            ok, errs = asm.approve_video_generation(approver="telegram")
            if ok:
                transitioned = self.jq.transition_job(job_id, to_state="APPROVED", from_states=("WAITING_APPROVAL",))
                if transitioned:
                    self.notifier.send_message(
                        f"🚀 <b>[GATE 4 - VIDEO GENERATION ĐÃ KHÓA & PHÊ DUYỆT]:</b> Job <code>{job_id}</code>!\n"
                        f"🔒 Manifest: <code>{asm.active_manifest.manifest_id if asm.active_manifest else 'locked'}</code>\n"
                        f"Dây chuyền worker bắt đầu sinh shot hình ảnh/video..."
                    )
                    qa_report = asm.get_qa_report()
                    if qa_report:
                        self.notifier.send_message(qa_report)
                else:
                    self.notifier.send_message(f"⚠️ Không thể duyệt Job <code>{job_id}</code> (Job không ở trạng thái WAITING_APPROVAL hoặc đã kết thúc).")
            else:
                self.notifier.send_message(f"⚠️ <b>[GATE 4 CHƯA ĐỦ ĐIỀU KIỆN SẴN SÀNG]:</b>\n" + "\n".join(f"• {e}" for e in errs))

        elif cmd == "/approve":
            if len(parts) < 2:
                self.notifier.send_message("⚠️ Vui lòng chỉ định mã Job: <code>/approve &lt;job_id&gt;</code>")
                return
            job_id = parts[1]
            job = self.jq.get_job(job_id)
            if not job or job.get("status") != "WAITING_APPROVAL":
                self.notifier.send_message(f"⚠️ Không thể duyệt Job <code>{job_id}</code> (Job không ở trạng thái CHỜ DUYỆT hoặc không tồn tại).")
                return

            p_dir = Path(job.get("project_dir") or f"data/automated_runs/{job_id}")
            state_file = p_dir / "approval_state.json"

            # 4-Gate Smart Routing
            if state_file.exists():
                from services.approval_state_machine import ApprovalStateMachine
                asm = ApprovalStateMachine(p_dir, job_id)

                # Gate 1 pending?
                if not asm.is_script_gate_passed():
                    self.notifier.send_message(
                        f"⏳ <b>[GATE 1 ĐANG CHỜ]:</b> Kịch bản chưa được phê duyệt.\n"
                        f"👉 Vui lòng duyệt kịch bản trước: <code>/approve_script {job_id}</code>\n"
                        f"Hoặc gõ <code>/gates {job_id}</code> để xem tiến độ."
                    )
                    return

                # Gate 2 pending?
                if not asm.is_audio_gate_passed():
                    self.notifier.send_message(
                        f"⏳ <b>[GATE 2 ĐANG CHỜ]:</b> Kịch bản đã duyệt, nhưng chưa duyệt đủ cả 2 bản Audio VI và EN.\n"
                        f"• Duyệt tiếng Việt: <code>/approve_audio_vi {job_id}</code>\n"
                        f"• Duyệt tiếng Anh: <code>/approve_audio_en {job_id}</code>\n"
                        f"👉 Gõ <code>/gates {job_id}</code> để xem tình trạng file."
                    )
                    return

                # Gate 3 pending?
                if not asm.is_character_gate_passed():
                    self.notifier.send_message(
                        f"⏳ <b>[GATE 3 ĐANG CHỜ]:</b> Còn ảnh nhân vật bắt buộc chưa được duyệt.\n"
                        f"👉 Gõ <code>/approve_character {job_id} all</code> để duyệt toàn bộ nhân vật."
                    )
                    return

                # Gate 4 ready check
                ok, errs = asm.approve_video_generation(approver="telegram")
                if not ok:
                    self.notifier.send_message(f"⚠️ <b>[GATE 4 CHƯA THỂ BẮT ĐẦU VIDEO]:</b>\n" + "\n".join(f"• {e}" for e in errs))
                    return

                # All 4 gates passed -> Transition job
                transitioned = self.jq.transition_job(job_id, to_state="APPROVED", from_states=("WAITING_APPROVAL",))
                if transitioned:
                    self.notifier.send_message(
                        f"✅ <b>[4 GATES HOÀN TẤT - BẮT ĐẦU GENERATE VIDEO] Job: {job_id}!</b>\n"
                        f"🚀 Manifest đã khóa an toàn. Dây chuyền worker đang tiến hành sinh và ráp các cảnh tự động..."
                    )
                    qa_report = asm.get_qa_report()
                    if qa_report:
                        self.notifier.send_message(qa_report)
                else:
                    self.notifier.send_message(f"⚠️ Không thể duyệt Job <code>{job_id}</code>.")
                return

            # Fail-closed check: Mode 2 / Remake jobs CANNOT bypass 4-gate approval
            mode = str(job.get("mode", "")).lower()
            if mode in ("mode2", "remake", "ai_genesis") or "remake" in job_id:
                self.notifier.send_message(
                    f"❌ <b>[LỖI 4-GATE]:</b> Job Mode 2 <code>{job_id}</code> thiếu file approval_state.json.\n"
                    "Không thể duyệt theo quy tắc fail-closed."
                )
                return

            # Legacy Flow (Only for Reup jobs without 4-gate state machine)
            ok = self.jq.approve_job(job_id, enforce_gates=False)
            if ok:
                self.notifier.send_message(f"✅ <b>Đã duyệt Job Reup: {job_id}!</b>\n🚀 Đang kích hoạt render video hoàn chỉnh...")
                if self.on_approve_handler:
                    self._executor.submit(self.on_approve_handler, job_id)
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
