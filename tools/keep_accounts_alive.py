"""One-shot Google session keep-alive for the account pool.

Sends one cheap message to Gemini per registered account, which makes Google
rotate each account's rolling __Secure-1PSIDTS; the fresh token is written
back to sessions/acc_XX.json atomically. Run this on a schedule (the 30-minute
cron automation) and the pasted cookies never age out:

    python -I tools/keep_accounts_alive.py

Exit code 0 when every account refreshed (or has no session yet), 1 when any
account failed — so the cron run can surface the failure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.account_pool import AccountPoolManager


def main() -> int:
    pool = AccountPoolManager()
    results = pool.ping_and_refresh_all_accounts(pacing_delay=5.0)
    if not results:
        print("No accounts pinged (kill switch active or pool empty).")
        return 1
    failures = [acc for acc, ok in results.items() if not ok]
    for acc, ok in results.items():
        age = pool.session_age_hours(acc)
        print(f"{acc}: {'OK' if ok else 'FAILED'} (session age {age and round(age, 1) or '?'}h)")
    if failures:
        print(f"FAILED accounts: {', '.join(failures)} — paste fresh cookies.")
        return 1
    print("All accounts refreshed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
