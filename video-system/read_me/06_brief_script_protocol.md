# Brief → Script Duel → Agreement (agent protocol, 2026-11-23)

> Dành cho Muse/agent. Mục tiêu: hai agent chốt kịch bản bằng **record có hash**, không
> chốt bằng chat. Sau khi `agreed`, hệ mới được phép tạo job (Phase 2 — chưa mở ở bản này).

**Base URL:** `https://vidfac.gacdero.vn` · **Auth:** `Authorization: Bearer <key riêng>`
(identity = tên key). Mọi POST nên kèm `Idempotency-Key` (scoped theo agent).

---

## 1. Brief phải approved trước

```bash
curl -X POST "$BASE/api/v1/briefs" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -H "Idempotency-Key: brief-2026-11-24" \
  -d '{
    "title": "AI chips: the next oil",
    "angle": "Supply chain + capex race",
    "target_duration_sec": 780,
    "source_links": ["https://example.com/a", "https://example.com/b"],
    "approval_mode": "objection",
    "objection_window_sec": 1800,
    "critic": "agent-b"
  }'
```

**`critic` là bắt buộc nếu muốn agent khác review.** Không có `critic` thì mọi chữ ký /
finding của agent khác trả `409 critic_not_assigned` và brief chỉ còn owner ký được.
`critic` phải khác người tạo brief (`422 critic_is_author`) — cần người ký đối trọng.

`approval_mode`:
- `calendar` → approved ngay (chủ đề đã nằm trong content calendar). **Chỉ owner được tạo**
  (`403 calendar_owner_only` nếu agent tự tạo) — nếu không, agent tự duyệt chủ đề của chính nó.
- `objection` → tự approved sau `objection_window_sec` nếu owner không phản đối. Việc duyệt
  do scheduler 60s làm trong giờ làm việc **08:00–18:00 (VN)**; **GET không duyệt** (tránh
  mở dashboard là duyệt hàng loạt). Ngoài giờ → brief nằm `draft` tới 08:00 hôm sau.
  Sàn `objection_window_sec` = **1800** (30 phút); ngắn hơn thì `422` — cửa sổ vài phút
  biến `objection` thành cửa sau để agent tự duyệt.
- `hard` → owner phải bấm `POST /briefs/{id}/approve`.
- `sensitive=true` → luôn bị ép `hard`.

Đổi critic sau khi tạo (**owner-only**):

```bash
curl -X POST "$BASE/api/v1/briefs/$BRIEF/critic" -H "Authorization: Bearer $OWNER_KEY" \
  -H "Content-Type: application/json" -d '{"critic":"agent-c"}'
```

Đổi critic sau khi đã có chữ ký → `409 critic_locked`.

Kiểm tra: `GET /briefs/{id}` → `status` phải là `approved`.

Nếu brief còn `draft`:
- `approval_mode=hard` (hoặc `sensitive`) → **dừng**, chỉ owner duyệt được. Báo owner.
- `approval_mode=objection` → chờ hết cửa sổ **và** vào giờ làm việc. Poll `GET /briefs/{id}`
  cách 5–10 phút; ngoài 08:00–18:00 VN thì **đừng poll**, hẹn lại sau 08:00.
- `status=cancelled` → dừng hẳn, tạo brief mới nếu còn nhu cầu.

---

## 2. Author nộp proposal

```bash
curl -X POST "$BASE/api/v1/script-proposals" -H "Authorization: Bearer $AUTHOR" \
  -H "Content-Type: application/json" -H "Idempotency-Key: script-v1" \
  -d '{
    "brief_id": "brf_...",
    "shots": [
      {"index": 1, "narration": "...", "visual_prompt": "...", "shot_type": "scene",
       "source_url": "https://example.com/a"}
    ]
  }'
```

Response có `validation`:
```json
{"ok": false,
 "errors": [{"code": "missing_source", "shot_index": 3,
             "message": "shot 3 có số liệu/năm nhưng thiếu source_url"}],
 "warnings": [{"code": "shot_duration_off", "shot_index": 1,
               "message": "shot 1 ≈ 9.4s, chuẩn 12–18s"}],
 "stats": {"shots": 52, "words": 1950, "wpm": 150, "estimated_duration_sec": 780,
           "target_duration_sec": 780, "min_shots": 35, "max_shots": 78}}
```

`errors[].code` — branch theo đây, đừng parse `message`:

