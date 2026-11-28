"""FastAPI wiring — the single door (plan §1.6).

Every POST accepts Idempotency-Key; replays return the first response.
Every mutation writes events; matching webhooks are dispatched.
"""
from __future__ import annotations

import html
import hashlib
import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import domain
from .auth import SESSION_COOKIE, SESSION_TTL_SEC, Auth, webauth
from .config import db_path, data_dir, RENDER_SPEC, AUDIO_CONFIG, REPO_ROOT
from .pipeline_en.script_generator_en import llm_status
from .providers import AdapterNotConfigured, registry
from .schemas import (AudioRegenerateRequest, BatchCreate, BgmTrackSubmitJson,
                      CharacterSheetCreate,
                      ConfirmAssembleRequest, FeedbackBatchCreate, FeedbackItemCreate,
                      FeedbackResolveRequest, GateDecision, GenerateRequest,
                      HoldRequest, ItemCreate, KnowledgeCreateRequest,
                      KnowledgeForkRequest, KnowledgePatchRequest,
                      KnowledgePublishRequest, KnowledgeSyncRequest,
                      KnowledgeUpdateRequest,
                      ReadMeCreateRequest, ReadMePatchRequest,
                      ReadMePutRequest, ShortAudioIngest, ShortCreate, ShortScriptUpdate,
                      ShotChecklistBatchUpdate,
                      ShotCheckUpdate, ShotImport, SyncGdriveRequest,
                      TodoCollectReferencesRequest, TodoFromScriptCreate,
                      TodoFromYoutubeCreate, TodoImageSubmitJson,
                      ShortClipSubmitJson, TodoImportPackageCreate, TodoVideoSubmitJson,
                      WebLoginRequest, WebhookRegister,
                      YouTubePublishRequest, YouTubeVideoPatchRequest)
from .service_assembler import AssemblerService, ShortfallWarningError, ThumbnailMissingError
from .service_news import (dismiss_news, list_news, push_selected as push_selected_news,
                           run_once as news_run_once, start_news_scheduler)
from .service_digest import (get_digest_status, get_digest_submission,
                             get_digest_status_today, run_daily_digest,
                             start_digest_scheduler, submit_digest_stories,
                             approve_digest, reject_digest)
from .service_telegram_bot import start_telegram_bot
from .storage_gdrive import check_storage_health, cleanup_item_intermediates, sync_item_to_gdrive

from .service_batches import BatchService
from .service_bgm import BgmService
from .service_board import BoardError, BoardService, defect_codes_public
from .service_proposals import ProposalError, ProposalService
from .service_import import ImportGate, ImportRejected, sha256_file
from .service_items import ItemService
from .service_readme import ReadMeService
from .service_knowledge import KnowledgeError, KnowledgeService
from .service_shots import ShotService
from .service_shorts import ShortService, ShortfallError
from .service_todos import TodoService
from .service_youtube import YouTubeConflictError, YouTubeError, YouTubeService
from .service_instagram import InstagramConflictError, InstagramError, InstagramService
from .service_affiliate import (
    AffiliateError,
    AffiliateService,
    set_affiliate_service,
    start_affiliate_sync_scheduler,
)
from .schemas import (AffiliateProductCreate, AffiliateProductPatch, BioPageUpdate,
                      BriefCreate, BriefCriticSet, BriefDecisionCreate, DigestSubmitBody,
                      InstagramPublishRequest, ScriptFindingCreate,
                      ScriptProposalCreate, ScriptSignatureCreate)
from .service_chat import ChatService, ChatError
from .service_briefs import BriefError, BriefService
from .service_script_duel import ScriptDuelError, ScriptDuelService
from .agreement_notifier import AgreementNotifier
from .store import SQLiteStore, new_id, now
from .webhooks import WebhookService

logger = logging.getLogger("video_system.main")

app = FastAPI(title="video-system", version="0.2.0",
              description="EN-Only TODO & External Video Assembly Pipeline (plan v3.2)")
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
store = SQLiteStore(db_path())
items = ItemService(store)
chat = ChatService(store)
briefs = BriefService(store)
duel = ScriptDuelService(store, briefs)
shots = ShotService(store, items)
batches = BatchService(store, items, shots)
importer = ImportGate(store, items)
todos = TodoService(store, items)
board = BoardService(store, items)
proposals = ProposalService(store, briefs)
assembler = AssemblerService(store, items, todos)
webhooks = WebhookService(store)
# next_action của agreement đổi -> phát webhook (dedupe theo brief_agreement_state).
# `duel` tạo ở trên nên truyền thẳng callable, không cần import service trong notifier.
agreement_notifier = AgreementNotifier(store, webhooks, agreement_fn=duel.agreement)
auth = Auth()

# -- Short Studio adapters (Phụ lục G.4): fail-closed, chỉ wire khi có key ----
_XKIRO_KEY = os.environ.get("VIDEO_XKIRO_API_KEY", "").strip()
if _XKIRO_KEY:
    from .adapters.image_xkiro import XkiroImageAdapter
    from .adapters.llm_xkiro import XkiroLlmAdapter
    registry.llm = XkiroLlmAdapter(_XKIRO_KEY)
    registry.image_gen = XkiroImageAdapter(_XKIRO_KEY)
    logger.info("xkiro adapters wired (llm=%s image=%s)",
                registry.llm.model, registry.image_gen.model)
else:
    logger.info("VIDEO_XKIRO_API_KEY unset — Short Studio LLM/image adapters fail-closed")

shorts = ShortService(store, webhooks)
youtube = YouTubeService(store, webhooks)
instagram = InstagramService(store, webhooks)
affiliate = AffiliateService(store, webhooks)
set_affiliate_service(affiliate)
bgm_service = BgmService(store)
readme_service = ReadMeService()
knowledge = KnowledgeService(store)


@app.on_event("startup")
def _start_news_scheduler() -> None:
    # Phụ lục G.10 — hộp tin nóng: chỉ bật khi VIDEO_NEWS_SYNC_ENABLED=1.
    start_news_scheduler(store)
    start_digest_scheduler(store, shorts, youtube_svc=youtube)
    youtube.startup_sweep()
    instagram.startup_sweep()
    start_affiliate_sync_scheduler(store, webhooks)
    # Daily Brief human-in-the-loop: chốt /approve /reject /status từ Telegram
    start_telegram_bot(store, shorts, youtube_svc=youtube, proposals_svc=proposals)
    # Muse proposals: quét awaiting_owner quá hạn mỗi giờ (mặc định bật)
    proposals.start_expire_scheduler()
    # Bảng chat agent: dọn tin cũ theo retention (mặc định 30 ngày)
    chat.start_retention_worker()
    # Brief: tự duyệt objection-window trong giờ làm việc (scheduler, không read-path)
    briefs.start_objection_scheduler()
    # Bắt các chuyển next_action do scheduler/auto-approve, không chỉ route API
    agreement_notifier.start_sweeper(30)
    # Webhook: gửi lại delivery đã tới hạn (backoff theo BACKOFF trong webhooks.py)
    webhooks.start_retry_worker(60)

# -- Web PIN gate (chặn mọi request không có session/bearer hợp lệ) -----------
# Mở: health, 3 endpoint auth/*, OPTIONS (preflight), "/favicon.ico", và "/" (shell HTML chứa
# màn hình khóa — dữ liệu vẫn nằm sau API được bảo vệ).
# Ngoại lệ prefix: /b/{slug} + /bio/{handle} (trang công khai cho bio TikTok/IG),
# /api/v1/instagram/media/ (signed URL để IG pull video), callback OAuth IG.
_WEB_OPEN_PATHS = {"/", "/todos", "/shorts", "/youtube", "/publish", "/chat",
                   "/library", "/favicon.ico", "/api/v1/health",
                   "/api/v1/auth/login", "/api/v1/auth/session", "/api/v1/auth/logout",
                   "/api/v1/youtube/auth/callback",
                   "/api/v1/instagram/auth/callback"}
_WEB_OPEN_PREFIXES = ("/b/", "/bio/", "/api/v1/instagram/media/", "/static/")


