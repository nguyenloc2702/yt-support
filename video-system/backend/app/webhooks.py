"""Webhook dispatch — HMAC-SHA256 signature + replay-safe event id (Appendix E)."""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.request

from . import new_id, now
from .config import webhook_secret
from .store import Store

BACKOFF = [60, 300, 1800, 7200]
GIVE_UP_AFTER = 24 * 3600


def _assert_public_webhook_url(url: str) -> None:
    """SSRF guard for outbound webhooks (§7.3): block private/loopback targets."""
    import ipaddress as _ip
    import socket as _socket
    import urllib.parse as _up
    host = _up.urlsplit(url).hostname or ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        return  # local receivers are legitimate in dev
    for info in _socket.getaddrinfo(host, None):
        ip = _ip.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"webhook target resolves to private IP: {host}")

EVENTS = {
    "item.analyzed", "item.awaiting_review", "shot.held", "shot.appended",
    "item.ready", "item.failed",
    "youtube.channel.connected", "youtube.channel.disconnected",
    "youtube.upload.completed", "youtube.upload.failed",
}


def build_payload(event_row: dict, extra: dict | None = None) -> bytes:
    payload = {
        "event": event_row["type"],
        "event_id": event_row["id"],
        "item_id": event_row["item_id"],
        "shot_id": event_row["shot_id"],
        "version": {},
        "data": json.loads(event_row["payload_json"] or "{}") if event_row.get("payload_json") else {},
        "timestamp": event_row["at"],
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class WebhookService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def register(self, url: str, secret: str | None = None, events: list[str] | None = None,
                 item_id: str | None = None) -> dict:
        """Secret must NOT be the URL (audit P0): use the provided secret or
        fall back to the global VIDEO_WEBHOOK_SECRET; never the callback URL."""
        from .config import webhook_secret as _global_secret
        sec = secret or _global_secret()
        if not sec:
            raise ValueError("webhook secret required (pass secret or set VIDEO_WEBHOOK_SECRET)")
        wid = new_id("wh")
        self.store.execute(
            "INSERT INTO webhooks (id,url,secret,events,item_id,active,created_at) "
            "VALUES (?,?,?,?,?,1,?)",
            (wid, url, sec, json.dumps(events or sorted(EVENTS)), item_id, now()))
        return self.store.query_one("SELECT * FROM webhooks WHERE id=?", (wid,))

    def dispatch_event(self, event_row: dict, synchronous: bool = False) -> list[str]:
        secret_global = webhook_secret()
        hooks = self.store.query(
            "SELECT * FROM webhooks WHERE active=1 AND (item_id IS NULL OR item_id=?)",
            (event_row["item_id"],))
        delivered = []
        for h in hooks:
            secret = h["secret"] or secret_global
            if not secret:
                continue
            subs = set(json.loads(h["events"] or "[]"))
            if subs and event_row["type"] not in subs:
                continue
            body = build_payload(event_row)
            did = new_id("wht")
            self.store.execute(
                "INSERT INTO webhook_deliveries (id,webhook_id,event_id,attempts,"
                "next_attempt_at,created_at) VALUES (?,?,?,0,?,?)",
                (did, h["id"], event_row["id"], now(), now()))
            if synchronous:
                self._attempt(did, h["url"], secret, body)
            else:
                threading.Thread(target=self._attempt, args=(did, h["url"], secret, body),
                                 daemon=True).start()
            delivered.append(did)
        return delivered

    def _attempt(self, delivery_id: str, url: str, secret: str, body: bytes) -> None:
        try:
            _assert_public_webhook_url(url)
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "X-Signature": sign(secret, body),
                         "X-Event-Id": json.loads(body)["event_id"]})
            with urllib.request.urlopen(req, timeout=10) as r:
                status = r.status
        except Exception as e:  # noqa: BLE001 — deliveries must never crash the app
            status = "error:delivery_failed"  # never store raw exception (may leak URL)
        ok = status == 200
        d = self.store.query_one(
            "SELECT attempts FROM webhook_deliveries WHERE id=?", (delivery_id,))
        attempts = (d["attempts"] if d else 0) + 1
        if ok or attempts > len(BACKOFF) or (attempts * BACKOFF[0]) > GIVE_UP_AFTER:
            self.store.execute(
                "UPDATE webhook_deliveries SET attempts=?, last_status=?, "
                "next_attempt_at=NULL WHERE id=?",
                (attempts, str(status), delivery_id))
            return
        self.store.execute(
            "UPDATE webhook_deliveries SET attempts=?, last_status=?, next_attempt_at=? "
            "WHERE id=?", (attempts, str(status), now() + BACKOFF[attempts - 1],
                           delivery_id))
