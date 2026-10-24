"""Telegram Notification Service for Video Automation Pipeline.

Sends progress updates, shot completion alerts, and preview videos directly to Telegram.
Configured via TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional
import requests

logger = logging.getLogger("telegram_notifier")


def get_default_keyboard() -> dict:
    """Returns persistent Telegram reply keyboard with shortcut buttons."""
    return {
        "keyboard": [
            [{"text": "📋 /accounts"}, {"text": "📊 /status"}],
            [{"text": "⏸ /pause"}, {"text": "▶️ /resume"}],
            [{"text": "ℹ️ /help"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


class TelegramNotifier:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.token}" if self.token else ""

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def set_bot_commands(self) -> bool:
        """Registers the bot command autocomplete menu in Telegram."""
        if not self.is_configured:
            return False
        url = f"{self.base_url}/setMyCommands"
        commands = [
            {"command": "accounts", "description": "📋 Quota realtime & trạng thái từng tài khoản"},
            {"command": "status", "description": "📊 Trạng thái hệ thống, hàng đợi & đĩa VPS"},
            {"command": "reup", "description": "✂️ Mode 1: Tóm tắt & cắt dựng từ video gốc"},
            {"command": "remake", "description": "🎬 Mode 2: Sinh 100% video AI Veo mới"},
            {"command": "custom_script", "description": "✍️ Nạp kịch bản tự tạo cho Job"},
            {"command": "voice", "description": "🎙 Chọn giọng đọc tài liệu (Nam Minh/Guy)"},
            {"command": "pause", "description": "⏸ Tạm dừng sinh video AI"},
            {"command": "resume", "description": "▶️ Tiếp tục chạy dây chuyền"},
            {"command": "help", "description": "ℹ️ Hướng dẫn sử dụng chi tiết"},
        ]
        try:
            resp = requests.post(url, json={"commands": commands}, timeout=15)
            if resp.status_code == 200:
                logger.info("Successfully registered Telegram bot command menu.")
                return True
            logger.warning("Telegram setMyCommands error (%d): %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logger.error("Failed to set Telegram bot commands: %s", e)
            return False

    def send_message(self, text: str, parse_mode: str = "HTML", reply_markup: Optional[dict] = None) -> bool:
        """Send a formatted text message to Telegram, automatically chunking if > 4000 chars."""
        if not self.is_configured:
            logger.debug("TelegramNotifier not configured. Skipping message.")
            return False

        # Split message into chunks <= 4000 characters
        if len(text) > 4000:
            chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
            all_ok = True
            for i, chunk in enumerate(chunks):
                # Only attach reply_markup to the final chunk
                markup = reply_markup if i == len(chunks) - 1 else None
                if not self._send_single_message(chunk, parse_mode, reply_markup=markup):
                    all_ok = False
            return all_ok

        return self._send_single_message(text, parse_mode, reply_markup=reply_markup)

    @staticmethod
    def _sanitize_markup(reply_markup: Optional[dict]) -> Optional[dict]:
        """Drop inline buttons Telegram would reject: callback_data is 1-64 bytes.

        One oversized payload fails the whole sendMessage request, so a character
        whose id is a Vietnamese name silently killed the Gate 3 notification.
        """
        if not isinstance(reply_markup, dict) or "inline_keyboard" not in reply_markup:
            return reply_markup
        markup = dict(reply_markup)
        rows = []
        for row in markup.get("inline_keyboard") or []:
            kept = [
                btn for btn in row
                if "callback_data" not in btn
                or 1 <= len(str(btn["callback_data"]).encode("utf-8")) <= 64
            ]
            if len(kept) != len(row):
                logger.warning(
                    "Dropping Telegram button(s) with callback_data outside 1-64 bytes: %s",
                    [btn.get("text") for btn in row if btn not in kept],
                )
            if kept:
                rows.append(kept)
        if not rows:
            return None
        markup["inline_keyboard"] = rows
        return markup

    def _send_single_message(self, text: str, parse_mode: str = "HTML", reply_markup: Optional[dict] = None) -> bool:
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        reply_markup = self._sanitize_markup(reply_markup)
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                return True
            logger.warning("Telegram API error (%d): %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logger.error("Failed to send Telegram message: %s", e)
            return False

    def send_video(self, video_path: str, caption: str = "") -> bool:
        """Upload and send a video clip to Telegram."""
        if not self.is_configured:
            return False

        path = Path(video_path)
        if not path.exists():
            logger.warning("Video file not found for Telegram upload: %s", video_path)
            return False

        # Telegram bot API limit for sendVideo is 50MB
        if path.stat().st_size > 50 * 1024 * 1024:
            logger.info("Video %s exceeds 50MB, sending text notification instead.", path.name)
            return self.send_message(f"📹 <b>Clip hoàn thành:</b> {path.name}\n{caption}")

        url = f"{self.base_url}/sendVideo"
        try:
            with open(path, "rb") as vf:
                files = {"video": vf}
                data = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"}
                resp = requests.post(url, data=data, files=files, timeout=60)
                return resp.status_code == 200
        except Exception as e:
            logger.error("Failed to send Telegram video: %s", e)
            return False

    def send_voice(self, voice_path: str, caption: str = "") -> bool:
        """Send a voice audio sample to Telegram."""
        if not self.is_configured:
            return False
        path = Path(voice_path)
        if not path.exists():
            logger.warning("Voice file not found for Telegram upload: %s", voice_path)
            return False
        url = f"{self.base_url}/sendVoice"
        try:
            with open(path, "rb") as vf:
                files = {"voice": vf}
                data = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"}
                resp = requests.post(url, data=data, files=files, timeout=30)
                return resp.status_code == 200
        except Exception as e:
            logger.error("Failed to send Telegram voice: %s", e)
            return False

    def send_audio(self, audio_path: str, caption: str = "", title: str = "", performer: str = "") -> bool:
        """Send an audio file to Telegram."""
        if not self.is_configured:
            return False
        path = Path(audio_path)
        if not path.exists():
            logger.warning("Audio file not found for Telegram upload: %s", audio_path)
            return False
        url = f"{self.base_url}/sendAudio"
        try:
            with open(path, "rb") as af:
                files = {"audio": af}
                data = {
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "title": title or path.stem,
                    "performer": performer or "Video Voiceover",
                }
                resp = requests.post(url, data=data, files=files, timeout=45)
                return resp.status_code == 200
        except Exception as e:
            logger.error("Failed to send Telegram audio: %s", e)
            return False

    def send_document(self, document_path: str, caption: str = "", reply_markup: Optional[dict] = None) -> bool:
        """Send a document (.txt, .srt, .json, etc.) to Telegram."""
        if not self.is_configured:
            return False
        path = Path(document_path)
        if not path.exists():
            logger.warning("Document file not found for Telegram upload: %s", document_path)
            return False
        url = f"{self.base_url}/sendDocument"
        try:
            with open(path, "rb") as df:
                files = {"document": df}
                data = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"}
                if reply_markup is not None:
                    data["reply_markup"] = json.dumps(reply_markup)
                resp = requests.post(url, data=data, files=files, timeout=45)
                return resp.status_code == 200
        except Exception as e:
            logger.error("Failed to send Telegram document: %s", e)
            return False


    def notify_shot_started(self, shot_idx: int, total_shots: int, shot_id: str, acc_id: str, prompt: str):
        prompt_snippet = (prompt[:140] + "...") if len(prompt) > 140 else prompt
        text = (
            f"🎬 <b>[BẮT ĐẦU TẠO] Cảnh {shot_idx}/{total_shots} ({shot_id})</b>\n"
            f"👤 Tài khoản: <code>{acc_id}</code>\n"
            f"📝 <i>Prompt:</i> {prompt_snippet}"
        )
        self.send_message(text)

    def notify_shot_completed(
        self,
        shot_idx: int,
        total_shots: int,
        shot_id: str,
        acc_id: str,
        duration_sec: float,
        gdrive_url: Optional[str] = None,
        video_path: Optional[str] = None,
    ):
        percent = (shot_idx / total_shots) * 100
        bar = self._render_progress_bar(percent)
        gdrive_info = f"\n☁️ <a href='{gdrive_url}'>Xem trên Google Drive</a>" if gdrive_url else ""

        text = (
            f"✅ <b>[HOÀN THÀNH] Cảnh {shot_idx}/{total_shots} ({shot_id})</b>\n"
            f"📊 Tiến độ: {bar} <b>{percent:.1f}%</b> ({shot_idx}/{total_shots})\n"
            f"👤 Tài khoản: <code>{acc_id}</code> | ⏱ Render: {duration_sec:.1f}s"
            f"{gdrive_info}"
        )

        if video_path and Path(video_path).exists():
            self.send_video(video_path, caption=text)
        else:
            self.send_message(text)

    def notify_shot_failed(self, shot_idx: int, total_shots: int, shot_id: str, acc_id: str, error_msg: str):
        text = (
            f"⚠️ <b>[LỖI TẠO CẢNH] Cảnh {shot_idx}/{total_shots} ({shot_id})</b>\n"
            f"👤 Tài khoản: <code>{acc_id}</code>\n"
            f"❌ <i>Chi tiết lỗi:</i> <code>{error_msg[:300]}</code>\n"
            f"🔄 Hệ thống sẽ tự động chuyển sang tài khoản khác và thử lại."
        )
        self.send_message(text)

    def notify_batch_completed(self, total_completed: int, total_shots: int, drive_folder: str = ""):
        text = (
            f"🎉 <b>HOÀN THÀNH TOÀN BỘ B-ROLL!</b>\n"
            f"Đã tạo thành công <b>{total_completed}/{total_shots}</b> cảnh video B-roll.\n"
            f"📁 Toàn bộ clips đã sẵn sàng tại Google Drive: <code>{drive_folder}</code>\n"
            f"🚀 Bạn có thể chạy script ráp phim tài liệu ngay bây giờ!"
        )
        self.send_message(text)

    @staticmethod
    def _render_progress_bar(percent: float, length: int = 10) -> str:
        filled = int(round(length * percent / 100))
        return "▓" * filled + "░" * (length - filled)


if __name__ == "__main__":
    import sys
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    notifier = TelegramNotifier(token, chat_id)
    if not notifier.is_configured:
        print("Chưa cấu hình TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID.")
        print("Ví dụ test: TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy python services/notifier.py")
        sys.exit(1)

    print("Đang gửi tin nhắn thử nghiệm...")
    ok = notifier.send_message("🚀 <b>Test kết nối Telegram Notifier thành công!</b>\nHệ thống sẵn sàng gửi báo cáo tiến độ.")
    if ok:
        print("[OK] Đã gửi tin nhắn test thành công!")
    else:
        print("[FAIL] Gửi tin nhắn test thất bại. Vui lòng kiểm tra lại token và chat_id.")