@app.middleware("http")
async def pin_gate(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path in _WEB_OPEN_PATHS:
        resp = await call_next(request)
        if path.startswith("/static/"):
            # JS/CSS tách module: luôn lấy bản mới (tránh heuristic cache
            # khiến agent chạy code cũ sau mỗi lần deploy frontend)
            resp.headers["Cache-Control"] = "no-store"
        return resp
    if any(path.startswith(p) for p in _WEB_OPEN_PREFIXES):
        resp = await call_next(request)
        if path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp
    if webauth.request_allowed(request):
        resp = await call_next(request)
        if path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp
    return JSONResponse({"detail": "Yêu cầu đăng nhập — nhập mã PIN để tiếp tục",
                         "code": "pin_required"}, status_code=401)


# -- auth: PIN login -----------------------------------------------------------

@app.post("/api/v1/auth/login")
def web_login(body: WebLoginRequest, request: Request):
    """Đăng nhập bằng PIN owner (`pin`) hoặc mã 6 số của agent (`code`).

    Mã 6 số KHÔNG dùng trực tiếp làm API key: nó chỉ đổi lấy một token dài ký
    HMAC. Không gian 10^6 tổ hợp mà endpoint này nằm trên domain public, nên
    mã chỉ an toàn khi đi kèm khoá sau 5 lần sai (login_with_code/verify_pin).
    """
    ip = webauth.client_ip(request)
    if body.code:
        identity, scope = webauth.login_with_code(body.code, ip)
    elif body.pin:
        webauth.verify_pin(body.pin, ip)
        identity, scope = "owner", "admin"
    else:
        raise HTTPException(422, "Cần 'pin' (owner) hoặc 'code' (agent).")
    token = webauth.make_token(identity, scope)
    is_https = (request.url.scheme == "https") or (
        request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    resp = JSONResponse({"authenticated": True,
                         "identity": identity,
                         "scope": scope,
                         "token": token,
                         "expires_in_sec": SESSION_TTL_SEC})
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SEC,
                    httponly=True, samesite="lax",
                    secure=is_https, path="/")
    return resp


@app.get("/api/v1/auth/session")
def web_session(request: Request):
    return {"authenticated": webauth.session_ok(request)}


@app.get("/api/v1/auth/whoami")
def auth_whoami(request: Request):
    """Tôi là ai và có quyền gì — để agent tự kiểm key/mã trước khi chạy hàng loạt."""
    caller, scope = auth.identity_for(request)
    return {"identity": caller, "scope": scope}


@app.post("/api/v1/auth/logout")
def web_logout():
    resp = JSONResponse({"authenticated": False})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


def _idempotent(request: Request, payload: dict, status_code: int = 200):
    """Store/replay responses for POST Idempotency-Key (§1.6)."""
    key = request.headers.get("Idempotency-Key")
    if not key:
        return JSONResponse(payload, status_code=status_code)
    row = store.query_one("SELECT * FROM idempotency WHERE key=?", (key,))
    if row:
        resp = JSONResponse(json.loads(row["response_json"]),
                            status_code=row["status_code"])
        resp.headers["Idempotency-Replayed"] = "true"
        return resp
    resp = JSONResponse(payload, status_code=status_code)
    if 200 <= status_code < 300:
        store.execute(
            "INSERT OR IGNORE INTO idempotency (key,method,path,status_code,"
            "response_json,created_at) VALUES (?,?,?,?,?,?)",
            (key, request.method, request.url.path, status_code,
             json.dumps(payload, ensure_ascii=False), now()))
    return resp


IDEMPOTENCY_STALE_SEC = 300


def _idempotency_scope(request: Request) -> str | None:
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    try:
        identity, _ = auth.identity_for(request)
    except HTTPException:
        identity = "anon"
    return f"{identity}:{key}"


def _payload_hash(payload: dict | None) -> str | None:
    if payload is None:
        return None
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check_replay_match(row: dict, request: Request, req_hash: str | None) -> None:
    """Cùng key mà khác method/path/body -> 422, không im lặng trả response cũ."""
    stored = row.get("request_hash")
    if not stored or not req_hash:
        return  # bản ghi cũ trước migration: không so được, chấp nhận replay
    if row["method"] != request.method or row["path"] != request.url.path \
            or stored != req_hash:
        raise ApiError(
            422, "Idempotency-Key đã dùng cho request khác (method/path/body không khớp).",
            "idempotency_key_reused")


def _idempotent_call(
    request: Request,
    payload: dict,
    fn,
    status_code: int = 201,
    *,
    replay_status_200: bool = False,
    add_replay_flag: bool = False,
    reject_cross_caller: bool = False,
    preflight=None,
):
    """Chiếm Idempotency-Key TRƯỚC khi chạy nghiệp vụ, chốt kết quả sau.

    Vì sao không phải "chạy xong rồi INSERT": hai request song song cùng key đều
    không thấy row, cùng chạy nghiệp vụ, cùng INSERT OR IGNORE -> sinh 2 brief.

    Quyền sở hữu key là `claim_token`, không phải `status_code`:
    - INSERT OR IGNORE mang theo token của mình -> ai insert được thì chạy.
    - Row `status_code=0` quá `IDEMPOTENCY_STALE_SEC` (process bị kill) được reclaim
      bằng UPDATE có `WHERE claim_token = <token cũ>` — đây là CAS thật: 2 reclaimer
      đọc cùng token cũ, chỉ 1 UPDATE khớp, người kia nhận 409.
    - Finalize/delete đều kèm `WHERE claim_token = <token của mình>`, nên owner cũ
      không thể ghi đè kết quả của người đã reclaim.
    """
    scoped = _idempotency_scope(request)
    if not scoped:
        # Không có key thì không có replay để lo, nhưng preflight vẫn phải chạy.
        if preflight is not None:
            preflight()
        return JSONResponse(fn(), status_code=status_code)

    if reject_cross_caller:
        raw_key = request.headers.get("Idempotency-Key")
        if raw_key:
            other = store.query_one(
                "SELECT 1 AS ok FROM idempotency WHERE key LIKE ? AND key != ?",
                (f"%:{raw_key}", scoped),
            )
            if other:
                raise ApiError(
                    409,
                    "Idempotency-Key đã được agent khác sử dụng.",
                    "idempotency_key_conflict",
                )

    req_hash = _payload_hash(payload)
    token = uuid.uuid4().hex
    ts = now()
    owned = store.execute(
        "INSERT OR IGNORE INTO idempotency (key,method,path,status_code,response_json,"
        "created_at,request_hash,claim_token) VALUES (?,?,?,0,'{}',?,?,?)",
        (scoped, request.method, request.url.path, ts, req_hash, token)) == 1

    if not owned:
        row = store.query_one("SELECT * FROM idempotency WHERE key=?", (scoped,))
        if row and int(row["status_code"] or 0) == 0:
            stale = (ts - float(row["created_at"] or 0)) > IDEMPOTENCY_STALE_SEC
            won = stale and store.cas_update(
                "UPDATE idempotency SET claim_token=?, created_at=?, request_hash=? "
                "WHERE key=? AND status_code=0 AND COALESCE(claim_token,'')=?",
                (token, ts, req_hash, scoped, row.get("claim_token") or ""))
            if not won:
                raise ApiError(
                    409, "Request cùng Idempotency-Key đang được xử lý — thử lại sau.",
                    "idempotency_in_progress")
            # reclaim thắng -> tiếp tục như owner
        elif row:
            _check_replay_match(row, request, req_hash)
            body = json.loads(row["response_json"])
            if add_replay_flag and isinstance(body, dict):
                body["idempotent_replay"] = True
            r_status = 200 if replay_status_200 else row["status_code"]
            resp = JSONResponse(body, status_code=r_status)
            resp.headers["Idempotency-Replayed"] = "true"
            return resp
        else:
            raise ApiError(409, "Không chiếm được Idempotency-Key — thử lại.",
                           "idempotency_in_progress")

    try:
        # `preflight` chạy SAU khi đã claim key và SAU khi replay đã thoát ở trên:
        # - replay không bao giờ bị validate lại trên state mà lần đầu vừa đổi;
        # - request đầu tiên vẫn bị từ chối trước khi `fn` đọc media.
        if preflight is not None:
            preflight()
        result = fn()
    except BaseException:
        # Không cache lỗi: nhả key để agent retry được (kể cả KeyboardInterrupt).
        _release_idempotency(scoped, token)
        raise
    try:
        store.execute(
            "UPDATE idempotency SET status_code=?, response_json=?, claim_token=NULL "
            "WHERE key=? AND claim_token=?",
            (status_code, json.dumps(result, ensure_ascii=False, default=str), scoped, token))
    except sqlite3.OperationalError:
        _release_idempotency(scoped, token)
        raise
    return JSONResponse(result, status_code=status_code)


def _release_idempotency(scoped: str, token: str) -> None:
    """Nhả key — chỉ xoá row của chính mình, không xoá claim của người khác."""
    try:
        store.execute("DELETE FROM idempotency WHERE key=? AND claim_token=?",
                      (scoped, token))
    except Exception:  # noqa: BLE001 — DB chết thì để stale reclaim dọn
        logger.warning("không nhả được idempotency key %s", scoped)


@app.get("/api/v1/health")
def health():
    db_ok = store.query_one("SELECT 1 AS ok")["ok"] == 1
    llm = llm_status()
    return {"status": "ok" if db_ok else "degraded", "db": db_ok,
            "style_lock": RENDER_SPEC["version"],
            "audio_loudness_lufs": AUDIO_CONFIG["master"]["loudness_lufs"],
            "auth_configured": not auth.keys == {},
            "engine": llm["engine"],
            "llm": llm}


@app.api_route("/favicon.ico", methods=["GET", "HEAD"])
def favicon():
    return Response(status_code=204)


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    # MPA: mỗi feature 1 trang — / redirect về trang Điều phối chính
    return RedirectResponse("/todos", status_code=307)


def _page(fname: str):
    return FileResponse(
        FRONTEND_DIR / fname,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.api_route("/todos", methods=["GET", "HEAD"])
def todos_page():
    return _page("todos.html")


@app.api_route("/shorts", methods=["GET", "HEAD"])
def shorts_page():
    return _page("shorts.html")


@app.api_route("/youtube", methods=["GET", "HEAD"])
def youtube_page():
    return _page("youtube.html")


@app.api_route("/publish", methods=["GET", "HEAD"])
def publish_page():
    return _page("publish.html")


@app.api_route("/chat", methods=["GET", "HEAD"])
def chat_page():
    return _page("chat.html")


@app.api_route("/library", methods=["GET", "HEAD"])
def library_page():
    return FileResponse(
        FRONTEND_DIR / "library.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


# -- items -------------------------------------------------------------------

@app.get("/api/v1/items")
def list_items(status: str | None = None, q: str | None = None, page: int = 1,
               request: Request = None):
    auth.require(request, "read")
    return {"items": items.list(status=status, q=q, page=page)}


@app.post("/api/v1/items")
def create_item(body: ItemCreate, request: Request = None):
    auth.require(request, "ingest")
    src = body.source or {}
    url = src.get("url", "")
    from .service_import import fetch_url  # allowlist guard applied at ingest time
    if src.get("type") in {"youtube_url", "drive_url"} and url:
        import urllib.parse
        host = urllib.parse.urlsplit(url).hostname or ""
        from .config import allowed_hosts
        if host not in allowed_hosts():
            raise HTTPException(422, f"url host not allowed: {host}")
    item = items.create(body.title, src.get("type", "upload"),
                        body.target.get("language"), body.target.get("style_lock_version", "v3"))
    items.event(item["id"], None, "item.source", "api", src)
    return _idempotent(request, {"item_id": item["id"], "status": item["status"],
                                 "detail_url": f"/items/{item['id']}"}, 201)


@app.get("/api/v1/items/{item_id}")
def get_item(item_id: str, request: Request = None):
    auth.require(request, "read")
    item = items.get(item_id)
    if not item or item["status"] == "deleted":
        raise HTTPException(404, "item not found")
    item["gates"] = items.gates(item_id)
    return item


@app.delete("/api/v1/items/{item_id}")
def delete_item(item_id: str, request: Request = None):
    auth.require(request, "admin")
    if not items.get(item_id):
        raise HTTPException(404, "item not found")
    try:
        item = items.soft_delete(item_id)
    except domain.TransitionError as e:
        raise HTTPException(409, str(e))
    return _idempotent(request, {"item_id": item_id, "status": item["status"]})


@app.get("/api/v1/items/{item_id}/activity")
def item_events(item_id: str, request: Request = None):
    auth.require(request, "read")
    return {"events": items.events(item_id)}


# -- shots import ---------------------------------------------------------------

@app.post("/api/v1/items/{item_id}/shots/import")
def import_shots(item_id: str, body: ShotImport, request: Request = None):
    auth.require(request, "ingest")
    audio = body.audio or {}
    path = audio.get("file_path")
    try:
        result = importer.import_shots(
            item_id, body.model_dump(),
            Path(path) if path else None, audio.get("file_url"))
    except ImportRejected as e:
        return JSONResponse(e.report, status_code=e.status_code)
    except KeyError:
        raise HTTPException(404, "item not found")
    return _idempotent(request, result, 201)


@app.get("/api/v1/items/{item_id}/shots")
def list_shots(item_id: str, status: str | None = None, request: Request = None):
    auth.require(request, "read")
    return {"shots": shots.list(item_id, status=status)}


def _shot_in_item(item_id: str, shot_id: str) -> dict:
    """Chống IDOR (red-team P0): shot phải thuộc đúng item trong path."""
    s = shots.get(shot_id)
    if not s or s["item_id"] != item_id:
        raise HTTPException(404, "shot not found in this item")
    return s


@app.post("/api/v1/items/{item_id}/shots/{shot_id}/retry")
def retry_shot(item_id: str, shot_id: str, request: Request = None):
    auth.require(request, "ingest")
    _shot_in_item(item_id, shot_id)
    try:
        s = shots.retry_held(shot_id)
    except domain.TransitionError as e:
        raise HTTPException(409, str(e))
    return {"shot": s}


@app.post("/api/v1/items/{item_id}/shots/{shot_id}/hold")
def hold_shot(item_id: str, shot_id: str, body: HoldRequest, request: Request = None):
    auth.require(request, "ingest")
    _shot_in_item(item_id, shot_id)
    try:
        s = shots.hold_manual(shot_id, body.reason, body.detail)
    except (domain.TransitionError, ValueError) as e:
        raise HTTPException(409, str(e))
    return {"shot": s}


@app.post("/api/v1/items/{item_id}/shots/{shot_id}/keyframe/approve")
def approve_keyframe(item_id: str, shot_id: str, request: Request = None):
    auth.require(request, "ingest")
    _shot_in_item(item_id, shot_id)
    try:
        return {"shot": shots.approve_keyframe(shot_id)}
    except domain.TransitionError as e:
        raise HTTPException(409, str(e))


@app.post("/api/v1/items/{item_id}/shots/{shot_id}/generate")
def generate_shot(item_id: str, shot_id: str, request: Request = None,
                  body: GenerateRequest | None = None):
    auth.require(request, "ingest")
    _shot_in_item(item_id, shot_id)
    try:
        s = shots.generate(shot_id, actor=(body.actor if body else "worker"))
    except domain.TransitionError as e:
        raise HTTPException(409, str(e))
    return {"shot": s}


@app.post("/api/v1/items/{item_id}/shots/{shot_id}/qc")
def qc_shot(item_id: str, shot_id: str, request: Request = None):
    auth.require(request, "ingest")
    _shot_in_item(item_id, shot_id)
    try:
        s = shots.run_qc(shot_id)
    except (domain.TransitionError, ValueError) as e:
        raise HTTPException(409, str(e))
    return {"shot": s}


# -- batches ----------------------------------------------------------------

@app.post("/api/v1/items/{item_id}/batches")
def create_batch(item_id: str, body: BatchCreate, request: Request = None):
    auth.require(request, "ingest")
    try:
        return _idempotent(request, batches.create(item_id, body.size), 201)
    except domain.TransitionError as e:
        raise HTTPException(409, str(e))


@app.post("/api/v1/items/{item_id}/batches/{batch_id}/approve")
def approve_batch(item_id: str, batch_id: str, request: Request = None):
    auth.require(request, "admin")  # approve is admin-only (§7.1)
    try:
        return _idempotent(request, batches.approve(batch_id))
    except domain.TransitionError as e:
        raise HTTPException(409, str(e))


@app.get("/api/v1/items/{item_id}/cumulative")
def get_cumulative(item_id: str, request: Request = None):
    auth.require(request, "read")
    return batches.cumulative(item_id)


# -- gates / webhooks ----------------------------------------------------------

@app.post("/api/v1/items/{item_id}/approve")
def approve_gate(item_id: str, body: GateDecision, request: Request = None):
    auth.require(request, "admin")
    try:
        return {"gates": items.approve(item_id, body.gate, body.decision,
                                       body.reason, "api", body.version)}
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))


@app.post("/api/v1/webhooks")
def register_webhook(body: WebhookRegister, request: Request = None):
    auth.require(request, "admin")
    try:
        wh = webhooks.register(body.url, body.secret, body.events, body.item_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"webhook_id": wh["id"]}


# internal event dispatch hook — maps internal event types to the webhook
# contract of Appendix E (audit P0: emitter names ≠ webhook names before this)
_orig_event = items.event
_WF_EVENTS = {"item.analyzed", "item.awaiting_review", "shot.held",
              "shot.appended", "item.ready", "item.failed",
              "item.todo", "item.completed",
              "short.job_ready", "short.clip_submitted", "short.ready"}

def _map_webhook_type(type_: str, payload: dict) -> str | None:
    if type_ in _WF_EVENTS:
        return type_
    if type_ == "shot.status":
        to = payload.get("to")
        if to == "held":
            return "shot.held"
        if to == "appended":
            return "shot.appended"
        return None
    if type_ == "item.status":
        to = payload.get("to")
        if to == "awaiting_review":
            return "item.awaiting_review"
        if to == "ready":
            return "item.ready"
        if to == "failed":
            return "item.failed"
        if to == "todo":
            return "item.todo"
        if to == "completed":
            return "item.completed"
        return None
    return None

def _event_with_webhooks(item_id, shot_id, type_, actor, payload):
    eid = _orig_event(item_id, shot_id, type_, actor, payload)
    row = store.query_one("SELECT * FROM events WHERE id=?", (eid,))
    wt = _map_webhook_type(type_, payload or {})
    if row and wt:
        webhooks.dispatch_event({**dict(row), "type": wt})
    return eid

items.event = _event_with_webhooks  # type: ignore[method-assign]


# -- character sheets (§4.B.2) ---------------------------------------------------

@app.post("/api/v1/items/{item_id}/characters")
def create_character(item_id: str, body: CharacterSheetCreate, request: Request = None):
    auth.require(request, "ingest")
    if not items.get(item_id):
        raise HTTPException(404, "item not found")
    import hashlib as _hl
    from .service_import import sha256_file as _sha
    ref_path = Path(body.reference_path).resolve()
    sandbox = data_dir().resolve()
    if sandbox not in ref_path.parents:
        raise HTTPException(422, "reference_path must live under VIDEO_DATA_DIR")
    aid = new_id("ast")
    store.execute(
        "INSERT INTO assets (id,item_id,kind,storage_key,sha256,provenance_engine,"
        "created_at) VALUES (?,?,?,?,?,?,?)",
        (aid, item_id, "image",
         f"items/{item_id}/characters/{body.name}.png",
         _sha(ref_path), "imported", now()))
    version = (store.query_one(
        "SELECT MAX(version) m FROM character_sheets WHERE item_id=? AND name=?",
        (item_id, body["name"] if isinstance(body, dict) else body.name))["m"] or 0) + 1
    cid = new_id("chr")
    store.execute(
        "INSERT INTO character_sheets (id,item_id,version,name,role,"
        "reference_asset_id,hash,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, item_id, version, body.name, body.role, aid,
         _hl.sha256(f"{item_id}:{body.name}:v{version}".encode()).hexdigest()[:16],
         "draft", now()))
    items.event(item_id, None, "character.created", "api",
                {"name": body.name, "version": version})
    return {"character_id": cid, "version": version, "status": "draft"}


@app.get("/api/v1/items/{item_id}/characters")
def list_characters(item_id: str, request: Request = None):
    auth.require(request, "read")
    return {"characters": store.query(
        "SELECT * FROM character_sheets WHERE item_id=? ORDER BY name, version",
        (item_id,))}


# -- EN-only TODO & External Video Assembly (§Phase 1–3) --------------------

@app.post("/api/v1/todos/from-script")
@app.post("/api/v1/items/from-script")
def create_todo_from_script(body: TodoFromScriptCreate, request: Request = None):
    auth.require(request, "ingest")
    try:
        pkg = todos.create_from_script(
            title=body.title,
            script_text=body.script_text,
            shots=body.shots,
            bgm_enabled=body.bgm_enabled,
            voice=body.voice,
            collect_references=body.collect_references,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"failed to create EN todo from script: {e}")
    return _idempotent(request, pkg, 201)


@app.post("/api/v1/todos/from-youtube")
@app.post("/api/v1/items/from-youtube")
def create_todo_from_youtube(body: TodoFromYoutubeCreate, request: Request = None):
    auth.require(request, "ingest")
    try:
        pkg = todos.create_from_youtube(
            youtube_url=body.youtube_url,
            title=body.title,
            target_duration_sec=body.target_duration_sec,
            bgm_enabled=body.bgm_enabled,
            voice=body.voice,
            transcript_segments=body.transcript_segments,
            collect_references=body.collect_references,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"failed to create EN todo from youtube: {e}")
    return _idempotent(request, pkg, 201)


