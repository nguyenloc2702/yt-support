# 09 — Knowledge Library (`/library`)

> Kho tổng hợp **tất cả** tài liệu Markdown của hệ thống, đọc được trên web và qua API.
> Đây là nơi agent tra cứu trước khi hỏi người, và là nơi ghi lại tri thức mới.
> Bảng endpoint đầy đủ: `read_me/02_api_contracts.md` mục **4b**.

---

## 1. Vì sao có kho này

Trước đây tài liệu nằm rải ở `read_me/`, `docs/`, `skills/`, `plans/` và hàng chục file
`.md` ở root. Muốn tra một contract phải biết nó ở đâu; muốn đọc trên web thì không có
đường nào; và không ai biết bản nào là bản mới nhất.

Knowledge Library giải đúng ba việc đó:

| Vấn đề | Cách giải |
|---|---|
| Không biết tài liệu nằm đâu | Một danh mục chung, search cả tiêu đề và nội dung |
| Không đọc được trên web | Trang `/library` render Markdown, có mục lục, có tìm kiếm |
| Không biết bản nào mới | `version`, `ETag`, `knowledge_events` (audit), phát hiện trùng lặp |

**Không phải** nơi thay thế git. Nội dung vẫn là file thật trên đĩa — git vẫn là lịch sử
chuẩn của repo. Library chỉ thêm index, version ngắn hạn (20 bản) và audit.

---

## 2. Source registry — hai loại, đừng nhầm

| `alias` | Đường dẫn | Mode | Ghi được? |
|---|---|---|---|
| `read_me` | `video-system/read_me/` | `rw` | ✅ (tương thích legacy) |
| `library` | `video-system/data/knowledge/` | `rw` | ✅ **nơi duy nhất nên tạo tài liệu mới** |
| `docs` | `video-system/docs/` | `ro` | ❌ fork sang `library` |
| `skills` | `video-system/skills/` | `ro` | ❌ fork |
| `plans` | `<repo>/plans/` | `ro` | ❌ fork |
| `repo_docs` / `repo_skills` | `<repo>/docs`, `<repo>/skills` | `ro` | ❌ fork |
| `video_root` / `repo_root` | root `*.md` (không đệ quy) | `ro` | ❌ fork |

### Vì sao repo docs là read-only

Production deploy bằng `git pull` + `rsync` từ `/opt/support-video-edit` sang
`/opt/video-system`. File trong `docs/`, `skills/`, `plans/` bị **ghi đè mỗi lần deploy**.
Sửa trực tiếp ở đó = mất bài trong lần deploy sau, và tệ hơn là git sẽ báo conflict.

Muốn sửa một tài liệu repo: **fork** rồi sửa bản fork.

```bash
curl -sX POST "$BASE/api/v1/knowledge/docs/$DOC_ID/fork" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"title": "Deploy runbook (bản của tôi)"}'
```

Bản fork nằm ở `library` path `forks/<source>/<path gốc>`, `status=draft`,
`source_refs=[<doc_id gốc>]` — giữ được dấu vết về nguồn.

### `read_me/` cũng bị rsync

`read_me/` được deploy rsync y như `docs/`, nhưng vẫn để `rw` vì đây là nơi agent ghi log
vận hành từ trước tới nay. Hệ quả cần nhớ: **file nào đã có trong git thì lần deploy sau sẽ
ghi đè**. Dùng `read_me/` cho log/ghi chú phát sinh; tài liệu muốn sửa lâu dài thì để trong
`library`.

---

## 3. Định danh tài liệu

`doc_id` = `knd_` + `sha1("<source>:<path>")[:16]`, **ổn định theo đường dẫn**.
Đổi nội dung không đổi id; đổi đường dẫn (rename) thì id đổi — coi như tài liệu mới.

Mỗi tài liệu có:

```json
{
  "id": "knd_1a2b3c4d5e6f7a8b",
  "source": "library",
  "path": "ops/deploy.md",
  "title": "Deploy runbook",
  "category": "Vận hành",
  "tags": ["ops", "deploy"],
  "status": "draft",
  "owner": "video-agent-1",
  "read_only": false,
  "content_hash": "9f2c...64 ký tự",
  "version": 3,
  "etag": "\"9f2c1d4e5f6a7b8c-v3\"",
  "source_refs": [],
  "size_bytes": 2048,
  "created_at": 1763000000.0,
  "updated_at": 1763000500.0
}
```

`status`: `draft` → `published` → `canonical` (owner publish), cộng `archived` và `trashed`.

---

## 4. Đọc

### 4.1 Tra cứu

```bash
# Tìm trong cả tiêu đề, đường dẫn, tags và nội dung
curl -s "$BASE/api/v1/knowledge/docs?q=etag&limit=20" -H "Authorization: Bearer $KEY"

# Lọc theo source / category / tag / status
curl -s "$BASE/api/v1/knowledge/docs?source=library&tag=ops" -H "Authorization: Bearer $KEY"

# Bức tranh tổng thể
curl -s "$BASE/api/v1/knowledge/stats" -H "Authorization: Bearer $KEY"
```

