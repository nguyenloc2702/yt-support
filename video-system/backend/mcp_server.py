"""MCP bridge cho vidfac — 9 tool bọc API brief / script / knowledge.

Chạy: ``python -m backend.mcp_server`` (transport stdio).

Cấu hình qua env, đọc tại thời điểm gọi (test monkeypatch được):

- ``VIDFAC_BASE_URL``  — mặc định ``http://127.0.0.1:8090``
- ``VIDFAC_API_KEY``   — bắt buộc; thiếu thì tool trả lỗi rõ ràng
- ``VIDFAC_TIMEOUT``   — mặc định 20 giây
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import quote, urlencode

import httpx

try:  # mcp 2.x: FastMCP đã đổi tên thành MCPServer
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

try:  # mcp 2.x
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover - mcp 1.x
    try:
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError:

        class ToolError(ValueError):
            """Fallback khi SDK không cung cấp ToolError."""


DEFAULT_BASE_URL = "http://127.0.0.1:8090"
DEFAULT_TIMEOUT = 20.0
API_PREFIX = "/api/v1"

mcp = MCPServer(name="vidfac")

# Test có thể monkeypatch hai biến này thay vì gọi mạng thật.
_CLIENT: VidfacClient | None = None
_TRANSPORT: httpx.BaseTransport | None = None


def _settings() -> tuple[str, str, float]:
    """Đọc env tại thời điểm gọi — không cache để test đổi env là ăn ngay."""
    base = os.environ.get("VIDFAC_BASE_URL", DEFAULT_BASE_URL).strip()
    key = os.environ.get("VIDFAC_API_KEY", "").strip()
    raw_timeout = os.environ.get("VIDFAC_TIMEOUT", "").strip()
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    return (base or DEFAULT_BASE_URL), key, timeout


class VidfacClient:
    """HTTP client mỏng cho vidfac API.

    ``base_url`` có thể là host hoặc base API; nếu chưa kết thúc bằng
    ``/api/v1`` thì tự thêm, nhờ vậy default ``http://127.0.0.1:8090``
    vẫn gọi đúng route.
    """

    def __init__(self, base_url: str, api_key: str, transport=None,
                 timeout: float = DEFAULT_TIMEOUT):
        base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        if not base.endswith(API_PREFIX):
            base += API_PREFIX
        self.base_url = base
        self._api_key = api_key
        kwargs: dict = {"base_url": base, "timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, path: str, json=None, headers=None):
        """Gọi API, trả ``(status_code, body)``; 4xx/5xx → ``ToolError``.

        Body 2xx không phải JSON object được bọc thành ``{"raw": body}`` để tool
        luôn trả ``dict`` đúng annotation.

        Không bao giờ đưa API key vào message hay log.
        """
        if not self._api_key:
            raise ToolError(
                "Thiếu VIDFAC_API_KEY — đặt biến môi trường trước khi gọi tool.")
        merged = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if headers:
            merged.update(headers)
        try:
            resp = self._client.request(method, path, json=json, headers=merged)
        except httpx.HTTPError as exc:
            raise ToolError(
                f"Không gọi được vidfac: {type(exc).__name__}") from None
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        if resp.status_code >= 400:
            code = body.get("code") if isinstance(body, dict) else None
            detail = body.get("detail") if isinstance(body, dict) else body
            raise ToolError(f"HTTP {resp.status_code} {code or 'error'}: {detail}")
        if not isinstance(body, dict):
            # 2xx nhưng body không phải JSON object: tool annotation `-> dict`,
            # trả str sẽ làm SDK MCP 2.3.0 lỗi convert kết quả.
            body = {"raw": body}
        return resp.status_code, body


def _client() -> VidfacClient:
    """Client theo env hiện tại; test có thể monkeypatch ``_CLIENT``."""
    if _CLIENT is not None:
        return _CLIENT
    base, key, timeout = _settings()
    if not key:
        raise ToolError(
            "Thiếu VIDFAC_API_KEY — đặt biến môi trường này trước khi gọi tool.")
    return VidfacClient(base, key, transport=_TRANSPORT, timeout=timeout)


def _request(method: str, path: str, json=None, headers=None):
    """Gọi qua client hiện tại và đóng client tạm (nếu không phải client test)."""
    client = _client()
    try:
        return client.request(method, path, json=json, headers=headers)
    finally:
        if client is not _CLIENT:
            client.close()


# -- Knowledge Library -------------------------------------------------------


@mcp.tool()
def knowledge_search(query: str, source: str | None = None,
                     limit: int = 10) -> dict:
    """Tìm tài liệu trong Knowledge Library (scope read)."""
    params: dict = {"q": query, "limit": limit}
    if source:
        params["source"] = source
    _, body = _request("GET", f"/knowledge/docs?{urlencode(params)}")
    return body


@mcp.tool()
def knowledge_read(doc_id: str) -> dict:
    """Đọc một tài liệu kèm content + etag (scope read)."""
    _, body = _request("GET", f"/knowledge/docs/{quote(doc_id, safe='')}")
    return body


# -- Brief -------------------------------------------------------------------


@mcp.tool()
def brief_create(title: str, angle: str | None = None,
                 target_duration_sec: float = 780, language: str = "vi",
                 approval_mode: str = "hard",
                 source_links: list[str] | None = None) -> dict:
    """Tạo brief (scope ingest). Critic do owner gán bằng tool riêng."""
    body: dict = {
        "title": title,
        "target_duration_sec": target_duration_sec,
        "language": language,
        "approval_mode": approval_mode,
        "source_links": list(source_links or []),
    }
    if angle:
        body["angle"] = angle
    _, out = _request("POST", "/briefs", json=body)
    return out


@mcp.tool()
def brief_assign_critic(brief_id: str, critic: str) -> dict:
    """Chỉ định critic cho brief (scope owner)."""
    _, out = _request(
        "POST", f"/briefs/{quote(brief_id, safe='')}/critic",
        json={"critic": critic})
    return out


@mcp.tool()
def brief_approve(brief_id: str) -> dict:
    """Duyệt brief (scope owner)."""
    _, out = _request("POST", f"/briefs/{quote(brief_id, safe='')}/approve")
    return out


@mcp.tool()
def agreement_read(brief_id: str) -> dict:
    """Đọc trạng thái chốt kịch bản + next_action (scope read)."""
    _, out = _request("GET", f"/briefs/{quote(brief_id, safe='')}/agreement")
    return out


# -- Script proposal ---------------------------------------------------------


@mcp.tool()
def proposal_submit(brief_id: str, shots: list[dict],
                    script_text: str | None = None,
                    parent_id: str | None = None,
                    idempotency_key: str | None = None) -> dict:
    """Nộp proposal (scope ingest). Tự sinh Idempotency-Key nếu thiếu."""
    key = (idempotency_key or "").strip() or uuid.uuid4().hex
    body = {
        "brief_id": brief_id,
        "parent_id": parent_id,
        "script_text": script_text,
        "shots": shots,
    }
    _, out = _request(
        "POST", "/script-proposals", json=body,
        headers={"Idempotency-Key": key})
    return out


@mcp.tool()
def finding_write(proposal_id: str, category: str, severity: str,
                  evidence: str | None = None, blocking: bool = False) -> dict:
    """Ghi finding cho proposal (scope ingest; phải là critic/owner)."""
    body = {
        "category": category,
        "severity": severity,
        "evidence": evidence,
        "blocking": blocking,
    }
    _, out = _request(
        "POST", f"/script-proposals/{quote(proposal_id, safe='')}/findings",
        json=body)
    return out


@mcp.tool()
def proposal_sign(proposal_id: str, verdict: str,
                  note: str | None = None) -> dict:
    """Ký proposal bằng content_hash hiện tại (scope ingest)."""
    _, out = _request(
        "POST", f"/script-proposals/{quote(proposal_id, safe='')}/sign",
        json={"verdict": verdict, "note": note})
    return out


if __name__ == "__main__":
    mcp.run(transport="stdio")
