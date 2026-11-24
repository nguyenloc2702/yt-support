"""E2E: Muse writer + critic đi qua MCP bridge, trọn luồng tới `build_job`.

Không gọi mạng thật và không ghi file vào `data/`:

- ``VidfacClient`` được gắn transport sync bọc ASGI app (dùng lại
  ``_SyncASGITransport`` của ``test_mcp_server``) — mọi request đi trong process.
- ``webhooks.dispatch_event`` bị chặn để chỉ bắt event, không bắn HTTP.

Mỗi bước đổi ``mcp_server._CLIENT`` sang client mang đúng key, mô phỏng đúng
ràng buộc "một process MCP mang một key".
"""
from __future__ import annotations

import json

import pytest

from backend import mcp_server
from script_helpers import agents  # noqa: F401 — fixture dùng chung
from script_helpers import make_shots
from test_mcp_server import _SyncASGITransport


@pytest.fixture()
def transport():
    from backend.app.main import app

    return _SyncASGITransport(app)


@pytest.fixture()
def captured(monkeypatch):
    """Bắt event thay vì gửi webhook — không có receiver trong test."""
    from backend.app.main import webhooks as webhook_svc

    events: list[dict] = []

    def fake_dispatch(event_row, synchronous=False):
        events.append(dict(event_row))
        return []

    # Patch trên instance: AgreementNotifier giữ đúng object webhooks này.
    monkeypatch.setattr(webhook_svc, "dispatch_event", fake_dispatch)
    return events


def _agreement_sequence(events: list[dict]) -> list[str]:
    """Chỉ lấy event agreement, theo đúng thứ tự phát."""
    seq: list[str] = []
    for evt in events:
        if evt.get("type") != "agreement.next_action_changed":
            continue
        seq.append(json.loads(evt["payload_json"])["next_action"])
    return seq


def test_muse_writer_critic_full_flow_to_build_job(
        client, agents, transport, captured, monkeypatch, knowledge_env):
    keys = ("owner", "test-ingest", "agent-b", "test-read")
    clients = {
        k: mcp_server.VidfacClient("http://test", k, transport=transport)
        for k in keys
    }

    def use(key: str) -> None:
        monkeypatch.setattr(mcp_server, "_CLIENT", clients[key])

    try:
        # 1-3. Owner giữ vòng đời brief: tạo, gán critic, duyệt.
        use("owner")
        brief = mcp_server.brief_create(
            title="Muse E2E brief", target_duration_sec=120,
            language="vi", approval_mode="hard")
        assert brief["status"] == "draft"
        brief_id = brief["id"]

        assigned = mcp_server.brief_assign_critic(brief_id, "agent-b")
        assert assigned["critic"] == "agent-b"
        # Brief draft: chưa được nộp proposal, writer phải chờ owner duyệt.
        assert mcp_server.agreement_read(brief_id)["next_action"] == "await_brief_approval"
        assert mcp_server.brief_approve(brief_id)["status"] == "approved"
        assert mcp_server.agreement_read(brief_id)["next_action"] == "create_proposal"

        # 4. Writer nộp proposal — validator phải xanh.
        use("test-ingest")
        proposal = mcp_server.proposal_submit(brief_id, shots=make_shots(8, 300))
        assert proposal["validation"]["ok"] is True, proposal["validation"]
        proposal_id = proposal["id"]

        # 5. Critic ghi finding không blocking (không đổi next_action).
        use("agent-b")
        finding = mcp_server.finding_write(
            proposal_id, category="style", severity="low",
            evidence="nhịp câu ổn, chỉ góp ý nhẹ", blocking=False)
        assert finding["category"] == "style"

        # 6-7. Hai chữ ký pass: writer trước, critic sau.
        use("test-ingest")
        first = mcp_server.proposal_sign(proposal_id, "pass")
        assert first["agreed"] is False

        use("agent-b")
        second = mcp_server.proposal_sign(proposal_id, "pass")
        assert second["agreed"] is True
        assert second["status"] == "agreed"

        # 8. Owner đọc agreement — nguồn sự thật cho bước kế.
        use("owner")
        agreement = mcp_server.agreement_read(brief_id)
        assert agreement["next_action"] == "build_job"
        assert agreement["status"] == "agreed"

        # Webhook chỉ phát khi next_action ĐỔI:
        # await_brief_approval -> create -> review -> build.
        assert _agreement_sequence(captured) == [
            "await_brief_approval", "create_proposal", "review_or_sign", "build_job"]

        # Knowledge cũng đi qua MCP được với key read.
        use("test-read")
        out = mcp_server.knowledge_search("contract")
        assert "docs" in out
    finally:
        for c in clients.values():
            c.close()

    # `client` fixture: xác nhận trạng thái brief qua HTTP, không chỉ qua MCP.
    r = client.get(f"/api/v1/briefs/{brief_id}", headers=agents["read"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
