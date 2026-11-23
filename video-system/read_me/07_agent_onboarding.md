# 07 — Đưa 2 agent ngoài vào hệ thống (onboarding)

> Dành cho **owner** (bạn) và để dán cho **2 Muse agent**. Đọc mục 1–2 một lần,
> mục 3 là prompt copy-paste, mục 5 là bảng tra khi agent báo lỗi.

Base URL: `https://vidfac.gacdero.vn` — mọi endpoint dưới đây nằm dưới `/api/v1`.

---

## 1. Tạo 3 key trên VPS (owner làm một lần)

### 1.1 Hiểu đúng format — chỗ này ai cũng sai lần đầu

Biến `VIDEO_API_KEYS` có dạng `tên:quyền`, phân cách bằng dấu phẩy:

```
VIDEO_API_KEYS="owner:admin,muse-writer:ingest,muse-critic:ingest"
```

- `tên` → chính là **agent identity** trong mọi record (ai tạo proposal, ai ký, ai ghi finding).
- `quyền` → `read` < `ingest` < `admin`.
- **Token Bearer là `tên`, KHÔNG kèm `:quyền`.**

```bash
# ĐÚNG
curl -H "Authorization: Bearer muse-writer" ...

# SAI — sẽ trả 401 "unknown key"
curl -H "Authorization: Bearer muse-writer:ingest" ...
```

Mỗi agent phải có key riêng. Dùng chung key thì mọi record hiện cùng một người ký, và
luật "author + critic là 2 người khác nhau" sẽ chặn luôn flow.

### 1.2 Sửa file env của service

```bash
# tìm file env thật của service đang chạy
sudo systemctl cat local-video-editor | grep -i EnvironmentFile
# theo deploy/README.md thường là:
sudo nano /etc/local-video-editor/app.env
```

Thêm/ sửa dòng (giữ nguyên các dòng khác):

```bash
VIDEO_API_KEYS=owner:admin,muse-writer:ingest,muse-critic:ingest
VIDEO_ALLOWED_HOSTS=vidfac.gacdero.vn,talk.gacdero.vn,localhost,127.0.0.1
```

```bash
sudo systemctl restart local-video-editor
sudo systemctl status local-video-editor --no-pager
```

> Nếu `VIDEO_API_KEYS` để trống, hệ thống chỉ phục vụ loopback — agent ngoài sẽ nhận 401.
> Đây là hành vi cố ý (deny-by-default), không phải bug.

### 1.3 Kiểm tra key sống

```bash
BASE=https://vidfac.gacdero.vn/api/v1
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer muse-writer" $BASE/briefs
# 200 = key đúng. 401 = sai token (coi lại 1.1). 403 = thiếu scope.
```

---

## 2. Smoke test trước khi giao agent (owner tự chạy, ~2 phút)

Chạy hết chuỗi này. Nếu ra `agreed` là hệ thống sẵn sàng. Đừng giao agent khi bước này chưa xanh.

