# Knowledge Agent Contract — v1.0

> Hợp đồng dùng chung cho **mọi agent** cần tra cứu hoặc ghi tri thức của hệ thống.
>
> - **Raw (dán vào prompt / cho agent đọc):** `GET /api/v1/knowledge/contract?format=raw`
> - **JSON index:** `GET /api/v1/knowledge/contract`
> - **Auth:** `Authorization: Bearer <KEY>` — bất kỳ key có scope `read` trở lên. Không cần PIN.
> - **Guide đầy đủ:** `read_me/09_knowledge_library.md` · **Bảng endpoint:** `read_me/02_api_contracts.md` mục `4b`
> - **UI đọc:** `https://vidfac.gacdero.vn/library`
>
> Production base: `https://vidfac.gacdero.vn`

---

## 1. Sáu luật cứng

1. **Tra cứu trước khi hỏi người.** `GET /api/v1/knowledge/docs?q=<từ khoá>` — tìm cả title, path, tags và nội dung.
2. **Chỉ tạo/sửa trong source `library`.** Tài liệu `docs/`, `skills/`, `plans/`, root là `ro` → gọi `/fork`.
3. **Thêm log = `PATCH append`** (không cần `If-Match`). **Đổi nội dung = `PUT` + `If-Match` nguyên ETag.**
4. **`412` → đọc lại, hợp nhất, ghi lại.** Không retry y nguyên — retry sẽ đè mất bài của người khác.
5. **Không ghi secret thật.** Dùng `sk-REPLACE_ME`, `ghp_xxxxxxxxxxxxxxxxxxxx`.
6. **Xoá / publish / restore chỉ owner** (session PIN). Agent key nhận `403 owner_only` — đừng tìm cách workaround; hãy nhờ owner.

---

## 2. Quyết định nhanh

```text
Cần thông tin? ──> GET /docs?q=...
   │
   ├─ doc["read_only"] == true ──────────> POST /docs/{id}/fork ──> sửa bản fork
   ├─ chỉ muốn thêm log/ghi chú ─────────> PATCH {"mode":"append"}   (không cần ETag)
   ├─ muốn đổi nội dung có chủ đích ─────> GET /docs/{id} lấy ETag ──> PUT / PATCH replace_text + If-Match
   ├─ chưa có tài liệu ──────────────────> POST /docs  {"source":"library","path":...,"content":...}
   └─ muốn publish / xoá / restore ─────> nhờ owner (API key không đủ quyền)
```

---

## 3. Endpoint (chính xác)

| Method | Path | Scope tối thiểu | Body / tham số |
|---|---|---|---|
| `GET` | `/api/v1/knowledge/contract` | `read` | `?format=raw` → Markdown thuần; không → JSON index |
| `GET` | `/api/v1/knowledge/sources` | `read` | — |
| `GET` | `/api/v1/knowledge/docs` | `read` | `q`, `source`, `category`, `status`, `tag`, `limit` (mặc định 50, trần 200), `offset` |
| `GET` | `/api/v1/knowledge/stats` | `read` | — |
| `GET` | `/api/v1/knowledge/docs/{id}` | `read` | Trả header `ETag`; body có `content`, `secret_redacted` |
| `GET` | `/api/v1/knowledge/docs/{id}/raw` | `read` | Tải `.md` (`text/markdown`, `Content-Disposition: attachment`) |
| `GET` | `/api/v1/knowledge/docs/{id}/versions` | `read` | Mới nhất trước, tối đa 20 |
| `POST` | `/api/v1/knowledge/docs` | `ingest` | `source`(mặc định `library`), `path`, `content`, `title?`, `category?`, `tags?`, `source_refs?`, `overwrite?` · hỗ trợ `Idempotency-Key` |
| `POST` | `/api/v1/knowledge/docs/{id}/fork` | `ingest` | `path?`, `title?`, `category?`, `tags?` · hỗ trợ `Idempotency-Key` |
| `PUT` | `/api/v1/knowledge/docs/{id}` | `ingest` | `content`, `title?`, `category?`, `tags?`, `note?` + **`If-Match`** |
| `PATCH` | `/api/v1/knowledge/docs/{id}` | `ingest` | `mode`: `append` \| `prepend` \| `replace_text` \| `overwrite`; `content`, `target_text?`, `replacement?`, `note?` + `If-Match` (chỉ `replace_text`/`overwrite`) |
| `DELETE` | `/api/v1/knowledge/docs/{id}` | **owner** | Xoá mềm → `.trash/<doc_id>/` |
| `POST` | `/api/v1/knowledge/docs/{id}/restore` | **owner** | — |
| `POST` | `/api/v1/knowledge/docs/{id}/versions/{n}/restore` | **owner** | Tạo version **mới** từ nội dung cũ |
| `POST` | `/api/v1/knowledge/docs/{id}/publish` | **owner** | `status`: `published` \| `canonical` |
| `POST` | `/api/v1/knowledge/sync` | `admin` | Body tuỳ chọn `{"prune_missing": true}` |

