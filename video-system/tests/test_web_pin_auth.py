"""Web PIN gate — session cookie auth, lockout, middleware default-deny."""
from __future__ import annotations

import pytest

from backend.app.auth import webauth

BROWSER = {"Sec-Fetch-Site": "same-origin"}  # header mọi browser luôn gửi
PIN = "997220"


@pytest.fixture(autouse=True)
def _clean_lockout():
    """Không để lockout trong memory rò rỉ sang test khác."""
    webauth._fails.clear()
    yield
    webauth._fails.clear()


def _login(client, pin=PIN):
    return client.post("/api/v1/auth/login", headers=BROWSER, json={"pin": pin})


def test_health_and_login_page_open_without_auth(client):
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/").status_code == 200  # shell HTML chứa màn hình khóa


def test_browser_without_session_blocked(client):
    r = client.get("/api/v1/todos", headers=BROWSER)
    assert r.status_code == 401
    assert "đăng nhập" in r.json()["detail"]
    # cả /docs bị default-deny
    assert client.get("/docs", headers=BROWSER).status_code == 401


def test_wrong_pin_then_correct_pin_grants_admin_session(client):
    r = _login(client, "000001")
    assert r.status_code == 401
    assert "lần thử" in r.json()["detail"]

    r = _login(client)
    assert r.status_code == 200
    assert r.json()["authenticated"] is True
    assert "vs_session" in r.cookies

    # session cookie → request browser được vào, scope admin (không cần bearer)
    assert client.get("/api/v1/todos", headers=BROWSER).status_code == 200
    assert client.get("/api/v1/auth/session", headers=BROWSER).json()["authenticated"] is True


def test_tampered_session_cookie_rejected(client):
    _login(client)
    bad = {"Sec-Fetch-Site": "same-origin", "Cookie": "vs_session=deadbeef.deadbeef"}
    r = client.get("/api/v1/todos", headers=bad)
    assert r.status_code == 401


def test_logout_clears_session(client):
    _login(client)
    assert client.get("/api/v1/todos", headers=BROWSER).status_code == 200
    r = client.post("/api/v1/auth/logout", headers=BROWSER)
    assert r.status_code == 200
    assert client.get("/api/v1/todos", headers=BROWSER).status_code == 401


def test_five_wrong_pins_lockout_60s(client):
    for i in range(4):  # 4 lần đầu: 401 kèm số lần thử còn lại
        r = _login(client, f"{i:06d}")
        assert r.status_code == 401, r.json()
    r = _login(client, "000004")  # lần 5: bắt đầu khóa
    assert r.status_code == 429
    assert "khóa" in r.json()["detail"]
    r = _login(client, PIN)  # đúng PIN cũng bị chặn khi đang khóa
    assert r.status_code == 429


def test_bearer_key_still_works_for_agents(client, admin):
    """Agent API dùng Bearer key — không bị PIN gate làm hỏng."""
    assert client.get("/api/v1/todos", headers=admin).status_code == 200


def test_forwarded_ip_isolation_and_proxy_loopback_protection(client):
    """Requests behind Nginx (X-Forwarded-For) must isolate lockouts per real IP
    and never bypass auth as direct loopback."""
    ip1_headers = {**BROWSER, "X-Forwarded-For": "203.0.113.10, 127.0.0.1"}
    ip2_headers = {**BROWSER, "X-Forwarded-For": "198.51.100.22, 127.0.0.1"}

    # Lock out IP 1 with 5 failed attempts
    for i in range(4):
        r = client.post("/api/v1/auth/login", headers=ip1_headers, json={"pin": f"{i:06d}"})
        assert r.status_code == 401
    r5 = client.post("/api/v1/auth/login", headers=ip1_headers, json={"pin": "000004"})
    assert r5.status_code == 429

    # IP 2 is NOT locked out and can log in with the valid PIN
    r_ip2 = client.post("/api/v1/auth/login", headers=ip2_headers, json={"pin": PIN})
    assert r_ip2.status_code == 200
    assert r_ip2.json()["authenticated"] is True


def test_redesigned_ui_4_column_grid_checklist_and_no_raw_clutter(client):
    """Verify the redesigned index.html includes the 4-column shot grid,
    empty slot placeholders, agent checklist board, in-page viewer modal,
    Quick Job Directory, Cache-Control no-store, scroll-margin-top, filter-conflict recovery,
    inline video #t=0.05 preview frame, and removes raw API key/endpoint clutter."""
    r = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", "").lower()
    html = r.text
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in html
    assert "shot-video-grid" in html
    assert "jobDirectoryContainer" in html
    assert "TRỐNG — THIẾU SHOT" in html
    assert "webViewerModal" in html
    assert "/shot-checklist" in html
    assert "scroll-margin-top: 76px" in html
    assert "syncStatusTabsUi" in html
    assert "cleanupVideosInContainer" in html
    assert "rerenderSingleCard" in html
    assert "syncCinemaModalShotState" in html
    assert "#t=0.05" in html
    assert "devkey-read" not in html


def test_browser_blocked_even_if_proxy_injects_default_bearer(client):
    """If Caddy/reverse-proxy injects Authorization: Bearer devkey-read on a browser request,
    the browser MUST still be blocked by the PIN gate until vs_session is established."""
    proxy_browser_headers = {**BROWSER, "Authorization": "Bearer devkey-read"}
    assert client.get("/api/v1/auth/session", headers=proxy_browser_headers).json()["authenticated"] is False
    assert client.get("/api/v1/todos", headers=proxy_browser_headers).status_code == 401