```bash
BASE=https://vidfac.gacdero.vn/api/v1
OWNER="Authorization: Bearer owner"
A="Authorization: Bearer muse-writer"
B="Authorization: Bearer muse-critic"

# 1) owner tạo brief, chỉ định critic
BRIEF=$(curl -s -X POST $BASE/briefs -H "$OWNER" -H "Content-Type: application/json" \
  -H "Idempotency-Key: smoke-brief-1" -d '{
    "title":"Smoke test",
    "target_duration_sec":120,
    "approval_mode":"hard",
    "critic":"muse-critic"
  }' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "BRIEF=$BRIEF"

# 2) owner duyệt
curl -s -X POST $BASE/briefs/$BRIEF/approve -H "$OWNER" | head -c 200; echo

# 3) agent A nộp kịch bản 8 shot (300 từ ≈ 120s)
python - "$BRIEF" <<'PY' > /tmp/prop.json
import json,sys
shots=[{"index":i,"shot_type":"scene",
        "narration":" ".join(["filler"]*35+[f"scene{i}","beat"]),
        "visual_prompt":f"cinematic wide shot number {i}"} for i in range(1,9)]
print(json.dumps({"brief_id":sys.argv[1],"shots":shots}))
PY

PROP=$(curl -s -X POST $BASE/script-proposals -H "$A" -H "Content-Type: application/json" \
  -H "Idempotency-Key: smoke-prop-1" -d @/tmp/prop.json \
  | python -c "import sys,json;d=json.load(sys.stdin);print(d['id'],d['status'],d['validation']['ok'])")
echo "PROP=$PROP"

# 4) agent B ghi finding
curl -s -X POST $BASE/script-proposals/${PROP%% *}/findings -H "$B" \
  -H "Content-Type: application/json" \
  -d '{"category":"structure","severity":"medium","evidence":"smoke"}' | head -c 160; echo

# 5) A ký, rồi B ký  → agreed
curl -s -X POST $BASE/script-proposals/${PROP%% *}/sign -H "$A" \
  -H "Content-Type: application/json" -d '{"verdict":"pass"}' | head -c 120; echo
curl -s -X POST $BASE/script-proposals/${PROP%% *}/sign -H "$B" \
  -H "Content-Type: application/json" -d '{"verdict":"pass"}' | head -c 200; echo

# 6) kiểm tra chốt
curl -s $BASE/briefs/$BRIEF/agreement -H "$OWNER" \
  | python -m json.tool | grep -E '"status"|"next_action"|"phase2_open"'
```

Kỳ vọng: `status: agreed`, `next_action: build_job`, `phase2_open: false`.
`phase2_open: false` là **đúng** — xem mục 6.

---

## 3. Prompt dán cho từng agent

Mỗi agent chỉ cần dán đúng một khối. Không cần đọc repo.

### 3.1 Agent A — người viết (author)

```
Bạn là Muse Writer, agent viết kịch bản video cho kênh của owner.

HỆ THỐNG
- Base URL: https://vidfac.gacdero.vn/api/v1
- Token của bạn: muse-writer
- Mọi request: -H "Authorization: Bearer muse-writer"
- Mọi POST: thêm -H "Idempotency-Key: <chuỗi cố định cho hành động đó>"
- KHÔNG tự tạo brief calendar (403). Brief do owner tạo và duyệt.
- Identity của bạn trong hệ thống là "muse-writer". Người review bạn là "muse-critic".

VIỆC CẦN LÀM MỖI LƯỢT (làm đúng 1 việc rồi dừng)
1. GET /briefs?status=approved  → tìm brief chưa có proposal.
2. GET /briefs/{id}/proposals   → nếu đã có proposal của bạn thì đọc validation.
3. Nếu chưa có: POST /script-proposals với brief_id + shots.
   Mỗi shot: {"index","narration","visual_prompt","shot_type","source_url"}.
   - narration phải có thật, không rỗng.
   - Có số liệu/năm/%/tiền trong narration thì BẮT BUỘC kèm source_url.
   - Tổng độ dài phải khớp target_duration_sec ±10% (150 từ/phút với tiếng Anh,
     160 với tiếng Việt).
4. Nếu validation.ok = false: đọc errors[].code, sửa, nộp version mới kèm
   parent_id = proposal_id cũ. Sửa bao nhiêu lần cũng được (proposal invalid
   không tính vào 2 vòng review).
5. Nếu đã valid và critic đã ghi finding: đọc findings, sửa thành version mới.
6. Khi critic đã pass hoặc bạn thấy kịch bản ổn: POST /script-proposals/{id}/sign
   với {"verdict":"pass","note":"..."}.
7. Dừng. Không poll, không tự tạo job.

SAU MỖI LƯỢT, đọc GET /briefs/{id}/agreement và báo lại đúng 1 dòng:
- next_action=build_job  → "Đã chốt kịch bản."
- next_action=review_or_sign → "Chờ muse-critic."
- next_action=fix_proposal → "Đang sửa lỗi validator."
- next_action=owner_required → "Hết 2 vòng review, cần owner quyết."

LỖI → HÀNH ĐỘNG
- 409 duplicate_content  → proposal đã tồn tại, ĐỪNG tạo lại. Đọc lại proposals.
- 409 version_conflict   → đọc proposals, lấy version mới nhất, gửi lại với parent_id đó.
- 409 round_limit_reached / next_action=owner_required → DỪNG, báo owner.
- 409 critic_not_assigned → brief chưa có critic, báo owner.
- 409 brief_agreed_frozen → brief đã chốt, không sửa nữa.
- 409 brief_cancelled    → dừng hẳn.
- 422 idempotency_key_reused → đổi Idempotency-Key khác.
- 503 database_busy / 409 idempotency_in_progress → chờ 5–10s rồi thử lại 1 lần.
- 401 → token sai, báo owner.

QUY TẮC
- Không bao giờ bịa số liệu không có source_url.
- Không sửa kịch bản sau khi đã agreed.
- Không gọi endpoint admin (approve/cancel/decisions) — đó là của owner.
```

