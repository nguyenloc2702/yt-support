"""Muse agent tối giản — đăng nhập bằng mã 6 số, xin việc từ board.

Mục đích: chứng minh 4 danh tính khác nhau cùng chạy được trên một server, và
mỗi danh tính nhận việc đúng phần của mình. Không gen video, không upload —
đây là smoke test cho tầng danh tính + dispatch.

    python tools/muse_agent.py --base https://vidfac.gacdero.vn --code 997222
    python tools/muse_agent.py --base http://127.0.0.1:8099 --code 997222 --once

Mã 6 số chỉ dùng một lần để đổi token; token mới là thứ đi kèm mọi request.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


class Agent:
    def __init__(self, base: str, code: str, label: str = "") -> None:
        self.base = base.rstrip("/")
        self.code = code
        self.label = label or code
        self.token: str | None = None
        self.identity = "?"
        self.scope = "?"
        self.client = httpx.Client(base_url=self.base, timeout=20.0)

    def login(self) -> bool:
        r = self.client.post("/api/v1/auth/login", json={"code": self.code})
        if r.status_code != 200:
            print(f"[{self.label}] login FAIL {r.status_code}: {r.text[:120]}")
            return False
        body = r.json()
        self.token = body["token"]
        self.identity = body.get("identity", "?")
        self.scope = body.get("scope", "?")
        self.client.headers["Authorization"] = f"Bearer {self.token}"
        return True

    def pick_item(self) -> str | None:
        r = self.client.get("/api/v1/todos")
        if r.status_code != 200:
            print(f"[{self.label}] GET /todos -> {r.status_code}")
            return None
        body = r.json()
        items = body.get("todos") or body.get("items") if isinstance(body, dict) else body
        for it in items or []:
            iid = it.get("id") or it.get("item_id")
            if iid:
                return iid
        return None

    def next_work(self, item_id: str) -> dict | None:
        r = self.client.get(f"/api/v1/todos/{item_id}/shots/next")
        if r.status_code != 200:
            print(f"[{self.label}] shots/next -> {r.status_code}: {r.text[:120]}")
            return None
        return r.json()

    def run_once(self) -> dict:
        if not self.login():
            return {"label": self.label, "ok": False, "stage": "login"}
        item = self.pick_item()
        if not item:
            return {"label": self.label, "ok": False, "stage": "pick_item",
                    "identity": self.identity, "scope": self.scope}
        work = self.next_work(item)
        shot = (work or {}).get("shot") or {}
        reason = (work or {}).get("reason") or "-"
        return {
            "label": self.label,
            "ok": True,
            "identity": self.identity,
            "scope": self.scope,
            "item": item,
            "reason": reason,
            "shot": shot.get("index"),
            "status": shot.get("status"),
            "tier": shot.get("tier"),
            "has_brief": bool(shot.get("regen_brief")),
        }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Muse agent smoke test")
    p.add_argument("--base", required=True, help="URL API, vd https://vidfac.gacdero.vn")
    p.add_argument("--code", action="append", required=True,
                   help="mã 6 số của agent; lặp lại nhiều lần để chạy nhiều agent")
    p.add_argument("--interval", type=float, default=0.0, help="giây giữa các vòng (0 = chạy 1 vòng)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    agents = [Agent(args.base, c) for c in args.code]
    round_no = 0
    try:
        while True:
            round_no += 1
            results = [a.run_once() for a in agents]
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=1))
            else:
                print(f"--- vòng {round_no} ---")
                for r in results:
                    if not r["ok"]:
                        print(f"  {r['label']:<12} FAIL ở bước {r['stage']}")
                        continue
                    print(
                        f"  {r['label']:<12} {r['identity']:<14} {r['scope']:<8} "
                        f"shot {r['shot']} · {r['reason']:<14} "
                        f"tier={r['tier']} brief={r['has_brief']}"
                    )
            if not args.interval:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
