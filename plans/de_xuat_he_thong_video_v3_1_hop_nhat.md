# Đề xuất hệ thống sản xuất video v3.2 — Item-centric (ĐÃ ĐÓNG BĂNG CHỜ CODE)

> **Trạng thái tài liệu:** v3.2 — đã áp đầy đủ 10 điểm P0 + 8 điểm P1 của vòng
> review thứ hai. Các chỗ trống từng buộc agent "tự điền theo cảm tính" đã được
> định nghĩa chặt bằng phụ lục A-F (bảng transition, render spec, enum
> held_reason, JSON mẫu import report, JSON mẫu webhook, spec migration
> BlackRock). Tài liệu này là nền duy nhất để code; mọi quyết định mới sau khi
> đóng băng phải sửa vào đây, không nằm ở chat.
>
> Lịch sử: v3.1 (hợp nhất audit repo + reviewer Item-centric + feedback UI
> skills) → v3.2 (áp review P0/P1, thêm phụ lục, tách migration khỏi lõi).
>
> Ngày: 2026-11-05. Số tuần ở §10 là ước lượng, không cam kết.

---

## 0. Bối cảnh và nguồn gốc

### 0.1. Hệ thống hiện tại

Repo `support-video-edit` chứa 3 lớp trong 1 repo:
1. **Streamlit Studio** (`app.py` + 12 trang) — biên tập local.
2. **Automated Factory** (`core/worker.py` + `pipeline/automated_factory.py`) —
   Telegram điều khiển, `/reup` và `/remake`, Veo qua cookie Google Pro.
3. **Job Inbox API** (`services/job_inbox_api.py`, :8787) — nhận job Muse.

### 0.2. Kết luận audit (bằng chứng file:line)

**Nhạy bén, giữ làm nền:** CAS transition có bảng luật + double-guard
(`job_queue.py:393-455`); heavy lock heartbeat + fail-loud (`job_queue.py:126-127`);
4-gate hash versioned + manifest re-hash trước mỗi shot
(`approval_state_machine.py:1162-1205`, `worker.py:220-241`); tách sinh khỏi
upload; upload idempotent + MD5 verify; audio stack (word timeline, ducking,
`normalize=0` + loudnorm, pairwise amix); atomic write kỷ luật; zero-fake Mode 2.

**Rác, không được lặp lại:**

| # | Vấn đề | Bằng chứng | Mức |
|---|---|---|---|
| 1 | Hot-loop shot PENDING: shot fail lúc verify-manifest khi còn PENDING, nhưng `mark_shot_failed` chỉ UPDATE `WHERE status='IN_PROGRESS'` → kẹt, chọn lại ngay, không sleep, không tăng attempt | `job_queue.py:751-766` + `worker.py:220-241` | 🔴 |
| 2 | Telegram guard fail-open *bên trong class*: `:174` chỉ chặn khi `allowed_chat_id` truthy. Trong đường worker bình thường KHÔNG có bypass (`worker.py:124-125` chỉ start bot khi `is_configured` = token **và** chat_id, `telegram_bot.py:116`) — nhưng class không tự bảo vệ nếu gọi `start_polling` trực tiếp. Thiết kế mới phải deny-by-default ở cấp class, không phụ thuộc caller | `telegram_bot.py:116,174`, `worker.py:124-125` | 🟠 |
| 3 | 3 Style Bible mâu thuẫn | `prompt_sanitizer.py:115-124,227-234`, `script_auditor.py:897-902`, `algorithm_qa.py:45-48` | 🔴 |
| 4 | Sanitizer phá nội dung: xóa ngoặc, đổi war→"historic era", hardcode tên, ép "No text" | `prompt_sanitizer.py:50-124`, `gemini_webapi_provider.py:39-65` | 🔴 |
| 5 | Hạ cấp âm thầm, không cột `render_engine` | `worker.py:261-312` | 🔴 |
| 6 | `is_key_scene` luôn True → B-roll dead code, 100% đốt Veo | `script_factory.py:1456,1462`, `script_auditor.py:1086,1093` | 🔴 |
| 7 | Audio bị video lái: pad/cắt master, atempo 1.35 + fade `truncated=True` vẫn ship | `documentary_assembler.py:246-425,360` | 🔴 |
| 8 | Chữ tự thêm, không allowlist | `documentary_assembler.py:145-154` | 🟠 |
| 9 | `reset_interrupted_shots` nhận `worker_id` nhưng không dùng | `job_queue.py:809-817` | 🟠 |
| 10 | Kill-switch bị vượt: đường ảnh scan thẳng `sessions/` | `gemini_image_provider.py:142-156` | 🟠 |
| 11 | 3 hệ state song song | `core/database.py`, `job_queue.py:23`, `task_state.py:16` | 🟠 |
| 12 | 2 đường transition (raw UPDATE bypass luật) | `job_queue.py:591-647` | 🟠 |
| 13 | Muse dead-end RENDERING + không verify lại Gate 4 | `automated_factory.py:484-489` | 🟠 |
| 14 | 3 chuẩn thời lượng/WPM lệch nhau | `script_factory.py:79-81`, `algorithm_qa.py:34` | 🟡 |
| 15 | Streamlit blocking + polling rerun + stage/badge giả | `05_media_analysis.py:162-186`, `02_analysis.py:296-315`, `07_export.py:944-970` | 🟡 |
| 16 | Pages bypass service ghi JSON trực tiếp | `05_script_review.py:322-324` | 🟡 |
| 17 | Secrets: cookie qua chat, session plaintext, token trong URL | `telegram_bot.py:509-566` | 🟡 |
| 18 | Thiếu rclone vẫn DONE/COMPLETED | `worker.py:161-168` | 🟠 |
| 19 | `delete_project` rmtree không kiểm tra job đang chạy | `project_service.py:206-216` | 🟠 |
| 20 | Repo bãi rác + `PYTEST_CURRENT_TEST` vào production path | root, `script_auditor.py:1017-1018` | 🟡 |

### 0.3. Nguồn hợp nhất

(1) Audit repo đầy đủ — 3 agent. (2) Đề xuất Item-centric reviewer song song +
phụ lục v3.1. (3) Feedback UI/anti-slop skills. (4) Vòng review thứ hai
(10 P0 + 8 P1) — nguồn của v3.2.

---

## 1. Nguyên tắc thiết kế (không thỏa hiệp)

