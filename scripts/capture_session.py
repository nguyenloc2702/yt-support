"""Session Capturing Utility for Google Gemini Pro Accounts.

Usage:
    python scripts/capture_session.py --acc 1
    python scripts/capture_session.py --acc 2
    ... up to --acc 10

This script launches a visible browser window, navigates to Gemini,
allows the user to log in and satisfy 2FA/Passkeys, then extracts and saves
the storage state (cookies + tokens) to sessions/acc_XX.json for headless VPS worker.
"""

import argparse
import json
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

SESSION_DIR = Path("sessions")
SESSION_DIR.mkdir(parents=True, exist_ok=True)

TARGET_URL = "https://gemini.google.com/app"


def capture_session(acc_index: int):
    session_file = SESSION_DIR / f"acc_{acc_index:02d}.json"
    user_data_dir = SESSION_DIR / f"chrome_profile_acc_{acc_index:02d}"

    print(f"\n==================================================")
    print(f"👉 CHUẨN BỊ LẤY SESSION CHO TÀI KHOẢN PRO #{acc_index}")
    print(f"👉 File lưu đích: {session_file}")
    print(f"==================================================")

    with sync_playwright() as p:
        # Launch persistent context to look authentic to Google
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,
            viewport={"width": 1280, "height": 800},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(TARGET_URL)

        print("\n[!] Trình duyệt đã mở. Vui lòng:")
        print("  1. Đăng nhập tài khoản Google Pro trên cửa sổ trình duyệt vừa hiện lên.")
        print("  2. Vượt qua 2FA / Mã OTP điện thoại (nếu có).")
        print("  3. Đảm bảo bạn đã vào được giao diện chính của Gemini (có khung chat).")
        print("--------------------------------------------------")
        input(">> Sau khi đã đăng nhập thành công vào Gemini, NHẤN PHÍM ENTER để lưu session: ")

        # Save storage state
        context.storage_state(path=str(session_file))
        context.close()

    if session_file.exists():
        data = json.loads(session_file.read_text(encoding="utf-8"))
        cookies_count = len(data.get("cookies", []))
        print(f"\n[OK] Đã lưu thành công {cookies_count} cookies vào: {session_file}")
        print(f"[OK] Hoàn tất tài khoản #{acc_index}!")
    else:
        print(f"\n[LỖI] Không thể tạo file {session_file}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Capture session for Google Pro Account")
    parser.add_argument("--acc", type=int, required=True, help="Account index (1 to 10)")
    args = parser.parse_args()

    if not (1 <= args.acc <= 50):
        print("Lỗi: --acc phải nằm trong khoảng từ 1 đến 50.")
        sys.exit(1)

    capture_session(args.acc)


if __name__ == "__main__":
    main()
