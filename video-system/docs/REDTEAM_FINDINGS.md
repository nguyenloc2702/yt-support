# REDTEAM_FINDINGS — video-system @ http://127.0.0.1:8123

- Ngày chạy: 2026-11-05. Tester: red-team chức năng (black-box qua HTTP thật + đọc source để hiểu, không sửa source).
- Keys dùng: `dev-admin` (admin), `dev-ingest` (ingest), `dev-read` (read) — header `Authorization: Bearer <key>`.
- Dữ liệu test: item tạo riêng (`RT item A cross`, `RT item B cross`, `RT sabotage`, `RT statemachine`, `RT race*`). **Không đụng `itm_1035b9eee174`.**
- Audio test: `C:/app/work/video-system/data/uploads/rt_audio_32s.wav` (32s, sinh bằng stdlib `wave`), import qua `audio.file_path`.
- Script: `C:/app/work/rt_run.py` + addendum race `python inline` (kết quả chi tiết: `C:/app/work/rt_results.json`, `C:/app/work/rt_race2.json`).
- Bằng chứng dưới đây là response thật đã chạy. 4 lần chạy lặp cho kết quả nhất quán.

## Bảng findings

| Mức | Kịch bản | Cách tái hiện | Response thật | Đề xuất fix |
|---|---|---|---|---|
| **P0** | Authz chéo item: shot của item A thao tác được qua path item B | Import 4 shot vào item A + item B, tạo batch đưa shot A0 về `keyframe_pending`, rồi `POST /api/v1/items/{itemB}/shots/{shotA}/hold` với `{"reason":"TIMECODE_SHIFTED"}` (key ingest) | **200** — shot thuộc `item_id: itm_cae154cb9c66` (item A) bị đặt `status: held, held_reason: TIMECODE_SHIFTED, held_detail: "via item B path"` dù path là item B. Tương tự `POST /items/{itemB}/shots/{shotA2}/keyframe/approve` → **200** (`keyframe_approved`), `POST /items/{itemB}/shots/{shotA2}/generate` → **200** (held `REF_IMAGE_MISSING` — transition vẫn diễn ra) | Mọi endpoint shot (`hold`, `retry`, `keyframe/approve`, `generate`, `qc`) phải load shot rồi kiểm tra `shot["item_id"] == item_id` trong path, không khớp → 404/403. Hiện tại `ShotService` chỉ lookup theo `shot_id`, `item_id` trong path bị bỏ qua hoàn toàn |
| **P1** | Import chấp nhận `index` âm | `POST /shots/import` với shots `[{"index": -5, "narration": "a", "audio_start_sec": 0, "audio_end_sec": 32, "visual_prompt_clean": "a"}]` (audio 32s khớp) | **201** — `{"item_id": "...", "script_version": 900007, "count": 1, ...}`. Shot với `index_num = -5` được insert. Lặp lại với `index: -1` cũng **201** (`script_version: 900002`) | `_validate_shots` chỉ check duplicate/adjacency, không check `index >= 0`. Thêm check `0 <= index < max_shots_per_import` (và nên check index liên tiếp 0..n-1) |
| **P2** | Lỗi fetch không được map → 500 thay vì 422 | `POST /shots/import` với `keyframe_asset_url: "http://localhost:9999/kf.png"` (host nằm trong allowlist mặc định nhưng kết nối bị từ chối) | **500** `Internal Server Error` (uvicorn log traceback: `urllib.error.URLError` ném ra từ `fetch_url` không được bắt trong `ImportGate.import_shots`) | Bọc `fetch_url` trong try/except (URLError, socket.timeout, HTTPError) → `ImportRejected({"error": "audio_fetch_failed"/"keyframe_fetch_failed", "detail": ...}, 422)` |
| **P2** | DELETE item không tồn tại → 500 | `DELETE /api/v1/items/itm_nonexistent_rt` (key admin) | **500** `Internal Server Error` (KeyError từ `items.soft_delete` không được map 404) | Bắt `KeyError` trong route DELETE → 404 "item not found" |
| **P2** | Idempotency-Key không bind với body | `POST /items` 2 lần, cùng `Idempotency-Key`, body khác title ("RT idem 1" / "RT idem 2 KHAC TITLE") | Lần 2 trả **201** `item_id: itm_4345cff16758` (item của lần 1) + header `Idempotency-Replayed: true` — response cũ được replay, body thứ 2 bị bỏ qua âm thầm | Đúng spec §1.6 (replay-first-response) nhưng nên lưu hash body vào bảng idempotency và trả 409/422 khi key được tái sử dụng với body khác, tránh client tưởng request thứ 2 đã được xử lý |

