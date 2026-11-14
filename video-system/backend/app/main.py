"""FastAPI wiring — the single door (plan §1.6).

Every POST accepts Idempotency-Key; replays return the first response.
Every mutation writes events; matching webhooks are dispatched.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from . import domain
from .auth import SESSION_COOKIE, SESSION_TTL_SEC, Auth, webauth
from .config import db_path, data_dir, RENDER_SPEC, AUDIO_CONFIG, REPO_ROOT
from .providers import AdapterNotConfigured, registry
from .schemas import (AudioRegenerateRequest, BatchCreate, BgmTrackSubmitJson,
                      CharacterSheetCreate,
                      ConfirmAssembleRequest, FeedbackBatchCreate, FeedbackItemCreate,
                      FeedbackResolveRequest, GateDecision, GenerateRequest,
                      HoldRequest, ItemCreate, ShortCreate, ShortScriptUpdate,
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
from .storage_gdrive import check_storage_health, cleanup_item_intermediates, sync_item_to_gdrive

from .service_batches import BatchService
from .service_bgm import BgmService
from .service_import import ImportGate, ImportRejected, sha256_file
from .service_items import ItemService
from .service_shots import ShotService
from .service_shorts import ShortService, ShortfallError
from .service_todos import TodoService
from .service_youtube import YouTubeConflictError, YouTubeError, YouTubeService
from .store import SQLiteStore, new_id, now
from .webhooks import WebhookService

logger = logging.getLogger("video_system.main")

app = FastAPI(title="video-system", version="0.2.0",
              description="EN-Only TODO & External Video Assembly Pipeline (plan v3.2)")
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
store = SQLiteStore(db_path())
items = ItemService(store)
shots = ShotService(store, items)
batches = BatchService(store, items, shots)
importer = ImportGate(store, items)
todos = TodoService(store, items)
assembler = AssemblerService(store, items, todos)
webhooks = WebhookService(store)
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
bgm_service = BgmService(store)


@app.on_event("startup")
def _start_news_scheduler() -> None:
    # Phụ lục G.10 — hộp tin nóng: chỉ bật khi VIDEO_NEWS_SYNC_ENABLED=1.
    start_news_scheduler(store)
    youtube.startup_sweep()

# -- Web PIN gate (chặn mọi request không có session/bearer hợp lệ) -----------
# Mở: health, 3 endpoint auth/*, OPTIONS (preflight), "/favicon.ico", và "/" (shell HTML chứa
# màn hình khóa — dữ liệu vẫn nằm sau API được bảo vệ).
_WEB_OPEN_PATHS = {"/", "/favicon.ico", "/api/v1/health", "/api/v1/auth/login",
                   "/api/v1/auth/session", "/api/v1/auth/logout",
                   "/api/v1/youtube/auth/callback"}


@app.middleware("http")
async def pin_gate(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path in _WEB_OPEN_PATHS:
        return await call_next(request)
    if webauth.request_allowed(request):
        return await call_next(request)
    return JSONResponse({"detail": "Yêu cầu đăng nhập — nhập mã PIN để tiếp tục"},
                        status_code=401)


# -- auth: PIN login -----------------------------------------------------------

@app.post("/api/v1/auth/login")
def web_login(body: WebLoginRequest, request: Request):
    ip = webauth.client_ip(request)
    webauth.verify_pin(body.pin, ip)
    token = webauth.make_token()
    is_https = (request.url.scheme == "https") or (
        request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    resp = JSONResponse({"authenticated": True,
                         "expires_in_sec": SESSION_TTL_SEC})
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SEC,
                    httponly=True, samesite="lax",
                    secure=is_https, path="/")
    return resp


@app.get("/api/v1/auth/session")
def web_session(request: Request):
    return {"authenticated": webauth.session_ok(request)}


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


@app.get("/api/v1/health")
def health():
    db_ok = store.query_one("SELECT 1 AS ok")["ok"] == 1
    return {"status": "ok" if db_ok else "degraded", "db": db_ok,
            "style_lock": RENDER_SPEC["version"],
            "audio_loudness_lufs": AUDIO_CONFIG["master"]["loudness_lufs"],
            "auth_configured": not auth.keys == {}}


@app.api_route("/favicon.ico", methods=["GET", "HEAD"])
def favicon():
    return Response(status_code=204)


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    return FileResponse(
        Path(__file__).parents[2] / "frontend" / "index.html",
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
                todos.submit_video,
                item_id,
                seq_num=seq_num,
                file_bytes=raw_bytes,
                filename=getattr(upload, "filename", "video.mp4"),
                covered_shot_indexes=covered,
                note=note,
            )
        else:
            payload = await request.json()
            body = TodoVideoSubmitJson(**payload)
            res = await run_in_threadpool(
                todos.submit_video,
                item_id,
                seq_num=body.seq_num,
                file_path=Path(body.file_path) if body.file_path else None,
                file_url=body.file_url,
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
        result = assembler.confirm_and_assemble(
            item_id,
            burn_subtitles=req.burn_subtitles,
            allow_shortfall=req.allow_shortfall,
            watermark_enabled=req.watermark_enabled,
            watermark_text=req.watermark_text,
            allow_missing_thumbnail=req.allow_missing_thumbnail,
        )
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
        guide_path = Path("AGENT_API_GUIDE.md")
    content = guide_path.read_text(encoding="utf-8") if guide_path.exists() else "# External Agent API Guide\n"

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
    return FileResponse(p, media_type="audio/wav",
                        filename=f"{short_id}_voice_vi.wav",
                        content_disposition_type="attachment" if download else "inline")


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
        return RedirectResponse(f"/#/youtube?yt=error&reason={_up.quote(error)}", status_code=302)
    if not code or not state:
        return RedirectResponse("/#/youtube?yt=error&reason=missing_code_or_state", status_code=302)
    try:
        youtube.handle_callback(code, state)
        return RedirectResponse("/#/youtube?yt=connected", status_code=302)
    except YouTubeError as exc:
        return RedirectResponse(f"/#/youtube?yt=error&reason={_up.quote(exc.message)}", status_code=302)


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


@app.post("/api/v1/todos/{item_id}/youtube/publish")
def youtube_publish_todo(item_id: str, body: YouTubePublishRequest, request: Request = None):
    auth.require(request, "admin")
    try:
        res = youtube.publish_todo(
            item_id,
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


@app.delete("/api/v1/bgm/{track_id}")
def delete_bgm_track(track_id: str, request: Request = None):
    auth.require(request, "admin")
    try:
        return bgm_service.delete_track(track_id)
    except KeyError:
        raise HTTPException(404, "bgm track not found")