| code | nghĩa | sửa thế nào |
|---|---|---|
| `duration_out_of_range` | tổng từ quy ra giây lệch >10% target | thêm/bớt narration |
| `shot_count_out_of_range` | số shot ngoài `min_shots`–`max_shots` | gộp/tách shot |
| `missing_narration` | shot không có chữ | điền narration |
| `missing_visual_prompt` | shot không có mô tả hình | điền visual_prompt |
| `shot_too_short` / `shot_too_long` | 1 shot <4s hoặc >40s | cân lại độ dài |
| `duplicate_narration` | 2 shot trùng lời | viết lại |
| `duplicate_index` | `index` bị trùng | đánh lại số |
| `missing_source` | có số liệu/năm/%/tiền nhưng thiếu `source_url` | thêm nguồn hoặc bỏ số liệu |
| `bad_style_lock` | `style_lock_version` ≠ `v3` | dùng v3 |

Lưu ý về shape:
- `shot_index` chỉ có ở lỗi gắn với một shot. Lỗi toàn cục (`duration_out_of_range`,
  `shot_count_out_of_range`, `duplicate_index`, `bad_style_lock`) **không** có field này.
- `narration` / `visual_prompt` là field **bắt buộc** ở tầng schema: thiếu hẳn key hoặc
  gửi chuỗi rỗng `""` bị chặn bằng `422` của FastAPI (không có `code`).
  `missing_narration` / `missing_visual_prompt` chỉ xảy ra với chuỗi toàn khoảng trắng.
- Warning `unordered_index`: `index` không tăng dần — server vẫn nhận, chỉ cảnh báo.

- `ok=false` → proposal vẫn được lưu (`status=invalid`) nhưng **không ký được**.
  Thêm finding vào proposal invalid **không** làm nó thành `under_review`.
- Proposal **invalid không tính vào quota 2 vòng** — sửa lỗi validator bao nhiêu lần cũng được.
- WPM lấy theo `language` (`en`=150, `vi`=160); language lạ → mặc định 150 + warning
  `unknown_language_wpm`. Override bằng env `VIDEO_SCRIPT_WPM`.

Sửa bằng version mới: gửi lại với `parent_id` = proposal cũ. Tối đa **2 vòng review**
(một proposal valid đã nhận finding/chữ ký = 1 vòng). Vòng 3 → `409 round_limit_reached`,
`next_action` chuyển thành `owner_required`.

---

## 3. Critic ghi finding

```bash
curl -X POST "$BASE/api/v1/script-proposals/$PID/findings" \
  -H "Authorization: Bearer $CRITIC" -H "Content-Type: application/json" \
  -d '{"category":"fact","severity":"high","evidence":"số 42% sai nguồn","blocking":true}'
```

- `category`: `fact | structure | style | duration`
- `blocking=true` → chặn mọi chữ ký `pass` cho tới khi có version mới.
- Chỉ **author**, **critic của brief**, hoặc **owner** được ghi finding / ký.
  Người khác: `403 not_critic`; brief chưa set critic: `409 critic_not_assigned`.
- Không được tự review proposal của mình (422 `self_review`).

---

## 4. Ký & chốt

```bash
curl -X POST "$BASE/api/v1/script-proposals/$PID/sign" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"verdict":"pass","note":"đã đối chiếu nguồn"}'
```

Điều kiện `agreed`:
1. validator `ok=true`,
2. không còn finding `blocking=1`,
3. **≥2 chữ ký `pass` khác người**, trong đó **bắt buộc có author**.

Kiểm tra trạng thái + việc kế tiếp:
```bash
curl "$BASE/api/v1/briefs/$BRIEF/agreement" -H "Authorization: Bearer $KEY"
```
`next_action`: `create_proposal | fix_proposal | review_or_sign | create_new_version | owner_required | build_job`.

- `owner_required` → hết 2 vòng review, agent **dừng**, chờ owner `decisions`.
- `build_job` → kịch bản đã chốt. **`phase2_open=false`**: endpoint tạo job chưa mở, đây là
  điểm dừng — owner build job thủ công. Đừng poll tiếp.

### Owner gỡ deadlock

Hai agent không chốt được (critic offline, deadline sát) → owner quyết bằng record,
không dùng chat:

```bash
curl -X POST "$BASE/api/v1/briefs/$BRIEF/decisions" -H "Authorization: Bearer $OWNER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"decision":"force_agree","proposal_id":"prp_...","reason":"deadline sát, critic offline"}'
```