### 3.2 Agent B — người phản biện (critic)

```
Bạn là Muse Critic, agent phản biện kịch bản video cho kênh của owner.

HỆ THỐNG
- Base URL: https://vidfac.gacdero.vn/api/v1
- Token của bạn: muse-critic
- Mọi request: -H "Authorization: Bearer muse-critic"
- Identity của bạn là "muse-critic". Người viết là "muse-writer".
- Bạn CHỈ được review brief mà owner đã chỉ định bạn làm critic. Brief khác → 403.

VIỆC CẦN LÀM MỖI LƯỢT (làm đúng 1 việc rồi dừng)
1. GET /briefs?status=approved
2. Với mỗi brief: GET /briefs/{id}/agreement → nếu next_action=build_job thì bỏ qua.
3. GET /briefs/{id}/proposals → lấy proposal mới nhất.
4. Nếu validation.ok=false: KHÔNG cần ghi finding (validator đã bắt lỗi), để author sửa.
5. Nếu validation.ok=true và proposal chưa có finding của bạn:
   POST /script-proposals/{id}/findings
   {"category":"fact|structure|style|duration","severity":"low|medium|high",
    "evidence":"...","blocking":true|false}
   - blocking=true CHỈ dùng khi sai sự thật / sai số liệu / lệch thời lượng nặng.
     Một finding blocking sẽ chặn mọi chữ ký pass cho tới khi có version mới.
   - Tối đa 3 finding mỗi lượt. Đừng bắt bẻ vặt.
6. Nếu proposal đã ổn (không còn vấn đề blocking): POST /script-proposals/{id}/sign
   với {"verdict":"pass","note":"..."}.
   Nếu sai nghiêm trọng và không muốn sửa: {"verdict":"block","note":"..."}
   (block sẽ đánh proposal thành rejected, author phải làm version mới).
7. Dừng.

SAU MỖI LƯỢT, báo lại 1 dòng theo next_action như trên.

LỖI → HÀNH ĐỘNG
- 403 not_critic        → brief này không phải của bạn, bỏ qua.
- 422 self_review       → bạn là author của proposal này, không tự review.
- 409 open_blocking_findings → còn finding blocking, không ký pass được.
- 409 proposal_locked   → proposal đã agreed/superseded, bỏ qua.
- 409 brief_cancelled   → dừng hẳn.
- 409 brief_agreed_frozen → brief đã chốt.
- 503 database_busy / 409 idempotency_in_progress → chờ 5–10s rồi thử lại 1 lần.

QUY TẮC
- Không tự ký pass khi bạn vừa ghi finding blocking.
- Không sửa kịch bản (bạn không có quyền) — chỉ đề xuất qua finding.
- Không gọi endpoint admin.
```

---

## 4. Hai agent nói chuyện với nhau (kênh chat)

Chat là **polling**, không realtime. Dùng khi cần thương lượng trước khi sửa kịch bản.

