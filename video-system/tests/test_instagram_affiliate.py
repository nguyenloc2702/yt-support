"""Tests for Instagram Reels publishing (OAuth/token/container/publish) + Affiliate
links (ClickBank/eBay deeplink, slug redirect, click tracking) + conversions sync."""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from backend.app import main as app_main
from backend.app.service_affiliate import (
    AffiliateService,
    build_clickbank_link,
    build_ebay_link,
    make_subid,
)
from backend.app.service_instagram import InstagramService, build_caption

ITEM_ID = "item_igtest0001"


@pytest.fixture()
def ig_env(monkeypatch):
    fkey = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("INSTAGRAM_APP_ID", "ig-app-123")
    monkeypatch.setenv("INSTAGRAM_APP_SECRET", "ig-sec-456")
    monkeypatch.setenv("IG_TOKEN_ENCRYPTION_KEY", fkey)
    return fkey


@pytest.fixture()
def aff_env(monkeypatch):
    monkeypatch.setenv("CLICKBANK_NICKNAME", "mystore")
    monkeypatch.setenv("EBAY_CAMPAIGN_ID", "1234567890")


def _setup_mock_ig(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    ig = InstagramService(
        app_main.store,
        app_main.webhooks,
        http_factory=lambda: httpx.Client(transport=transport, timeout=30.0),
        poll_interval_sec=0.0,
        scheduler_interval_sec=3600.0,
        retry_sleep_fn=lambda _: None,
        enable_scheduler=False,
    )
    monkeypatch.setattr(app_main, "instagram", ig)
    return ig


def _setup_mock_aff(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    svc = AffiliateService(
        app_main.store,
        app_main.webhooks,
        http_factory=lambda: httpx.Client(transport=transport, timeout=30.0),
    )
    monkeypatch.setattr(app_main, "affiliate", svc)
    from backend.app.service_affiliate import set_affiliate_service
    set_affiliate_service(svc)
    return svc


def _connect_sample_account(ig: InstagramService, *, expires_in: float = 60 * 24 * 3600.0):
    store = app_main.store
    t = time.time()
    store.execute("DELETE FROM ig_accounts")
    store.execute("DELETE FROM ig_publishes")
    store.execute(
        "INSERT INTO ig_accounts "
        "(id,ig_user_id,username,account_type,media_count,access_token_enc,"
        "token_expires_at,status,is_default,connected_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "iga_test1",
            "17841400000000001",
            "sticksto_test",
            "BUSINESS",
            3,
            ig.tokens.encrypt("ig-acc-tok-1"),
            t + expires_in,
            "connected",
            1,
            t,
            t,
        ),
    )


def _create_sample_todo_video(item_id: str = ITEM_ID) -> Path:
    data = Path(os.environ["VIDEO_DATA_DIR"])
    p = data / "objects" / item_id / "exports"
    p.mkdir(parents=True, exist_ok=True)
    f = p / "final_en.mp4"
    f.write_bytes(b"\x00\x01\x02fakevideo" * 100)
    app_main.store.execute(
        "INSERT OR IGNORE INTO items (id,title,type,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        (item_id, "IG Test Job", "todo", "assembled", time.time(), time.time()),
    )
    return f


def _attach_clickbank_product(svc: AffiliateService, item_id: str = ITEM_ID):
    prod = svc.create_product(network="clickbank", label="Test Course", cb_promoter="vendor99")
    return svc.attach_product(item_id, prod["id"])


# -- caption builder -----------------------------------------------------------

def test_caption_builder_disclosure_and_cta():
    out = build_caption(body="Best gadget of 2026\nSetup under 30 seconds",
                        affiliate_slug="ab12cd34", is_affiliate=True, base_url="https://vidfac.gacdero.vn")
    assert out.startswith("Best gadget of 2026 #ad")
    assert "vidfac.gacdero.vn/b/ab12cd34" in out
    assert "#ad" in out[:125]

    out2 = build_caption(body="No affiliate post", is_affiliate=False)
    assert "#ad" not in out2
    assert "/b/" not in out2

    out3 = build_caption(body="x", is_affiliate=True)
    assert out3 == "x #ad"


def test_caption_builder_truncates_but_keeps_ad_and_cta():
    body = "\n".join(f"line {i} " + "y" * 60 for i in range(60))
    out = build_caption(body=body, affiliate_slug="slug0001", is_affiliate=True)
    assert len(out) <= 2200
    assert "#ad" in out
    assert "/b/slug0001" in out


# -- deeplink builders ---------------------------------------------------------

def test_deeplink_builders():
    link = build_clickbank_link("mystore", "vendor99", "job_item_abc1234567890123456789")
    assert link == "https://mystore.vendor99.hop.clickbank.net/?tid=jobitemabc12345678901234"
    assert len(link.split("?tid=")[1]) <= 24
    assert len(make_subid("item_abc123456789")) <= 24

    eb = build_ebay_link("https://www.ebay.com/itm/123", "1112223334", "subid1")
    assert eb == "https://www.ebay.com/itm/123?campid=1112223334&mkevt=1&customid=subid1"
    eb2 = build_ebay_link("https://www.ebay.com/itm/123?query=x", "1112223334", "")
    assert eb2.endswith("&campid=1112223334&mkevt=1")

    with pytest.raises(Exception):
        build_clickbank_link("", "vendor99", "t1")


# -- OAuth ---------------------------------------------------------------------

def test_oauth_start_callback_and_replay_protection(client, read_key, ig_env, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.instagram.com" and req.url.path == "/oauth/access_token":
            return httpx.Response(200, json={"access_token": "short-tok", "user_id": "17841400000000001",
                                             "permissions": ["instagram_business_basic"]})
        if req.url.host == "graph.instagram.com" and req.url.path == "/access_token":
            return httpx.Response(200, json={"access_token": "long-tok", "token_type": "bearer",
                                             "expires_in": 60 * 24 * 3600})
        if req.url.host == "graph.instagram.com" and req.url.path == "/me":
            return httpx.Response(200, json={"user_id": "17841400000000001", "username": "sticksto_test",
                                             "account_type": "BUSINESS", "media_count": 3})
        if req.url.host == "graph.instagram.com" and req.url.path == "/17841400000000001/content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_total": 100, "quota_remaining": 100}]})
        raise AssertionError(f"Unexpected: {req.method} {req.url}")

    _setup_mock_ig(monkeypatch, handler)

    r_start = client.get("/api/v1/instagram/auth/start", headers=read_key)
    assert r_start.status_code == 200, r_start.text
    auth_url = r_start.json()["auth_url"]
    assert "ig-app-123" in auth_url
    assert "instagram_business_content_publish" in auth_url
    state = httpx.URL(auth_url).params["state"]

    r_cb = client.get(f"/api/v1/instagram/auth/callback?code=xyz&state={state}", follow_redirects=False)
    assert r_cb.status_code == 302
    assert r_cb.headers["location"] == "/#/publish?ig=connected"

    r_replay = client.get(f"/api/v1/instagram/auth/callback?code=xyz&state={state}", follow_redirects=False)
    assert r_replay.status_code == 302
    assert "ig=error" in r_replay.headers["location"]

    r_st = client.get("/api/v1/instagram/status", headers=read_key)
    assert r_st.status_code == 200
    st = r_st.json()
    assert st["configured"] is True
    assert st["connected"] is True
    assert st["account"]["username"] == "sticksto_test"
    row = app_main.store.query_one("SELECT access_token_enc FROM ig_accounts WHERE ig_user_id='17841400000000001'")
    assert row["access_token_enc"] != "long-tok"


