# CONTRACT — Muse ↔ vidfac: Brief → Script Agreement

**Version:** `2026-11-24` · **Status:** Phase 1 (live) · **Base URL:** `https://vidfac.gacdero.vn/api/v1`

Hợp đồng này là nguồn sự thật duy nhất cho agent. Có gì không rõ trong đây thì **hỏi owner**,
không tự suy diễn. Machine-readable schema: `GET https://vidfac.gacdero.vn/openapi.json`.

---

## 1. Danh tính & xác thực

| Vai | Token (Bearer) | Scope | Làm được |
|---|---|---|---|
| `muse-writer` | `muse-writer` | `ingest` | tạo brief*, nộp proposal, ký, ghi finding |
| `muse-critic` | `muse-critic` | `ingest` | ký, ghi finding, đọc |
| `owner` | `owner` | `admin` | duyệt/huỷ brief, chỉ định critic, gỡ bế tắc |

```
Authorization: Bearer muse-writer
```

**Token là tên key, không kèm `:scope`.** Gửi `Bearer muse-writer:ingest` → `401 unknown key`.

Mọi record lưu identity = tên key. Hai agent **bắt buộc** hai key khác nhau, nếu không
luật "author + critic là 2 người" sẽ chặn vĩnh viễn.

`*` brief `approval_mode=calendar` chỉ owner tạo được.

---

## 2. Quy ước chung

**Envelope lỗi** — mọi lỗi của nhóm endpoint này:

```json
{"detail": "chuỗi cho người đọc", "code": "ma_cho_may_doc"}
```

Branch theo `code`. **Không parse `detail`.**

**Idempotency** — mọi POST nên gửi:

```
Idempotency-Key: <chuỗi cố định cho hành động đó>
```

- Key scoped theo agent, hai agent dùng cùng key không nuốt nhau.
- Cùng key + body khác → `422 idempotency_key_reused`.
- Request trước còn đang chạy → `409 idempotency_in_progress` (backoff 5–10s).
- Request lỗi **nhả key** → retry được ngay.

**Timestamp** — Unix giây, float. **ID** — tiền tố: `brf_` brief, `prp_` proposal,
`fnd_` finding, `thr_` chat thread.

---

## 3. Object model

### Brief — `job_briefs`

| Field | Type | Ghi chú |
|---|---|---|
| `id` | str | `brf_...` |
| `title`, `angle` | str | |
| `target_duration_sec` | float | 60–3600 |
| `language` | str | `en` \| `vi` — quyết định WPM |
| `style_lock_version` | str | phải `v3` |
| `source_links` | str[] | JSON array |
| `status` | enum | `draft` `approved` `cancelled` `done` |
| `approval_mode` | enum | `calendar` `objection` `hard` |
| `sensitive` | 0/1 | `1` ⇒ ép `hard` |
| `objection_window_sec` | int | ≥ 1800 |
| `critic` | str \| null | agent được chỉ định review |
| `approved_by`, `approved_at` | | `auto:objection-window` nếu tự duyệt |
| `created_by`, `created_at`, `updated_at` | | |

### Proposal — `script_proposals`

| Field | Type | Ghi chú |
|---|---|---|
| `id` | str | `prp_...` |
| `brief_id`, `parent_id` | str | `parent_id` = version trước |
| `version` | int | 1,2,3… unique trong brief |
| `author` | str | identity người nộp |
| `content_hash` | str | sha256 của shots+script_text; **chữ ký gắn vào đây** |
| `status` | enum | `draft` `invalid` `under_review` `rejected` `agreed` `superseded` |
| `shots` | ShotProposal[] | |
| `script_text` | str \| null | |
| `validation` | obj | `{ok, errors[], warnings[], stats{}}` |
| `signatures` | obj[] | chỉ có ở response của `sign` |

### Shot

| Field | Bắt buộc | Ghi chú |
|---|---|---|
| `index` | không (tự đánh) | phải unique, tăng dần |
| `narration` | **có** | `min_length=1`, không rỗng |
| `visual_prompt` | nên có | thiếu ⇒ lỗi validator |
| `shot_type` | không | mặc định `scene` |
| `source_url` | khi có số liệu | xem `missing_source` |

### Finding — `script_findings`

`{id, proposal_id, reviewer, category, severity, evidence, blocking, created_at}`

`category`: `fact` `structure` `style` `duration` · `severity`: `low` `medium` `high`

### Agreement — read model

`{brief_id, status, agreement, latest, decision, rounds_used, max_rounds, phase2_open, next_action}`

---

## 4. Endpoints

### 4.1 `POST /briefs` — tạo brief *(ingest)*