API legacy `GET/POST/PATCH/DELETE /api/v1/read-me/...` **vẫn hoạt động như cũ**; library chỉ thêm index.

---

## 4. Đối tượng tài liệu

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
  "content": "# Deploy runbook\n...",
  "content_hash": "9f2c...",
  "version": 3,
  "etag": "\"9f2c1d4e5f6a7b8c-v3\"",
  "source_refs": [],
  "secret_redacted": false,
  "size_bytes": 2048,
  "created_at": 1763000000.0,
  "updated_at": 1763000500.0
}
```

- `doc_id` = `knd_` + `sha1("<source>:<path>")[:16]` — **ổn định theo đường dẫn**. Rename = tài liệu mới.
- `status`: `draft` → `published` → `canonical` (chỉ owner publish), cộng `archived`, `trashed`.

---

## 5. Giao thức ETag (chỗ dễ sai nhất)

```bash
ETAG=$(curl -si "$BASE/api/v1/knowledge/docs/$ID" -H "Authorization: Bearer $KEY" \
       | tr -d '\r' | grep -i '^etag:' | cut -d' ' -f2-)
# ETAG bây giờ là  "9f2c1d4e5f6a7b8c-v3"  (có dấu nháy kép)

curl -X PUT "$BASE/api/v1/knowledge/docs/$ID" \
  -H "Authorization: Bearer $KEY" -H "If-Match: $ETAG" \
  -H 'Content-Type: application/json' -d '{"content":"# Mới\n..."}'
```

- Gửi **nguyên chuỗi** `sha16-vN` kể cả dấu `"`. Cắt prefix, gửi `""`, `-v0`, 1 ký tự hex → `412`.
- `If-Match: *` chỉ dùng khi chắc chắn mình là writer duy nhất.
- `append`/`prepend` **không cần** ETag; service khoá read-modify-write nên không mất dòng khi nhiều agent ghi cùng lúc.

---

## 6. Bảng phản xạ lỗi

| HTTP | `code` | Agent phải làm gì |
|---|---|---|
| `401` | `pin_required` / `unknown key` | Kiểm tra `Authorization: Bearer` |
| `403` | `read_only` | `POST /docs/{id}/fork`, sửa bản fork |
| `403` | `published_locked` | Bản `library` đã publish → nhờ owner hoặc tạo `draft` mới |
| `403` | `owner_only` | Dừng lại, nhờ owner. Không retry |
| `409` | `doc_exists` | Dùng path khác, hoặc `overwrite: true` khi chắc chắn |
| `409` | `target_not_found` | `target_text` sai → đọc lại tài liệu |
| `410` | `doc_trashed` | Đang trong trash → `POST /restore` (owner) |
| `412` | `hash_mismatch` | **Đọc lại → hợp nhất → ghi lại**. Không retry y nguyên |
| `413` | `doc_too_large` | > 5 MB — chia nhỏ tài liệu |
| `415` | `unsupported_media_type` | Chỉ `.md`, không NUL/binary |
| `422` | `bad_path` | Sai đường dẫn / thư mục bị bỏ qua / quá 180 ký tự |
| `422` | `secret_detected` | Thay token bằng placeholder |
| `428` | `missing_if_match` | Đọc lấy ETag rồi gửi lại |
| `404` | `doc_not_found` / `source_not_found` / `version_not_found` | Id/source/version sai — tra lại qua `/docs` |

---

## 7. Nguồn & đường dẫn