def test_token_refresh_and_expiry(client, read_key, ig_env, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/refresh_access_token":
            assert req.url.params["grant_type"] == "ig_refresh_token"
            return httpx.Response(200, json={"access_token": "refreshed-tok", "expires_in": 60 * 24 * 3600})
        if req.url.path == "/17841400000000001/content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_total": 100, "quota_remaining": 100,
                                                       "quota_reset_at": "2026-12-01T00:00:00+0000"}]})
        raise AssertionError(f"Unexpected: {req.url}")

    ig = _setup_mock_ig(monkeypatch, handler)
    _connect_sample_account(ig, expires_in=3 * 24 * 3600.0)  # < 7 ngày -> refresh

    r = client.get("/api/v1/instagram/status", headers=read_key)
    assert r.status_code == 200
    st = r.json()
    assert st["quota"]["quota_remaining"] == 100
    row = app_main.store.query_one("SELECT access_token_enc, token_expires_at FROM ig_accounts WHERE id='iga_test1'")
    assert ig.tokens.decrypt(row["access_token_enc"]) == "refreshed-tok"

    # invalid -> expired
    app_main.store.execute("UPDATE ig_accounts SET token_expires_at=? WHERE id='iga_test1'", (time.time() - 10,))
    calls = {"n": 0}

    def bad_handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error_type": "OAuthException", "message": "bad token"})

    ig2 = _setup_mock_ig(monkeypatch, bad_handler)
    r2 = client.get("/api/v1/instagram/status", headers=read_key)
    assert r2.status_code == 200
    st2 = r2.json()
    assert st2["quota"]["checked"] is False  # fail-open khi endpoint lỗi
    row2 = app_main.store.query_one("SELECT status FROM ig_accounts WHERE id='iga_test1'")
    assert row2["status"] == "expired"
    assert calls["n"] >= 1