Kết quả khớp ở **nội dung** (không phải title) có thêm `snippet` — dùng để hiển thị
ngữ cảnh mà không phải tải cả tài liệu.

### 4.2 Đọc một tài liệu

```bash
curl -si "$BASE/api/v1/knowledge/docs/$DOC_ID" -H "Authorization: Bearer $KEY"
```

- Header `ETag` — **giữ lại**, cần cho lần ghi.
- Body: `content` (Markdown đã che secret) + `secret_redacted` (bool).

Tải file gốc: `GET /api/v1/knowledge/docs/{id}/raw` → `text/markdown` kèm
`Content-Disposition: attachment`.

---

## 5. Ghi

### 5.1 Tạo mới (chỉ trong `library`)

```bash
curl -sX POST "$BASE/api/v1/knowledge/docs" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{
        "source": "library",
        "path": "ops/2026-11-24_deploy.md",
        "content": "# Deploy 24/11\n- service restart OK\n",
        "title": "Deploy 24/11",
        "category": "Vận hành",
        "tags": ["ops", "deploy"]
      }'
```

`path` phải là đường dẫn **tương đối**, kết thúc `.md`, không chứa `..`, không absolute.
Tạo trùng `path` → `409 doc_exists`; muốn ghi đè thì `"overwrite": true` (biết rõ mình đang làm gì).

### 5.2 Nối thêm log (cách agent nên dùng 90% thời gian)

```bash
curl -sX PATCH "$BASE/api/v1/knowledge/docs/$DOC_ID" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"mode": "append", "content": "- 10:32 render xong 42 shot\n"}'
```

`append` và `prepend` **không cần `If-Match`** — đây là chủ ý: nhiều agent cùng ghi log
nối tiếp thì không phải lo tranh chấp. Service tự khoá read-modify-write nên không mất dòng.

### 5.3 Sửa nội dung (cần `If-Match`)

```bash
ETAG=$(curl -sI "$BASE/api/v1/knowledge/docs/$DOC_ID" -H "Authorization: Bearer $KEY" \
       | grep -i '^etag:' | tr -d '\r' | cut -d' ' -f2)

curl -sX PUT "$BASE/api/v1/knowledge/docs/$DOC_ID" \
  -H "Authorization: Bearer $KEY" \
  -H "If-Match: $ETAG" \
  -H 'Content-Type: application/json' \
  -d '{"content": "# Deploy runbook\nNội dung mới\n"}'
```

Quy tắc phản xạ khi gặp lỗi:

| Lỗi | Nghĩa | Làm gì |
|---|---|---|
| `428 missing_if_match` | Bạn quên `If-Match` | Đọc lại lấy ETag rồi gửi lại |
| `412 hash_mismatch` | Người khác đã sửa sau khi bạn đọc | **Đọc lại**, hợp nhất thay đổi của họ, rồi ghi. Đừng retry y nguyên — sẽ ghi đè mất bài của họ |
| `403 published_locked` | Tài liệu đã publish | Nhờ owner, hoặc tạo bản `draft` mới trong `library` |
| `403 read_only` | Tài liệu repo | `POST /fork` rồi sửa bản fork |

`If-Match: *` được chấp nhận khi bạn chắc chắn là writer duy nhất.
Phải gửi **nguyên ETag** (`sha16-vN`) — cắt prefix, gửi `""` hay `-v0` đều bị 412.

### 5.4 Sửa cục bộ

```bash
# Thay đúng một cụm (lần xuất hiện đầu tiên) — vẫn cần If-Match
curl -sX PATCH "$BASE/api/v1/knowledge/docs/$DOC_ID" \
  -H "Authorization: Bearer $KEY" -H "If-Match: $ETAG" \
  -H 'Content-Type: application/json' \
  -d '{"mode": "replace_text", "target_text": "port 8080", "replacement": "port 8090"}'
```

Không tìm thấy `target_text` → `409 target_not_found`, file **không** bị đổi.

---

## 6. Version, trash, audit

- Mỗi lần ghi tạo **một version mới** (không ghi đè lịch sử), nén gzip trong DB.
- Giữ **20 version gần nhất** mỗi tài liệu; cũ hơn bị cắt tự động.
- `GET /versions` → danh sách mới nhất trước.
- `POST /versions/{n}/restore` → khôi phục nội dung cũ thành version **mới** (không tua ngược lịch sử).
- `DELETE` là **xoá mềm**: file chuyển vào `data/knowledge/.trash/<doc_id>/`, row giữ nguyên,
  đọc lại trả `410 doc_trashed`. `POST /restore` đưa về chỗ cũ.
- Mọi thao tác ghi/xoá/publish ghi một dòng `knowledge_events` (actor, action, sha trước/sau).

---

## 7. Secret bị chặn hai lớp

Tài liệu vận hành hay chứa token thật, mà library đọc được qua HTTP. Vì vậy:

1. **Lúc ghi** — nội dung khớp pattern secret thì bị từ chối `422 secret_detected`, file
   không được tạo. Pattern: GitHub PAT `ghp_…`, `sk-…`, `VIDEO_API_KEYS=…`,
   `Authorization: Bearer …`, `VIDEO_WEB_PIN=…`.