@app.get("/api/v1/todos")
def list_todos(status: str | None = None, q: str | None = None, page: int = 1,
               request: Request = None):
    auth.require(request, "read")
    try:
        return {"todos": todos.list_todos(status=status, q=q, page=page)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list todos (status=%s, q=%s, page=%s)", status, q, page)
        raise HTTPException(500, f"Lỗi máy chủ khi tải danh sách công việc: {exc}")


@app.get("/api/v1/todos/{item_id}")
def get_todo(item_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return todos.get_todo(item_id)
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get todo %s", item_id)
        raise HTTPException(500, f"Lỗi máy chủ khi tải công việc {item_id}: {exc}")


@app.api_route("/api/v1/todos/{item_id}/audio", methods=["GET", "HEAD"])
def get_todo_audio(item_id: str, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = todos.get_audio_path(item_id)
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(
        p,
        media_type="audio/wav",
        filename=f"{item_id}_audio_en.wav",
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/api/v1/todos/{item_id}/script")
def get_todo_script(item_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        todo = todos.get_todo(item_id)
    except KeyError:
        raise HTTPException(404, "todo item not found")
    try:
        p = todos.get_script_path(item_id)
        data = json.loads(p.read_text(encoding="utf-8"))
        if "skill_bundle" not in data:
            from .pipeline_en.skill_bundle import get_skill_summary
            data["skill_bundle"] = get_skill_summary(item_id)
        data["shots"] = todo["shots"]
        data["shot_checklist"] = todo["shot_checklist"]
        data["video_progress"] = todo["video_progress"]
        return JSONResponse(data)
    except FileNotFoundError:
        return JSONResponse({
            "item_id": item_id,
            "title": todo["title"],
            "language": "en",
            "audio_duration_sec": todo["audio_duration_sec"],
            "total_shots": todo["total_shots"],
            "skill_bundle": todo["skill_bundle"],
            "shots": todo["shots"],
            "shot_checklist": todo["shot_checklist"],
            "video_progress": todo["video_progress"],
        })


@app.get("/api/v1/skills")
def get_global_skills(request: Request = None):
    auth.require(request, "read")
    return todos.get_skills(None)


@app.get("/api/v1/todos/{item_id}/skills")
@app.get("/api/v1/todos/{item_id}/skill")
def get_todo_skills(item_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return todos.get_skills(item_id)
    except KeyError:
        raise HTTPException(404, "todo item not found")


@app.post("/api/v1/todos/import-package")
def import_existing_todo_package(body: TodoImportPackageCreate, request: Request = None):
    auth.require(request, "ingest")
    try:
        pkg = todos.import_existing_package(
            title=body.title,
            audio_src_path=Path(body.audio_file_path),
            shots=body.shots,
            source_url=body.source_url,
            bgm_enabled=body.bgm_enabled,
            voice=body.voice,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"failed to import existing todo package: {e}")
    return _idempotent(request, pkg, 201)


@app.get("/api/v1/todos/{item_id}/images")
@app.get("/api/v1/todos/{item_id}/keyframes")
def list_todo_images(item_id: str, kind: str | None = None, request: Request = None):
    auth.require(request, "read")
    if not items.get(item_id):
        raise HTTPException(404, "todo item not found")
    imgs = todos.list_images(item_id, kind=kind)
    return {
        "item_id": item_id,
        "images": imgs,
        "count": len(imgs),
    }


@app.get("/api/v1/todos/{item_id}/references")
def list_todo_references(item_id: str, request: Request = None):
    auth.require(request, "read")
    if not items.get(item_id):
        raise HTTPException(404, "todo item not found")
    refs = todos.list_images(item_id, kind="reference")
    return {
        "item_id": item_id,
        "reference_images": refs,
        "count": len(refs),
    }


@app.post("/api/v1/todos/{item_id}/collect-references")
def collect_todo_references(
    item_id: str,
    body: TodoCollectReferencesRequest | None = None,
    request: Request = None,
):
    auth.require(request, "ingest")
    count = body.count if body else 15
    force = body.force if body else False
    try:
        return todos.collect_script_references(item_id=item_id, count=count, force=force)
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except Exception as e:
        raise HTTPException(422, f"failed to collect script references: {e}")


@app.post("/api/v1/todos/{item_id}/images")
@app.post("/api/v1/todos/{item_id}/keyframes")
async def submit_todo_image(item_id: str, request: Request):
    """Push a generated image (keyframe or character sheet) back onto the TODO item.

    Supports BOTH:
      1. `multipart/form-data` with fields:
         - `file` (UploadFile: PNG/JPG/WEBP image)
         - `kind` (optional: 'keyframe' | 'character' | 'scene', default 'keyframe')
         - `seq_num` (optional int)
         - `shot_index` (optional int)
         - `name` (optional string)
         - `prompt_used` (optional string)
         - `covered_shot_indexes` (optional JSON array or comma-separated ints)
         - `note` (optional string)
      2. `application/json` matching `TodoImageSubmitJson`:
         - `base64_data` OR `file_path` OR `file_url`
         - `kind`, `seq_num`, `shot_index`, `name`, `prompt_used`, `covered_shot_indexes`, `note`
    """
    auth.require(request, "ingest")
    ctype = (request.headers.get("content-type") or "").lower()
    try:
        if "multipart/form-data" in ctype:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(422, "multipart form must include 'file'")
            raw_bytes = await upload.read()
            kind = str(form.get("kind") or "keyframe").strip()
            seq_raw = form.get("seq_num")
            seq_num = int(seq_raw) if seq_raw not in (None, "") else None
            shot_raw = form.get("shot_index")
            shot_index = int(shot_raw) if shot_raw not in (None, "") else None
            name = str(form.get("name") or "") or None
            prompt_used = str(form.get("prompt_used") or "") or None
            cov_raw = form.get("covered_shot_indexes")
            covered: list[int] | None = None
            if cov_raw:
                cov_str = str(cov_raw).strip()
                if cov_str.startswith("["):
                    covered = [int(x) for x in json.loads(cov_str)]
                else:
                    covered = [int(x.strip()) for x in cov_str.split(",") if x.strip()]
            note = str(form.get("note") or "") or None
            res = await run_in_threadpool(
                todos.submit_image,
                item_id,
                kind=kind,
                seq_num=seq_num,
                shot_index=shot_index,
                name=name,
                file_bytes=raw_bytes,
                filename=getattr(upload, "filename", "keyframe.png"),
                prompt_used=prompt_used,
                covered_shot_indexes=covered,
                note=note,
            )
        else:
            payload = await request.json()
            body = TodoImageSubmitJson(**payload)
            res = await run_in_threadpool(
                todos.submit_image,
                item_id,
                kind=body.kind,
                seq_num=body.seq_num,
                shot_index=body.shot_index,
                name=body.name,
                base64_data=body.base64_data,
                file_path=Path(body.file_path) if body.file_path else None,
                file_url=body.file_url,
                prompt_used=body.prompt_used,
                covered_shot_indexes=body.covered_shot_indexes,
                note=body.note,
            )
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except ImportRejected as e:
        return JSONResponse(e.report, status_code=e.status_code)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return await run_in_threadpool(_idempotent, request, res, 201)


@app.api_route("/api/v1/todos/{item_id}/images/{seq_num}/file", methods=["GET", "HEAD"])
def get_todo_image_file(
    item_id: str, seq_num: int, kind: str = "keyframe", download: bool = False, request: Request = None
):
    auth.require(request, "read")
    try:
        p = todos.get_uploaded_image_path(item_id, seq_num, kind=kind)
    except KeyError:
        raise HTTPException(404, f"image {kind} #{seq_num} not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    suf = p.suffix.lower()
    mime = "image/jpeg" if suf in {".jpg", ".jpeg"} else ("image/webp" if suf == ".webp" else "image/png")
    return FileResponse(
        p,
        media_type=mime,
        filename=f"{item_id}_{kind}_{seq_num:04d}{suf}",
        content_disposition_type="attachment" if download else "inline",
    )


@app.delete("/api/v1/todos/{item_id}/images/{seq_num}")
def delete_todo_image(
    item_id: str, seq_num: int, kind: str = "keyframe", request: Request = None
):
    auth.require(request, "ingest")
    try:
        return todos.delete_image(item_id, seq_num, kind=kind)
    except KeyError:
        raise HTTPException(404, f"image {kind} #{seq_num} not found in item {item_id}")


@app.api_route("/api/v1/todos/{item_id}/subtitles.srt", methods=["GET", "HEAD"])
def get_todo_srt(item_id: str, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = todos.get_subtitle_path(item_id, "srt")
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(
        p,
        media_type="text/plain; charset=utf-8",
        filename=f"{item_id}_subtitles_en.srt",
        content_disposition_type="attachment" if download else "inline",
    )


@app.api_route("/api/v1/todos/{item_id}/subtitles.ass", methods=["GET", "HEAD"])
def get_todo_ass(item_id: str, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = todos.get_subtitle_path(item_id, "ass")
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(
        p,
        media_type="text/plain; charset=utf-8",
        filename=f"{item_id}_subtitles_en.ass",
        content_disposition_type="attachment" if download else "inline",
    )


def _parse_optional_bool(val) -> bool | None:
    if val in (None, ""):
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in {"true", "1", "yes"}:
        return True
    if s in {"false", "0", "no"}:
        return False
    return None


@app.post("/api/v1/todos/{item_id}/videos")
async def submit_todo_video(item_id: str, request: Request):
    """Accepts an external video clip sequentially (seq_num = 1, 2, 3...).

    Supports BOTH:
      1. `multipart/form-data` with fields:
         - `file` (UploadFile: MP4 video)
         - `seq_num` (optional int, auto-increments if omitted)
         - `covered_shot_indexes` (optional JSON array or comma-separated ints, e.g. "1,2,3")
         - `note` (optional string)
      2. `application/json` matching `TodoVideoSubmitJson`:
         - `file_path` (path under VIDEO_DATA_DIR) or `file_url` (allowlisted URL)
         - `seq_num` (optional int)
         - `covered_shot_indexes` (optional list[int])
         - `note` (optional string)
    """
    auth.require(request, "ingest")
    caller, _ = auth.identity_for(request)
    ctype = (request.headers.get("content-type") or "").lower()
    shot_index: int | None = None
    declared_generator: str | None = None
    addresses_review_id: str | None = None
    staging_changed: bool | None = None
    try:
        if "multipart/form-data" in ctype:
            form = await request.form()
            si_raw = form.get("shot_index")
            if si_raw not in (None, ""):
                shot_index = int(si_raw)
            dg_raw = form.get("declared_generator")
            if dg_raw not in (None, ""):
                declared_generator = str(dg_raw)
            ar_raw = form.get("addresses_review_id")
            if ar_raw not in (None, ""):
                addresses_review_id = str(ar_raw).strip()
            staging_changed = _parse_optional_bool(form.get("staging_changed"))

            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(422, "multipart form must include 'file'")
            seq_raw = form.get("seq_num")
            seq_num = int(seq_raw) if seq_raw not in (None, "") else None
            cov_raw = form.get("covered_shot_indexes")
            covered: list[int] | None = None
            if cov_raw:
                cov_str = str(cov_raw).strip()
                if cov_str.startswith("["):
                    covered = [int(x) for x in json.loads(cov_str)]
                else:
                    covered = [int(x.strip()) for x in cov_str.split(",") if x.strip()]
            note = str(form.get("note") or "") or None
            # B2: chặn lối đi vòng quality gate — upload phải gắn shot
            # (shot_index đơn hoặc covered_shot_indexes gộp). Riêng orphan clip
            # chủ đích (covered=[] — clip ngoài kịch bản) được phép: rơi vào
            # bucket 'ngoài board' của summary, không âm thầm vào board.
            covered_present = cov_raw not in (None, "")
            if shot_index is None and not covered and not covered_present:
                raise ApiError(
                    422,
                    "Upload phải gắn shot: gửi 'shot_index' (1 shot) hoặc "
                    "'covered_shot_indexes' (gộp nhiều shot) — video không gắn "
                    "shot không vào board và sẽ bị từ chối.",
                    "shot_binding_required",
                )
            idem_payload = {
                "shot_index": shot_index,
                "seq_num": seq_num,
                "covered": covered,
                "declared_generator": declared_generator,
                "addresses_review_id": addresses_review_id,
                "staging_changed": staging_changed,
            }

            def _run_multipart():
                # Đọc media Ở ĐÂY, không phải trước khi claim key: preflight phải
                # từ chối được trước khi tốn vài chục MB, mà replay phải thoát
                # trước preflight. Chỉ đọc sau khi cả hai đã quyết xong.
                upload.file.seek(0)
                raw_bytes = upload.file.read()
                res = todos.submit_video(
                    item_id,
                    seq_num=seq_num,
                    file_bytes=raw_bytes,
                    filename=getattr(upload, "filename", "video.mp4"),
                    covered_shot_indexes=covered,
                    note=note,
                    actor=caller,
                )
                if shot_index is not None:
                    res["board_shot"] = board.record_board_upload(
                        item_id,
                        shot_index,
                        caller,
                        res["video"]["sha256"],
                        declared_generator=declared_generator,
                    )
                elif covered:
                    res["board_shots"] = board.record_board_upload_covered(
                        item_id,
                        covered,
                        caller,
                        res["video"]["sha256"],
                        declared_generator=declared_generator,
                    )
                return res

            if shot_index is not None:
                return await run_in_threadpool(
                    _idempotent_call,
                    request,
                    idem_payload,
                    _run_multipart,
                    201,
                    replay_status_200=True,
                    add_replay_flag=True,
                    reject_cross_caller=True,
                    preflight=lambda: board.preflight_upload(
                        item_id,
                        shot_index,
                        caller,
                        addresses_review_id=addresses_review_id,
                        staging_changed=staging_changed,
                    ),
                )
            # B2: upload gộp (covered, không shot_index) — preflight từng shot
            # được phủ trước khi đọc media, rồi board áp cho từng shot.
            return await run_in_threadpool(
                _idempotent_call,
                request,
                idem_payload,
                _run_multipart,
                201,
                replay_status_200=True,
                add_replay_flag=True,
                reject_cross_caller=True,
                preflight=lambda: board.preflight_upload_covered(item_id, covered, caller),
            )
            res = await run_in_threadpool(_run_multipart)
            return await run_in_threadpool(_idempotent, request, res, 201)
        else:
            payload = await request.json()
            if isinstance(payload, dict):
                if payload.get("shot_index") not in (None, ""):
                    shot_index = int(payload["shot_index"])
                if payload.get("declared_generator") not in (None, ""):
                    declared_generator = str(payload["declared_generator"])
                if payload.get("addresses_review_id") not in (None, ""):
                    addresses_review_id = str(payload["addresses_review_id"]).strip()
                staging_changed = _parse_optional_bool(payload.get("staging_changed"))

            body = TodoVideoSubmitJson(**payload)
            # B2: gate áp cho cả nhánh JSON (orphan chủ đích covered=[] được phép)
            if (shot_index is None and not body.covered_shot_indexes
                    and "covered_shot_indexes" not in payload):
                raise ApiError(
                    422,
                    "Upload phải gắn shot: gửi 'shot_index' (1 shot) hoặc "
                    "'covered_shot_indexes' (gộp nhiều shot) — video không gắn "
                    "shot không vào board và sẽ bị từ chối.",
                    "shot_binding_required",
                )

            def _run_json():
                res = todos.submit_video(
                    item_id,
                    seq_num=body.seq_num,
                    file_path=Path(body.file_path) if body.file_path else None,
                    file_url=body.file_url,
                    covered_shot_indexes=body.covered_shot_indexes,
                    note=body.note,
                    actor=caller,
                )
                if shot_index is not None:
                    res["board_shot"] = board.record_board_upload(
                        item_id,
                        shot_index,
                        caller,
                        res["video"]["sha256"],
                        declared_generator=declared_generator,
                    )
                elif body.covered_shot_indexes:
                    res["board_shots"] = board.record_board_upload_covered(
                        item_id,
                        body.covered_shot_indexes,
                        caller,
                        res["video"]["sha256"],
                        declared_generator=declared_generator,
                    )
                return res

            if shot_index is not None:
                return await run_in_threadpool(
                    _idempotent_call,
                    request,
                    payload if isinstance(payload, dict) else {},
                    _run_json,
                    201,
                    replay_status_200=True,
                    add_replay_flag=True,
                    reject_cross_caller=True,
                    preflight=lambda: board.preflight_upload(
                        item_id,
                        shot_index,
                        caller,
                        addresses_review_id=addresses_review_id,
                        staging_changed=staging_changed,
                    ),
                )
            res = await run_in_threadpool(_run_json)
            return await run_in_threadpool(_idempotent, request, res, 201)
    except BoardError as e:
        raise ApiError(e.status_code, e.message, e.code)
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except ImportRejected as e:
        return JSONResponse(e.report, status_code=e.status_code)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.api_route("/api/v1/todos/{item_id}/videos/{seq_num}/file", methods=["GET", "HEAD"])
def get_todo_video_file(item_id: str, seq_num: int, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = todos.get_uploaded_video_path(item_id, seq_num)
    except KeyError:
        raise HTTPException(404, f"video #{seq_num} not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(
        p,
        media_type="video/mp4",
        filename=f"{item_id}_seq_{seq_num:04d}.mp4",
        content_disposition_type="attachment" if download else "inline",
    )


@app.delete("/api/v1/todos/{item_id}/videos/{seq_num}")
def delete_todo_video(item_id: str, seq_num: int, request: Request = None):
    auth.require(request, "ingest")
    try:
        return todos.delete_video(item_id, seq_num)
    except KeyError:
        raise HTTPException(404, f"video #{seq_num} not found in item {item_id}")


# -- Shot Checklist & Per-Shot Duration Tracking for Agent -------------------

@app.get("/api/v1/todos/{item_id}/shot-checklist")
@app.get("/api/v1/todos/{item_id}/shots-status")
def get_todo_shot_checklist(item_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return todos.get_shot_checklist(item_id)
    except KeyError:
        raise HTTPException(404, "todo item not found")


@app.patch("/api/v1/todos/{item_id}/shot-checklist")
@app.post("/api/v1/todos/{item_id}/shot-checklist")
async def update_todo_shot_checklist(item_id: str, request: Request):
    auth.require(request, "ingest")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(422, "Invalid JSON payload")
    try:
        if isinstance(payload, dict) and "updates" in payload:
            body = ShotChecklistBatchUpdate(**payload)
            updates_list = [u.model_dump(exclude_none=True) for u in body.updates]
            return await run_in_threadpool(
                todos.update_shot_checklist,
                item_id,
                updates=updates_list,
                check_all_with_video=body.check_all_with_video,
                uncheck_missing=body.uncheck_missing,
                actor="user",
            )
        elif isinstance(payload, dict) and (
            payload.get("check_all_with_video") or payload.get("uncheck_missing")
        ):
            body = ShotChecklistBatchUpdate(**payload)
            return await run_in_threadpool(
                todos.update_shot_checklist,
                item_id,
                updates=[],
                check_all_with_video=body.check_all_with_video,
                uncheck_missing=body.uncheck_missing,
                actor="user",
            )
        elif isinstance(payload, dict):
            single = ShotCheckUpdate(**payload)
            return await run_in_threadpool(
                todos.update_shot_checklist,
                item_id,
                updates=[single.model_dump(exclude_none=True)],
                actor="user",
            )
        elif isinstance(payload, list):
            updates_list = [ShotCheckUpdate(**u).model_dump(exclude_none=True) for u in payload]
            return await run_in_threadpool(todos.update_shot_checklist, item_id, updates=updates_list, actor="user")
        else:
            raise ValueError("Invalid shot checklist update payload")
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


def _is_board_privileged(caller: str, scope: str) -> bool:
    """Ai được thấy điểm review của người khác + `?reveal=1`.

    Dùng scope `manager` (hoặc `admin`) thay vì so tên cứng: tên agent là dữ
    liệu cấu hình, không phải capability. Trước đây hàm này so tên
    `{"owner", "muse-manager"}`, nghĩa là đổi tên agent là mất quyền, còn đặt
    tên trùng là được quyền — cả hai đều sai.
    """
    return scope in {"manager", "admin"} or caller == "owner"


@app.get("/api/v1/todos/{item_id}/shots/next")
def get_next_board_shot(item_id: str, mode: str | None = None, request: Request = None):
    auth.require(request, "read")
    caller, scope = auth.identity_for(request)
    try:
        return board.next_shot(
            item_id, caller, privileged=_is_board_privileged(caller, scope), mode=mode
        )
    except BoardError as e:
        raise ApiError(e.status_code, e.message, e.code)
    except KeyError:
        raise HTTPException(404, "todo item not found")


@app.get("/api/v1/todos/{item_id}/shots/{shot_index}")
def get_single_board_shot(
    item_id: str, shot_index: int, reveal: int = 0, request: Request = None
):
    auth.require(request, "read")
    caller, scope = auth.identity_for(request)
    # ?reveal=1 chỉ có tác dụng với manager/owner; agent thường bị bỏ qua im lặng (không 403).
    privileged = _is_board_privileged(caller, scope)
    try:
        return board.get_shot(item_id, shot_index, caller=caller, privileged=privileged)
    except BoardError as e:
        raise ApiError(e.status_code, e.message, e.code)
    except KeyError:
        raise HTTPException(404, "todo item not found")


@app.post("/api/v1/todos/{item_id}/shots/{shot_index}/reviews")
async def post_shot_review(item_id: str, shot_index: int, request: Request):
    auth.require(request, "ingest")
    caller, _ = auth.identity_for(request)
    try:
        payload = await request.json()
    except Exception:
        raise ApiError(422, "Invalid JSON payload", "validation_error")
    if not isinstance(payload, dict):
        raise ApiError(422, "Review body must be a JSON object", "validation_error")
    try:
        return await run_in_threadpool(
            _idempotent_call,
            request,
            payload,
            lambda: board.submit_review(item_id, shot_index, caller, payload),
            201,
            replay_status_200=True,
            add_replay_flag=True,
            reject_cross_caller=True,
        )
    except BoardError as e:
        raise ApiError(e.status_code, e.message, e.code)
    except KeyError:
        raise HTTPException(404, "todo item not found")


@app.get("/api/v1/board/defect-codes")
def get_board_defect_codes(request: Request = None):
    """Bảng trừ điểm §4.2 — UI render checkbox từ đây, không hardcode."""
    auth.require(request, "read")
    return {"codes": defect_codes_public()}


@app.get("/api/v1/todos/{item_id}/shots/{shot_index}/comments")
def get_shot_comments(item_id: str, shot_index: int, request: Request = None):
    auth.require(request, "read")
    caller, scope = auth.identity_for(request)
    try:
        return board.list_comments(
            item_id, shot_index,
            caller=caller, privileged=_is_board_privileged(caller, scope),
        )
    except BoardError as e:
        raise ApiError(e.status_code, e.message, e.code)
    except KeyError:
        raise HTTPException(404, "todo item not found")


@app.post("/api/v1/todos/{item_id}/shots/{shot_index}/comments")
async def post_shot_comment(item_id: str, shot_index: int, request: Request):
    auth.require(request, "ingest")
    caller, _ = auth.identity_for(request)
    try:
        payload = await request.json()
    except Exception:
        raise ApiError(422, "Invalid JSON payload", "validation_error")
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), str):
        raise ApiError(422, "Comment body must be a string", "validation_error")
    try:
        return await run_in_threadpool(
            _idempotent_call,
            request,
            payload,
            lambda: board.add_comment(item_id, shot_index, caller, payload["body"]),
            201,
            replay_status_200=True,
            add_replay_flag=True,
            reject_cross_caller=True,
        )
    except BoardError as e:
        raise ApiError(e.status_code, e.message, e.code)
    except KeyError:
        raise HTTPException(404, "todo item not found")


@app.post("/api/v1/muse-proposals")
async def post_muse_proposal(request: Request):
    """Muse agent đẩy đề xuất ý tưởng short (tự schedule mỗi giờ). Trùng ý
    trong 72h → gộp vào đề xuất cũ (deduped=true), không tạo mới."""
    auth.require(request, "ingest")
    caller, _ = auth.identity_for(request)
    try:
        payload = await request.json()
    except Exception:
        raise ApiError(422, "Invalid JSON payload", "validation_error")
    if not isinstance(payload, dict):
        raise ApiError(422, "Proposal body must be a JSON object", "validation_error")
    try:
        return await run_in_threadpool(
            _idempotent_call, request, payload,
            lambda: proposals.create_proposal(caller, payload),
            201, replay_status_200=True, add_replay_flag=True, reject_cross_caller=True,
        )
    except ProposalError as e:
        raise ApiError(e.status_code, e.message, e.code)


@app.get("/api/v1/muse-proposals/queue/needs-script")
def get_proposal_needs_script_queue(request: Request = None):
    """Hàng chờ ưu tiên gen kịch bản: priority ASC rồi created_at ASC."""
    auth.require(request, "read")
    return proposals.needs_script_queue()


@app.get("/api/v1/muse-proposals/next")
def get_next_proposal_review(mode: str | None = None, request: Request = None):
    """Reviewer xin 1 đề xuất đủ điều kiện chấm (kiểu board mode=review)."""
    auth.require(request, "read")
    caller, _ = auth.identity_for(request)
    if mode and mode != "review":
        raise ApiError(422, f"Unknown dispatch mode: {mode}", "invalid_mode")
    try:
        return proposals.next_review_proposal(caller)
    except ProposalError as e:
        raise ApiError(e.status_code, e.message, e.code)


@app.get("/api/v1/muse-proposals")
def list_muse_proposals(status: str | None = None, request: Request = None):
    auth.require(request, "read")
    caller, scope = auth.identity_for(request)
    try:
        out = proposals.list_proposals(status)
        out["proposals"] = [
            proposals.get_proposal(p["id"], caller=caller,
                                   privileged=_is_board_privileged(caller, scope))
            for p in out["proposals"]
        ]
        return out
    except ProposalError as e:
        raise ApiError(e.status_code, e.message, e.code)


@app.get("/api/v1/muse-proposals/{proposal_id}")
def get_muse_proposal(proposal_id: str, request: Request = None):
    auth.require(request, "read")
    caller, scope = auth.identity_for(request)
    try:
        return proposals.get_proposal(proposal_id, caller=caller,
                                      privileged=_is_board_privileged(caller, scope))
    except ProposalError as e:
        raise ApiError(e.status_code, e.message, e.code)


@app.post("/api/v1/muse-proposals/{proposal_id}/reviews")
async def post_proposal_review(proposal_id: str, request: Request):
    auth.require(request, "ingest")
    caller, _ = auth.identity_for(request)
    try:
        payload = await request.json()
    except Exception:
        raise ApiError(422, "Invalid JSON payload", "validation_error")
    if not isinstance(payload, dict):
        raise ApiError(422, "Review body must be a JSON object", "validation_error")
    if "score" in payload:
        # Q3: score do server tính từ tổng 4 trục — client gửi score bị chặn
        # (mirror client_score_rejected của Shot Board).
        raise ApiError(
            422, "Client cannot submit score; server computes score from dimensions.",
            "client_score_rejected",
        )
    if "dimensions" not in payload:
        raise ApiError(
            422, "Review body phải có dimensions (hook/topicality/freshness/feasibility).",
            "validation_error",
        )
    try:
        return await run_in_threadpool(
            _idempotent_call, request, payload,
            lambda: proposals.submit_proposal_review(
                caller, proposal_id, payload["dimensions"], payload.get("comment"),
            ),
            201, replay_status_200=True, add_replay_flag=True, reject_cross_caller=True,
        )
    except ProposalError as e:
        raise ApiError(e.status_code, e.message, e.code)


@app.post("/api/v1/muse-proposals/{proposal_id}/convert-to-brief")
async def post_proposal_convert(proposal_id: str, request: Request):
    """Script-writer tiêu thụ hàng chờ needs_script (Q6): tạo brief qua
    BriefService hiện có, proposal chuyển in_production kèm brief_id."""
    auth.require(request, "ingest")
    caller, _ = auth.identity_for(request)
    try:
        return await run_in_threadpool(
            _idempotent_call, request, {},
            lambda: proposals.convert_to_brief(proposal_id, caller),
            201, replay_status_200=True, add_replay_flag=True, reject_cross_caller=True,
        )
    except ProposalError as e:
        raise ApiError(e.status_code, e.message, e.code)


@app.post("/api/v1/muse-proposals/{proposal_id}/approve")
async def post_proposal_approve(proposal_id: str, request: Request):
    """Owner duyệt → needs_script (hàng chờ ưu tiên gen kịch bản)."""
    auth.require(request, "admin")
    priority: int | None = None
    try:
        payload = await request.json()
        if isinstance(payload, dict) and payload.get("priority") is not None:
            priority = int(payload["priority"])
    except Exception:
        pass
    try:
        return await run_in_threadpool(proposals.approve_proposal, proposal_id, priority)
    except ProposalError as e:
        raise ApiError(e.status_code, e.message, e.code)


@app.post("/api/v1/muse-proposals/{proposal_id}/reject")
async def post_proposal_reject(proposal_id: str, request: Request):
    auth.require(request, "admin")
    reason = None
    try:
        payload = await request.json()
        if isinstance(payload, dict):
            reason = (payload.get("reason") or "").strip() or None
    except Exception:
        pass
    try:
        return await run_in_threadpool(proposals.reject_proposal, proposal_id, reason)
    except ProposalError as e:
        raise ApiError(e.status_code, e.message, e.code)


@app.get("/api/v1/agent/inbox")
def get_agent_inbox(request: Request):
    auth.require(request, "read")
    caller, scope = auth.identity_for(request)
    return board.agent_inbox(caller, privileged=_is_board_privileged(caller, scope))


@app.get("/api/v1/todos/{item_id}/board/summary")
def get_board_summary(item_id: str, request: Request):
    auth.require(request, "read")
    try:
        return board.board_summary(item_id)
    except KeyError:
        raise HTTPException(404, "todo item not found")


@app.post("/api/v1/board/migrate-legacy-videos")
async def post_migrate_legacy_videos(request: Request):
    """B2: map todo_videos chưa gắn board về shots (covered trước, heuristic
    theo thứ tự sau) + grandfathered — idempotent, chạy lại trả mapped=0."""
    auth.require(request, "admin")
    return await run_in_threadpool(board.migrate_legacy_videos)


@app.post("/api/v1/board/migrate-grandfathered")
async def post_migrate_grandfathered(request: Request):
    auth.require(request, "admin")
    board_enabled_at: float | None = None
    try:
        payload = await request.json()
        if isinstance(payload, dict) and payload.get("board_enabled_at") is not None:
            board_enabled_at = float(payload["board_enabled_at"])
    except Exception:
        pass
    return await run_in_threadpool(board.migrate_grandfathered, board_enabled_at)


@app.patch("/api/v1/todos/{item_id}/shots/{shot_index}")
@app.post("/api/v1/todos/{item_id}/shots/{shot_index}/check")
def update_single_todo_shot_check(
    item_id: str,
    shot_index: int,
    body: ShotCheckUpdate,
    request: Request = None,
):
    auth.require(request, "ingest")
    upd = body.model_dump(exclude_none=True)
    upd["shot_index"] = int(shot_index)
    try:
        return todos.update_shot_checklist(item_id, updates=[upd], actor="user")
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/v1/todos/{item_id}/confirm-assemble")
def confirm_assemble_todo(
    item_id: str,
    body: ConfirmAssembleRequest | None = None,
    request: Request = None,
):
    auth.require(request, "ingest")
    req = body or ConfirmAssembleRequest()
    try:
        board.check_assemble_gate(item_id)
        result = assembler.confirm_and_assemble(
            item_id,
            burn_subtitles=req.burn_subtitles,
            allow_shortfall=req.allow_shortfall,
            watermark_enabled=req.watermark_enabled,
            watermark_text=req.watermark_text,
            allow_missing_thumbnail=req.allow_missing_thumbnail,
        )
    except BoardError as e:
        raise ApiError(e.status_code, e.message, e.code)
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except ThumbnailMissingError as e:
        return JSONResponse(
            {
                "error": "thumbnail_missing",
                "message": "Job chưa có ảnh Thumbnail (kind=thumbnail). Vui lòng upload 1 ảnh thumbnail trước khi hoàn thành job.",
                "hint": f"POST /api/v1/todos/{item_id}/images với kind=thumbnail",
                "video_progress": e.progress,
            },
            status_code=409,
        )
    except ShortfallWarningError as e:
        return JSONResponse(
            {
                "error": "video_duration_shortfall",
                "shortfall_warning": e.progress["shortfall_warning"],
                "hint": (
                    "Tổng thời lượng tất cả video gửi vào vẫn ngắn hơn thời lượng Audio EN. "
                    "Hãy gửi thêm video tiếp theo hoặc truyền allow_shortfall=true để xác nhận vẫn ráp."
                ),
                "video_progress": e.progress,
            },
            status_code=409,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except domain.TransitionError as e:
        raise HTTPException(409, str(e))
    return _idempotent(request, result, 200)


@app.api_route("/api/v1/todos/{item_id}/final-video", methods=["GET", "HEAD"])
def get_todo_final_video(item_id: str, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = todos.get_final_video_path(item_id)
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(
        p,
        media_type="video/mp4",
        filename=f"{item_id}_final_en.mp4",
        content_disposition_type="attachment" if download else "inline",
    )


# -- Storage & Google Drive Sync ---------------------------------------------

@app.get("/api/v1/storage/status")
def get_storage_status(request: Request = None):
    auth.require(request, "read")
    return check_storage_health()


@app.post("/api/v1/todos/{item_id}/sync-gdrive")
def sync_todo_to_gdrive(
    item_id: str,
    purge_raw_clips: bool = False,
    body: SyncGdriveRequest | None = None,
    request: Request = None,
):
    auth.require(request, "ingest")
    item = items.get(item_id)
    if not item or item["status"] == "deleted":
        raise HTTPException(404, "todo item not found")
    purge = (body.purge_raw_clips if body else False) or purge_raw_clips
    res = sync_item_to_gdrive(item_id, purge_raw_clips_after_sync=purge)
    t = now()
    store.execute(
        "UPDATE items SET gdrive_status=?, gdrive_remote_path=?, "
        "gdrive_synced_at=?, purged_bytes=COALESCE(purged_bytes, 0) + ?, updated_at=? WHERE id=?",
        (
            res.get("status"),
            res.get("remote_path"),
            t if res.get("status") == "synced" else None,
            res.get("freed_bytes", 0),
            t,
            item_id,
        ),
    )
    if res.get("status") == "synced":
        items.event(item_id, None, "todo.gdrive_synced", "api", res)
    return res


# -- External Agent Feedback API ---------------------------------------------

@app.post("/api/v1/todos/{item_id}/feedback")
@app.post("/api/v1/todos/{item_id}/feedbacks")
async def submit_todo_feedback(item_id: str, request: Request):
    auth.require(request, "ingest")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(422, "Invalid JSON payload")
    try:
        res = await run_in_threadpool(todos.submit_feedback, item_id, payload, actor="external_video_agent")
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return await run_in_threadpool(_idempotent, request, res, 201)


@app.get("/api/v1/todos/{item_id}/feedback")
@app.get("/api/v1/todos/{item_id}/feedbacks")
def list_todo_feedbacks(
    item_id: str,
    status: str | None = None,
    category: str | None = None,
    request: Request = None,
):
    auth.require(request, "read")
    item = items.get(item_id)
    if not item or item["status"] == "deleted":
        raise HTTPException(404, "todo item not found")
    fbks = todos.list_feedbacks(item_id, status=status, category=category)
    summary = todos.get_feedback_summary(item_id)
    return {
        "item_id": item_id,
        "feedbacks": fbks,
        "summary": summary,
        "count": len(fbks),
    }


@app.patch("/api/v1/todos/{item_id}/feedback/{feedback_id}")
@app.post("/api/v1/todos/{item_id}/feedback/{feedback_id}/resolve")
def resolve_todo_feedback(
    item_id: str,
    feedback_id: str,
    body: FeedbackResolveRequest | None = None,
    request: Request = None,
):
    auth.require(request, "ingest")
    req = body or FeedbackResolveRequest()
    try:
        res = todos.resolve_feedback(
            item_id,
            feedback_id,
            status=req.status,
            resolution_note=req.resolution_note,
            actor="user",
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return res


@app.delete("/api/v1/todos/{item_id}/feedback/{feedback_id}")
def delete_todo_feedback(
    item_id: str,
    feedback_id: str,
    request: Request = None,
):
    auth.require(request, "ingest")
    try:
        res = todos.delete_feedback(item_id, feedback_id, actor="api")
    except KeyError as e:
        raise HTTPException(404, str(e))
    return res


# -- Audio Regeneration (Slower speech + Longer pauses + Warmer BGM) ---------

@app.post("/api/v1/todos/{item_id}/regenerate-audio")
def regenerate_todo_audio(
    item_id: str,
    body: AudioRegenerateRequest | None = None,
    request: Request = None,
):
    auth.require(request, "ingest")
    req = body or AudioRegenerateRequest()
    try:
        res = todos.regenerate_item_audio(
            item_id,
            slow_factor=req.slow_factor,
            extra_pause_sec=req.extra_pause_sec,
            bgm_enabled=req.bgm_enabled,
            bgm_duck_db=req.bgm_duck_db,
        )
    except KeyError:
        raise HTTPException(404, "todo item not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return res


# -- External Agent Communication Guide --------------------------------------

@app.get("/api/v1/agent-guide")
def get_agent_guide(format: str | None = None, request: Request = None):
    auth.require(request, "read")
    guide_path = Path(__file__).resolve().parents[2] / "AGENT_API_GUIDE.md"
    if not guide_path.exists():
        # Fail loud: serving a placeholder silently let external agents run on
        # a stale standard. Missing guide is an ops error, not an empty doc.
        raise HTTPException(status_code=404, detail="Agent guide file missing on server")
    content = guide_path.read_text(encoding="utf-8")

    accept = request.headers.get("accept", "") if request else ""
    fmt_lower = (format or "").strip().lower()
    if fmt_lower in {"raw", "markdown", "md", "text"} or "text/markdown" in accept or "text/plain" in accept:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content, media_type="text/markdown")

    return {
        "title": "External Video Agent API Guide",
        "production_base_url": "https://vidfac.gacdero.vn",
        "raw_guide_url": "/api/v1/agent-guide?format=raw",
        "endpoints": [
            {"method": "GET", "path": "/api/v1/todos?status=todo", "description": "Step 1: Poll pending jobs"},
            {"method": "GET", "path": "/api/v1/todos/{item_id}", "description": "Step 2: Fetch full job package"},
            {"method": "GET", "path": "/api/v1/todos/{item_id}/shot-checklist", "description": "Step 2b: Poll shot generation checklist & remaining seconds per shot"},
            {"method": "PATCH", "path": "/api/v1/todos/{item_id}/shots/{shot_index}", "description": "Step 6b: Tick/update generated status & duration for a specific shot"},
            {"method": "PATCH", "path": "/api/v1/todos/{item_id}/shot-checklist", "description": "Step 6c: Batch tick/update shot checklist for video gen agent"},
            {"method": "GET", "path": "/api/v1/todos/{item_id}/audio", "description": "Step 3a: Download audio_en.wav"},
            {"method": "GET", "path": "/api/v1/todos/{item_id}/script", "description": "Step 3b: Download script_en.json"},
            {"method": "GET", "path": "/api/v1/todos/{item_id}/references", "description": "Step 3c: Harvest reference images"},
            {"method": "POST", "path": "/api/v1/todos/{item_id}/feedback", "description": "Step 4: Audit audio & submit feedback"},
            {"method": "POST", "path": "/api/v1/todos/{item_id}/images", "description": "Step 5: Upload generated keyframes/sheets AND mandatory 1 thumbnail (kind=thumbnail)"},
            {"method": "POST", "path": "/api/v1/todos/{item_id}/videos", "description": "Step 6: Upload sequential video clips"},
            {"method": "POST", "path": "/api/v1/todos/{item_id}/confirm-assemble", "description": "Step 7: Confirm & assemble"},
            {"method": "POST", "path": "/api/v1/todos/{item_id}/sync-gdrive", "description": "Manual trigger GDrive sync"},
            {"method": "GET", "path": "/api/v1/storage/status", "description": "Check storage health & GDrive status"},
        ],
        "markdown": content,
    }


# -- Short Studio (Phụ lục G) ---------------------------------------------------

@app.post("/api/v1/shorts")
def create_short(body: ShortCreate, request: Request = None):
    auth.require(request, "ingest")
    try:
        short = shorts.create(
            mode=body.mode, source_text=body.source_text,
            source_note=body.source_note or "",
            source_link=body.source_link or "",
            target_seconds=body.target_seconds, title=body.title,
            lang=body.lang,
        )
    except AdapterNotConfigured as e:
        raise HTTPException(503, f"Chưa cấu hình adapter LLM: {e}")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _idempotent(request, short, 201)


@app.get("/api/v1/shorts")
def list_shorts(status: str | None = None, q: str | None = None,
                request: Request = None):
    auth.require(request, "read")
    return {"shorts": shorts.list(status=status, q=q)}


@app.get("/api/v1/shorts/{short_id}")
def get_short(short_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return shorts.get(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")


@app.patch("/api/v1/shorts/{short_id}/script")
def update_short_script(short_id: str, body: ShortScriptUpdate, request: Request = None):
    auth.require(request, "ingest")
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(422, "Không có trường nào để cập nhật.")
    try:
        return shorts.update_script(short_id, patch)
    except KeyError:
        raise HTTPException(404, "short not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/v1/shorts/{short_id}/produce")
def produce_short(short_id: str, request: Request = None):
    auth.require(request, "ingest")
    try:
        return shorts.produce(short_id)  # sync def → FastAPI chạy threadpool sẵn
    except KeyError:
        raise HTTPException(404, "short not found")
    except AdapterNotConfigured as e:
        raise HTTPException(503, f"Chưa cấu hình adapter cần thiết: {e}")
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001 — provider/ffmpeg lỗi: trả lý do tiếng người
        raise HTTPException(502, f"Tổng hợp Short thất bại: {e}")


@app.delete("/api/v1/shorts/{short_id}")
def delete_short(short_id: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return shorts.cancel(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")
    except domain.TransitionError as e:
        raise HTTPException(409, str(e))


@app.api_route("/api/v1/shorts/{short_id}/audio", methods=["GET", "HEAD"])
def get_short_audio(short_id: str, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = shorts.audio_path(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, media_type=_AUDIO_MEDIA_TYPES.get(p.suffix.lower(), "audio/wav"),
                        filename=f"{short_id}_voice_vi{p.suffix.lower() or '.wav'}",
                        content_disposition_type="attachment" if download else "inline")


_AUDIO_MEDIA_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


@app.post("/api/v1/shorts/{short_id}/audio")
async def ingest_short_audio(short_id: str, request: Request):
    """Owner nạp audio + danh sách shot (multipart: `file` + `shots` JSON).

    Audio owner thay cho TTS: scenes + timing do owner khai báo, `publish_job`
    sau đó chỉ chuyển queued mà không gọi TTS.
    """
    auth.require(request, "ingest")
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(422, "multipart form must include 'file'")
    raw_shots = form.get("shots")
    if raw_shots is None or not str(raw_shots).strip():
        raise HTTPException(422, "multipart form must include 'shots' (JSON list)")
    try:
        decoded = json.loads(str(raw_shots))
        if isinstance(decoded, list):
            decoded = {"shots": decoded}
        parsed = ShortAudioIngest.model_validate(decoded)
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(422, f"shots không hợp lệ: {e}")
    raw_bytes = await upload.read()
    filename = getattr(upload, "filename", None) or "audio.mp3"
    try:
        res = await run_in_threadpool(
            shorts.ingest_audio,
            short_id,
            raw_bytes=raw_bytes,
            filename=filename,
            shots=[s.model_dump() for s in parsed.shots],
        )
    except KeyError:
        raise HTTPException(404, "short not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return res


@app.delete("/api/v1/shorts/{short_id}/audio")
def clear_short_audio(short_id: str, request: Request = None):
    """Bỏ audio owner, quay lại luồng TTS (sửa kịch bản hoặc nạp audio khác)."""
    auth.require(request, "ingest")
    try:
        return shorts.clear_audio(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/v1/shorts/audio")
async def create_short_from_audio(request: Request):
    """Tạo short mới từ audio + shot list của owner (không gọi LLM).

    multipart: `file` (audio), `shots` (JSON list {text,start_sec,end_sec}),
    `title`, `mode`, `lang`. Trả 201 kèm short đã hydrate (status `scripted`,
    `audio_source='owner'`) — gọi `publish-job` để agent nhận ngay.
    """
    auth.require(request, "ingest")
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(422, "multipart form must include 'file'")
    raw_shots = form.get("shots")
    if raw_shots is None or not str(raw_shots).strip():
        raise HTTPException(422, "multipart form must include 'shots' (JSON list)")
    try:
        decoded = json.loads(str(raw_shots))
        if isinstance(decoded, list):
            decoded = {"shots": decoded}
        parsed = ShortAudioIngest.model_validate(decoded)
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(422, f"shots không hợp lệ: {e}")
    raw_bytes = await upload.read()
    try:
        res = await run_in_threadpool(
            shorts.create_from_audio,
            raw_bytes=raw_bytes,
            filename=getattr(upload, "filename", None) or "audio.mp3",
            shots=[s.model_dump() for s in parsed.shots],
            title=str(form.get("title") or "").strip(),
            mode=str(form.get("mode") or "cinematic").strip() or "cinematic",
            lang=str(form.get("lang") or "vi").strip() or "vi",
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    return JSONResponse(res, status_code=201)


@app.post("/api/v1/shorts/upload")
async def upload_new_short_video(request: Request):
    """Tạo Short mới trực tiếp từ file MP4 thành phẩm (không cần AI tạo kịch bản)."""
    auth.require(request, "ingest")
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(422, "multipart form must include 'file'")
    raw_bytes = await upload.read()
    title = str(form.get("title") or "").strip()
    mode = str(form.get("mode") or "news").strip() or "news"
    lang = str(form.get("lang") or "vi").strip() or "vi"
    source_note = str(form.get("source_note") or "").strip()
    description = str(form.get("description") or "").strip()
    filename = getattr(upload, "filename", None) or "short.mp4"
    try:
        res = await run_in_threadpool(
            shorts.create_from_upload,
            title=title,
            mode=mode,
            lang=lang,
            raw_bytes=raw_bytes,
            filename=filename,
            source_note=source_note,
            description=description,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    return JSONResponse(res, status_code=201)


@app.post("/api/v1/shorts/{short_id}/video")
async def upload_short_video(short_id: str, request: Request):
    """Upload hoặc thay thế file MP4 thành phẩm cho một Short đã tồn tại."""
    auth.require(request, "ingest")
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(422, "multipart form must include 'file'")
    raw_bytes = await upload.read()
    filename = getattr(upload, "filename", None) or "short.mp4"
    try:
        res = await run_in_threadpool(
            shorts.upload_final_video,
            short_id,
            raw_bytes=raw_bytes,
            filename=filename,
        )
    except KeyError:
        raise HTTPException(404, "short not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return res


@app.api_route("/api/v1/shorts/{short_id}/video", methods=["GET", "HEAD"])
def get_short_video(short_id: str, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = shorts.video_path(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, media_type="video/mp4",
                        filename=f"{short_id}_short_9x16.mp4",
                        content_disposition_type="attachment" if download else "inline")


@app.get("/api/v1/shorts/{short_id}/script")
def get_short_script(short_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return shorts.get(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")


@app.api_route("/api/v1/shorts/{short_id}/images/{seq}/file", methods=["GET", "HEAD"])
def get_short_scene_image(short_id: str, seq: int, request: Request = None):
    auth.require(request, "read")
    try:
        p = shorts.scene_image_path(short_id, int(seq))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    media = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(p, media_type=media)


@app.api_route("/api/v1/shorts/{short_id}/subtitles.ass", methods=["GET", "HEAD"])
def get_short_ass(short_id: str, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = shorts.ass_path(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, media_type="text/plain; charset=utf-8",
                        filename=f"{short_id}_karaoke.ass",
                        content_disposition_type="attachment" if download else "inline")


@app.api_route("/api/v1/shorts/{short_id}/subtitles.srt", methods=["GET", "HEAD"])
def get_short_srt(short_id: str, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = shorts.srt_path(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, media_type="text/plain; charset=utf-8",
                        filename=f"{short_id}_subs.srt",
                        content_disposition_type="attachment" if download else "inline")


@app.api_route("/api/v1/shorts/{short_id}/publish.md", methods=["GET", "HEAD"])
def get_short_publish(short_id: str, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = shorts.publish_path(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, media_type="text/markdown; charset=utf-8",
                        filename=f"{short_id}_publish.md",
                        content_disposition_type="attachment" if download else "inline")


# -- G.13: Job queue cho agent video ngoài (short EN) ---------------------------

@app.get("/api/v1/short-jobs")
def list_short_jobs(status: str | None = None, limit: int = 50, request: Request = None):
    """Agent poll job: chỉ trả short EN ở queued/rendering (hoặc status chỉ định)."""
    auth.require(request, "read")
    if status and status not in {"queued", "rendering"}:
        raise HTTPException(422, "status chỉ nhận: queued | rendering")
    rows = shorts.list(status=status, q=None)
    jobs = [r for r in rows if (r.get("lang") or "vi") == "en"
            and r["status"] in {"queued", "rendering"}]
    return {"jobs": jobs[:max(1, min(int(limit), 200))]}


@app.post("/api/v1/shorts/{short_id}/publish-job")
def publish_short_job(short_id: str, request: Request = None):
    auth.require(request, "ingest")
    try:
        return shorts.publish_job(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    except domain.TransitionError as e:
        raise HTTPException(409, str(e))


@app.get("/api/v1/shorts/{short_id}/job")
def get_short_job(short_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return shorts.get_job(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")


@app.api_route("/api/v1/shorts/{short_id}/job/audio", methods=["GET", "HEAD"])
def get_short_job_audio(short_id: str, download: bool = False, request: Request = None):
    auth.require(request, "read")
    try:
        p = shorts.audio_path(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, media_type="audio/wav",
                        filename=f"{short_id}_voice_en.wav",
                        content_disposition_type="attachment" if download else "inline")


@app.post("/api/v1/shorts/{short_id}/clips")
async def submit_short_clip(short_id: str, request: Request):
    """Agent submit clip: multipart `file` hoặc JSON `file_path`/`file_url`.

    `covered_scene_indexes` (tùy chọn): 1 clip gộp nhiều scene.
    """
    auth.require(request, "ingest")
    ctype = (request.headers.get("content-type") or "").lower()
    try:
        if "multipart/form-data" in ctype:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(422, "multipart form must include 'file'")
            raw_bytes = await upload.read()
            seq_raw = form.get("seq_num")
            seq_num = int(seq_raw) if seq_raw not in (None, "") else None
            cov_raw = form.get("covered_scene_indexes")
            covered: list[int] | None = None
            if cov_raw:
                cov_str = str(cov_raw).strip()
                if cov_str.startswith("["):
                    covered = [int(x) for x in json.loads(cov_str)]
                else:
                    covered = [int(x.strip()) for x in cov_str.split(",") if x.strip()]
            note = str(form.get("note") or "") or None
            res = await run_in_threadpool(
                shorts.submit_clip, short_id, seq_num=seq_num,
                raw_bytes=raw_bytes,
                filename=getattr(upload, "filename", "clip.mp4"),
                covered_scene_indexes=covered, note=note)
        else:
            payload = await request.json()
            body = ShortClipSubmitJson(**payload)
            res = await run_in_threadpool(
                shorts.submit_clip, short_id, seq_num=body.seq_num,
                file_path=Path(body.file_path) if body.file_path else None,
                file_url=body.file_url,
                covered_scene_indexes=body.covered_scene_indexes, note=body.note)
    except KeyError:
        raise HTTPException(404, "short not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return await run_in_threadpool(_idempotent, request, res, 201)


@app.api_route("/api/v1/shorts/{short_id}/clips/{seq_num}/file", methods=["GET", "HEAD"])
def get_short_clip_file(short_id: str, seq_num: int, download: bool = False,
                        request: Request = None):
    auth.require(request, "read")
    try:
        p = shorts.clip_file_path(short_id, int(seq_num))
    except KeyError:
        raise HTTPException(404, f"clip #{seq_num} not found")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return FileResponse(p, media_type="video/mp4",
                        filename=f"seq_{seq_num:04d}.mp4",
                        content_disposition_type="attachment" if download else "inline")


@app.delete("/api/v1/shorts/{short_id}/clips/{seq_num}")
def delete_short_clip(short_id: str, seq_num: int, request: Request = None):
    auth.require(request, "ingest")
    try:
        shorts.delete_clip(short_id, int(seq_num))
    except KeyError:
        raise HTTPException(404, f"clip #{seq_num} not found")
    return {"deleted": True, "seq_num": seq_num}


@app.post("/api/v1/shorts/{short_id}/assemble")
def assemble_short_job(short_id: str, request: Request = None):
    auth.require(request, "ingest")
    try:
        return shorts.assemble_job(short_id)
    except KeyError:
        raise HTTPException(404, "short not found")
    except ShortfallError as e:
        raise HTTPException(409, {"detail": str(e), "progress": e.progress,
                                  "code": "video_duration_shortfall"})
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001 — ffmpeg/provider lỗi tiếng người
        raise HTTPException(502, f"Assemble Short job thất bại: {e}")


@app.api_route("/api/v1/short-agent-guide", methods=["GET", "HEAD"])
def short_agent_guide(request: Request = None):
    auth.require(request, "read")
    guide = REPO_ROOT / "AGENT_SHORT_GUIDE.md"
    if not guide.exists():
        raise HTTPException(404, "AGENT_SHORT_GUIDE.md chưa có trong repo")
    return FileResponse(guide, media_type="text/markdown; charset=utf-8")


# -- Hộp tin nóng (Phụ lục G.10) ------------------------------------------------

@app.post("/api/v1/news/sync")
def news_sync(request: Request = None):
    """Trigger 1 kỳ sync ngay (fetch RSS → lưu → push Telegram)."""
    auth.require(request, "ingest")
    return news_run_once(store)  # sync def → FastAPI chạy threadpool sẵn


@app.get("/api/v1/news")
def news_list(status: str | None = None, lang: str | None = None, limit: int = 50,
              request: Request = None):
    auth.require(request, "read")
    if status and status not in {"new", "used", "dismissed"}:
        raise HTTPException(422, "status chỉ nhận: new | used | dismissed")
    if lang and lang not in {"vi", "en"}:
        raise HTTPException(422, "lang chỉ nhận: vi | en")
    return {"news": list_news(store, status=status, limit=limit, lang=lang)}


@app.post("/api/v1/news/{news_id}/dismiss")
def news_dismiss(news_id: str, request: Request = None):
    auth.require(request, "ingest")
    row = dismiss_news(store, news_id)
    if not row:
        raise HTTPException(404, "news item not found")
    return row


@app.post("/api/v1/news/{news_id}/push")
def news_push_selected(news_id: str, request: Request = None):
    """Gửi ĐÚNG tin user chọn qua Telegram (G.11 — không tự push hàng loạt)."""
    auth.require(request, "ingest")
    try:
        return push_selected_news(store, news_id)
    except KeyError:
        raise HTTPException(404, "news item not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/v1/shorts/daily-digest/status")
def daily_digest_status(request: Request = None):
    """Trạng thái lịch chạy Daily Digest EN (@StickSto · 06:30 America/New_York)."""
    auth.require(request, "read")
    return get_digest_status(store)


@app.post("/api/v1/shorts/daily-digest/run")
def daily_digest_run(send_telegram: bool = True, sync_first: bool = True,
                     request: Request = None):
    """Chạy ngay 1 bản Daily World Brief EN (12-15 tin → Render MP4 → gửi Telegram)."""
    auth.require(request, "ingest")
    try:
        return run_daily_digest(
            store, shorts,
            sync_first=sync_first,
            send_telegram=send_telegram,
            force=True,
            actor="api",
            youtube_svc=youtube,
        )
    except AdapterNotConfigured as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


# -- Daily Brief submission flow (16:00 VN đòi nội dung → submit → chốt → đăng) --


@app.post("/api/v1/digest/submit")
def digest_submit(body: DigestSubmitBody, request: Request = None):
    """Owner nộp list tin đã chốt (kèm link bài/ảnh/thumbnail) → thu thập media → render → Telegram chờ /approve."""
    auth.require(request, "ingest")
    try:
        return submit_digest_stories(
            store, shorts,
            [s.model_dump() for s in body.stories],
            date_key=body.date,
            force=body.force,
            actor="api",
            build_async=True,
            youtube_svc=youtube,
        )
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/v1/digest/submissions")
def digest_submissions_list(date: str = "", request: Request = None):
    auth.require(request, "read")
    if date:
        sub = get_digest_submission(store, date)
        return {"submission": sub}
    rows = store.query("SELECT * FROM digest_submissions ORDER BY date DESC LIMIT 30")
    return {"submissions": [dict(r) for r in rows]}


@app.get("/api/v1/digest/today")
def digest_today(request: Request = None):
    auth.require(request, "read")
    return get_digest_status_today(store)


@app.post("/api/v1/digest/{submission_id}/approve")
def digest_approve(submission_id: str, request: Request = None):
    """Chốt đăng YouTube (privacy theo news_config submission_flow.approve). Idempotent."""
    auth.require(request, "admin")
    sub = store.query_one("SELECT * FROM digest_submissions WHERE id=?", (submission_id,))
    if not sub:
        raise HTTPException(404, f"Submission không tồn tại: {submission_id}")
    try:
        return approve_digest(store, youtube, str(sub["short_id"] or ""), actor="api")
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/v1/digest/{submission_id}/reject")
def digest_reject(submission_id: str, request: Request = None, reason: str = ""):
    auth.require(request, "admin")
    sub = store.query_one("SELECT * FROM digest_submissions WHERE id=?", (submission_id,))
    if not sub:
        raise HTTPException(404, f"Submission không tồn tại: {submission_id}")
    try:
        return reject_digest(store, str(sub["short_id"] or ""), reason, actor="api")
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


# -- Bảng chat chung cho agent (thread 1-1, tối đa 2 participants) --------------


def _chat_identity(request: Request) -> tuple[str, bool]:
    """(sender, is_owner) — sender = key-name; is_owner CHỈ khi key tên 'owner'
    (bearer 'owner' từ entry 'owner:admin') hoặc session PIN — đúng mô hình
    'key của tôi là key owner riêng' của owner. Key admin khác = participant thường."""
    identity, scope = auth.identity_for(request)
    return identity, identity == "owner"


@app.post("/api/v1/chat/threads")
def chat_create_thread(body: dict, request: Request = None):
    """Agent/owner tạo thread 1-1 (slot cho agent thứ 2 để trống, tự claim)."""
    scope = auth.require(request, "ingest")
    sender, _ = _chat_identity(request)
    try:
        return chat.create_thread(sender, str(body.get("title") or ""),
                                  item_id=body.get("item_id") or None,
                                  actor_scope=scope)
    except ChatError as e:
        raise HTTPException(e.status_code, e.message)


@app.get("/api/v1/chat/threads")
def chat_list_threads(limit: int = 50, request: Request = None):
    """Metadata threads — public cho mọi key (agent mới tìm được agent cũ để claim)."""
    auth.require(request, "read")
    return {"threads": chat.list_threads(limit)}


@app.get("/api/v1/chat/threads/{thread_id}")
def chat_get_thread(thread_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return chat.get_thread(thread_id)
    except ChatError as e:
        raise HTTPException(e.status_code, e.message)


@app.post("/api/v1/chat/threads/{thread_id}/messages")
def chat_post_message(thread_id: str, body: dict, request: Request = None):
    """Gửi tin — sender phải là participant (slot trống thì tự claim bằng CAS)."""
    auth.require(request, "ingest")
    sender, is_owner = _chat_identity(request)
    try:
        return chat.post_message(thread_id, sender, str(body.get("body") or ""),
                                 is_owner=is_owner)
    except ChatError as e:
        raise HTTPException(e.status_code, e.message)


@app.get("/api/v1/chat/threads/{thread_id}/messages")
def chat_get_messages(thread_id: str, request: Request = None, since_id: int = 0,
                      limit: int = 100):
    """Đọc tin mới theo cursor `since_id` — participants + owner + admin (moderator read)."""
    identity, scope = auth.identity_for(request)
    is_owner = identity == "owner" or scope == "admin"
    try:
        return chat.get_messages(thread_id, identity, since_id=since_id, limit=limit,
                                 is_owner=is_owner)
    except ChatError as e:
        raise HTTPException(e.status_code, e.message)


@app.delete("/api/v1/chat/threads/{thread_id}")
def chat_close_thread(thread_id: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return chat.close_thread(thread_id)
    except ChatError as e:
        raise HTTPException(e.status_code, e.message)


# -- Authoring flow: brief -> script duel (2026-11-23) -------------------------

class ApiError(HTTPException):
    """HTTPException có `code` máy đọc được.

    Agent cần phân biệt 409 `duplicate_content` (đừng thử lại) với 409
    `version_conflict` (thử lại với parent mới). Body vẫn giữ `detail` là string
    để client cũ không vỡ; `code` là field thêm.
    """

    def __init__(self, status_code: int, message: str, code: str) -> None:
        super().__init__(status_code, message)
        self.code = code


@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError):
    return JSONResponse({"detail": exc.detail, "code": exc.code},
                        status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    """422 phải có `code` — contract hứa mọi lỗi đều branch được bằng code."""
    return JSONResponse({
        "detail": "Request không hợp lệ (schema).",
        "code": "validation_error",
        "errors": jsonable_encoder(exc.errors()),
    }, status_code=422)


@app.exception_handler(sqlite3.OperationalError)
async def _sqlite_error_handler(request: Request, exc: sqlite3.OperationalError):
    """Lock timeout không được thành 500 — agent cần biết là 'thử lại sau'."""
    msg = str(exc).lower()
    busy = "locked" in msg or "busy" in msg
    return JSONResponse(
        {"detail": "Database đang bận — thử lại sau vài giây." if busy
                   else "Lỗi truy cập database.",
         "code": "database_busy" if busy else "database_error"},
        status_code=503 if busy else 500)


def _fail(exc: Exception) -> None:
    """Bọc BriefError/ScriptDuelError thành response có `code`."""
    raise ApiError(getattr(exc, "status_code", 400), str(getattr(exc, "message", exc)),
                   getattr(exc, "code", "error"))


def _require_owner(request: Request) -> str:
    auth.require(request, "admin")
    identity, _ = auth.identity_for(request)
    if identity != "owner":
        raise ApiError(403, "Chỉ owner được thực hiện thao tác này.", "owner_only")
    return identity


def _sync_agreement(brief_id: str, reason: str) -> None:
    """Side-effect webhook sau mutation: không bao giờ được làm hỏng response.

    `AgreementNotifier.sync` đã tự bọc try/except; bọc thêm ở đây vì `duel.agreement`
    cũng có thể ném (brief vừa bị xoá/huỷ giữa 2 bước).
    """
    try:
        agreement_notifier.sync(brief_id, duel.agreement(brief_id), reason)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agreement sync thất bại (%s, %s): %s", brief_id, reason, exc)


@app.post("/api/v1/briefs")
def create_brief(body: BriefCreate, request: Request = None):
    """Tạo brief (Phase 0). Brief phải approved trước khi nộp script proposal."""
    auth.require(request, "ingest")
    identity, _ = auth.identity_for(request)
    if body.approval_mode == "calendar" and not body.sensitive and identity != "owner":
        raise ApiError(403, "Chỉ owner được tạo brief calendar (tự duyệt).",
                       "calendar_owner_only")
    if (body.critic or "").strip() and identity != "owner":
        raise ApiError(
            403, "Chỉ owner được chỉ định critic — bỏ qua field này, owner sẽ gọi "
                 "POST /briefs/{id}/critic sau khi brief được tạo.",
            "critic_owner_only")
    payload = body.model_dump()

    def run() -> dict:
        try:
            brief = briefs.create(
                title=body.title, created_by=identity, angle=body.angle,
                target_duration_sec=body.target_duration_sec, language=body.language,
                style_lock_version=body.style_lock_version,
                source_links=body.source_links, deadline_at=body.deadline_at,
                budget=body.budget, approval_mode=body.approval_mode,
                sensitive=body.sensitive, objection_window_sec=body.objection_window_sec,
                critic=body.critic)
        except BriefError as e:
            _fail(e)
        _sync_agreement(brief["id"], "brief.created")
        return brief

    return _idempotent_call(request, payload, run, 201)


@app.get("/api/v1/briefs")
def list_briefs(status: str | None = None, limit: int = 50, request: Request = None):
    auth.require(request, "read")
    return {"briefs": briefs.list(status=status, limit=limit)}


@app.get("/api/v1/briefs/{brief_id}")
def get_brief(brief_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return briefs.get(brief_id)
    except BriefError as e:
        _fail(e)


@app.post("/api/v1/briefs/{brief_id}/approve")
def approve_brief(brief_id: str, request: Request = None):
    actor = _require_owner(request)
    try:
        brief = briefs.approve(brief_id, approved_by=actor)
    except BriefError as e:
        _fail(e)
    _sync_agreement(brief_id, "brief.approved")
    return brief


@app.post("/api/v1/briefs/{brief_id}/cancel")
def cancel_brief(brief_id: str, request: Request = None):
    actor = _require_owner(request)
    try:
        brief = briefs.cancel(brief_id, actor=actor)
    except BriefError as e:
        _fail(e)
    _sync_agreement(brief_id, "brief.cancelled")
    return brief


@app.post("/api/v1/briefs/{brief_id}/critic")
def set_brief_critic(brief_id: str, body: BriefCriticSet, request: Request = None):
    """Owner chỉ định critic trước chữ ký đầu tiên (chưa có registry)."""
    actor = _require_owner(request)
    try:
        brief = briefs.set_critic(brief_id, critic=body.critic, actor=actor)
    except BriefError as e:
        _fail(e)
    _sync_agreement(brief_id, "brief.critic_assigned")
    return brief


@app.post("/api/v1/briefs/{brief_id}/decisions")
def decide_brief_script(brief_id: str, body: BriefDecisionCreate,
                        request: Request = None):
    """Owner phá bế tắc: force_agree đúng hash hiện tại, hoặc reject kèm lý do."""
    actor = _require_owner(request)
    try:
        proposal = duel.decide(brief_id, decided_by=actor, decision=body.decision,
                               proposal_id=body.proposal_id, reason=body.reason)
    except (ScriptDuelError, BriefError) as e:
        _fail(e)
    _sync_agreement(brief_id, "brief.decision")
    return proposal


@app.post("/api/v1/script-proposals")
def create_script_proposal(body: ScriptProposalCreate, request: Request = None):
    """Author nộp một version kịch bản; validator máy chạy ngay trong request."""
    auth.require(request, "ingest")
    identity, _ = auth.identity_for(request)
    payload = body.model_dump()

    def run() -> dict:
        try:
            proposal = duel.create_proposal(
                brief_id=body.brief_id, author=identity,
                shots=[s.model_dump() for s in body.shots],
                script_text=body.script_text, parent_id=body.parent_id)
        except (ScriptDuelError, BriefError) as e:
            _fail(e)
        _sync_agreement(body.brief_id, "proposal.created")
        return proposal

    return _idempotent_call(request, payload, run, 201)


@app.get("/api/v1/briefs/{brief_id}/proposals")
def list_script_proposals(brief_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return {"proposals": duel.list_proposals(brief_id)}
    except BriefError as e:
        _fail(e)


@app.get("/api/v1/script-proposals/{proposal_id}")
def get_script_proposal(proposal_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        p = duel.get_proposal(proposal_id)
        p["findings"] = duel.list_findings(proposal_id)
        return p
    except ScriptDuelError as e:
        _fail(e)


@app.post("/api/v1/script-proposals/{proposal_id}/findings")
def add_script_finding(proposal_id: str, body: ScriptFindingCreate, request: Request = None):
    """Critic ghi finding có cấu trúc; blocking=1 chặn ký pass cho tới khi có version mới."""
    auth.require(request, "ingest")
    identity, _ = auth.identity_for(request)
    try:
        finding = duel.add_finding(
            proposal_id, reviewer=identity, category=body.category,
            severity=body.severity, evidence=body.evidence, blocking=body.blocking,
            is_owner=identity == "owner")
    except ScriptDuelError as e:
        _fail(e)
    # Response của finding không mang brief_id (giữ nguyên shape) — tra 1 lần.
    try:
        brief_id = duel.get_proposal(proposal_id)["brief_id"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("finding: không tra được brief của %s: %s", proposal_id, exc)
        brief_id = None
    if brief_id:
        _sync_agreement(brief_id, "finding.created")
    return finding


@app.get("/api/v1/script-proposals/{proposal_id}/findings")
def list_script_findings(proposal_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return {"findings": duel.list_findings(proposal_id)}
    except ScriptDuelError as e:
        _fail(e)


@app.post("/api/v1/script-proposals/{proposal_id}/sign")
def sign_script_proposal(proposal_id: str, body: ScriptSignatureCreate,
                         request: Request = None):
    """Author + critic ký cùng content_hash; đủ 2 chữ ký (có author) ⇒ agreed."""
    auth.require(request, "ingest")
    identity, _ = auth.identity_for(request)
    try:
        proposal = duel.sign(proposal_id, signer=identity, verdict=body.verdict,
                             note=body.note, is_owner=identity == "owner")
    except ScriptDuelError as e:
        _fail(e)
    _sync_agreement(proposal["brief_id"], "proposal.signed")
    return proposal


@app.get("/api/v1/briefs/{brief_id}/agreement")
def get_script_agreement(brief_id: str, request: Request = None):
    """Trạng thái chốt kịch bản + next_action cho agent (build_job khi agreed)."""
    auth.require(request, "read")
    try:
        return duel.agreement(brief_id)
    except BriefError as e:
        _fail(e)


# -- YouTube Channel OAuth & Publishing ----------------------------------------

def _yt_error_response(exc: YouTubeError) -> JSONResponse:
    payload: dict = {"error": exc.code, "message": exc.message, "detail": exc.message}
    if isinstance(exc, YouTubeConflictError) and exc.upload_id:
        payload["upload_id"] = exc.upload_id
    return JSONResponse(payload, status_code=exc.status_code)


@app.get("/api/v1/youtube/status")
def youtube_status(request: Request = None):
    auth.require(request, "read")
    return youtube.get_status()


@app.get("/api/v1/youtube/auth/start")
def youtube_auth_start(request: Request = None):
    auth.require(request, "read")
    try:
        return {"auth_url": youtube.make_auth_url()}
    except YouTubeError as exc:
        return _yt_error_response(exc)


@app.get("/api/v1/youtube/auth/callback")
def youtube_auth_callback(code: str = "", state: str = "", error: str = ""):
    import urllib.parse as _up
    if error:
        return RedirectResponse(f"/youtube#/youtube?yt=error&reason={_up.quote(error)}", status_code=302)
    if not code or not state:
        return RedirectResponse("/youtube#/youtube?yt=error&reason=missing_code_or_state", status_code=302)
    try:
        youtube.handle_callback(code, state)
        return RedirectResponse("/youtube#/youtube?yt=connected", status_code=302)
    except YouTubeError as exc:
        return RedirectResponse(f"/youtube#/youtube?yt=error&reason={_up.quote(exc.message)}", status_code=302)


@app.get("/api/v1/youtube/channel")
def youtube_get_channel(request: Request = None):
    auth.require(request, "read")
    try:
        return youtube.get_channel()
    except YouTubeError as exc:
        return _yt_error_response(exc)


@app.delete("/api/v1/youtube/channel")
def youtube_disconnect_channel(request: Request = None):
    auth.require(request, "admin")
    try:
        return youtube.disconnect_channel()
    except YouTubeError as exc:
        return _yt_error_response(exc)


@app.get("/api/v1/youtube/videos")
def youtube_list_videos(limit: int = 25, request: Request = None):
    auth.require(request, "read")
    try:
        return youtube.list_videos(limit=limit)
    except YouTubeError as exc:
        return _yt_error_response(exc)


@app.patch("/api/v1/youtube/videos/{video_id}")
def youtube_patch_video(video_id: str, body: YouTubeVideoPatchRequest, request: Request = None):
    auth.require(request, "admin")
    try:
        return youtube.patch_video(
            video_id,
            title=body.title,
            description=body.description,
            tags=body.tags,
            privacy_status=body.privacy_status,
        )
    except YouTubeError as exc:
        return _yt_error_response(exc)


@app.post("/api/v1/youtube/videos/{video_id}/thumbnail")
async def youtube_set_video_thumbnail(video_id: str, request: Request):
    """Upload or replace custom thumbnail (JPEG/PNG <= 2MB) for an existing YouTube video or Short."""
    auth.require(request, "admin")
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(422, "multipart form must include 'file'")
    raw_bytes = await upload.read()
    ctype = getattr(upload, "content_type", None)
    filename = getattr(upload, "filename", None)
    try:
        return await run_in_threadpool(
            youtube.set_video_thumbnail,
            video_id,
            raw_bytes,
            content_type=ctype,
            filename=filename,
        )
    except YouTubeError as exc:
        return _yt_error_response(exc)


def _run_yt_publish(publish_fn, target_id: str, body: YouTubePublishRequest) -> JSONResponse:
    try:
        res = publish_fn(
            target_id,
            title=body.title,
            description=body.description,
            tags=body.tags,
            privacy_status=body.privacy_status,
            publish_at=body.publish_at,
            upload_captions=body.upload_captions,
            category_id=body.category_id,
        )
        status = 200 if res.get("already_published") else 202
        return JSONResponse(res, status_code=status)
    except YouTubeError as exc:
        return _yt_error_response(exc)


@app.post("/api/v1/todos/{item_id}/youtube/publish")
def youtube_publish_todo(item_id: str, body: YouTubePublishRequest, request: Request = None):
    auth.require(request, "admin")
    return _run_yt_publish(youtube.publish_todo, item_id, body)


@app.post("/api/v1/shorts/{short_id}/youtube/publish")
def youtube_publish_short(short_id: str, body: YouTubePublishRequest, request: Request = None):
    auth.require(request, "admin")
    return _run_yt_publish(youtube.publish_short, short_id, body)


@app.get("/api/v1/youtube/uploads/{upload_id}")
def youtube_get_upload(upload_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return youtube.get_upload(upload_id)
    except YouTubeError as exc:
        return _yt_error_response(exc)


@app.post("/api/v1/youtube/uploads/{upload_id}/retry")
def youtube_retry_upload(upload_id: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return youtube.retry_upload(upload_id)
    except YouTubeError as exc:
        return _yt_error_response(exc)


@app.post("/api/v1/youtube/uploads/{upload_id}/cancel")
def youtube_cancel_upload(upload_id: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return youtube.cancel_upload(upload_id)
    except YouTubeError as exc:
        return _yt_error_response(exc)


# -- Instagram Reels publishing (Instagram API with Instagram Login) -----------

def _ig_error_response(exc: InstagramError) -> JSONResponse:
    payload: dict = {"error": exc.code, "message": exc.message, "detail": exc.message}
    if isinstance(exc, InstagramConflictError) and exc.publish_id:
        payload["publish_id"] = exc.publish_id
    return JSONResponse(payload, status_code=exc.status_code)


@app.get("/api/v1/instagram/status")
def instagram_status(request: Request = None):
    auth.require(request, "read")
    return instagram.get_status()


@app.get("/api/v1/instagram/auth/start")
def instagram_auth_start(request: Request = None):
    auth.require(request, "read")
    try:
        return {"auth_url": instagram.make_auth_url()}
    except InstagramError as exc:
        return _ig_error_response(exc)


@app.get("/api/v1/instagram/auth/callback")
def instagram_auth_callback(code: str = "", state: str = "", error: str = ""):
    import urllib.parse as _up
    if error:
        return RedirectResponse(f"/publish#/publish?ig=error&reason={_up.quote(error)}", status_code=302)
    if not code or not state:
        return RedirectResponse("/publish#/publish?ig=error&reason=missing_code_or_state", status_code=302)
    try:
        instagram.handle_callback(code, state)
        return RedirectResponse("/publish#/publish?ig=connected", status_code=302)
    except InstagramError as exc:
        return RedirectResponse(f"/publish#/publish?ig=error&reason={_up.quote(exc.message)}", status_code=302)


@app.delete("/api/v1/instagram/account")
def instagram_disconnect_account(request: Request = None):
    auth.require(request, "admin")
    try:
        return instagram.disconnect_account()
    except InstagramError as exc:
        return _ig_error_response(exc)


@app.get("/api/v1/instagram/media/{item_id}")
def instagram_media_file(item_id: str, token: str = "", target_type: str = "todo"):
    """Signed URL công khai cho Instagram pull video (HMAC + expiry 24h)."""
    if not instagram.verify_signed_media_token(item_id, target_type, token):
        raise HTTPException(403, "Signed URL không hợp lệ hoặc đã hết hạn.")
    exists, video_path = instagram._resolve_video_path(target_type, item_id)  # noqa: SLF001 — endpoint nội bộ
    if not exists:
        raise HTTPException(404, "Không tìm thấy job/short.")
    if not video_path or not video_path.exists():
        raise HTTPException(404, "File video không tồn tại.")
    return FileResponse(video_path, media_type="video/mp4")


def _run_ig_publish(target_type: str, target_id: str, body: InstagramPublishRequest) -> JSONResponse:
    try:
        res = instagram.publish(
            target_type,
            target_id,
            caption=body.caption,
            affiliate_product_id=body.affiliate_product_id,
            publish_at=body.publish_at,
            share_to_feed=body.share_to_feed,
            is_ai_generated=body.is_ai_generated,
        )
        status = 200 if res.get("already_published") else 202
        return JSONResponse(res, status_code=status)
    except InstagramError as exc:
        return _ig_error_response(exc)


@app.post("/api/v1/todos/{item_id}/instagram/publish")
def instagram_publish_todo(item_id: str, body: InstagramPublishRequest, request: Request = None):
    auth.require(request, "admin")
    return _run_ig_publish("todo", item_id, body)


@app.post("/api/v1/shorts/{short_id}/instagram/publish")
def instagram_publish_short(short_id: str, body: InstagramPublishRequest, request: Request = None):
    auth.require(request, "admin")
    return _run_ig_publish("short", short_id, body)


@app.get("/api/v1/instagram/publishes")
def instagram_list_publishes(limit: int = 50, request: Request = None):
    auth.require(request, "read")
    return instagram.list_publishes(limit=limit)


@app.get("/api/v1/instagram/publishes/{publish_id}")
def instagram_get_publish(publish_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return instagram.get_publish(publish_id)
    except InstagramError as exc:
        return _ig_error_response(exc)


@app.post("/api/v1/instagram/publishes/{publish_id}/retry")
def instagram_retry_publish(publish_id: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return instagram.retry_publish(publish_id)
    except InstagramError as exc:
        return _ig_error_response(exc)


@app.post("/api/v1/instagram/publishes/{publish_id}/cancel")
def instagram_cancel_publish(publish_id: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return instagram.cancel_publish(publish_id)
    except InstagramError as exc:
        return _ig_error_response(exc)


# -- Affiliate (deeplink + bio page + sync doanh thu) ---------------------------

def _aff_error_response(exc: AffiliateError) -> JSONResponse:
    return JSONResponse(
        {"error": exc.code, "message": exc.message, "detail": exc.message},
        status_code=exc.status_code,
    )


@app.get("/api/v1/affiliate/products")
def affiliate_list_products(request: Request = None):
    auth.require(request, "read")
    return {"products": affiliate.list_products()}


@app.post("/api/v1/affiliate/products")
def affiliate_create_product(body: AffiliateProductCreate, request: Request = None):
    auth.require(request, "admin")
    try:
        return affiliate.create_product(
            network=body.network,
            label=body.label,
            cb_promoter=body.cb_promoter,
            ebay_item_url=body.ebay_item_url,
            is_active=body.is_active,
        )
    except AffiliateError as exc:
        return _aff_error_response(exc)


@app.patch("/api/v1/affiliate/products/{product_id}")
def affiliate_patch_product(product_id: str, body: AffiliateProductPatch, request: Request = None):
    auth.require(request, "admin")
    try:
        return affiliate.update_product(
            product_id,
            label=body.label,
            cb_promoter=body.cb_promoter,
            ebay_item_url=body.ebay_item_url,
            is_active=body.is_active,
        )
    except AffiliateError as exc:
        return _aff_error_response(exc)


@app.delete("/api/v1/affiliate/products/{product_id}")
def affiliate_delete_product(product_id: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return affiliate.delete_product(product_id)
    except AffiliateError as exc:
        return _aff_error_response(exc)


@app.get("/api/v1/affiliate/links")
def affiliate_list_links(item_id: str = "", limit: int = 100, request: Request = None):
    auth.require(request, "read")
    return {"links": affiliate.list_links(item_id=item_id or None, limit=limit)}


@app.post("/api/v1/affiliate/links")
def affiliate_attach_link(body: dict, request: Request = None):
    """Body: {item_id, product_id} — sinh deeplink + slug /b/{slug} cho job."""
    auth.require(request, "admin")
    item_id = (body or {}).get("item_id") or ""
    product_id = (body or {}).get("product_id") or ""
    if not item_id or not product_id:
        raise HTTPException(422, "Cần item_id và product_id")
    try:
        return affiliate.attach_product(item_id, product_id)
    except AffiliateError as exc:
        return _aff_error_response(exc)


@app.get("/api/v1/affiliate/conversions")
def affiliate_list_conversions(limit: int = 100, request: Request = None):
    auth.require(request, "read")
    return {"conversions": affiliate.list_conversions(limit=limit)}


@app.post("/api/v1/affiliate/sync")
def affiliate_sync_now(request: Request = None):
    """Bấm sync thủ công ngoài cron 6h."""
    auth.require(request, "admin")
    return {"results": affiliate.sync_all()}


@app.get("/api/v1/bio/pages/{handle}")
def bio_get_page(handle: str, request: Request = None):
    auth.require(request, "read")
    return affiliate.get_bio_page(handle)


@app.put("/api/v1/bio/pages/{handle}")
def bio_put_page(handle: str, body: BioPageUpdate, request: Request = None):
    auth.require(request, "admin")
    return affiliate.set_bio_page(handle, title=body.title, items=body.items)


# -- Public: bio page + slug redirect (cho bio TikTok/IG/YT) --------------------

@app.get("/b/{slug}")
def affiliate_redirect(slug: str, request: Request = None):
    try:
        info = affiliate.record_click(slug, referer=request.headers.get("referer", "") or "",
                                      user_agent=request.headers.get("user-agent", "") or "")
    except AffiliateError as exc:
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
            f"<h2>Link không tồn tại</h2></body></html>",
            status_code=exc.status_code,
        )
    # 302, không 301: redirect vĩnh viễn bị browser cache thì click lặp lại
    # không hit server (undercount) và đổi deeplink trên slug cũ không có hiệu lực.
    # no-store vì một số proxy vẫn cache cả 302.
    return RedirectResponse(info["deeplink"], status_code=302,
                            headers={"Cache-Control": "no-store"})


@app.get("/bio/{handle}")
def bio_page(handle: str, request: Request = None):
    # Public page: mọi label/title/url do admin nhập nhưng render cho stranger —
    # escape toàn bộ để stored XSS không chạm tới người xem bio TikTok/IG.
    page = affiliate.get_bio_page(handle)
    items = affiliate.render_bio_items(handle)
    rows = "".join(
        f"<a href='{html.escape(it['url'], quote=True)}' "
        f"style='display:block;margin:10px auto;padding:14px 18px;max-width:420px;"
        f"border:1px solid #e5e5e5;border-radius:12px;text-decoration:none;color:#111;font-weight:600'>"
        f"{html.escape(it['label'])}</a>"
        for it in items
    ) or "<p style='color:#888'>Chưa có link nào.</p>"
    title = html.escape(page.get("title") or f"@{handle}")
    # FTC disclosure cố định — trang công khai cho TikTok/IG bio (bắt buộc, xem
    # plans/README.md quyết định owner 2026-11-21).
    disclosure = ("<p style='font-size:11px;color:#999;margin-top:18px'>"
                  "We may earn a commission from links on this page.</p>")
    return HTMLResponse(
        f"<html><head><meta charset='utf-8'><title>{title}</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'></head>"
        f"<body style='font-family:-apple-system,sans-serif;background:#fafafa;padding:24px;text-align:center'>"
        f"<h2 style='margin:18px 0'>{title}</h2>{rows}{disclosure}</body></html>"
    )


# -- BGM / Background Music Collection -----------------------------------------

def _parse_bool_flag(val: object, default: bool = False) -> bool:
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


@app.post("/api/v1/bgm")
async def submit_bgm_track(request: Request):
    """Add a track into the BGM library (supports multipart/form-data & application/json)."""
    auth.require(request, "ingest")
    ctype = (request.headers.get("content-type") or "").lower()
    try:
        if "multipart/form-data" in ctype:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(422, "multipart form must include 'file'")
            raw_bytes = await upload.read()
            title = str(form.get("title") or Path(getattr(upload, "filename", "track.mp3")).stem).strip()
            mood = str(form.get("mood") or "").strip()
            if not mood:
                raise HTTPException(422, "multipart form must include 'mood'")
            tags_raw = form.get("tags")
            tags: list[str] = []
            if tags_raw:
                ts = str(tags_raw).strip()
                tags = json.loads(ts) if ts.startswith("[") else [x.strip() for x in ts.split(",") if x.strip()]
            bpm_raw = form.get("bpm")
            lufs_raw = form.get("lufs")
            tp_raw = form.get("true_peak_dbtp")
            res = await run_in_threadpool(
                bgm_service.add_track,
                title=title,
                mood=mood,
                track_type=str(form.get("track_type") or "bgm"),
                beat_role=str(form.get("beat_role") or "") or None,
                genre=str(form.get("genre") or "") or None,
                bpm=int(bpm_raw) if bpm_raw not in (None, "") else None,
                energy=str(form.get("energy") or "") or None,
                tags=tags,
                artist=str(form.get("artist") or "") or None,
                license=str(form.get("license") or "") or None,
                source_url=str(form.get("source_url") or "") or None,
                note=str(form.get("note") or "") or None,
                lufs=float(lufs_raw) if lufs_raw not in (None, "") else None,
                true_peak_dbtp=float(tp_raw) if tp_raw not in (None, "") else None,
                overwrite=_parse_bool_flag(form.get("overwrite"), False),
                allow_custom_mood=_parse_bool_flag(form.get("allow_custom_mood"), False),
                file_bytes=raw_bytes,
                filename=getattr(upload, "filename", "track.mp3"),
            )
        else:
            payload = await request.json()
            body = BgmTrackSubmitJson(**payload)
            res = await run_in_threadpool(
                bgm_service.add_track,
                title=body.title,
                mood=body.mood,
                track_type=body.track_type,
                beat_role=body.beat_role,
                genre=body.genre,
                bpm=body.bpm,
                energy=body.energy,
                tags=body.tags,
                artist=body.artist,
                license=body.license,
                source_url=body.source_url,
                note=body.note,
                lufs=body.lufs,
                true_peak_dbtp=body.true_peak_dbtp,
                overwrite=body.overwrite,
                allow_custom_mood=body.allow_custom_mood,
                filename=body.filename,
                base64_data=body.base64_data,
                file_path=Path(body.file_path) if body.file_path else None,
                file_url=body.file_url,
            )
        return JSONResponse(res, status_code=201)
    except FileExistsError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/v1/bgm")
def list_bgm_tracks(
    mood: str | None = None,
    track_type: str | None = None,
    beat_role: str | None = None,
    genre: str | None = None,
    q: str | None = None,
    request: Request = None,
):
    auth.require(request, "read")
    return bgm_service.list_tracks(mood=mood, track_type=track_type, beat_role=beat_role, genre=genre, q=q)


@app.get("/api/v1/bgm/{track_id}/file")
def get_bgm_track_file(track_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        p = bgm_service.get_track_file(track_id)
    except (KeyError, FileNotFoundError):
        raise HTTPException(404, "bgm track not found")
    return FileResponse(p, media_type="audio/mpeg", filename=p.name)


@app.delete("/api/v1/bgm/disk/{filename}")
def delete_disk_bgm_track(filename: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return bgm_service.delete_disk_track(filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except FileNotFoundError:
        raise HTTPException(404, "bgm disk file not found")


@app.delete("/api/v1/bgm/{track_id}")
def delete_bgm_track(track_id: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return bgm_service.delete_track(track_id)
    except KeyError:
        raise HTTPException(404, "bgm track not found")


# -- read_me Knowledge Base & Logs CRUD ----------------------------------------

@app.get("/api/v1/read-me")
def list_read_me_docs(prefix: str | None = None, q: str | None = None, request: Request = None):
    auth.require(request, "read")
    return readme_service.list_docs(prefix=prefix, q=q)


@app.get("/api/v1/read-me/{doc_path:path}")
def get_read_me_doc(doc_path: str, request: Request = None):
    auth.require(request, "read")
    try:
        return readme_service.get_doc(doc_path)
    except FileNotFoundError:
        raise HTTPException(404, "read_me doc not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/v1/read-me")
def create_read_me_doc(body: ReadMeCreateRequest, request: Request = None):
    auth.require(request, "ingest")
    try:
        res = readme_service.create_doc(body.path, body.content, overwrite=body.overwrite)
        return JSONResponse(res, status_code=201)
    except FileExistsError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.put("/api/v1/read-me/{doc_path:path}")
def put_read_me_doc(doc_path: str, body: ReadMePutRequest, request: Request = None):
    auth.require(request, "ingest")
    try:
        return readme_service.put_doc(doc_path, body.content)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.patch("/api/v1/read-me/{doc_path:path}")
def patch_read_me_doc(doc_path: str, body: ReadMePatchRequest, request: Request = None):
    auth.require(request, "ingest")
    try:
        return readme_service.patch_doc(
            doc_path,
            mode=body.mode,
            content=body.content,
            target_text=body.target_text,
            replacement=body.replacement,
        )
    except FileNotFoundError:
        raise HTTPException(404, "read_me doc not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.delete("/api/v1/read-me/{doc_path:path}")
def delete_read_me_doc(doc_path: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return readme_service.delete_doc(doc_path)
    except FileNotFoundError:
        raise HTTPException(404, "read_me doc not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# -- Knowledge Library API ----------------------------------------------------
# FS là source of truth; API này index repo docs read-only và cho ghi trong
# data/knowledge/ + read_me/ legacy. `/api/v1/read-me` phía trên vẫn giữ nguyên.

@app.get("/api/v1/knowledge/contract")
def knowledge_contract(format: str | None = None, request: Request = None):
    """Hợp đồng agent-facing — cùng mô hình với `/api/v1/agent-guide`.

    Agent chỉ cần một URL để có toàn bộ contract thay vì phải biết đọc 2 file
    read_me. File thiếu là lỗi vận hành: fail loud, không trả placeholder để
    agent chạy tiếp trên một bản cũ.
    """
    auth.require(request, "read")
    contract_path = Path(__file__).resolve().parents[2] / "KNOWLEDGE_AGENT_CONTRACT.md"
    if not contract_path.exists():
        raise HTTPException(status_code=404, detail="Knowledge contract file missing on server")
    content = contract_path.read_text(encoding="utf-8")

    accept = request.headers.get("accept", "") if request else ""
    fmt = (format or "").strip().lower()
    if (fmt in {"raw", "markdown", "md", "text"}
            or "text/markdown" in accept or "text/plain" in accept):
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content, media_type="text/markdown")

    return {
        "title": "Knowledge Agent Contract",
        "version": "1.0",
        "raw_contract_url": "/api/v1/knowledge/contract?format=raw",
        "web_url": "/library",
        "production_base_url": "https://vidfac.gacdero.vn",
        "auth": {"header": "Authorization: Bearer <KEY>", "minimum_scope": "read"},
        "workflow": [
            "GET /api/v1/knowledge/docs?q=... trước khi hỏi người",
            "read_only=true -> POST /docs/{id}/fork roi sua ban fork",
            "them log -> PATCH mode=append (khong can If-Match)",
            "doi noi dung -> GET lay ETag -> PUT/PATCH + If-Match",
            "412 -> doc lai, hop nhat, ghi lai (khong retry nguyen)",
            "xoa/publish/restore -> nho owner (API key khong du quyen)",
        ],
        "endpoints": [
            {"method": "GET", "path": "/api/v1/knowledge/contract", "scope": "read"},
            {"method": "GET", "path": "/api/v1/knowledge/sources", "scope": "read"},
            {"method": "GET", "path": "/api/v1/knowledge/docs", "scope": "read"},
            {"method": "GET", "path": "/api/v1/knowledge/stats", "scope": "read"},
            {"method": "GET", "path": "/api/v1/knowledge/docs/{id}", "scope": "read"},
            {"method": "GET", "path": "/api/v1/knowledge/docs/{id}/raw", "scope": "read"},
            {"method": "GET", "path": "/api/v1/knowledge/docs/{id}/versions", "scope": "read"},
            {"method": "POST", "path": "/api/v1/knowledge/docs", "scope": "ingest"},
            {"method": "POST", "path": "/api/v1/knowledge/docs/{id}/fork", "scope": "ingest"},
            {"method": "PUT", "path": "/api/v1/knowledge/docs/{id}", "scope": "ingest"},
            {"method": "PATCH", "path": "/api/v1/knowledge/docs/{id}", "scope": "ingest"},
            {"method": "DELETE", "path": "/api/v1/knowledge/docs/{id}", "scope": "owner"},
            {"method": "POST", "path": "/api/v1/knowledge/docs/{id}/restore", "scope": "owner"},
            {"method": "POST", "path": "/api/v1/knowledge/docs/{id}/versions/{n}/restore", "scope": "owner"},
            {"method": "POST", "path": "/api/v1/knowledge/docs/{id}/publish", "scope": "owner"},
            {"method": "POST", "path": "/api/v1/knowledge/sync", "scope": "admin"},
        ],
        "error_reflex": {
            "403 read_only": "POST /docs/{id}/fork",
            "403 owner_only": "stop and ask owner",
            "410 doc_trashed": "POST /docs/{id}/restore (owner only)",
            "412 hash_mismatch": "re-read, merge, write again — never blind retry",
            "422 secret_detected": "replace token with placeholder",
            "428 missing_if_match": "GET doc, copy ETag, retry with If-Match",
        },
        "full_guide": "read_me/09_knowledge_library.md",
        "endpoint_table": "read_me/02_api_contracts.md section 4b",
    }


@app.get("/api/v1/knowledge/sources")
def knowledge_sources(request: Request = None):
    auth.require(request, "read")
    try:
        return knowledge.sources()
    except KnowledgeError as exc:
        _fail(exc)


@app.get("/api/v1/knowledge/docs")
def knowledge_list_docs(q: str | None = None, source: str | None = None,
                        category: str | None = None, status: str | None = None,
                        tag: str | None = None, limit: int = 50,
                        offset: int = 0, request: Request = None):
    auth.require(request, "read")
    try:
        return knowledge.list_docs(q=q, source=source, category=category,
                                  status=status, tag=tag, limit=limit, offset=offset)
    except KnowledgeError as exc:
        _fail(exc)


@app.get("/api/v1/knowledge/stats")
def knowledge_stats(request: Request = None):
    auth.require(request, "read")
    try:
        return knowledge.stats()
    except KnowledgeError as exc:
        _fail(exc)


@app.get("/api/v1/knowledge/docs/{doc_id}/raw")
def knowledge_raw_doc(doc_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        content, title = knowledge.raw_doc(doc_id)
    except KnowledgeError as exc:
        _fail(exc)
    # Content-Disposition chỉ chứa ASCII. Title do ingest nhập có thể là tiếng Việt
    # (→ UnicodeEncodeError 500) hoặc chứa `"`/CRLF (→ inject filename/header).
    # sanitize cho phần ascii + RFC 5987 filename* cho tên thật.
    import re as _re
    from urllib.parse import quote as _quote
    raw_title = str(title or doc_id)
    # filename* giữ nguyên Unicode (RFC 5987); filename= là bản ASCII dự phòng.
    utf8_name = _re.sub(r"[\x00-\x1f/\\]+", "-", raw_title).strip("- ") or doc_id
    if not utf8_name.lower().endswith(".md"):
        utf8_name += ".md"
    utf8_name = utf8_name[-120:]
    ascii_name = _re.sub(r"[^A-Za-z0-9._-]+", "-", utf8_name).strip("-")
    ascii_name = (ascii_name or "document.md")[-120:]
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{_quote(utf8_name, safe='')}")
    return Response(content=content, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": disposition})


@app.get("/api/v1/knowledge/docs/{doc_id}/versions")
def knowledge_versions(doc_id: str, request: Request = None):
    auth.require(request, "read")
    try:
        return knowledge.versions(doc_id)
    except KnowledgeError as exc:
        _fail(exc)


@app.get("/api/v1/knowledge/docs/{doc_id}")
def knowledge_get_doc(doc_id: str, response: Response, request: Request = None):
    auth.require(request, "read")
    try:
        doc = knowledge.get_doc(doc_id)
    except KnowledgeError as exc:
        _fail(exc)
    response.headers["ETag"] = doc["etag"]
    return doc


@app.post("/api/v1/knowledge/docs")
def knowledge_create_doc(body: KnowledgeCreateRequest, request: Request = None):
    auth.require(request, "ingest")
    identity, _ = auth.identity_for(request)
    payload = body.model_dump()

    def run() -> dict:
        try:
            return knowledge.create_doc(
                source=body.source, path=body.path, content=body.content,
                actor=identity, title=body.title, category=body.category,
                tags=body.tags, source_refs=body.source_refs,
                overwrite=body.overwrite)
        except KnowledgeError as exc:
            _fail(exc)

    return _idempotent_call(request, payload, run, 201)


@app.post("/api/v1/knowledge/docs/{doc_id}/fork")
def knowledge_fork_doc(doc_id: str, body: KnowledgeForkRequest, request: Request = None):
    auth.require(request, "ingest")
    identity, _ = auth.identity_for(request)
    payload = {"doc_id": doc_id, **body.model_dump()}

    def run() -> dict:
        try:
            return knowledge.fork_doc(
                doc_id, identity, path=body.path, title=body.title,
                category=body.category, tags=body.tags)
        except KnowledgeError as exc:
            _fail(exc)

    return _idempotent_call(request, payload, run, 201)


@app.put("/api/v1/knowledge/docs/{doc_id}")
def knowledge_put_doc(doc_id: str, body: KnowledgeUpdateRequest,
                      response: Response, request: Request = None):
    auth.require(request, "ingest")
    identity, _ = auth.identity_for(request)
    try:
        doc = knowledge.update_doc(
            doc_id, body.content, identity, request.headers.get("If-Match"),
            title=body.title, category=body.category, tags=body.tags,
            note=body.note or "put")
    except KnowledgeError as exc:
        _fail(exc)
    response.headers["ETag"] = doc["etag"]
    return doc


@app.patch("/api/v1/knowledge/docs/{doc_id}")
def knowledge_patch_doc(doc_id: str, body: KnowledgePatchRequest,
                        response: Response, request: Request = None):
    auth.require(request, "ingest")
    identity, _ = auth.identity_for(request)
    try:
        doc = knowledge.patch_doc(
            doc_id, mode=body.mode, content=body.content, actor=identity,
            if_match=request.headers.get("If-Match"), target_text=body.target_text,
            replacement=body.replacement, note=body.note or "patch")
    except KnowledgeError as exc:
        _fail(exc)
    response.headers["ETag"] = doc["etag"]
    return doc


@app.delete("/api/v1/knowledge/docs/{doc_id}")
def knowledge_delete_doc(doc_id: str, request: Request = None):
    identity = _require_owner(request)
    try:
        return knowledge.delete_doc(doc_id, identity)
    except KnowledgeError as exc:
        _fail(exc)


@app.post("/api/v1/knowledge/docs/{doc_id}/restore")
def knowledge_restore_doc(doc_id: str, request: Request = None):
    identity = _require_owner(request)
    try:
        return knowledge.restore_doc(doc_id, identity)
    except KnowledgeError as exc:
        _fail(exc)


@app.post("/api/v1/knowledge/docs/{doc_id}/versions/{version_no}/restore")
def knowledge_restore_version(doc_id: str, version_no: int, request: Request = None):
    identity = _require_owner(request)
    try:
        return knowledge.restore_version(doc_id, version_no, identity)
    except KnowledgeError as exc:
        _fail(exc)


@app.post("/api/v1/knowledge/docs/{doc_id}/publish")
def knowledge_publish_doc(doc_id: str, body: KnowledgePublishRequest,
                          request: Request = None):
    identity = _require_owner(request)
    try:
        return knowledge.publish_doc(doc_id, identity, body.status)
    except KnowledgeError as exc:
        _fail(exc)


@app.post("/api/v1/knowledge/sync")
def knowledge_sync(body: KnowledgeSyncRequest | None = None, request: Request = None):
    auth.require(request, "admin")
    identity, _ = auth.identity_for(request)
    try:
        return knowledge.sync(prune_missing=body.prune_missing if body else True,
                              actor=identity, trigger="api")
    except KnowledgeError as exc:
        _fail(exc)

