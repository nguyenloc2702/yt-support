"""Agent-facing contract phải đọc được bằng một URL, khớp với route thật.

Contract là thứ agent bên ngoài sẽ copy vào prompt. Nếu nó drift so với code,
agent chạy sai mà không có ai để ý — nên ở đây đối chiếu cả chiều ngược lại.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.app.main import app

CONTRACT = "/api/v1/knowledge/contract"


def test_contract_requires_read_key(client):
    assert client.get(CONTRACT).status_code == 401
    bad = client.get(CONTRACT, headers={"Authorization": "Bearer wrong-key"})
    assert bad.status_code == 401


def test_contract_raw_is_markdown(client, read_key, ingest, admin):
    for headers in (read_key, ingest, admin):
        r = client.get(f"{CONTRACT}?format=raw", headers=headers)
        assert r.status_code == 200, r.text
        assert "text/markdown" in r.headers["content-type"]
        body = r.text
        assert body.startswith("# Knowledge Agent Contract")
        for marker in ("If-Match", "412", "/fork", "secret_detected",
                       "owner_only", "Idempotency-Key"):
            assert marker in body, marker


def test_contract_accept_header_also_returns_raw(client, read_key):
    r = client.get(CONTRACT, headers={**read_key, "Accept": "text/markdown"})
    assert r.status_code == 200
    assert r.text.startswith("# Knowledge Agent Contract")


def test_contract_json_index(client, read_key):
    r = client.get(CONTRACT, headers=read_key)
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Knowledge Agent Contract"
    assert data["version"] == "1.0"
    assert data["raw_contract_url"] == f"{CONTRACT}?format=raw"
    assert data["auth"]["minimum_scope"] == "read"
    assert len(data["endpoints"]) == 16
    assert any("412" in step for step in data["workflow"])
    assert data["full_guide"] == "read_me/09_knowledge_library.md"


def test_contract_endpoints_match_registered_routes(client, read_key):
    """Mọi path contract quảng cáo phải tồn tại trong OpenAPI của app."""
    data = client.get(CONTRACT, headers=read_key).json()
    routes = app.openapi()["paths"]
    for ep in data["endpoints"]:
        path = ep["path"].replace("{id}", "{doc_id}").replace("{n}", "{version_no}")
        assert path in routes, f"contract quảng cáo route không tồn tại: {path}"
        assert ep["method"].lower() in routes[path], ep


def test_contract_error_codes_exist_in_code(client, read_key):
    data = client.get(CONTRACT, headers=read_key).json()
    root = Path(__file__).resolve().parents[1]
    source = (root / "backend/app/service_knowledge.py").read_text(encoding="utf-8")
    source += (root / "backend/app/main.py").read_text(encoding="utf-8")
    for label in data["error_reflex"]:
        code = label.split(" ", 1)[1]
        assert re.search(rf'"{re.escape(code)}"', source), code


def test_contract_file_has_no_live_secret(client, read_key):
    """Contract lưu trong repo, tuyệt đối không được chứa key/PIN thật."""
    r = client.get(f"{CONTRACT}?format=raw", headers=read_key)
    body = r.text
    # Bỏ placeholder đã chủ động ghi trong contract rồi mới soát pattern thật.
    scrubbed = body.replace("ghp_xxxxxxxxxxxxxxxxxxxx", "").replace("sk-REPLACE_ME", "")
    assert not re.search(r"ghp_[A-Za-z0-9]{20,}", scrubbed)
    assert not re.search(r"sk-[A-Za-z0-9_-]{20,}", scrubbed)
    assert "VIDEO_WEB_PIN=000000" in body  # placeholder, không phải PIN thật