2. **Lúc đọc** — file đã lỡ nằm trên đĩa (agent ghi tay) thì chuỗi secret bị thay bằng
   `[REDACTED]` và `secret_redacted: true`. Áp dụng cho cả `/raw`.

Muốn ghi ví dụ về key: dùng placeholder — `ghp_xxxxxxxxxxxxxxxxxxxx`, `sk-REPLACE_ME`.

---

## 8. Index & sync

- Index tự chạy khi đọc, tối đa mỗi **60s** một lần (`VIDEO_KNOWLEDGE_SYNC_TTL`).
- Process restart reset mốc TTL, nên **sau mỗi deploy, lần đọc đầu tiên tự sync lại toàn bộ**.
  Không có bước deploy nào bắt buộc phải gọi tay.
- Muốn làm nóng index ngay (tránh request đầu chậm) hoặc kiểm tra sau khi rsync:

```bash
curl -sX POST "$BASE/api/v1/knowledge/sync" -H "Authorization: Bearer $ADMIN_KEY"
# → {"scanned":137,"added":2,"updated":1,"unchanged":134,"removed":0,"skipped":0,"errors":[]}
```

- File agent ghi thẳng bằng shell/`PATCH /read-me` cũng được index ở lần sync kế tiếp —
  không cần gọi API library để "đăng ký".
- `prune_missing` (mặc định `true`) chỉ xoá row của source **`ro`** khi file biến mất.
  Row `rw` không bị xoá tự động (tránh mất lịch sử khi ai đó `mv` file).

### Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `VIDEO_KNOWLEDGE_DIR` | `<data>/knowledge` | Root của source `library` |
| `VIDEO_KNOWLEDGE_REPO_DIR` | tự dò `/opt/support-video-edit` | Repo chứa `plans/`, `skills/` |
| `VIDEO_KNOWLEDGE_REPO_SOURCES` | `1` | `0` = chỉ index `library` + `read_me` (dùng cho test) |
| `VIDEO_KNOWLEDGE_SYNC_TTL` | `60` | Giây giữa hai lần tự sync |

---

## 9. Quy ước viết tài liệu

**Đường dẫn** — theo chủ đề, không theo ngày:

```
library/ops/deploy.md              ✅
library/ops/2026-11-24_deploy.md   ⚠️ chỉ dùng cho log
library/arch/etag.md               ✅
```

**Front-matter** (tuỳ chọn, giúp search và UI tốt hơn):

```markdown
---
title: Deploy runbook
category: Vận hành
tags: [ops, deploy]
source_refs: [knd_1a2b3c4d5e6f7a8b]
---
```

`title`, `category`, `tags`, `source_refs`, `owner` trong front-matter được tôn trọng.
`status` và `read_only` **thì không** — hai field đó do API và source quyết định, để một
file chưa ai duyệt không tự phong `canonical` rồi khoá đường sửa của agent khác.

**Nội dung** — một `#` duy nhất ở đầu (thành title), heading `##` trở thành mục lục bên phải.

---

## 10. Trang web `/library`

`https://vidfac.gacdero.vn/library` — mở trực tiếp, không cần qua SPA.

- Cột trái: source + bộ lọc + danh sách (có badge `read-only` / `draft` / `published`).
- Giữa: Markdown render (`marked` + `DOMPurify`, vendor sẵn trong `/static/vendor/` —
  phần JS không gọi CDN nên vẫn render được offline. Font Google cũng giống app chính; khi
  mạng chết chỉ fallback sang font hệ thống, không ảnh hưởng chức năng).
- Phải: mục lục sinh từ heading `##`.
- Nút: tạo mới, sửa, fork, tải `.md`, xoá/khôi phục, sync. Nút xoá/publish hiện với mọi
  tài liệu ghi được; **backend mới là nơi enforce owner** — agent không có session PIN sẽ
  nhận `403 owner_only` kèm toast.
- Editor gửi `If-Match`; gặp `412` sẽ báo "tài liệu đã đổi, mở lại rồi sửa".

Trang này cần PIN gate như phần còn lại của web UI (`/library` và `/static/` nằm trong
allowlist để tải được CSS/JS trước khi đăng nhập).

---

## 11. Những điều không nên làm

| ❌ | Vì sao | ✅ |
|---|---|---|
| Sửa file trong `docs/`, `skills/`, `plans/` trên prod | Deploy sau ghi đè, mất bài | Fork sang `library` |
| `PUT` không kèm `If-Match` | Ghi mù, đè mất bài người khác | Đọc lấy ETag trước |
| Retry y nguyên sau `412` | Sẽ ghi đè thay đổi của người khác | Đọc lại, hợp nhất, ghi lại |
| Ghi token thật vào tài liệu | Bị chặn `422`, và nếu lọt thì bị che khi đọc | Dùng `sk-REPLACE_ME` |
| Xoá cả cây `data/knowledge/` để "dọn" | Mất `.trash` + lịch sử version | Dùng `DELETE` từng tài liệu |
| Dùng library thay git cho code | Library không có diff/branch/PR | Git cho code, library cho tri thức vận hành |
