"""CLI Tool for Managing Google Gemini Pro Accounts & Cookies.

Allows:
- Adding accounts securely without exposing cookies in shell history (via getpass or file)
- Checking if cookies are still valid / expired / requiring verification
- Inspecting account tier (PRO vs FREE) and remaining 5-hour credits
- Listing, disabling, and enabling accounts in the pool
"""

import argparse
import getpass
import json
import os
from pathlib import Path
import sys

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from services.gemini_webapi_provider import GeminiWebApiProvider, atomic_save_session, extract_cookies_from_file

SESSIONS_DIR = Path("sessions")


def get_account_path(acc_id: int) -> Path:
    return SESSIONS_DIR / f"acc_{acc_id:02d}.json"


def list_accounts():
    """Lists all accounts in sessions directory."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    all_files = sorted(list(SESSIONS_DIR.glob("*.json")) + list(SESSIONS_DIR.glob("*.disabled")))

    print("\n" + "=" * 65)
    print("📋 DANH SÁCH TÀI KHOẢN TRONG THƯ MỤC SESSIONS")
    print("=" * 65)

    if not all_files:
        print("Chưa có tài khoản nào được cấu hình.")
        print("Sử dụng: python scripts/manage_accounts.py add --acc 1")
        return

    print(f"{'File':<25} {'Trạng thái':<15} {'Kích thước':<10}")
    print("-" * 65)
    for p in all_files:
        status = "🟢 Enabled" if p.suffix == ".json" else "🔴 Disabled"
        size = f"{p.stat().st_size} B"
        print(f"{p.name:<25} {status:<15} {size:<10}")
    print("=" * 65)


def check_account(account_file: Path, provider: GeminiWebApiProvider):
    """Validates a single account session file."""
    print(f"\n🔍 Đang kiểm tra tài khoản: {account_file.name}...")
    psid, psidts = extract_cookies_from_file(account_file)
    if not psid:
        print(f"❌ Không tìm thấy __Secure-1PSID trong file {account_file.name}")
        return

    res = provider.validate_session(account_file)
    if res.valid:
        tier_icon = "⭐" if res.tier == "PRO" else "⚠️"
        print(f"✅ COOKIE HỢP LỆ! {tier_icon} Gói: {res.tier}")
        if res.credits_remaining is not None:
            print(f"📊 Credits còn lại (5h): {res.credits_remaining}")
        if res.reset_at:
            print(f"⏱ Thời điểm reset quota: {res.reset_at}")
        if res.tier == "FREE":
            print("⚠️ CẢNH BÁO: Tài khoản này là FREE, Google không cho phép sinh video Veo!")
    else:
        print(f"❌ COOKIE HẾT HẠN HOẶC KHÔNG HỢP LỆ!")
        print(f"Lý do ({res.error_kind}): {res.message}")
        if res.error_kind and res.error_kind.value == "VERIFY":
            print("🚨 YÊU CẦU XÁC MINH: Đăng nhập lại trình duyệt để vượt qua Captcha/Verification!")


def check_all_accounts():
    """Checks validity of all enabled accounts in sessions/."""
    provider = GeminiWebApiProvider()
    json_files = sorted(list(SESSIONS_DIR.glob("acc_*.json")))
    if not json_files:
        # Check test_account.json
        test_file = SESSIONS_DIR / "test_account.json"
        if test_file.exists():
            json_files = [test_file]

    if not json_files:
        print("❌ Không tìm thấy file tài khoản nào để kiểm tra.")
        return

    for f in json_files:
        check_account(f, provider)


def add_account(acc_id: int, from_file: str = None):
    """Adds a new account session, validating before saving."""
    target_path = get_account_path(acc_id)
    psid = None
    psidts = None

    if from_file:
        src = Path(from_file)
        if not src.exists():
            print(f"❌ Không tìm thấy file nguồn: {src}")
            return
        psid, psidts = extract_cookies_from_file(src)
    else:
        print(f"\n--- NHẬP COOKIE CHO TÀI KHOẢN ACC {acc_id:02d} (Ẩn khi gõ) ---")
        psid = getpass.getpass("Enter __Secure-1PSID: ").strip()
        psidts = getpass.getpass("Enter __Secure-1PSIDTS: ").strip()

    if not psid:
        print("❌ Thiếu __Secure-1PSID!")
        return

    print("⏳ Đang xác thực cookie với Google server...")
    atomic_save_session(target_path, psid, psidts or "")

    provider = GeminiWebApiProvider()
    res = provider.validate_session(target_path)
    if res.valid:
        print(f"🎉 THÀNH CÔNG! Đã lưu tài khoản {target_path.name}")
        print(f"Gói: {res.tier} | Credits: {res.credits_remaining} | Reset: {res.reset_at}")
    else:
        print(f"⚠️ Cảnh báo: Cookie lưu thất bại hoặc không hợp lệ: {res.message}")


def disable_account(acc_id: int):
    target = get_account_path(acc_id)
    if target.exists():
        disabled = target.with_suffix(".disabled")
        target.rename(disabled)
        print(f"✔ Đã vô hiệu hóa: {target.name} -> {disabled.name}")
    else:
        print(f"❌ Không tìm thấy: {target.name}")


def enable_account(acc_id: int):
    disabled = SESSIONS_DIR / f"acc_{acc_id:02d}.disabled"
    if disabled.exists():
        target = get_account_path(acc_id)
        disabled.rename(target)
        print(f"✔ Đã kích hoạt lại: {disabled.name} -> {target.name}")
    else:
        print(f"❌ Không tìm thấy: {disabled.name}")


def main():
    parser = argparse.ArgumentParser(description="Google Gemini Account & Cookie Management")
    subparsers = parser.add_subparsers(dest="command")

    # list
    subparsers.add_parser("list", help="List all accounts")

    # check
    check_p = subparsers.add_parser("check", help="Check cookie validity")
    check_p.add_argument("--acc", type=int, help="Account ID number (e.g. 1)")
    check_p.add_argument("--all", action="store_true", help="Check all configured accounts")

    # add
    add_p = subparsers.add_parser("add", help="Add or update an account cookie")
    add_p.add_argument("--acc", type=int, required=True, help="Account ID number (1-10)")
    add_p.add_argument("--from-file", help="Path to JSON file containing cookies")

    # disable
    dis_p = subparsers.add_parser("disable", help="Disable an account")
    dis_p.add_argument("--acc", type=int, required=True, help="Account ID number")

    # enable
    en_p = subparsers.add_parser("enable", help="Enable an account")
    en_p.add_argument("--acc", type=int, required=True, help="Account ID number")

    args = parser.parse_args()

    if args.command == "list":
        list_accounts()
    elif args.command == "check":
        if args.acc:
            target = get_account_path(args.acc)
            if not target.exists():
                print(f"❌ Không tìm thấy file tài khoản: {target.name}")
            else:
                provider = GeminiWebApiProvider()
                check_account(target, provider)
        else:
            check_all_accounts()
    elif args.command == "add":
        add_account(args.acc, args.from_file)
    elif args.command == "disable":
        disable_account(args.acc)
    elif args.command == "enable":
        enable_account(args.acc)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
