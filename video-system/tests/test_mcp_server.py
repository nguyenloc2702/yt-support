"""Test MCP bridge — không cần mạng, không chạy process.

Dùng một transport sync bọc ``httpx.ASGITransport`` để ``httpx.Client``
(vốn đồng bộ) gọi thẳng FastAPI app trong cùng process.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from backend import mcp_server
from script_helpers import agents  # noqa: F401 — fixture dùng chung
from script_helpers import make_shots

TOOL_NAMES = {
    "knowledge_search",
    "knowledge_read",
    "brief_create",
    "brief_assign_critic",
    "brief_approve",
    "agreement_read",
    "proposal_submit",
    "finding_write",
    "proposal_sign",
}


class _SyncASGITransport(httpx.BaseTransport):
    """Bắc cầu ``httpx.Client`` (sync) → ``httpx.ASGITransport`` (async-only)."""

    def __init__(self, app):
        self._asgi = httpx.ASGITransport(app=app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.read()

        async def _send():
            async def _stream():
                if body:
                    yield body

            inner = httpx.Request(
                request.method, request.url, headers=request.headers,
                content=_stream())
            resp = await self._asgi.handle_async_request(inner)
            return resp, await resp.aread()

        resp, content = asyncio.run(_send())
        # `aread()` đã giải nén; bỏ header encoding để client không decode lần 2.
        skip = {b"content-encoding", b"content-length", b"transfer-encoding"}
        headers = [(k, v) for k, v in resp.headers.raw if k.lower() not in skip]
        return httpx.Response(
            resp.status_code, headers=headers, content=content,
            request=request)


class _RecordingTransport(httpx.BaseTransport):
    """Ghi lại request để kiểm header Authorization / Idempotency-Key."""

    def __init__(self, inner: httpx.BaseTransport):
        self.inner = inner
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.inner.handle_request(request)


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    monkeypatch.setattr(mcp_server, "_CLIENT", None)
    monkeypatch.setattr(mcp_server, "_TRANSPORT", None)


@pytest.fixture()
def transport():
    from backend.app.main import app

    return _SyncASGITransport(app)


def _client_for(key: str, transport) -> mcp_server.VidfacClient:
    return mcp_server.VidfacClient(
        "http://testserver", key, transport=transport)


def _create_doc(client, ingest, path: str, content: str) -> dict:
    resp = client.post(
        "/api/v1/knowledge/docs", headers=ingest,
        json={"source": "library", "path": path, "content": content})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_registers_exactly_nine_tools():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert {tool.name for tool in tools} == TOOL_NAMES


def test_knowledge_search_returns_docs_with_read_key(
        client, ingest, knowledge_env, transport, monkeypatch):
    _create_doc(client, ingest, "mcp/search.md",
                "# ETag guide\nNội dung tra cứu MCP.\n")
    rec = _RecordingTransport(transport)
    monkeypatch.setattr(
        mcp_server, "_CLIENT", _client_for("test-read", rec))

    out = mcp_server.knowledge_search("ETag")

    assert out["docs"], out
    assert rec.requests[-1].headers["authorization"] == "Bearer test-read"


def test_brief_create_assign_approve_with_owner_key(
        client, agents, transport, monkeypatch):
    rec = _RecordingTransport(transport)
    monkeypatch.setattr(
        mcp_server, "_CLIENT", _client_for("test-ingest", rec))

    brief = mcp_server.brief_create(
        "MCP brief", angle="test angle", target_duration_sec=120)
    assert brief["id"].startswith("brf_")
    assert brief["status"] == "draft"

    monkeypatch.setattr(mcp_server, "_CLIENT", _client_for("owner", rec))
    assigned = mcp_server.brief_assign_critic(brief["id"], "agent-b")
    assert assigned["critic"] == "agent-b"

    approved = mcp_server.brief_approve(brief["id"])
    assert approved["id"] == brief["id"]
    assert approved["status"] == "approved"


def test_proposal_submit_sends_idempotency_key_and_dedupes(
        client, agents, transport, monkeypatch):
    monkeypatch.setattr(
        mcp_server, "_CLIENT", _client_for("test-ingest", transport))
    brief = mcp_server.brief_create("Idem brief", target_duration_sec=120)

    monkeypatch.setattr(mcp_server, "_CLIENT", _client_for("owner", transport))
    mcp_server.brief_assign_critic(brief["id"], "agent-b")
    mcp_server.brief_approve(brief["id"])

    rec = _RecordingTransport(transport)
    monkeypatch.setattr(
        mcp_server, "_CLIENT", _client_for("test-ingest", rec))
    shots = make_shots(8, 300)
    key = "idem-mcp-1"

    first = mcp_server.proposal_submit(
        brief["id"], shots, idempotency_key=key)
    second = mcp_server.proposal_submit(
        brief["id"], shots, idempotency_key=key)

    assert first["id"] == second["id"]
    assert rec.requests[0].headers["idempotency-key"] == key
    listed = client.get(
        f"/api/v1/briefs/{brief['id']}/proposals", headers=agents["read"])
    assert len(listed.json()["proposals"]) == 1


def test_proposal_submit_auto_generates_idempotency_key(monkeypatch):
    captured: dict = {}

    def fake_request(self, method, path, json=None, headers=None):
        captured.update(headers or {})
        return 201, {"id": "prp_fake"}

    monkeypatch.setattr(mcp_server.VidfacClient, "request", fake_request)
    monkeypatch.setattr(
        mcp_server, "_CLIENT",
        mcp_server.VidfacClient("http://testserver", "test-ingest"))

    out = mcp_server.proposal_submit("brf_x", [{"narration": "a"}])

    assert out["id"] == "prp_fake"
    key = captured["Idempotency-Key"]
    assert len(key) == 32
    int(key, 16)  # hex hợp lệ


def test_tool_error_includes_code_and_hides_key(
        client, agents, transport, monkeypatch):
    rec = _RecordingTransport(transport)
    monkeypatch.setattr(
        mcp_server, "_CLIENT", _client_for("test-ingest", rec))
    brief = mcp_server.brief_create("Denied brief", target_duration_sec=120)

    # test-admin có scope admin nhưng không phải identity owner.
    monkeypatch.setattr(
        mcp_server, "_CLIENT", _client_for("test-admin", rec))
    with pytest.raises(mcp_server.ToolError) as err:
        mcp_server.brief_approve(brief["id"])

    msg = str(err.value)
    assert "HTTP 403" in msg
    assert "owner_only" in msg
    assert "test-admin" not in msg
    assert "Bearer" not in msg


def test_412_hash_mismatch_maps_to_tool_error(
        client, ingest, knowledge_env, transport):
    doc = _create_doc(client, ingest, "mcp/etag.md", "# ETag\nA\n")
    api = _client_for("test-ingest", transport)

    with pytest.raises(mcp_server.ToolError) as err:
        api.request(
            "PUT", f"/knowledge/docs/{doc['id']}",
            json={"content": "# ETag\nB\n"},
            headers={"If-Match": '"deadbeefdeadbeef-v1"'})

    msg = str(err.value)
    assert "HTTP 412" in msg
    assert "hash_mismatch" in msg
    assert "test-ingest" not in msg
    assert "Bearer" not in msg


def test_missing_api_key_is_clear_and_does_not_crash(monkeypatch):
    monkeypatch.delenv("VIDFAC_API_KEY", raising=False)
    monkeypatch.setattr(mcp_server, "_CLIENT", None)

    with pytest.raises(mcp_server.ToolError) as err:
        mcp_server.knowledge_search("x")

    assert "VIDFAC_API_KEY" in str(err.value)


class _PlainTextTransport(httpx.BaseTransport):
    """2xx nhưng body text/plain — không parse được JSON."""

    def __init__(self, body: str = "plain text ok"):
        self.body = body

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=self.body,
            headers={"content-type": "text/plain; charset=utf-8"},
            request=request)


def test_non_json_2xx_body_is_wrapped_in_raw(monkeypatch):
    """Tool annotation `-> dict`: body 2xx không phải object phải bọc `raw`."""
    monkeypatch.setattr(
        mcp_server, "_CLIENT",
        mcp_server.VidfacClient("http://testserver", "test-read",
                                transport=_PlainTextTransport()))

    out = mcp_server.knowledge_search("x")

    assert isinstance(out, dict)
    assert out == {"raw": "plain text ok"}