# -- publish pipeline -----------------------------------------------------------

def test_publish_happy_path_with_affiliate(client, admin, read_key, ig_env, aff_env, monkeypatch):
    from urllib.parse import parse_qs
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.setdefault("calls", []).append((req.method, str(req.url)))
        if req.url.path == "/17841400000000001/content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_total": 100, "quota_remaining": 99,
                                                       "quota_reset_at": "2026-12-01T00:00:00+0000"}]})
        if req.url.path == "/17841400000000001/media" and req.method == "POST":
            form = {k: v[0] for k, v in parse_qs(req.content.decode("utf-8")).items()}
            seen["form"] = form
            assert form["media_type"] == "REELS"
            assert form["is_ai_generated"] == "true"
            assert "#ad" in form["caption"]
            return httpx.Response(200, json={"id": "container_1"})
        if req.url.path == "/container_1":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if req.url.path == "/17841400000000001/media_publish":
            form = {k: v[0] for k, v in parse_qs(req.content.decode("utf-8")).items()}
            assert form["creation_id"] == "container_1"
            return httpx.Response(200, json={"id": "media_9"})
        if req.url.path == "/media_9":
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/abc/"})
        raise AssertionError(f"Unexpected: {req.method} {req.url}")

    ig = _setup_mock_ig(monkeypatch, handler)
    _setup_mock_aff(monkeypatch, lambda req: (_ for _ in ()).throw(AssertionError("no aff http")))
    _connect_sample_account(ig)
    _create_sample_todo_video()
    link = _attach_clickbank_product(app_main.affiliate, ITEM_ID)

    r = client.post(f"/api/v1/todos/{ITEM_ID}/instagram/publish", headers=admin, json={"caption": "Best gadget of 2026"})
    assert r.status_code == 202, r.text
    d = r.json()
    assert d["status"] in ("queued", "published")
    assert d["affiliate_slug"] == link["slug"]
    assert "#ad" in d["caption"]
    assert link["slug"] in d["caption"]

    # Đợi worker nền xong (worker là thread daemon)
    for _ in range(100):
        row = app_main.store.query_one("SELECT status, permalink, ig_media_id FROM ig_publishes WHERE id=?", (d["publish_id"],))
        if row and row["status"] in ("published", "failed"):
            break
        time.sleep(0.05)
    assert row["status"] == "published", row
    assert row["ig_media_id"] == "media_9"
    assert row["permalink"] == "https://www.instagram.com/reel/abc/"

    # caption gửi đi chứa slug CTA
    assert link["slug"] in seen["form"]["caption"]