## Các kịch bản PASS (đã chạy thật, fail-closed đúng)

| Mức | Kịch bản | Cách tái hiện | Response thật | Ghi chú |
|---|---|---|---|---|
| **PASS** | Import thiếu field bắt buộc | shots `[{"index":0,"audio_start_sec":0,"visual_prompt_clean":"x"}]` (thiếu `narration`, `audio_end_sec`) | 422 `{"error":"schema_violations","shots":[{"index":0,"reason":"missing_fields:['audio_end_sec', 'narration']"}]}` | |
| **PASS** | `audio_end <= audio_start` | shot index 0 với `audio_end_sec: 0` | 422 `{"error":"schema_violations","shots":[{"index":0,"reason":"bad_segment"}]}` | |
| **PASS** | `on_screen_type` lạ | `"on_screen_type": "laser_text"` | 422 `{"reason":"invalid_on_screen_type"}` | |
| **PASS** | `on_screen_text` trên shot_type `scene` | `"on_screen_text":" hacked","shot_type":"scene"` | 422 `{"reason":"on_screen_text_only_allowed_for_quote_diagram"}` | |
| **PASS** | Duplicate index | 2 shot cùng index 0 | 422 `{"reason":"duplicate_index"}` | |
| **PASS** | `script_version = 0` | body `script_version: 0` | 422 Pydantic `"Input should be greater than or equal to 1"` | |
| **PASS** | Re-import cùng script_version, nội dung khác | import sv=N (201), import lại sv=N với narration khác | 409 `{"error":"script_version_conflict","hint":"same script_version with different content — bump version"}` | |
| **PASS** | SSRF metadata: keyframe URL `http://169.254.169.254/latest/meta-data/` | POST import với keyframe URL trên | 422 `{"error":"url_host_not_allowed","host":"169.254.169.254"}` | Chặn trước khi fetch |
| **PASS** | Keyframe URL host ngoài allowlist | `http://evil.example.com/kf.png` | 422 `{"error":"url_host_not_allowed","host":"evil.example.com"}` | |
| **PASS** | `audio.file_path` ngoài data dir | `"file_path": "C:/Windows/win.ini"` | 422 `{"error":"file_path_outside_data_dir","hint":"upload the file first or use audio.file_url"}` | Sandbox chặn hash-oracle |
| **PASS** | Import thiếu audio hoàn toàn | body không có `audio` | 422 Pydantic `"Field required"` | |
| **PASS** | Duration payload không được tin | import với `duration_sec: 999` (audio thật 32s) | 201 kèm `"duration_sec_probed": 32.0, "duration_sec_payload": 999, "duration_trusted": false` | Probe wav (wave header + size cross-check) |
| **PASS** | Generate 2 lần liên tiếp | approve keyframe → generate (lần 1: held `REF_IMAGE_MISSING`) → generate lần 2 | Lần 2: **409** `{"detail":"generation requires keyframe_approved (no jump over QC)"}` | Transition table chặn |
| **PASS** | Hold reason sai group — human ở `keyframe_pending` | `POST hold {"reason":"HUMAN_REJECT"}` | 409 `"reason HUMAN_REJECT (human) invalid when held from keyframe_pending; allowed groups: ['keyframe', 'workflow']"` | |
| **PASS** | Hold reason sai group — video ở `keyframe_pending` | `POST hold {"reason":"DURATION_MISMATCH"}` | 409 `"reason DURATION_MISMATCH (video) invalid when held from keyframe_pending; allowed groups: ['keyframe', 'workflow']"` | |
| **PASS** | Hold reason không tồn tại | `POST hold {"reason":"TOTALLY_FAKE"}` | 409 `"'TOTALLY_FAKE' is not a valid HeldReason"` | |
| **PASS** | Hold reason hợp lệ (workflow) ở `keyframe_pending` | `POST hold {"reason":"TIMECODE_SHIFTED"}` | 200, shot `held` | Đường vui hoạt động |
| **PASS** | Retry shot held human/workflow reason | shot held `TIMECODE_SHIFTED` → `POST retry` | 409 `"held_reason TIMECODE_SHIFTED is resolved by human review, not retry"` | A12/A13 đúng |
| **PASS** | Retry shot held keyframe reason (đường hợp lệ) | generate → held `REF_IMAGE_MISSING` → `POST retry` | 200, shot về `keyframe_pending` | |
| **PASS** | Retry khi không held | `POST retry` trên shot `keyframe_pending` | 409 `"retry requires held"` | |
| **PASS** | Generate từ `pending` (nhảy cóc) | `POST generate` trên shot pending | 409 `"generation requires keyframe_approved (no jump over QC)"` | |
| **PASS** | Race approve + approve cùng shot | 2 thread POST `keyframe/approve` song song (4 trial) | Luôn `[200, 409]` — CAS giữ đúng 1 lần thắng | `cas_update WHERE status=?` |
| **PASS** | Race generate + generate cùng shot | 2 thread POST `generate` song song (4 trial + 1 lần qua item B path) | Luôn `[200, 409]`, final `held REF_IMAGE_MISSING`, `attempts=1`, `video_asset_id: None`, **0 asset** trong DB | Không tạo 2 asset. Lưu ý: generation adapter chưa cấu hình nên fail-closed tại `REF_IMAGE_MISSING` trước khi tới `_produce` — race 2-asset thật chỉ có thể xác nhận khi đã cắm provider |
| **PASS** | Race approve + generate cùng shot | 2 thread song song | 1×200 + 1×409, final state nhất quán (held `REF_IMAGE_MISSING`) | |
| **PASS** | Race `POST /items` cùng Idempotency-Key song song | 2 thread cùng key | Cả 2 trả cùng `item_id` — chỉ 1 item được tạo | |
| **PASS** | Idempotency replay | POST /items 2 lần cùng key | Lần 2 trả cùng `item_id`, header `Idempotency-Replayed: true` | |
| **PASS** | Scope: read key gọi POST | `POST /items`, `POST /shots/import` bằng `dev-read` | 403 `{"detail":"requires scope ingest"}` | |
| **PASS** | Scope: ingest key gọi DELETE item | `DELETE /api/v1/items/{id}` bằng `dev-ingest` | 403 `{"detail":"requires scope admin"}` | |
| **PASS** | Scope: ingest key approve batch | `POST /batches/{id}/approve` bằng `dev-ingest` | 403 `{"detail":"requires scope admin"}` | |
| **PASS** | Scope: ingest key approve gate | `POST /items/{id}/approve` bằng `dev-ingest` | 403 `{"detail":"requires scope admin"}` | |
| **PASS** | Scope: ingest key đăng ký webhook | `POST /webhooks` bằng `dev-ingest` | 403 `{"detail":"requires scope admin"}` | |
| **PASS** | Không key / key lạ | POST không header; `Bearer nope` | 401 `missing bearer key` / 401 `unknown key` | |
| **PASS** | Read key đọc được data | `GET /items` bằng `dev-read` | 200 | Phân quyền đọc đúng cấp |

## Tổng kết

- **1 P0**: mọi shot-endpoint không kiểm tra quan hệ shot↔item trong path (IDOR chéo item) — sửa bằng check `shot.item_id == item_id` ở tất cả route `/items/{item_id}/shots/{shot_id}/...`.
- **1 P1**: import gate thiếu kiểm tra `index >= 0`.
- **3 P2**: 500 không map (fetch_url lỗi, DELETE item lạ), idempotency không bind body.
- State machine, hold-group rules, retry-from-held, import schema gate, SSRF allowlist, sandbox file_path, idempotency replay và phân quyền scope đều fail-closed đúng trên mọi case đã thử.
- Hạn chế kiểm thử: generation/OCR/probe(ảnh) adapter chưa cấu hình → các case keyframe URL thật và race gắn 2 video asset chỉ xác nhận được đến mức fail-closed hiện tại.