```bash
# A mở thread (slot thứ 2 để trống)
TID=$(curl -s -X POST $BASE/chat/threads -H "$A" -H "Content-Type: application/json" \
  -d '{"title":"Brief brf_xxx — bàn hướng sửa"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# B vào thread bằng cách gửi tin (tự claim slot trống)
curl -s -X POST $BASE/chat/threads/$TID/messages -H "$B" \
  -H "Content-Type: application/json" -d '{"body":"Shot 3 thiếu nguồn, sửa giúp."}'

# Đọc tin mới — cursor là `seq`, không phải id. Lưu `last_seq` cho lượt sau.
curl -s "$BASE/chat/threads/$TID/messages?since_id=0" -H "$A"
```

Response: `{"thread_id","messages":[{"seq","sender","body","created_at"}],"last_seq","has_more"}`.
Lượt sau gọi `?since_id=<last_seq>`.

Mỗi thread tối đa 2 participant. Người thứ 3 đọc sẽ bị 403 `not_participant`;
owner (key tên `owner`) đọc được mọi thread.

---

## 5. Ai được làm gì

| Endpoint | Key cần | Ghi chú |
|---|---|---|
| `GET /health` | (không cần) | kiểm tra service sống |
| `GET /briefs`, `GET /briefs/{id}` | `read` | agent xem được |
| `POST /briefs` | `ingest` | `approval_mode=calendar` chỉ owner |
| `POST /briefs/{id}/approve` | `admin` + tên `owner` | owner |
| `POST /briefs/{id}/cancel` | `admin` + tên `owner` | owner |
| `POST /briefs/{id}/critic` | `admin` + tên `owner` | owner chỉ định critic |
| `POST /briefs/{id}/decisions` | `admin` + tên `owner` | owner gỡ bế tắc |
| `POST /script-proposals` | `ingest` | author |
| `GET /briefs/{id}/proposals` | `read` | |
| `POST /script-proposals/{id}/findings` | `ingest` | phải là author/critic/owner |
| `POST /script-proposals/{id}/sign` | `ingest` | phải là author/critic/owner |
| `GET /briefs/{id}/agreement` | `read` | nguồn sự thật cho `next_action` |

---

## 6. Giới hạn hiện tại — nói trước để không ai chờ vô vọng

1. **`agreed` là điểm dừng.** `phase2_open=false`: endpoint tự tạo job + chia shot
   (`POST /jobs/from-agreement`) **chưa mở**. Agent chốt kịch bản xong thì dừng,
   owner build job thủ công. Đừng để agent poll chờ.
2. **Không có push.** Agent là one-shot: mỗi lượt phải được gọi (owner bấm, hoặc
   automation/cron gọi định kỳ). Hệ thống không tự đánh thức agent.
3. **Chat là polling**, không realtime.
4. **`findings` không có Idempotency-Key.** Retry sẽ tạo finding trùng — agent phải
   `GET /script-proposals/{id}/findings` trước khi gửi lại.
5. **11 endpoint POST cũ** (items, shorts, batch…) vẫn dùng cơ chế idempotency kiểu cũ,
   chưa có claim. Không ảnh hưởng luồng brief→script.

---

## 7. Checklist "2 agent đã join thành công"

- [ ] `GET /briefs` với key `muse-writer` → 200
- [ ] `GET /briefs` với key `muse-critic` → 200
- [ ] Owner tạo brief có `critic: "muse-critic"` → 201, sau approve → `approved`
- [ ] `muse-writer` nộp proposal → 201, `validation.ok=true`
- [ ] `muse-critic` ghi finding → 200
- [ ] `muse-writer` ký pass → `agreed=false`
- [ ] `muse-critic` ký pass → `status=agreed`
- [ ] `GET /briefs/{id}/agreement` → `next_action=build_job`, `phase2_open=false`
- [ ] Chat: A mở thread, B gửi tin, A đọc được bằng `since_id`

Xong 9 mục này là cả hai đã thực sự nằm trong hệ thống, không chỉ "gọi được API".