def test_publish_container_error_marks_failed(client, admin, ig_env, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/17841400000000001/content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_total": 100, "quota_remaining": 50}]})
        if req.url.path == "/17841400000000001/media":
            return httpx.Response(200, json={"id": "container_err"})
        if req.url.path == "/container_err":
            return httpx.Response(200, json={"status_code": "ERROR"})
        raise AssertionError(f"Unexpected: {req.url}")

    ig = _setup_mock_ig(monkeypatch, handler)
    _connect_sample_account(ig)
    _create_sample_todo_video()

    r = client.post(f"/api/v1/todos/{ITEM_ID}/instagram/publish", headers=admin, json={})
    assert r.status_code == 202
    pid = r.json()["publish_id"]
    for _ in range(100):
        row = app_main.store.query_one("SELECT status, error_message FROM ig_publishes WHERE id=?", (pid,))
        if row and row["status"] in ("published", "failed"):
            break
        time.sleep(0.05)
    assert row["status"] == "failed"
    assert "ERROR" in (row["error_message"] or "")


def test_publish_quota_zero_fails(client, admin, ig_env, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/17841400000000001/content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_total": 100, "quota_remaining": 0,
                                                       "quota_reset_at": "2026-12-01T00:00:00+0000"}]})
        raise AssertionError(f"Unexpected: {req.url}")

    ig = _setup_mock_ig(monkeypatch, handler)
    _connect_sample_account(ig)
    _create_sample_todo_video()

    r = client.post(f"/api/v1/todos/{ITEM_ID}/instagram/publish", headers=admin, json={})
    assert r.status_code == 202
    pid = r.json()["publish_id"]
    for _ in range(100):
        row = app_main.store.query_one("SELECT status, error_message FROM ig_publishes WHERE id=?", (pid,))
        if row and row["status"] in ("published", "failed"):
            break
        time.sleep(0.05)
    assert row["status"] == "failed"
    assert "quota" in (row["error_message"] or "").lower()


def test_publish_idempotency_and_validation(client, admin, ig_env, monkeypatch):
    ig = _setup_mock_ig(monkeypatch, lambda req: (_ for _ in ()).throw(AssertionError("no http")))
    _connect_sample_account(ig)
    _create_sample_todo_video()

    # publish_at quá khứ -> 422
    r_past = client.post(f"/api/v1/todos/{ITEM_ID}/instagram/publish", headers=admin,
                         json={"publish_at": "2020-01-01T00:00:00Z"})
    assert r_past.status_code == 422

    # đang có publish active -> 409
    t = time.time()
    app_main.store.execute(
        "INSERT INTO ig_publishes (id,item_id,target_type,account_id,caption,status,created_at,updated_at) "
        "VALUES ('igp_active', ?, 'todo', 'iga_test1', '', 'creating', ?, ?)",
        (ITEM_ID, t, t),
    )
    r_conflict = client.post(f"/api/v1/todos/{ITEM_ID}/instagram/publish", headers=admin, json={})
    assert r_conflict.status_code == 409
    app_main.store.execute("DELETE FROM ig_publishes WHERE id='igp_active'")

    # không có video -> 422
    app_main.store.execute("DELETE FROM items WHERE id=?", (ITEM_ID,))
    r_missing = client.post(f"/api/v1/todos/{ITEM_ID}/instagram/publish", headers=admin, json={})
    assert r_missing.status_code == 404  # item không tồn tại -> 404