def test_list_todos_returns_full_shot_checklist_for_all_11_items(client, ingest):
    """Ensure GET /api/v1/todos returns full shots, shot_checklist (with audio_start_sec/audio_end_sec),
    and image lists for >8 items (e.g. 11 items) in a single request so no job is missing or truncated."""
    for i in range(11):
        res = client.post(
            "/api/v1/todos/from-script",
            headers=ingest,
            json={
                "title": f"Enterprise Case Study #{i+1:02d}",
                "shots": [
                    {"narration": f"Intro narration for job {i+1} shot 1.", "shot_type": "scene"},
                    {"narration": f"Analysis narration for job {i+1} shot 2.", "shot_type": "diagram"},
                ],
                "bgm_enabled": False,
                "collect_references": False,
            },
        )
        assert res.status_code == 201, res.text

    r = client.get("/api/v1/todos", headers={**ingest, "Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    todos = r.json()["todos"]
    assert len(todos) >= 11
    for t in todos[:11]:
        assert "shots" in t and len(t["shots"]) == 2
        assert "shot_checklist" in t
        cl = t["shot_checklist"]
        assert cl["total_shots"] == 2
        assert len(cl["shots"]) == 2
        s0 = cl["shots"][0]
        assert "audio_start_sec" in s0
        assert "audio_end_sec" in s0
        assert "expected_duration_sec" in s0
        assert "delta_duration_sec" in s0
        assert "remaining_duration_sec" in s0


def test_confirm_assemble_preserves_raw_shot_videos_for_4col_grid(client, ingest, monkeypatch, tmp_path):
    """Verify POST /api/v1/todos/{item_id}/confirm-assemble builds final_en.mp4 AND
    preserves individual shot videos (videos/seq_*.mp4) so the 4-column web grid still plays them."""
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")
    monkeypatch.setenv("VIDEO_DISABLE_GDRIVE_SYNC", "0")
    import backend.app.service_assembler as asm_mod
    monkeypatch.setattr(
        asm_mod,
        "sync_item_to_gdrive",
        lambda item_id, purge_raw_clips_after_sync=False: {
            "status": "synced",
            "remote_path": f"gdrive:video_system_assets/items/{item_id}",
            "freed_bytes": 0,
            "purge_raw_clips_after_sync": purge_raw_clips_after_sync,
        },
    )

    res = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Assemble Preservation Test",
            "shots": [
                {"narration": "First shot narration for assembly preservation check.", "shot_type": "scene"},
            ],
            "bgm_enabled": False,
            "collect_references": False,
        },
    )
    assert res.status_code == 201, res.text
    item_id = res.json()["item_id"]

    from tests.test_todo_en_flow import _make_test_mp4
    clip_path = _make_test_mp4(tmp_path / "shot1.mp4", 2.0, color="navy")
    with open(clip_path, "rb") as f:
        r_vid = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("shot1.mp4", f, "video/mp4")},
            data={"seq_num": "1", "covered_shot_indexes": "[1]"},
        )
    assert r_vid.status_code == 201, r_vid.text

    r_asm = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": False, "allow_shortfall": True},
    )
    assert r_asm.status_code == 200, r_asm.text
    asm_data = r_asm.json()
    assert asm_data["status"] == "completed"
    assert asm_data["storage_sync"]["purge_raw_clips_after_sync"] is False

    # Individual shot video file must still be accessible for the 4-column web grid
    r_shot_file = client.get(f"/api/v1/todos/{item_id}/videos/1/file", headers=ingest)
    assert r_shot_file.status_code == 200


def test_loading_states_try_catch_and_error_reporting(client, read_key, monkeypatch):
    # 1. /favicon.ico is open and returns 204 without auth errors
    r_fav = client.get("/favicon.ico")
    assert r_fav.status_code == 204

    # 2. Index HTML includes loading UI, skeleton states, error banner, toast, and try/catch
    r_html = client.get("/")
    assert r_html.status_code == 200
    html = r_html.text
    assert 'id="globalStatusBanner"' in html
    assert 'id="toastContainer"' in html
    assert 'id="refreshAllBtn"' in html
    assert "function extractApiError(" in html
    assert "function showStatusBanner(" in html
    assert "function renderLoadingSkeleton(" in html
    assert "function renderLoadErrorState(" in html
    assert "function isShotGridCollapsed(" in html
    assert "function isChecklistCollapsed(" in html
    assert "TODO_INFINITE_BATCH_SIZE" in html
    assert "function cancelPendingTodoAppendTimer(" in html
    assert "function appendNextTodoCardsBatch(" in html
    assert "function loadMoreShotsBatch(" in html
    assert "function loadMoreChecklistRowsBatch(" in html
    assert "function unloadOffscreenIdleVideo(" in html
    assert "function handleRefImagesToggle(" in html
    assert 'data-lazy-kind="checklist"' in html
    assert "video-lazy-overlay" in html
    assert "img-lazy-loader" in html
    assert "todoInfiniteSentinel" in html
    assert 'id="pageTodoList"' in html
    assert 'id="pageTodoDetail"' in html
    assert "function openTodoDetailPage(" in html
    assert "function backToTodoListPage(" in html
    assert "function switchDetailFlowTab(" in html
    assert "function renderTodoSummaryCard(" in html
    assert "function renderTodoDetailPage(" in html
    assert "todo-summary-card" in html
    assert "detail-flow-tabs" in html
    assert "Không thể tải dữ liệu danh sách công việc TODO" in html
    assert "Thử tải lại ngay" in html

    # 3. Simulated backend exception in list_todos returns structured 500 JSON with detail
    from backend.app import main as app_main

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated DB timeout")

    monkeypatch.setattr(app_main.todos, "list_todos", _boom)
    r_err = client.get("/api/v1/todos", headers=read_key)
    assert r_err.status_code == 500
    err_json = r_err.json()
    assert "Lỗi máy chủ khi tải danh sách công việc" in err_json["detail"]
    assert "simulated DB timeout" in err_json["detail"]
