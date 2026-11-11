# E2E PLAN — video-system

Định nghĩa E2E ở đây: kịch bản đi qua **API thật** (FastAPI TestClient hoặc
uvicorn + HTTP thật), dùng adapter thật khi có (ffprobe) và adapter giả chỉ
cho phần chưa cắm (generation/OCR). Mỗi kịch bản có: tiền đề, bước, điều kiện
đạt (assert), và map về plan v3.2.

## E2E-1 — Chuẩn Shot: import → gen → QC → append (core loop)

**Map:** §3.2, §4.B, §4.C, Phụ lục A/B. **Test:** `test_full_e2e_flow_to_appended`.

1. POST /items (prebuilt_import) → 201 `draft`.
2. POST /items/{id}/shots/import với audio 32s + 4 shot × 8s → 201, item
   `awaiting_review`, `duration_trusted=false`, shots_hash trả về.
3. Import lại đúng payload → `replayed=true` (idempotent).
4. POST /approve gate script + audio (scope **admin**) → gates approved.
5. POST /batches size 2 → 2 shot `keyframe_pending`, item `generating`.
6. Per shot: keyframe/approve → keyframe/generate → shot `generated`,
   `render_engine="veo"` (không hạ cấp âm thầm) → qc → `qc_passed`.
7. POST /batches/{id}/approve (scope **admin**) → `appended=[1,2]`,
   cumulative manifest_hash thay đổi, duration 16.0s.
8. GET /cumulative → segments [appended, appended].
9. Shot 3, 4 vẫn `keyframe_pending` — không có gì bị nối oan.

**Đạt khi:** toàn bộ assert xanh + events log đủ `shots.imported`,
`gate.decision`, `shot.status`, `batch.approved`.

## E2E-2 — Held chặn dòng: luật không vượt shot held

**Map:** §4.B.7, đúng workflow BlackRock 034-037.

1. Như E2E-1 đến bước batch 4 shot.
2. Shot 2 held (`KEYFRAME_STYLE_FAIL`); shot 1, 3 qc_passed.
3. Approve batch → chỉ `appended=[1]`, `stopped_at=2`; shot 3 VẪN `qc_passed`.
4. Retry shot 2 (keyframe group) → `keyframe_pending` → duyệt lại keyframe →
   gen → qc → approve batch lần 2 → `appended=[2,3]`.

**Đạt khi:** thứ tự append tuyệt đối liên tục; manifest hash sau lần 2 ≠ lần 1.

## E2E-3 — Import gate từ chối bằng chứng (report 422)

**Map:** §3.2, Phụ lục D.

1. Import cặp lệch (audio 30s, shots 32s) → 422, report có
   `duration_sec_probed`, `duration_trusted=false`, `global_drift_sec`, hint
   "Không tự kéo giãn".
2. Import shot scene có `on_screen_text` → 422 `schema_violations`.
3. Import trùng version khác nội dung → 409; bump version → shots cũ
   `superseded`, lịch sử giữ nguyên.

## E2E-4 — QC fail-closed khi thiếu công cụ

**Map:** §4.B.5. Bỏ OCR adapter khỏi registry → QC không được phép pass
→ held `QC_TOOL_UNAVAILABLE`. Chống lại bệnh "validator placeholder luôn True".

## E2E-5 — Auth & scope

1. Unset VIDEO_API_KEYS + client ngoài loopback → 401 (deny-by-default).
2. Key `read` gọi POST → 403; key `ingest` approve → 403 (E2E-1 đã phủ);
   key `admin` full.
3. Sai key → 401.

## E2E-6 — Webhook chữ ký

1. Đăng ký webhook (events: shot.held) với secret.
2. Trigger shot held → receiver nhận POST header `X-Signature` =
   HMAC-SHA256(secret, raw body); verify lại bằng `sign()`.
3. Event không đăng ký ký (vd shot.appended) → KHÔNG nhận.

## E2E-7 — Migration BlackRock (Phụ lục F) — sau Giai đoạn 1

1. Import 33 shot đã có + shot 034 với status held (`HUMAN_REJECT`/lý do gốc).
2. Keyframe 034-037 qua `keyframe_asset_url` → validate 1280x720.
3. Video 035-037 KHÔNG được mark appended — chỉ asset kèm ghi chú.
4. Progress khởi tạo: `0 appended / 169` — số cũ chỉ là context.

## E2E-8 — Generation thật (sau khi cắm adapter)

1. Adapter Veo/KB thật + ffprobe thật + OCR thật.
2. Chạy E2E-1 với media thật; assert render_engine ghi đúng engine đã gọi,
   manifest hash khớp sha256 file thật.
3. Chạy 429/quota path: provider ném QUOTA → shot `retry_later` với
   `next_retry_at`, KHÔNG tiêu attempt.

## E2E-9 — Short Studio flow (Phụ lục G) — `tests/test_shorts_flow.py`

1. `POST /shorts` (mode news, FakeLlm) → `scripted`, 107 từ trong ngân sách,
   5 scenes, image_prompt phong cách crayon dọc.
2. PATCH script → self-loop hợp lệ, word_count cập nhật; beats rỗng → 422.
3. `POST /shorts/{id}/produce` (offline TTS + fake ffmpeg + bank PNG thật):
   `ready`, fake container `SHORT_MP4_CONTAINER|geometry=1080x1920@30`,
   scene windows từ cue, word timeline JSON, hero gen ×2 + bank ×3.
4. FileResponse: video/audio/ass (chứa `{\k`)/publish.md (title + hashtag +
   nguồn) /images/{seq}/file (gen + bank đều resolve được).
5. Fail-closed: thiếu LLM → 503; thiếu ảnh nguồn (không adapter + bank trống)
   → 422 tiếng người + short `failed` với `error_detail`.
6. Scope: read POST → 403; delete cần admin; Idempotency-Key replay.
7. Hộp tin nóng G.10: parse RSS → dedupe theo link_hash → exclude keyword;
   thiếu Telegram → push 0 không lỗi; scheduler mặc định tắt.

## Chạy

```bash
.venv/bin/pytest tests/test_qc_and_batches.py tests/test_import_gate.py -q  # E2E-1..5, 8(fake)
.venv/bin/pytest -q                                                      # tất cả
VIDEO_DATA_DIR=data .venv/bin/python -m backend.app.seed                 # data demo UI
```