```json
{
  "title": "AI chips: the next oil",
  "angle": "Supply chain + capex race",
  "target_duration_sec": 780,
  "language": "vi",
  "source_links": ["https://example.com/a"],
  "approval_mode": "hard",
  "critic": "muse-critic"
}
```

`201` → object Brief. `critic` bắt buộc nếu muốn agent khác review.
`critic` trùng `created_by` → `422 critic_is_author`. `calendar` bởi non-owner → `403 calendar_owner_only`.

### 4.2 `GET /briefs?status=approved&limit=50` — tìm brief *(read)*

`200` → `{"briefs": [Brief]}`

### 4.3 `GET /briefs/{id}` *(read)* → Brief

### 4.4 `POST /briefs/{id}/approve` · `/cancel` *(admin + owner)*

`200` → Brief. Sai trạng thái → `409 bad_status` / `409 status_race`.

### 4.5 `POST /briefs/{id}/critic` *(admin + owner)*

`{"critic": "muse-critic"}` → `200` Brief. Đã có chữ ký → `409 critic_locked`.

### 4.6 `POST /briefs/{id}/decisions` *(admin + owner)*

```json
{"decision": "force_agree", "proposal_id": "prp_...", "reason": "deadline sát, critic offline"}
```

`force_agree` → proposal `agreed`, chữ ký owner ghi `owner_forced: <reason>`.
`reject` → `rejected`. `reason` rỗng → `422`. Proposal đã `agreed` → `409 already_agreed`.

### 4.7 `POST /script-proposals` — nộp kịch bản *(ingest)*

```json
{
  "brief_id": "brf_...",
  "parent_id": null,
  "script_text": "toàn văn nếu có",
  "shots": [
    {"index": 1, "narration": "...", "visual_prompt": "...",
     "shot_type": "scene", "source_url": "https://example.com/a"}
  ]
}
```

`201` → Proposal (kèm `validation`). `validation.ok=false` ⇒ `status=invalid`, **không ký được**,
nhưng **không tính vòng review** → sửa bao nhiêu lần cũng được.

Sửa = nộp version mới với `parent_id` = proposal cũ.

### 4.8 `GET /briefs/{id}/proposals` *(read)* → `{"proposals": [Proposal]}`

### 4.9 `GET /script-proposals/{id}` *(read)* → Proposal + `findings[]`

### 4.10 `POST /script-proposals/{id}/findings` *(ingest, author/critic/owner)*

```json
{"category": "fact", "severity": "high", "evidence": "42% sai nguồn", "blocking": true}
```

`200` → Finding. Tự review bài mình → `422 self_review`. Không phải critic → `403 not_critic`.
**Không có Idempotency-Key** → đọc `findings` trước khi gửi lại để tránh trùng.

### 4.11 `POST /script-proposals/{id}/sign` *(ingest, author/critic/owner)*

```json
{"verdict": "pass", "note": "đã đối chiếu nguồn"}
```

`200` → Proposal + `signatures[]` + `agreed: bool`. `verdict=block` → proposal `rejected`.

### 4.12 `GET /briefs/{id}/agreement` *(read)* — **nguồn sự thật cho bước kế**

```json
{
  "brief_id": "brf_...",
  "status": "agreed",
  "agreement": {"id": "prp_...", "content_hash": "...", "version": 2},
  "latest": {"id": "prp_...", "version": 2, "status": "agreed"},
  "decision": null,
  "rounds_used": 1,
  "max_rounds": 2,
  "phase2_open": false,
  "next_action": "build_job"
}
```

---

## 5. State machine

```
brief:     draft ──approve──► approved ──► done
             │                   │
             └──cancel──► cancelled ◄──cancel──┘

proposal:  invalid ──(sửa, version mới)──► superseded
           draft ──finding──► under_review ──2 chữ ký pass──► agreed  (ĐÓNG BĂNG)
             │                      │
             └──block──► rejected ──┴──► (author tạo version mới)
```

**`agreed` là đóng băng.** Hash đổi = ký lại từ đầu. Không có "sửa nhỏ sau khi chốt".

### `next_action` → việc phải làm

| Giá trị | Nghĩa | Hành động |
|---|---|---|
| `create_proposal` | brief chưa có proposal | author nộp |
| `fix_proposal` | proposal invalid | author sửa + version mới |
| `review_or_sign` | đang chờ chữ ký | critic finding / ký |
| `create_new_version` | proposal bị block | author làm version mới |
| `owner_required` | hết 2 vòng review | **dừng**, chờ owner |
| `build_job` | đã chốt | **dừng** (`phase2_open=false`) |

---

## 6. Validator — `validation.errors[].code`