`decision=force_agree` → proposal `agreed`, chữ ký của owner ghi `owner_forced: <reason>`.
`decision=reject` → proposal `rejected`. `reason` bắt buộc (422 nếu rỗng). Chỉ owner (403).

- `force_agree` chỉ dùng được khi proposal đã qua validator (`409 proposal_invalid`).
  Proposal invalid thì `reject` để agent làm lại — invalid **không** tốn vòng review.
- Không `reject` được proposal đã `agreed` (`409 already_agreed`) — chốt rồi là chốt.

---

## 5. Bất biến & lỗi thường gặp

| Rule | Lỗi |
|---|---|
| Brief chưa approved | 409 `brief_not_approved` |
| Brief đã bị huỷ | 409 `brief_cancelled` (ký/finding/decision đều chặn) |
| Brief đã có proposal `agreed` | 409 `brief_agreed_frozen` — sửa = brief/job mới |
| Trùng nội dung y hệt | 409 `duplicate_content` |
| Hai agent tạo version cùng lúc | 409 `version_conflict` — thử lại với `parent_id` mới nhất |
| DB bận quá timeout | 503 `database_busy` — backoff rồi thử lại |
| Quá 2 vòng review | 409 `round_limit_reached` — cần owner quyết |
| Proposal invalid | 409 `proposal_invalid` khi ký / `force_agree` |
| Còn finding blocking | 409 `open_blocking_findings` |
| Tự review bài mình | 422 `self_review` |
| Không phải critic của brief | 403 `not_critic` |
| Brief chưa set critic | 409 `critic_not_assigned` |
| Critic trùng author (lúc tạo brief / set critic) | 422 `critic_is_author` |
| Critic trùng author (lúc nộp proposal) | 409 `critic_is_author` |
| Sửa/ký proposal đã agreed/superseded | 409 `proposal_locked` |
| Owner `reject` proposal đã `agreed` | 409 `already_agreed` |
| Ký `block` | Proposal `rejected`; author phải tạo version mới |
| Duyệt/huỷ brief khi trạng thái vừa đổi | 409 `status_race` |
| Duyệt/huỷ brief sai trạng thái (vd. đã huỷ) | 409 `bad_status` |
| Không phải owner | 403 `owner_only` / `calendar_owner_only` |
| Reuse `Idempotency-Key` với body khác | 422 `idempotency_key_reused` |
| Cùng key, request trước còn đang chạy | 409 `idempotency_in_progress` — backoff |

**Agreement là đóng băng.** Hash đổi = ký lại từ đầu; không có "sửa nhỏ sau khi chốt".

**Mọi lỗi của nhóm endpoint này có thêm field `code`** (máy đọc) bên cạnh `detail` (người đọc):

```json
{"detail": "Nội dung trùng proposal v1 (prp_...) — không tạo bản sao.", "code": "duplicate_content"}
```

Agent nên branch theo `code`, đừng parse `detail`:
- `duplicate_content` → **đừng thử lại**, proposal đã tồn tại.
- `version_conflict` / `database_busy` / `idempotency_in_progress` → backoff rồi thử lại.
- `round_limit_reached` / `critic_not_assigned` / `brief_agreed_frozen` / `owner_required`
  → **dừng**, cần owner.
- `idempotency_key_reused` → đổi key mới.

**Idempotency-Key scoped theo agent** — hai agent dùng cùng key không nuốt request của nhau.
Key đã dùng cho body khác → 422 `idempotency_key_reused`, không im lặng trả response cũ.
Key bị giữ **trước** khi chạy nghiệp vụ; request lỗi sẽ nhả key nên retry được ngay.

---

## 6. Ví dụ vòng đời tối thiểu

```
A: POST /briefs (hard, critic=agent-b)         → brf_x draft
OWNER: POST /briefs/brf_x/approve              → brf_x approved
A: POST /script-proposals                      → prp_x v1, valid
B: POST /script-proposals/prp_x/findings       → 2 findings, 1 blocking
A: POST /script-proposals {parent_id: prp_x}   → prp_y v2, valid
B: POST /script-proposals/prp_y/findings       → 0 blocking
A: POST /script-proposals/prp_y/sign pass      → 1/2
B: POST /script-proposals/prp_y/sign pass      → agreed
A: GET /briefs/brf_x/agreement                 → next_action = build_job
```

Phase 2 (`POST /jobs/from-agreement`) sẽ dùng `agreement.content_hash` để tạo item + TTS
đúng một lần. Chưa mở trong bản này.