def test_scheduled_publish_runs_when_due(client, admin, ig_env, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/17841400000000001/content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_total": 100, "quota_remaining": 10}]})
        if req.url.path == "/17841400000000001/media":
            return httpx.Response(200, json={"id": "container_s"})
        if req.url.path == "/container_s":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if req.url.path == "/17841400000000001/media_publish":
            return httpx.Response(200, json={"id": "media_s"})
        if req.url.path == "/media_s":
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/s/"})
        raise AssertionError(f"Unexpected: {req.url}")

    ig = _setup_mock_ig(monkeypatch, handler)
    _connect_sample_account(ig)
    _create_sample_todo_video()

    due_iso = "2020-01-01T00:00:00Z"  # quá khứ nhưng là scheduled row (validate chỉ chặn request)
    app_main.store.execute(
        "INSERT INTO ig_publishes (id,item_id,target_type,account_id,caption,publish_at,status,created_at,updated_at) "
        "VALUES ('igp_sched', ?, 'todo', 'iga_test1', 'x', ?, 'scheduled', ?, ?)",
        (ITEM_ID, due_iso, time.time(), time.time()),
    )
    # sweep -> scheduler sẽ nhả scheduled đến giờ; gọi trực tiếp logic nhả việc
    from datetime import datetime, timezone
    due = app_main.store.query(
        "SELECT id FROM ig_publishes WHERE status='scheduled' AND publish_at <= ?",
        (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),),
    )
    assert len(due) == 1
    app_main.store.execute(
        "UPDATE ig_publishes SET status='queued', updated_at=? WHERE id='igp_sched'", (time.time(),)
    )
    ig._run_publish("igp_sched")  # chạy đồng bộ
    row = app_main.store.query_one("SELECT status, ig_media_id FROM ig_publishes WHERE id='igp_sched'")
    assert row["status"] == "published"
    assert row["ig_media_id"] == "media_s"


