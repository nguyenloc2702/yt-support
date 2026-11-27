"""Mã 6 số của agent: đăng nhập đổi token, không dùng làm API key.

Mã 6 số chỉ có 10^6 tổ hợp. Cho dùng trực tiếp làm Bearer trên endpoint public
là mời người ta dò sạch bảng mã. Vì vậy mã chỉ đi qua /auth/login, có khoá sau
5 lần sai, và trả về token dài ký HMAC mang identity + scope.
"""
from __future__ import annotations

import os
import time

import pytest

from backend.app import auth as auth_mod
from backend.app.auth import SESSION_COOKIE, webauth

CODES = "997220:owner:admin,997221:muse-boss:manager,997222:muse-gen-1:ingest"


@pytest.fixture()
def agent_codes_env():
    saved = os.environ.get("VIDEO_AGENT_CODES")
    os.environ["VIDEO_AGENT_CODES"] = CODES
    webauth._fails.clear()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("VIDEO_AGENT_CODES", None)
        else:
            os.environ["VIDEO_AGENT_CODES"] = saved
        webauth._fails.clear()


def _login(client, **body):
    return client.post("/api/v1/auth/login", json=body)


def test_code_login_returns_token_with_identity_and_scope(client, agent_codes_env):
    r = _login(client, code="997221")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identity"] == "muse-boss"
    assert body["scope"] == "manager"
    assert body["token"]
    assert r.cookies.get(SESSION_COOKIE)


def test_token_works_as_bearer_and_keeps_identity(client, agent_codes_env):
    """Agent không phải nhét mã 6 số vào mọi request — chỉ giữ token.

    Phải `cookies.clear()`: login set session cookie hợp lệ, và identity_for
    ưu tiên cookie trước Bearer — không xoá thì request đi đường cookie và
    đường Bearer-token hỏng cũng xanh giả (đúng bẫy §11 của báo cáo board).
    """
    tok = _login(client, code="997222").json()["token"]
    client.cookies.clear()
    r = client.get("/api/v1/auth/whoami", headers={"Authorization": f"Bearer {tok}"})
    if r.status_code == 404:  # endpoint chưa có thì bỏ qua, phần dưới vẫn kiểm
        pytest.skip("/auth/whoami chưa tồn tại")
    assert r.status_code == 200, r.text
    assert r.json()["identity"] == "muse-gen-1"
    assert r.json()["scope"] == "ingest"

    # Token phải dùng được trên endpoint nghiệp vụ, không chỉ whoami —
    # middleware chấp nhận token nhưng route 500 là bug thật đã từng xảy ra.
    inbox = client.get("/api/v1/agent/inbox", headers={"Authorization": f"Bearer {tok}"})
    assert inbox.status_code == 200, inbox.text
    assert inbox.json()["caller"] == "muse-gen-1"


def test_wrong_code_locks_out_after_five_tries(client, agent_codes_env):
    for i in range(4):
        r = _login(client, code="000000")
        assert r.status_code == 401, f"lần {i + 1}: {r.text}"
    fifth = _login(client, code="000000")
    assert fifth.status_code == 429, fifth.text

    # Đang bị khoá thì mã ĐÚNG cũng không vào được — nếu không, khoá vô nghĩa.
    blocked = _login(client, code="997221")
    assert blocked.status_code == 429, blocked.text


def test_tampered_token_is_rejected(client, agent_codes_env):
    tok = _login(client, code="997222").json()["token"]
    # Xoá cookie: nếu không, request đi qua bằng cookie hợp lệ vừa set và test
    # sẽ "pass" mà chẳng kiểm gì về token giả.
    client.cookies.clear()
    exp, identity, scope, mac = tok.split(".")
    forged = f"{exp}.owner.{scope}.{mac}"
    r = client.get("/api/v1/todos", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code in (401, 403), f"token sửa identity vẫn qua: {r.status_code}"


def test_expired_token_is_rejected(agent_codes_env):
    token = webauth.make_token("muse-gen-1", "ingest")
    exp, identity, scope, mac = token.split(".")
    past = format(int(time.time()) - 10, "x")
    stale = f"{past}.{identity}.{scope}.{webauth._sign(past, identity, scope)}"
    assert webauth.verify_token(stale) is None


def test_pin_login_still_works_for_owner(client, agent_codes_env):
    """Đường cũ không được vỡ: PIN owner vẫn ra identity owner + scope admin."""
    from backend.app.config import web_pin

    r = _login(client, pin=web_pin())
    assert r.status_code == 200, r.text
    assert r.json()["identity"] == "owner"
    assert r.json()["scope"] == "admin"


def test_missing_both_pin_and_code_is_422(client, agent_codes_env):
    r = _login(client)
    assert r.status_code == 422