| `alias` | Mode | Ghi được? |
|---|---|---|
| `library` (`data/knowledge/`) | `rw` | ✅ **nơi duy nhất nên tạo tài liệu mới** |
| `read_me` (`read_me/`) | `rw` | ✅ legacy — file đã có trong git sẽ bị rsync ghi đè lúc deploy |
| `docs`, `skills`, `plans`, `repo_docs`, `repo_skills`, `video_root`, `repo_root` | `ro` | ❌ fork sang `library` |

**Path hợp lệ:** tương đối, `.md`, ≤ 180 ký tự, không `..`, không absolute, không ký tự điều khiển.
Dùng `notes/`, `ops/`, `arch/`, `forks/`.
**Bị indexer bỏ qua** (tạo vào sẽ bị sync xoá): `assets/`, `data/`, `logs/`, `vendor/`, `frontend/`, `.trash/`, mọi thư mục bắt đầu bằng `.`.

---

## 8. Secret — hai lớp chặn

1. **Lúc ghi:** khớp `ghp_…`, `sk-…`, `VIDEO_API_KEYS=…`, `Authorization: Bearer …`, `VIDEO_WEB_PIN=…` → `422 secret_detected`, không tạo file.
2. **Lúc đọc:** file ghi tay lọt qua bị thay thành `[REDACTED]`, kèm `secret_redacted: true` — áp dụng cả `/raw`, snippet, title/category/tags và snapshot version.

Dùng placeholder: `sk-REPLACE_ME`, `ghp_xxxxxxxxxxxxxxxxxxxx`, `VIDEO_WEB_PIN=000000`.

---

## 9. Version & trash

- Mỗi lần ghi → **version mới** (gzip trong DB), giữ 20 bản gần nhất.
- `POST /versions/{n}/restore` tạo version **mới**, không tua ngược lịch sử.
- `DELETE` = xoá mềm; đọc trả `410`; file nằm ở `data/knowledge/.trash/<doc_id>/`; `POST /restore` đưa về chỗ cũ.
- Mọi ghi/xoá/publish ghi dòng `knowledge_events` (actor, action, sha trước/sau).

---

## 10. Copy-paste nhanh

```bash
BASE=https://vidfac.gacdero.vn
KEY='<api-key>'
A="Authorization: Bearer $KEY"
J='Content-Type: application/json'

# 1. Tra cứu
curl -s "$BASE/api/v1/knowledge/docs?q=etag&limit=20" -H "$A"

# 2. Tạo tài liệu mới
curl -sX POST "$BASE/api/v1/knowledge/docs" -H "$A" -H "$J" -d '{
  "source":"library", "path":"notes/etag.md", "content":"# ETag\n...",
  "title":"ETag", "category":"Kỹ thuật", "tags":["api"]
}'

# 3. Nối log (không cần ETag)
curl -sX PATCH "$BASE/api/v1/knowledge/docs/$ID" -H "$A" -H "$J" \
  -d '{"mode":"append","content":"- 10:32 xong\n"}'

# 4. Ghi đè có kiểm soát
ETAG=$(curl -si "$BASE/api/v1/knowledge/docs/$ID" -H "$A" | tr -d '\r' | grep -i '^etag:' | cut -d' ' -f2-)
curl -sX PUT "$BASE/api/v1/knowledge/docs/$ID" -H "$A" -H "$J" \
  -H "If-Match: $ETAG" -d '{"content":"# ETag\nnội dung mới\n"}'
```

---

## 11. Không làm

| ❌ | Lý do | ✅ |
|---|---|---|
| Sửa `docs/`, `skills/`, `plans/` trên prod | Deploy rsync ghi đè | `/fork` |
| `PUT` thiếu `If-Match` | Ghi mù, mất bài người khác | Đọc lấy ETag |
| Retry nguyên request sau `412` | Đè mất thay đổi mới | Đọc → hợp nhất → ghi |
| Ghi token thật | Bị chặn/che, lộ key | Placeholder |
| Tạo file trong thư mục bị bỏ qua | Sync prune xoá row | `notes/`, `ops/`, `arch/` |
| Xoá cả `data/knowledge/` để "dọn" | Mất `.trash` + version | `DELETE` từng doc |
| Dùng library thay git cho code | Không có diff/branch/PR | Git cho code, library cho tri thức vận hành |