1. **Item là source of truth.** Mọi resource thuộc Item, có ID/version/hash.
   Không state nằm trong log Telegram hay JSON lỏng.
2. **Audio-first tuyệt đối.** Cấm pad/cắt master theo video, cấm atempo/fade
   TTS để vừa hình. Shot lệch audio → HELD.
3. **Đúng 2 file khóa, mỗi tầng 1 file, versioned + test assert:**
   `style_lock_v3.yaml` (hình ảnh) và `DESIGN.md` (UI). Cấu hình kỹ thuật
   (loudness, WPM, tempo, timeout, QC threshold) nằm ở `audio_config.yaml` /
   `render_spec.yaml` / channel profile — một khái niệm một con số, một nguồn.
4. **Keyframe-first, hard gate, không hạ cấp âm thầm.** Mọi degradation phải có
   cờ duyệt tường minh trên chính shot đó + cột `render_engine`.
5. **Held là trạng thái chính thức** trong bảng transition (phụ lục A), có lý
   do từ enum cố định (phụ lục C), retry 1 nút từ keyframe.
6. **Một cửa cho mọi transition, idempotent mọi side-effect.** Idempotency-Key
   bắt buộc mọi POST. Không raw UPDATE — diệt bệnh mục #12 bảng audit.
7. **Một worker logic, nhiều worker vật lý an toàn:** lease/fencing + claim
   CAS + claim upload trước khi gọi rclone.
8. **Chữ trên hình là allowlist.** Chỉ `shot_type` ∈ {quote, diagram} với
   `on_screen_text` hợp lệ được có chữ (QC theo §4.B.3).
9. **Không hạ cấp im lặng ở tầng báo cáo.** Thiếu rclone → `LOCAL_ONLY`, không
   phải DONE; mọi chỉ số hiển thị là số liệu thật, cấm badge hardcode.
10. **Sanitizer tách 2 lớp.** Lớp an toàn (tối thiểu, log mọi thay đổi, không
    đổi tên, không xóa trích dẫn) tách khỏi lớp style. Quote/diagram giữ
    nguyên câu/số.
11. **Mọi file đi qua bảng `assets`.** Bảng khác chỉ giữ `asset_id`, không giữ
    URL trực tiếp (chi tiết §2.2).
12. **Character sheet là tài sản được duyệt, keyframe PHẢI dùng nó làm input
    hình ảnh** — không chỉ nhắc bằng chữ (chi tiết §4.B.2).

---

## 2. Mô hình dữ liệu

### 2.1. Trạng thái — MỘT bảng transition duy nhất

**Shot** (bảng đầy đủ ở phụ lục A, tóm tắt):

```
pending → keyframe_pending → keyframe_approved → generating
       → generated → qc_passed → appended
held  : nhánh rẽ từ bất kỳ bước nào TỪ keyframe_pending trở đi
failed: chỉ khi hết attempts (attempt cap theo config, không hardcode)
```

- **KHÔNG có trạng thái `done`.** Shot đã duyệt và đã nối video tích lũy là
  `appended`; QC đạt nhưng chưa nối là `qc_passed`. UI hiển thị nhóm
  "Hoàn tất" = `appended`, nhóm "Đạt chờ nối" = `qc_passed`.
- `keyframe_status` **bỏ** — keyframe là thuộc tính của transition
  (`keyframe_approved` là 1 bước), không phải 2 state song song. Giữ 2 cột dữ
  liệu `keyframe_asset_id` + `keyframe_hash` (không phải trạng thái).
- Item chỉ có MỘT giá trị status (§2.3); việc "script đã duyệt hay chưa" nằm
  ở bảng `approvals`, không nằm trong status.

**Item** (1 giá trị, tóm tắt — bảng đầy đủ ở §2.3):

```
draft → analyzing → awaiting_review → generating → assembling
      → ready → exported   (+ failed, paused)
```

Gate duyệt (script/audio/character/keyframe_batch/final) là các dòng trong
bảng `approvals`, không phải trạng thái Item. Item `awaiting_review` nghĩa là
"đang chờ ít nhất một gate"; gate nào đang chờ đọc từ `approvals`.

### 2.2. Bảng chính

> Công nghệ: SQLite WAL trước, Postgres khi ≥2 worker vật lý (§9). Buộc có
> repository abstraction ngay từ đầu.

- `items`: id, title, type (distill/remake/prebuilt_import), status, progress,
  held_count, done_count (= appended count), total_shots,
  cumulative_duration_sec, cumulative_video_asset_id, created_at, updated_at.
- `sources`: item_id, kind (youtube/upload/api_video/api_shots), **source_asset_id**,
  original_duration, original_title, checksum, metadata.
- `analyses`: item_id, version, transcript_asset_id, scenes_json,
  visual_dna_json, story_dna_json, audio_dna_json, evidence_pack_asset_id, status.
- `scripts`: item_id, version, language, text, scenes_json (mỗi scene: index,
  text, start_sec, end_sec, duration_sec, shot_type, visual_prompt_clean,
  on_screen_text, on_screen_type, **evidence_refs[]**, unverified_flags[]),
  hash, approved_by, approved_at.
- `audios`: item_id, version, kind (voice/master/final_mix), **audio_asset_id**,
  duration_sec (đo bằng probe, không tin payload), loudness_lufs, true_peak,
  word_timeline_asset_id, voice_id, bgm_plan_json, hash.
- `shots`: item_id, batch_id, index, shot_type, narration_text,
  audio_start_sec, audio_end_sec, visual_prompt (NGUỒN DUY NHẤT),
  on_screen_text, **keyframe_asset_id**, keyframe_hash,
  **video_asset_id**, **render_engine** (giá trị CHO PHÉP ở shot
  `appended`: `veo` | `ken_burns_approved`. Giá trị `ken_burns_downgrade`
  CHỈ xuất hiện khi shot có cờ duyệt downgrade tường minh trên chính shot,
  được set qua API bởi người duyệt — không tự động. `text_only` **KHÔNG** là
  giá trị hợp lệ của shot hoàn tất; lịch sử gen text-only thất bại nằm ở
  `attempts` + `events`, không ở engine), status (phụ lục A), held_reason
  (enum phụ lục C), held_detail (text tự do bổ sung cho enum), attempts,
  retry_later_count, next_retry_at, provider, model, seed,
  **downgrade_approved** (bool), created_at, updated_at.
