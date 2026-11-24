"""Webhook retry worker — delivery tới hạn phải được gửi lại.

Không bắn HTTP thật: `_attempt` bị monkeypatch để chỉ ghi nhận tham số.
"""
from __future__ import annotations

import json

import pytest

from backend.app.store import SQLiteStore, now
from backend.app.webhooks import WebhookService


@pytest.fixture()
def svc(tmp_path):
    return WebhookService(SQLiteStore(tmp_path / "retry.db"))


def _seed(svc, *, next_attempt_at, event_type="agreement.next_action_changed"):
    """Tạo 1 webhook + 1 event + 1 delivery với hạn gửi cho trước."""
    svc.store.execute(
        "INSERT INTO webhooks (id,url,secret,events,item_id,active,created_at) "
        "VALUES (?,?,?,?,NULL,1,?)",
        ("wh_1", "https://receiver.test/hook", "whsec-test",
         json.dumps([event_type]), now()))
    svc.store.execute(
        "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("evt_1", None, None, event_type, "system",
         json.dumps({"brief_id": "brf_1"}), now()))
    svc.store.execute(
        "INSERT INTO webhook_deliveries (id,webhook_id,event_id,attempts,"
        "next_attempt_at,created_at) VALUES (?,?,?,0,?,?)",
        ("wht_1", "wh_1", "evt_1", next_attempt_at, now()))


def test_retry_pending_attempts_due_delivery(svc, monkeypatch):
    _seed(svc, next_attempt_at=now() - 5)
    calls: list[tuple] = []
    monkeypatch.setattr(svc, "_attempt",
                        lambda did, url, secret, body: calls.append((did, url, secret, body)))

    assert svc.retry_pending() == 1
    assert len(calls) == 1
    did, url, secret, body = calls[0]
    assert (did, url, secret) == ("wht_1", "https://receiver.test/hook", "whsec-test")
    payload = json.loads(body)
    assert payload["event_id"] == "evt_1"
    assert payload["data"] == {"brief_id": "brf_1"}


def test_retry_pending_skips_delivery_not_due(svc, monkeypatch):
    _seed(svc, next_attempt_at=now() + 600)
    calls: list[tuple] = []
    monkeypatch.setattr(svc, "_attempt",
                        lambda *a, **kw: calls.append(a))

    assert svc.retry_pending() == 0
    assert calls == []


def test_retry_pending_ignores_delivery_without_schedule(svc, monkeypatch):
    """next_attempt_at=NULL = đã xong/hết lượt, không retry nữa."""
    _seed(svc, next_attempt_at=None)
    calls: list[tuple] = []
    monkeypatch.setattr(svc, "_attempt",
                        lambda *a, **kw: calls.append(a))

    assert svc.retry_pending() == 0
    assert calls == []


def test_retry_pending_skips_inactive_webhook(svc, monkeypatch):
    """Webhook đã tắt (active=0) thì không retry — khớp filter của dispatch_event."""
    _seed(svc, next_attempt_at=now() - 5)
    svc.store.execute("UPDATE webhooks SET active=0 WHERE id='wh_1'")
    calls: list[tuple] = []
    monkeypatch.setattr(svc, "_attempt",
                        lambda *a, **kw: calls.append(a))

    assert svc.retry_pending() == 0
    assert calls == []


def test_start_retry_worker_is_singleton(svc):
    th = svc.start_retry_worker(3600)
    try:
        assert th is not None and th.is_alive()
        assert svc.start_retry_worker(3600) is th
    finally:
        svc._retry_thread = None  # để thread daemon của test không bị join