| code | nghĩa |
|---|---|
| `duration_out_of_range` | tổng từ quy ra giây lệch >10% target |
| `shot_count_out_of_range` | số shot ngoài `min_shots`–`max_shots` |
| `missing_narration` | narration toàn khoảng trắng |
| `missing_visual_prompt` | thiếu visual_prompt |
| `shot_too_short` / `shot_too_long` | 1 shot <4s hoặc >40s |
| `duplicate_narration` | 2 shot trùng lời |
| `duplicate_index` | index trùng |
| `missing_source` | có số liệu/năm/%/tiền nhưng thiếu `source_url` |
| `bad_style_lock` | style ≠ v3 |

WPM: `en`=150, `vi`=160. `stats` trả `estimated_duration_sec`, `min_shots`, `max_shots` để agent tự tính.
`errors[].shot_index` chỉ có ở lỗi gắn với shot; lỗi toàn cục không có field này.

---

## 7. Error codes & cách xử lý

| code | HTTP | Agent làm gì |
|---|---|---|
| `brief_not_approved` | 409 | chờ owner duyệt |
| `brief_cancelled` | 409 | dừng hẳn |
| `brief_agreed_frozen` | 409 | đã chốt, không sửa |
| `duplicate_content` | 409 | **đừng tạo lại**, đọc lại proposals |
| `version_conflict` | 409 | đọc proposals, gửi lại với `parent_id` mới nhất |
| `round_limit_reached` | 409 | dừng, báo owner |
| `proposal_invalid` | 409 | sửa lỗi validator trước |
| `open_blocking_findings` | 409 | xử lý finding blocking |
| `proposal_locked` | 409 | proposal đã agreed/superseded |
| `proposal_rejected` | 409 | tạo version mới |
| `critic_not_assigned` | 409 | brief chưa có critic, báo owner |
| `critic_is_author` | 409/422 | critic trùng author, báo owner |
| `already_agreed` | 409 | owner không phủ quyết được bản đã chốt |
| `not_critic` | 403 | không phải brief của bạn, bỏ qua |
| `self_review` | 422 | không tự review bài mình |
| `idempotency_key_reused` | 422 | đổi key |
| `idempotency_in_progress` | 409 | backoff 5–10s, retry 1 lần |
| `database_busy` | 503 | backoff 5–10s, retry 1 lần |
| `owner_only` / `calendar_owner_only` | 403 | không phải việc của agent |

**Retry:** chỉ retry với `version_conflict`, `idempotency_in_progress`, `database_busy`.
Mọi `409` khác là trạng thái nghiệp vụ — retry vô ích, phải đọc lại state.

---

## 8. Ngoài phạm vi hợp đồng này

1. **`phase2_open=false`** — chưa có endpoint tự tạo job / chia shot. `agreed` là điểm dừng.
2. **Không có push.** Muse là one-shot: mỗi lượt phải được gọi từ bên ngoài.
3. **Chat là polling** (`/chat/threads`, cursor `since_id=<last_seq>`), không realtime.

---

## 9. Prompt cho agent (dán nguyên khối)

### muse-writer

```
Bạn là Muse Writer. Token: "muse-writer". Base: https://vidfac.gacdero.vn/api/v1
Hợp đồng: đọc read_me/08_muse_contract.md (hoặc openapi.json).

Mỗi lượt làm ĐÚNG 1 việc:
1. GET /briefs?status=approved → chọn brief chưa có proposal của bạn.
2. GET /briefs/{id}/agreement → nếu next_action không phải create_proposal/fix_proposal
   thì báo lại 1 dòng rồi dừng.
3. POST /script-proposals (kèm Idempotency-Key) → đọc validation.
4. validation.ok=false → sửa theo errors[].code, nộp version mới với parent_id.
5. Đã valid → POST /script-proposals/{id}/sign {"verdict":"pass"}.
6. Dừng. Không poll. Không tạo job.

Báo cáo cuối lượt = giá trị next_action của GET /briefs/{id}/agreement.
```

### muse-critic

```
Bạn là Muse Critic. Token: "muse-critic". Base: https://vidfac.gacdero.vn/api/v1
Hợp đồng: đọc read_me/08_muse_contract.md (hoặc openapi.json).

Mỗi lượt làm ĐÚNG 1 việc:
1. GET /briefs?status=approved → với mỗi brief: GET /briefs/{id}/agreement,
   bỏ qua nếu next_action=build_job.
2. GET /briefs/{id}/proposals → lấy proposal mới nhất.
3. validation.ok=false → không ghi finding, để author sửa.
4. valid + chưa có finding của bạn → POST /script-proposals/{id}/findings
   (tối đa 3, blocking=true chỉ khi sai sự thật / lệch thời lượng nặng).
5. Không còn vấn đề blocking → POST /script-proposals/{id}/sign {"verdict":"pass"}.
6. Dừng.
```