- `batches`: id, item_id, shot_indexes[], status, created_at, approved_at.
- `character_sheets`: item_id, version, name, role (protagonist/crowd/support),
  **reference_asset_id** (ảnh chính), **crowd_ref_asset_id** (ảnh quần chúng),
  hash, status (draft/approved/rejected), approved_by, approved_at.
- `renders`: item_id, manifest_hash, shot_ids_snapshot, video_asset_id,
  duration_sec, resolution, fps, qc_json, created_at.
- `approvals`: item_id, gate (script/audio/character/keyframe_batch/final),
  version, decision (approved/rejected/override), reason, approver, created_at.
  Decision `override` dành cho việc bỏ cờ `unverified` bởi người (§4.A3).
- `assets`: id, item_id, kind (video/audio/script/image/subtitle/final/json),
  **storage_key** theo mẫu cố định (§2.2.1), sha256, size, duration_s,
  **provenance.engine** (whisper|edge-tts|xkiro-tts|veo|ken_burns|
  youtube_subs|imagen|xkiro-image|dalle|ffmpeg), meta_json, created_at.
- `jobs`: id, item_id, type, status, idempotency_key, lease_until, worker_id,
  attempts, error_json, cost_json.
- `events`: append-only — item_id, shot_id (nullable), type, actor
  (user|worker|api), payload_json, at.

**2.2.1. Mẫu storage_key bắt buộc** (đổi storage backend = đổi adapter, không
đổi DB):

```
items/{item_id}/source/original.mp4
items/{item_id}/analysis/transcript_v{v}.json
items/{item_id}/audio/master_v{v}.wav
items/{item_id}/audio/words_v{v}.json
items/{item_id}/characters/{name}_v{v}.png
items/{item_id}/shots/{index:04d}/keyframe.png
items/{item_id}/shots/{index:04d}/video.mp4
items/{item_id}/renders/{render_id}.mp4
items/{item_id}/exports/final_{ts}.mp4
```

### 2.3. Trạng thái Item — bảng transition

| Từ | Được sang | Điều kiện |
|---|---|---|
| draft | analyzing, failed, cancelled | — |
| analyzing | awaiting_review, failed, paused | Evidence Pack + script + audio sinh xong |
| awaiting_review | generating, failed, paused | TẤT CẢ gate bắt buộc approved trong `approvals` |
| generating | assembling, held_review, failed, paused | có ≥1 shot held → held_review; 0 held và hết shot → assembling |
| held_review | generating, paused | hết shot held (retry/duyệt) |
| assembling | ready, failed, paused | cumulative video khớp manifest |
| ready | exported | duyệt gate final + export xong |
| exported | — | terminal; nguyên liệu KHÔNG bị xóa |
| failed | analyzing, awaiting_review, generating | qua API retry có chủ đích |

Transition chỉ qua state machine 1 cửa; `paused` khả dụng từ mọi trạng thái
không terminal. `cancelled` chỉ từ draft/paused, giữ audit trail.

---

## 3. API

### 3.1. Chuẩn Video (để đục/đúc kết)

```http
POST /api/v1/items
Authorization: Bearer <api_key>        # scope: ingest
Idempotency-Key: <uuid>

{ "title": "...",
  "source": { "type": "youtube_url", "url": "..." },  // hoặc upload/drive_url
  "target": { "language": "en", "style_lock_version": "v3" },
  "options": { "distill": true, "keep_original_audio": false } }
→ 201 { "item_id": "itm_...", "status": "analyzing", "detail_url": "..." }
```

URL `youtube_url` và `drive_url` đều qua **allowlist domain + SSRF guard**
(không chỉ keyframe — xem §7.3).

### 3.2. Chuẩn Shot (kịch bản + audio + shot list đã chốt)

```http
POST /api/v1/items/{item_id}/shots/import
Authorization: Bearer <api_key>        # scope: ingest
Idempotency-Key: <uuid>
```

Payload như v3.1 (shots[] với index, shot_type, narration, audio_start_sec,
audio_end_sec, visual_prompt_clean, on_screen_text, on_screen_type,
keyframe_asset_url) + audio.file_url + audio.duration_sec.

**Luật import (hard gate):**

1. Giữ nguyên index/text/timecode/visual prompt. Không rewrite/polish/sanitize.
2. **Không tin `audio.duration_sec` trong payload.** Hệ thống tải file, tính
   **SHA256** và **probe duration thật** (ffprobe), rồi mới chạy gate:
   ±1.0s toàn video, ±0.25s mỗi shot. Lệch → 422 kèm import report
   (phụ lục D), không tự kéo giãn.
3. `on_screen_text` chỉ hợp lệ khi `shot_type` ∈ {quote, diagram}.
4. `keyframe_asset_url` (nếu có): fetch qua allowlist/signed URL + SSRF guard,
   validate hash, 1280x720 (phụ lục B), style check, OCR nếu quote/diagram.
   Đạt → lưu vào bảng `assets` với storage_key chuẩn, set
   `keyframe_status` dữ liệu + hash.
5. **`shots_hash` định nghĩa chặt:** hash SHA256 của canonical JSON gồm mảng
   các object `{index, narration, audio_start_sec, audio_end_sec,
   visual_prompt_clean, on_screen_text, on_screen_type, keyframe_sha256}`,
   key sắp xếp alphabetically, số làm tròn 3 chữ số thập phân, không whitespace
   thừa (JSON dump `sort_keys=True, separators=(',',':')`).
6. **Idempotency + re-import:**
   - Import lại **cùng** `script_version` + cùng shots_hash → idempotent, trả
     trạng thái cũ, không tạo shot trùng.
   - Import **cùng** `script_version` nhưng shots_hash khác → 409, yêu cầu
     bump version.
   - Import `script_version` **mới** lên cùng Item → tạo version script mới,
     shot của version cũ chuyển `superseded` (trạng thái terminal riêng,
     giữ nguyên dữ liệu để truy vết), KHÔNG ghi đè lặng lẽ. Version cũ vẫn
     xem được qua API version history.
7. Sau import, Item → `awaiting_review`, chờ duyệt batch, không tự gen.

**Giới hạn payload:** tối đa **500 shot**, tối đa **10MB** JSON body
(413 nếu vượt). Audio file tải về tối đa 2GB.