def test_startup_sweep_resumes_container(client, ig_env, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/17841400000000001/content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_total": 100, "quota_remaining": 10}]})
        if req.url.path == "/container_resume":
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if req.url.path == "/17841400000000001/media_publish":
            return httpx.Response(200, json={"id": "media_r"})
        if req.url.path == "/media_r":
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/r/"})
        raise AssertionError(f"Unexpected: {req.url}")

    ig = _setup_mock_ig(monkeypatch, handler)
    _connect_sample_account(ig)
    _create_sample_todo_video()
    app_main.store.execute(
        "INSERT INTO ig_publishes (id,item_id,target_type,account_id,caption,container_id,status,created_at,updated_at) "
        "VALUES ('igp_resume', ?, 'todo', 'iga_test1', 'x', 'container_resume', 'processing', ?, ?)",
        (ITEM_ID, time.time(), time.time()),
    )
    resumed = ig.startup_sweep(run_async=False)
    assert "igp_resume" in resumed
    row = app_main.store.query_one("SELECT status FROM ig_publishes WHERE id='igp_resume'")
    assert row["status"] == "published"


# -- affiliate: attach + redirect + click + bio ----------------------------------

def test_affiliate_attach_redirect_click(client, admin, read_key, aff_env, monkeypatch):
    svc = _setup_mock_aff(monkeypatch, lambda req: (_ for _ in ()).throw(AssertionError("no http")))
    app_main.store.execute("DELETE FROM affiliate_links")
    app_main.store.execute("DELETE FROM affiliate_products")
    _create_sample_todo_video()
    link = _attach_clickbank_product(svc, ITEM_ID)
    assert link["url"].startswith("https://vidfac.gacdero.vn/b/")
    assert link["deeplink"].startswith("https://mystore.vendor99.hop.clickbank.net/?tid=")

    # attach lại cùng product+item -> trả lại link cũ, không sinh slug mới
    link2 = svc.attach_product(ITEM_ID, link["product_id"])
    assert link2["id"] == link["id"]

    r = client.get(f"/b/{link['slug']}", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == link["deeplink"]
    row = app_main.store.query_one("SELECT clicks FROM affiliate_links WHERE id=?", (link["id"],))
    assert row["clicks"] == 1

    r404 = client.get("/b/zzzzzzzz", follow_redirects=False)
    assert r404.status_code == 404

    # API list links + event
    rl = client.get(f"/api/v1/affiliate/links?item_id={ITEM_ID}", headers=read_key)
    assert rl.status_code == 200
    assert len(rl.json()["links"]) == 1
    ev = app_main.store.query_one(
        "SELECT * FROM events WHERE type='affiliate.link.clicked' ORDER BY at DESC LIMIT 1")
    assert ev is not None
    assert ev["item_id"] == ITEM_ID


def test_bio_page_and_public_render(client, admin, read_key, aff_env, monkeypatch):
    svc = _setup_mock_aff(monkeypatch, lambda req: (_ for _ in ()).throw(AssertionError("no http")))
    app_main.store.execute("DELETE FROM affiliate_links")
    app_main.store.execute("DELETE FROM affiliate_products")
    link = _attach_clickbank_product(svc, ITEM_ID)

    r_put = client.put("/api/v1/bio/pages/main", headers=admin,
                       json={"title": "My Store", "items": [{"label": "Top Deal", "slug": link["slug"]}]})
    assert r_put.status_code == 200
    assert r_put.json()["title"] == "My Store"

    r_get = client.get("/api/v1/bio/pages/main", headers=read_key)
    assert r_get.status_code == 200

    r_html = client.get("/bio/main")
    assert r_html.status_code == 200
    assert "My Store" in r_html.text
    assert f"/b/{link['slug']}" in r_html.text

    # auto bơm link chưa nằm trong manual list
    link_b = _attach_clickbank_product(svc, "item_biotest0002")
    if link_b["id"] != link["id"]:
        r_html2 = client.get("/bio/main")
        assert f"/b/{link_b['slug']}" in r_html2.text


# -- conversions sync ------------------------------------------------------------

def test_sync_clickbank_maps_subid_to_item(client, aff_env, monkeypatch):
    svc = _setup_mock_aff(monkeypatch, lambda req: (_ for _ in ()).throw(AssertionError("no http")))
    _create_sample_todo_video()
    link = _attach_clickbank_product(svc, ITEM_ID)
    subid = link["subid"]
    monkeypatch.setenv("CLICKBANK_API_KEY", "cb-api-key")

    xml = f"""<?xml version="1.0"?>
    <transactions>
      <transaction><transid>T1</transid><tid>{subid}</tid><amount>48.52</amount><currency>USD</currency><status>CLEARED</status><transdate>2026-11-18</transdate><title>Test Course</title></transaction>
      <transaction><transid>T2</transid><tid></tid><amount>12.00</amount><currency>USD</currency><status>PENDING</status><transdate>2026-11-18</transdate></transaction>
      <transaction><transid>T1</transid><tid>{subid}</tid><amount>48.52</amount><currency>USD</currency><status>CLEARED</status><transdate>2026-11-18</transdate></transaction>
    </transactions>"""

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "mock-cb.test"
        assert req.headers["Authorization"] == "cb-api-key"
        return httpx.Response(200, text=xml)

    transport = httpx.MockTransport(handler)
    svc2 = AffiliateService(app_main.store, app_main.webhooks,
                            http_factory=lambda: httpx.Client(transport=transport, timeout=30.0))
    monkeypatch.setattr("backend.app.service_affiliate.clickbank_api_base", lambda: "https://mock-cb.test")

    res = svc2.sync_clickbank(days=7)
    assert res["new"] == 2  # T1 + T2; T1 trùng bị bỏ qua

    rows = app_main.store.query("SELECT * FROM affiliate_conversions WHERE network='clickbank' ORDER BY network_txn_id")
    assert len(rows) == 2
    t1 = next(r for r in rows if r["network_txn_id"] == "T1")
    assert t1["item_id"] == ITEM_ID  # map subid -> item
    assert abs(t1["amount"] - 48.52) < 0.01

    # sync lại -> không tăng (idempotent theo network_txn_id)
    res2 = svc2.sync_clickbank(days=7)
    assert res2["new"] == 0


def test_sync_skipped_without_keys(client, monkeypatch):
    monkeypatch.delenv("CLICKBANK_API_KEY", raising=False)
    monkeypatch.delenv("EBAY_DEV_KEY", raising=False)
    svc = AffiliateService(app_main.store, app_main.webhooks)
    assert svc.sync_clickbank()["skipped"]
    assert svc.sync_ebay()["skipped"]