### 3.3. API đọc/vận hành + webhook

Giữ nguyên bộ endpoint v3.1 §3.3 (items, shots?status=, retry, hold, approve,
batches, cumulative, export, activity, health). Bổ sung:

- `GET /api/v1/items/{id}/scripts?version=` — version history (bắt buộc cho
  luật re-import §3.2.6).
- Mọi POST có Idempotency-Key; trùng key → trả kết quả lần đầu.

**Webhook** (mẫu payload ở phụ lục E): events `item.analyzed`,
`item.awaiting_review`, `shot.held`, `shot.appended`, `item.ready`,
`item.failed` — HMAC-SHA256 header `X-Signature`, timestamp trong payload để
chống replay, retry backoff (1m/5m/30m/2h, bỏ sau 24h).

---

## 4. Full luồng

### Luồng A — YouTube → đúc kết

**A1. Thu thập:** như v3.1 (yt-dlp khóa version, phụ đề-first, Whisper+VAD
fallback, audio_dna).

**A2. Phân tích + DNA CÓ CÔNG THỨC (P1#1):**

| Chỉ số | Công thức | Nguồn dữ liệu |
|---|---|---|
| cuts_per_min | số lần chuyển cảnh ÷ số phút | PySceneDetect scene list |
| shot_len_p50/p90 | percentile 50/90 của khoảng cách 2 cảnh liên tiếp | PySceneDetect |
| wpm | số từ ÷ số giây nói (trừ silence) | transcript + VAD |
| short_sentence_ratio | % câu < 8 từ | transcript |
| silence_gap_mean | khoảng lặng trung bình giữa câu | VAD |
| palette | 5 màu chủ đạo (k-means trên keyframe) | keyframes |

- **DNA ảnh hưởng kịch bản bằng ràng buộc CỨNG, không chỉ prompt:** mỗi scene
  sinh ra phải nằm trong band `[p50, p90]` của shot length DNA (trừ hook:
  band riêng `[0.5×p50, p50]`); wpm mục tiêu = wpm DNA ±10%; số cảnh =
  target_duration ÷ median shot length. Ràng buộc này là input cho
  ScriptFactory và cho QA chấm (không phải "đưa vào prompt cho may mắn").
- Story DNA (hook/open loops/callback) là định tính có trích dẫn: mỗi giá trị
  phải kèm `evidence_id` trỏ tới đoạn transcript.

**A3. Đúc kết 2 tài sản chuẩn:**

1. **Kịch bản chuẩn:** như v3.1, cộng **`evidence_refs[]`** (P1#2): mỗi số,
   tên, ngày trong scene phải trỏ tới evidence_id (đoạn transcript timestamped
   hoặc keyframe). Không trỏ được → `unverified`. Người duyệt được phép bỏ cờ
   (decision `override` trong bảng `approvals`) nhưng **bắt buộc ghi lý do**.
   Versioned + hash; sửa → version tăng.
2. **Audio chuẩn có nền:** như v3.1. Loudness -16 LUFS / TP ≤ -1.5 dBTP ở
   `audio_config.yaml` (quyết định đã chốt).

**Quy tắc sửa scene sau khi audio chốt (P0#5 — chọn 1, không chọn 2):**

> **Chọn phương án tự dịch chuyển + STALE:** người sửa text 1 scene → hệ thống
> regen TTS scene đó → duration mới lệch cũ quá **±0.25s** thì: (a) start/end
> của MỌI scene phía sau tự dịch chuyển theo delta; (b) TẤT CẢ shot downstream
> (audio_start/end đã chốt) chuyển `held` với lý do `TIMECODE_SHIFTED` và
> phải được duyệt lại timecode trước khi quay lại `pending`; (c) duration
> audio master thay đổi → gate audio phải duyệt lại. Lệch trong ±0.25s → chỉ
> regen scene đó, không dịch chuyển. Nếu muốn sửa nội dung mà không muốn lệch
> timecode → sửa cho khớp band ±0.25s (hệ thống hiện preview duration trước
> khi commit sửa).

### Luồng B — Gen shot chuẩn v3

**B.1. Batch:** chia ≤4 shot, bảng `batches` có trạng thái riêng.

**B.2. Character sheet (P0#7 — luồng đầy đủ):**

1. **Tạo:** từ mô tả nhân vật trong script (LLM sinh prompt character) →
   gen ảnh tham chiếu chính (protagonist) + ảnh quần chúng (crowd) bằng
   image provider theo `style_lock_v3`.
2. **Duyệt:** gate `character` — người xem trên UI, duyệt/reject từng sheet;
   đạt → hash khóa (`character_sheets.hash`), status `approved`.
3. **QUY TẮC BẮT BUỘC:** keyframe của shot có `character_ids` **phải dùng ảnh
   tham chiếu đã duyệt làm INPUT image-to-image** (kèm prompt mô tả), không
   được chỉ nhắc tên bằng chữ trong prompt. Provider adapter ký hiệu
   `generate_image(prompt, ref_image=...)`; ref_image thiếu khi shot khai báo
   character → shot held `REF_IMAGE_MISSING`, không gen.
4. Thay character sheet = version mới, mọi keyframe đã gen từ version cũ
   giữ hash cũ (không bị đổi ngược).

**B.3. Keyframe-first:** gen từ visual_prompt_clean + style lock nguyên văn +
**ref image theo B.2.3** → kiểm tự động (QC keyframe): 1280x720, style check,
OCR nếu có on_screen_text, lề ≥40px 4 phía. Đạt → khóa hash,
`keyframe_approved`. Hỏng → held + enum lý do.

**B.4. Animate từ đúng keyframe:** camera tĩnh, tối đa 1 chuyển động nhỏ chậm;
quote: chữ/bong bóng hiện dần, timeline 30-40-30. Cấm pan/zoom thay ý mới,
crop dọc→ngang, slideshow.

**B.5. QC TỰ ĐỘNG từng shot — NGƯỠNG CHÍNH XÁC (P0#2):**

| Kiểm tra | Ngưỡng | Tự động? |
|---|---|---|
| Resolution | đúng 1280x720 (phụ lục B) | ✅ |
| fps / codec / pixel format | đúng render spec (phụ lục B) | ✅ |
| Duration vs audio segment | ±0.25s | ✅ |
| OCR on_screen_text (quote/diagram) | khớp chính xác 100% chuỗi | ✅ |
| Lề chữ | ≥40px mỗi phía (cấu hình trong render_spec) | ✅ |
| Text thừa | OCR toàn khung phải trả về ĐÚNG tập text cho phép (rỗng hoặc on_screen_text), không có ký tự lạ | ✅ |
| Stream count | 1 video + 1 audio (hoặc 0 audio nếu spec cho phép) | ✅ |
| **Màu da nhân vật / biểu cảm / nhất quán character** | — | ❌ **KHÔNG tự động** — đây là QC NGƯỜI ở bước duyệt batch. Không model nào trong plan đủ tin cho việc này; khai báo dối là tái tạo bug "VLM placeholder luôn True" |

Kết quả tự động fail → held với enum lý do (phụ lục C). Kết quả tự động pass
→ `qc_passed`, chờ người duyệt batch.

**B.6. Duyệt batch:** người xem 4 keyframe + 4 video, "Duyệt cả batch" hoặc
"Giữ shot X" (held thủ công + enum). Duyệt xong → shot `appended` (xem Luồng C).

**B.7. Held + retry 1 nút** từ keyframe đã duyệt. **Luật không vượt held:**
video tích lũy chỉ nối tới shot liền mạch `qc_passed`-→-`appended` cuối cùng;
shot sau held dù xong cũng chưa nối.

### Luồng C — Ráp video tích lũy + xuất bản

**C.1. Điều kiện append (P0#4):** chỉ shot ĐÚNG render spec (phụ lục B — cùng
codec, fps, resolution, sample rate, timebase) mới được nối. Shot lệch spec →
QC fail, KHÔNG normalize tự động rồi nối. Mọi shot được gen từ provider đều
bị ép normalize về render spec NGAY TẠI BƯỚC GENERATING (normalize là bước
chuẩn hóa bắt buộc sau gen, kiểm lại bằng probe trước khi `qc_passed`) —
nhờ vậy append là concat `-c copy` thật sự, không re-encode.

**C.2. Append-only:** mỗi batch duyệt xong chỉ nối thêm, manifest mới (hash
shot + audio + style version). Sửa shot cuối không encode lại toàn bộ.

**C.3. Audio-first khi nối:** video cắt/giữ đúng audio segment. Video ngắn
hơn → giữ frame cuối (ghi manifest); dài hơn → cắt đúng mốc. Không kéo giãn
audio.

**C.4. Không chữ tự thêm ở tầng ráp.** Caption sidecar khi xuất.

**C.5. QC cuối là gate chặn:** từng shot + toàn video (loudness, duration
tổng vs audio, text allowlist).

**C.6. Xuất bản:** final + thumbnail 1280x720 + metadata YouTube (chapters
từ scene) + caption sidecar. GIỮ nguyên liệu sau export.

---

## 5. Giao diện

### 5.1. Skills (chốt)

Gen chính: `frontend-design-deslop` (samber). Audit: Hallmark chế độ audit
(hoặc anti-slop-design) — KHÔNG cả hai, KHÔNG bật xoay theme. Pin
version/commit. Không dùng taste-skill cho dashboard.

### 5.2. `DESIGN.md` — có TOKEN THẬT (P1#4)

- **Tham chiếu Linear: LAYOUT và MẬT ĐỘ** (grid dày, sidebar, bảng số). Font
  KHÔNG dùng Inter (vì Linear dùng Inter — copy cả font là copy cả identity;
  đây là chỗ "Linear-reference + cấm Inter" phải hiểu đúng: tham chiếu cấu
  trúc, không tham chiếu vỏ).
- **Font cụ thể:** UI = **IBM Plex Sans**; số/timecode/duration =
  **IBM Plex Mono** với `font-variant-numeric: tabular-nums`. Cấm các font
  "mặc định AI": Inter, Roboto, system-ui làm font chính.
- **Bảng màu:** nền light `#FAFAF9` (warm gray), surface `#FFFFFF`, text
  `#1C1917`, border `#E7E5E4`; accent hành động `#2563EB`; status: done
  `#16A34A`, running `#D97706`, held `#DC2626`, superseded `#78716C`.
  Màu ở chế độ OKLCH trong file đầy đủ; đây là token gốc.
- Held: màu riêng `#DC2626`, lý do bằng tiếng người từ enum (phụ lục C có
  bảng label VI), nút retry ngay tại chỗ.
- Waveform + word highlight là hero của tab Kịch bản & Audio; timeline
  click-jump; held gap có ghi chú.
- Empty state có hướng dẫn; lỗi tiếng người + hành động.
- Provider/quota lỗi: lỗi gì, tài khoản nào, bao giờ thử lại, shot nào ảnh hưởng.
- CẤM badge/progress hardcode — mọi chỉ số động từ API.
- Motion: ≤1 transition 150ms mỗi tương tác; không parallax/gimmick.

### 5.3. Cấu trúc màn hình

Như v3.1 (danh sách Item realtime SSE; chi tiết 8 tab: Tổng quan, Nguồn,
Phân tích, Kịch bản & Audio, Shots, Timeline tích lũy, Xuất bản, Jobs & Logs).
Cập nhật theo v3.2:
- Shot grid status màu theo **bảng transition mới**: xám `pending`/
  `keyframe_pending`, xanh dương `keyframe_approved`/`generating`, vàng nhạt
  `generated`/`qc_passed`, xanh `appended`, đỏ `held`, xám đậm `superseded`.
- Shot `qc_passed` hiển thị nhóm riêng "Đạt — chờ duyệt batch" để người không
  nhầm với đã nối.

---

## 6. Kiến trúc + tái sử dụng

Như v3.1: FastAPI (nâng cấp từ `job_inbox_api.py`), SQLite WAL → Postgres theo
§9, repository abstraction, queue theo điều kiện. Bảng tái sử dụng code cũ
giữ nguyên (tts.py, word_timing, slicer, storage_uploader, yt-dlp-first;
bỏ Streamlit in-process, task_state, 3 Style Bible, prepare_veo_prompt,
bot 770 dòng). Cảnh báo giữ nguyên: đường `uploaded_blocks` bypass loudnorm
không copy nguyên.

**Bổ sung hành vi hết quota (P1#5):**
- Worker đọc `reset_at` từ provider; hết quota giữa batch: shot đang gen chạy
  hết (không kill giữa chừng), shot còn lại trong batch giữ `pending` với
  `next_retry_at = reset_at`; Item giữ `generating` (không phải failed) với
  banner "hết quota, tự retry lúc HH:MM".
- Tự retry sau `reset_at`, tối đa `quota_retry_limit` (config, default 3) —
  hết trần → Item `paused` + thông báo, CHỜ NGƯỜI bấm resume (không loop vĩnh viễn).
- Fallback Ken Burns KHÔNG tự kích hoạt khi hết quota — chỉ khi
  `downgrade_approved` trên shot (nguyên tắc §1.4).

---

## 7. Bảo mật và vận hành (CÓ SỐ — P0#9)

**7.1. API key scope (ít nhất 3):**

| Scope | Được | Không được |
|---|---|---|
| `read` | GET toàn bộ endpoint đọc | mọi POST |
| `ingest` | POST items, shots/import, batches, retry, hold | approve, export, delete |
| `admin` | toàn bộ, gồm approve/export/delete | — |

Key `ingest` không approve được — chặn kịch bản "agent ngoài tự duyệt kịch bản
của chính nó". Thu hồi từng key riêng; log mọi gọi không log key.

**7.2. Rate limit:** `read` 120 req/phút, `ingest` 20 req/phút, webhook gửi
toàn bộ item events tuần tự (không parallel per item). Rate limit per key,
phản 429 kèm `Retry-After`.

**7.3. URL fetch:** `youtube_url`, `drive_url`, `keyframe_asset_url` đều qua
allowlist domain (youtube.com, youtu.be, drive.google.com, domains trong
config) + SSRF guard (chặn private IP, redirect phải re-check, tái dùng pattern
`project_service.py:134`).

**7.4. Telegram:** bot là client của API, deny-by-default **ở cấp class**
(`start_polling` tự refuse khi thiếu token hoặc chat_id — không dựa vào caller
kiểm tra giùm). Approve qua Telegram phải kèm **token 1 lần** (bot gọi API
lấy challenge, người bấm nút callback có chữ ký; token hết hạn 10 phút).
Không `/setcookie` qua chat; credential nạp qua UI/API mã hóa at-rest Fernet,
chmod 0600, không đường scan sessions ngoài pool.

**7.5. Worker:** lease/fencing + heartbeat; reset chỉ filter `worker_id` +
heartbeat quá hạn; claim upload trước rclone; `LOCAL_ONLY` ≠ DONE; kill-switch
chỉ chặn sinh mới; quota theo `reset_at` thật.

**7.6. Xóa Item (P1#6):** `DELETE /items/{id}` — (a) từ chối nếu có job active
trả 409 kèm job_id; (b) xóa mềm: status `deleted`, giữ toàn bộ audit trail +
events; (c) purge vật lý là job nền riêng chạy sau grace period 7 ngày, chỉ
xóa object storage, DB rows giữ (`deleted_at`). Diệt bug `delete_project` rmtree.

**7.7. Backup/runbook/staging:** như v3.1 §7 (backup DB + storage + secret key
diễn tập; runbook 429 và auth chết; staging tách prod, deploy bằng tag image
+ migration).

---

## 8. Bảng đối chiếu rác cũ → thiết kế mới

Như v3.1 §8 (14 dòng), cộng thêm:

| Rác cũ | Thiết kế mới |
|---|---|
| `keyframe_status` + status shot 2 state song song | 1 bảng transition (phụ lục A) |
| `done` mơ hồ | `qc_passed` vs `appended` phân biệt rõ |
| text_only là engine hợp lệ | Bỏ; lịch sử fail ở events/attempts |
| shot_hash tự định nghĩa | Canonical JSON chặt (§3.2.5) |
| Tin duration payload | Probe ffprobe bắt buộc (§3.2.2) |
| Re-import ghi đè lặng lẽ | `superseded` + version history (§3.2.6) |
| QC "mặt/da" tự động dối | Tự động chỉ hình học + chữ; mặt là QC người (§4.B.5) |
| held_reason text tự do | Enum cố định (phụ lục C) |

---

## 9. Quyết định công nghệ theo giai đoạn

Như v3.1 §9: SQLite WAL + CAS pattern trước; Postgres + Redis/RQ khi ≥2 worker
vật lý; Temporal chỉ khi đau thật. Điều kiện: repository abstraction + queue
interface ngay từ đầu.

---

## 10. Lộ trình + Definition of Done (P1#7)

**Bước 0 — Khóa trước, code sau (2-3 ngày).**
*DoD:* `style_lock_v3.yaml` + `render_spec.yaml` + `audio_config.yaml` +
`DESIGN.md` (có token §5.2) tồn tại, versioned, có test assert (prompt cuối
chứa khối v3, không chứa từ cấm; mọi số QC đọc từ config không hardcode);
phụ lục A-F đã implement thành Pydantic schema + enum + test.

**Giai đoạn 0 — Nền (1 tuần).**
*DoD:* FastAPI chạy, schema đầy đủ theo §2, state machine 1 cửa với bảng
transition phụ lục A, CRUD Item + assets theo storage_key mẫu, health endpoint,
CI test xanh, UI danh sách Item đọc từ API. Tạo/xem/xóa-mềm Item được.

**Giai đoạn 1 — Ingest + Đúc kết (2 tuần, giá trị cao nhất).**
*DoD:*
1. Import được shot list BYD qua chuẩn Shot §3.2 — gate ±1.0s/±0.25s chạy
   đúng, import report (phụ lục D) trả về khi cố import cặp lệch.
2. Import được trạng thái BlackRock theo phụ lục F (33 shot + shot 034 held).
3. Đúc kết được 1 video YouTube thành Evidence Pack (DNA theo công thức §4.A2)
   → kịch bản chuẩn có evidence_refs + audio chuẩn -16 LUFS có word timeline,
   dừng ở `awaiting_review` với gate duyệt chạy đúng.
4. Sửa 1 scene → quy tắc dịch chuyển/STALE §4.A3 chạy đúng.
5. Webhook HMAC bắn đúng event (phụ lục E).

**Giai đoạn 2 — Gen shot v3 + Held (2-3 tuần).**
*DoD:* batch 4 + character sheet luồng §4.B.2 (keyframe dùng ref image
image-to-image); QC tự động theo bảng §4.B.5 với ngưỡng từ render_spec.yaml;
held + retry từ keyframe; append-only với normalize-tại-generating (§4.C.1);
UI lưới Shots theo status mới; chạy song song đối chiếu với workflow cũ trước
khi chuyển hẳn.

**Giai đoạn 3 — Xuất bản + vận hành (1-2 tuần).**
*DoD:* export final/thumbnail/metadata/caption sidecar; webhook đầy đủ;
quota dashboard với hành vi hết quota §6; backup diễn tập thành công 1 lần;
staging tách prod.

Tổng ~6-8 tuần full-time. Không cắt QC và Held để đổi tốc độ.

---

## 11. Ba quyết định đã chốt

Như v3.1 §11, giữ nguyên: (1) Chuẩn Shot import GIỮ + `keyframe_asset_url`
có validate; (2) loudness -16 LUFS / TP -1.5 dBTP ở `audio_config.yaml`,
nhất quán > con số tuyệt đối; (3) Telegram giữ làm client báo cáo + duyệt
nhanh có token 1 lần, deny-by-default.

---

## Phụ lục A — Bảng transition SHOT (chuẩn duy nhất)

| # | Từ | Sang | Điều kiện / Actor |
|---|---|---|---|
| 1 | pending | keyframe_pending | batch được tạo (API `batches`) |
| 2 | keyframe_pending | keyframe_approved | QC keyframe tự động pass (người có thể override qua approve) |
| 3 | keyframe_pending | held | QC keyframe fail — enum: `KEYFRAME_STYLE_FAIL`, `KEYFRAME_SIZE_FAIL`, `KEYFRAME_TEXT_MISMATCH`, `REF_IMAGE_MISSING` |
| 4 | keyframe_approved | generating | worker claim (CAS) |
| 5 | generating | generated | provider trả video, normalize về render spec, probe đạt |
| 6 | generating | retry_later | RATE_LIMIT/QUOTA — hoàn attempt, set `next_retry_at` |
| 7 | generating | held | hết attempts — enum: `PROVIDER_FATAL`, `PROVIDER_AUTH` |
| 8 | generated | qc_passed | QC tự động pass (bảng §4.B.5) |
| 9 | generated | held | QC fail — enum: `DURATION_MISMATCH`, `VIDEO_TEXT_LEAK`, `RESOLUTION_FAIL`, `SPEC_MISMATCH` |
| 10 | qc_passed | appended | batch được duyệt (gate `keyframe_batch`) — nối video tích lũy |
| 11 | qc_passed | held | người giữ thủ công khi duyệt batch — enum: `CHARACTER_COLOR_SUSPECT`, `FACE_MISMATCH`, `HUMAN_REJECT` |
| 12 | held | keyframe_pending | retry keyframe (gen lại) — chỉ khi held_reason thuộc nhóm keyframe |
| 13 | held | generating | retry animate từ keyframe đã duyệt — giữ nguyên keyframe_hash |
| 14 | mọi trạng thái ≥ keyframe_pending | superseded | re-import script version mới (§3.2.6) |
| 15 | * | pending | retry_job có chủ đích qua API (admin) — reset attempts |

Luật: **KHÔNG có đường chuyển thẳng bỏ qua qc_passed**; held không tự thoát —
chỉ qua retry (12/13) hoặc human approve (11→10 qua duyệt lại batch).

## Phụ lục B — Render spec (khóa ở Bước 0, file `render_spec.yaml`)

```yaml
version: 1
video:
  width: 1280
  height: 720
  fps: 24
  codec: h264            # libx264
  pix_fmt: yuv420p
  crf: 20
  profile: high
  level: "3.1"
  gop: 48                # 2s — cắt nối không vỡ keyframe
audio:
  sample_rate: 48000
  channels: 2
  codec: aac
  bitrate: 192k
  loudness_lufs: -16
  true_peak_dbtp: -1.5
container: mp4
text:
  margin_min_px: 40      # mỗi phía
  ocr_match: exact       # quote/diagram khớp 100%
qc:
  duration_tolerance_sec: 0.25
  global_tolerance_sec: 1.0
```

Mọi provider output ĐƯỢC NORMALIZE về spec này ngay sau gen (bắt buộc, probe
kiểm lại) — concat `-c copy` hợp lệ. Shot không đạt sau normalize → held
`SPEC_MISMATCH`.

## Phụ lục C — Enum held_reason

```
# keyframe
KEYFRAME_STYLE_FAIL | KEYFRAME_SIZE_FAIL | KEYFRAME_TEXT_MISMATCH | REF_IMAGE_MISSING
# video QC
DURATION_MISMATCH | VIDEO_TEXT_LEAK | RESOLUTION_FAIL | SPEC_MISMATCH
# provider
PROVIDER_FATAL | PROVIDER_AUTH | QUOTA_EXHAUSTED
# human
CHARACTER_COLOR_SUSPECT | FACE_MISMATCH | HUMAN_REJECT
# workflow
TIMECODE_SHIFTED
```

Mỗi enum kèm label tiếng người (DESIGN.md) + hành động gợi ý (retry keyframe /
retry animate / chờ người / chờ duyệt lại timecode). `held_detail` là text tự
do BỔ SUNG, không thay thế enum. Thống kê held theo enum luôn truy vấn được.

## Phụ lục D — JSON mẫu: import report (422)

```json
{ "error": "import_gate_failed",
  "audio": { "sha256": "9f2a...", "duration_sec_probed": 1192.4,
             "duration_sec_payload": 1194.0, "duration_trusted": false },
  "global_drift_sec": -1.6,
  "shots": [
    { "index": 1,   "expected_start": 0.0,    "expected_end": 7.2,
      "delta_sec": 0.0,  "ok": true },
    { "index": 57,  "expected_start": 402.5,  "expected_end": 409.8,
      "delta_sec": -1.9, "ok": false,
      "reason": "DURATION_MISMATCH" },
    { "index": 58,  "expected_start": 409.8,  "expected_end": 417.0,
      "delta_sec": -0.3, "ok": false,
      "reason": "SEGMENT_GAP_OVERLAP" } ],
  "summary": { "total": 169, "ok": 167, "failed": 2,
               "max_abs_delta_sec": 1.9 },
  "hint": "Gen lại audio từ script v0.3 đầy đủ hoặc cắt lại script cho khớp. Không tự kéo giãn." }
```

## Phụ lục E — JSON mẫu: webhook event

```json
{ "event": "shot.held",
  "event_id": "evt_01HXYZ...",
  "item_id": "itm_abc123",
  "shot_id": "itm_abc123_shot_0034",
  "version": { "script": 3, "audio": 2 },
  "data": { "held_reason": "VIDEO_TEXT_LEAK",
            "held_detail": "OCR thấy 'DRAFT' ở góc dưới",
            "keyframe_hash": "b41c..." },
  "timestamp": "2026-11-05T10:00:00Z" }
// Header: X-Signature: hex(HMAC-SHA256(secret, raw_body))
// Header: X-Event-Id dùng để dedupe phía nhận
```

## Phụ lục F — Spec migration BlackRock (TÁCH KHỎI plan lõi — sẽ lỗi thời)

> File này ghi nhận trạng thái ngày 2026-11-05. Khi audio master mới ra, mục
> này cập nhật riêng, KHÔNG sửa plan lõi.

Trạng thái hiện tại: BlackRock tạm dừng 00:08 2026-11-05; 33 shot / 194,374s;
shot 034 held; keyframe 034-037 + video 035-037 đã lưu.

Bước migration khi Giai đoạn 1 + 2 xong:

1. **Verify trước khi tin:** probe duration video tích lũy cũ + đo loudness;
   so với audio gốc. Sai lệch ghi vào events, không vá im lặng.
2. **Tạo Item** `type=prebuilt_import`; source = video tích lũy cũ + audio
   hiện có; shots import theo chuẩn §3.2 (script v0.3 đầy đủ sẽ bị gate
   ±1.0s TỪ CHỐI nếu audio chưa gen lại — đó là gate đúng; chọn: gen lại
   audio từ v0.3 đầy đủ, HOẶC cắt script cho khớp audio 674,9s. Không ép vừa).
3. **Tái tạo manifest:** shot 001-033 mark từ artifact cũ; keyframe 034-037
   import qua `keyframe_asset_url` (qua validate §3.2.4); video 035-037 KHÔNG
   được mark `appended` — chỉ lưu như asset kèm ghi chú, vì luật không-vượt-held
   (§4.B.7) yêu cầu shot 034 phải giải quyết trước.
4. **Shot 034:** import với status `held` + enum từ lý do gốc; hiển thị ngay
   trên hàng Held của UI mới.
5. Progress khởi tạo: `0 appended / 169 shot • 194,374s đã có ở hệ cũ` — số
   cũ hiển thị dưới dạng context, không tính vào progress mới.
6. Chạy song song: hệ cũ dừng hoàn toàn trước khi import (đã dừng 00:08);
   không có 2 hệ cùng ghi.

---

*Người review vòng sau nên kiểm: (a) phụ lục A có transition nào thiếu/kẹt,
(b) phụ lục B render spec có khớp workflow BYD/BlackRock thật không (fps 24,
1280x720, margin 40px), (c) §4.A3 quy tắc sửa scene — phương án dịch chuyển
+ STALE có chấp nhận được không, (d) §7 scope key có đủ phân quyền thật không.*

---

## Phụ lục H — Daily Brief submission flow (3 bên 2026-11-22, PR kèm commit)

**Quyết định:** Daily Brief chuyển sang human-in-the-loop theo yêu cầu owner.

- **16:00 giờ VN** (timezone `Asia/Ho_Chi_Minh`, `run_at: "16:00"`): Telegram gửi
  tin đòi nội dung kèm hướng dẫn submit.
- **Owner submit** `POST /api/v1/digest/submit` (scope `ingest`): list 8–15 tin
  đã chốt, mỗi tin: headline, link bài gốc, summary, thumbnail_url, images[].
  Ảnh được tải về `objects/digest_submissions/{date}/s{seq:02d}/` (seq = story
  index + 2, produce ưu tiên owner-images trước scrape).
- **Build**: LLM viết script từ list chốt (bypass select_candidates) → **critic
  pass** — 1 call LLM reviewer độc lập kiểm số liệu khớp nguồn / tin trùng /
  unsupported claim / weak hook — fail → regen 1 lần với feedback; fail lần 2 →
  dừng + báo Telegram, KHÔNG render.
- **Preview** → Telegram (MP4 + lệnh chốt) → **/approve <short_id>** (long-polling
  bot, whitelist chat_id) → upload YouTube `privacy_status` theo config (mặc định
  public — human gate đã có). `/reject` → về rejected, nộp lại được.
- **Fallback**: không submit đến 18:00 → tự chọn tin RSS, vẫn dừng ở preview.
- **Auto-publish legacy bị bypass** khi `submission_flow.enabled=true` (gate
  service_digest, giữ key để rollback 1 dòng).
- State machine `digest_submissions`: requested → submitted → building → built
  → approved | rejected | failed.
- Tests: `video-system/tests/test_digest_submission_flow.py` (11 case).

---

## Phụ lục I — Bảng chat chung cho agent (3 bên 2026-11-23)

**Quyết định:** bảng chat 1-1 giữa agent qua REST polling + cursor (không
long-poll/SSE/WebSocket/broker — lý do: agent là script HTTP, anyio threadpool
~40 thread sẽ bị held requests đậu; GZipMiddleware + nginx mặc định gãy SSE;
<10 agent ~1 msg/phút không cần broker).

- Thread: `created_by` + `participant_b` (NULL = slot trống, agent thứ 2 claim
  bằng CAS `UPDATE ... WHERE participant_b IS NULL`); enforce sender ∈ participants
  hoặc owner; đọc nội dung chỉ participants + owner (admin scope); metadata list public.
- Identity: `auth.identity_for()` trả (key-name, scope) — **mỗi agent cần key
  riêng trong VIDEO_API_KEYS**; session PIN = `owner`.
- Luật: body ≤ 2000 ký tự; 20 tin/5 phút/sender → 429; 30 tin/thread → tự
  `locked` (409); retention 30 ngày (VIDEO_CHAT_RETENTION_DAYS) — seq
  AUTOINCREMENT không rewind sau prune.
- Endpoints: POST/GET `/api/v1/chat/threads`, GET `/{id}`, POST
  `/{id}/messages`, GET `/{id}/messages?since_id=`, DELETE `/{id}` (admin).
- SPA: tab 5 "💬 Agent Chat" — poll 3s, owner post, unread badge.
- Tests: `video-system/tests/test_chat_board.py` (9 case).
