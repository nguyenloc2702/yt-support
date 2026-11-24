# MCP bridge cho agent — gọi vidfac bằng tool có schema

**Trạng thái:** live · **Code:** `backend/mcp_server.py` · **Transport:** stdio

---

## 1. Cái này để làm gì

Agent thường phải đọc hợp đồng bằng prose (`read_me/08_muse_contract.md`), tự dựng
URL, tự nhớ scope, tự đoán field. Dễ sai và khó kiểm.

MCP bridge bọc 9 thao tác thường dùng thành **tool có schema**. Agent client khai báo
server một lần, sau đó gọi tool bằng tên + JSON arguments; bridge tự thêm
`Authorization` (khi dùng key) hoặc session cookie (khi dùng PIN), tự thêm `/api/v1`,
tự sinh `Idempotency-Key` khi thiếu.

Bridge **không thay đổi** hợp đồng API. Nó chỉ là lớp gọi HTTP mỏng:
`tool → VidfacClient → https://<host>/api/v1/...`. Mọi luật nghiệp vụ (scope, state
machine, validator) vẫn nằm ở backend.

---

## 2. Chạy

```bash
cd video-system
export VIDFAC_BASE_URL=https://vidfac.gacdero.vn
# writer / critic / read: dùng key
export VIDFAC_API_KEY=<key>
# owner: dùng PIN 6 số (không cần key `owner`)
# export VIDFAC_PIN=<6 số>
./.venv/Scripts/python.exe -m backend.mcp_server
```

- Đây là **stdio server**, không phải HTTP server. Nó nói JSON-RPC qua stdin/stdout.
  **Không** mở port, **không** có URL để gọi.
- Agent client tự spawn process này và giữ ống stdin/stdout. Bạn không chạy tay
  trừ khi đang debug.
- Env đọc **tại thời điểm gọi tool**, nhưng process giữ nguyên env lúc spawn — đổi
  key nghĩa là spawn process mới.

| Env | Mặc định | Ghi chú |
|---|---|---|
| `VIDFAC_BASE_URL` | `http://127.0.0.1:8090` | Host hoặc base API. Thiếu `/api/v1` thì client tự thêm. |
| `VIDFAC_API_KEY` | *(trống)* | Key cho writer / critic / read. Không dùng chung với PIN. |
| `VIDFAC_PIN` | *(trống)* | PIN 6 số cho process owner — đăng nhập một lần lấy session cookie. Không dùng chung với key. |
| `VIDFAC_TIMEOUT` | `20` | Giây. |

Cần **ít nhất một** trong `VIDFAC_API_KEY` / `VIDFAC_PIN`; thiếu cả hai → tool trả
lỗi rõ ràng, không crash process. Một process chỉ nên dùng **một** cách: key cho
writer/critic/read, PIN cho owner.

---

## 3. Config mẫu cho MCP client

Bất kỳ MCP client hỗ trợ stdio đều dùng được (Claude Desktop, Cursor, tự viết…).
Dưới đây là dạng `mcpServers` phổ biến — **không phải Muse hỗ trợ MCP native**,
Muse chỉ là một MCP client bất kỳ nếu nó spawn được process.

```json
{
  "mcpServers": {
    "vidfac-owner": {
      "command": "C:/app/work/support-video-edit/video-system/.venv/Scripts/python.exe",
      "args": ["-m", "backend.mcp_server"],
      "cwd": "C:/app/work/support-video-edit/video-system",
      "env": {
        "VIDFAC_BASE_URL": "https://vidfac.gacdero.vn",
        "VIDFAC_PIN": "<6 số>",
        "PYTHONPATH": "C:/app/work/support-video-edit/video-system"
      }
    }
  }
}
```

- Dùng **đường dẫn tuyệt đối** cho `command`. Client không kế thừa `PATH`/venv của bạn.
- `cwd` cần trỏ vào `video-system/` để `-m backend.mcp_server` import được. Client
  không hỗ trợ `cwd` thì thêm `PYTHONPATH` như trên.
- Mỗi entry `mcpServers` là **một process = một danh tính** (key hoặc PIN). Muốn 2
  vai thì 2 entry. Key dành cho writer/critic/read; **owner dùng `VIDFAC_PIN`**, không
  cần key `owner` (prod không có key nào mang identity `owner`).

---

## 4. Chín tool

| Tool | Tham số | Scope cần | API tương ứng | Ghi chú |
|---|---|---|---|---|
| `knowledge_search` | `query`, `source?`, `limit=10` | `read` | `GET /knowledge/docs` | Trả `{docs: [...]}`. |
| `knowledge_read` | `doc_id` | `read` | `GET /knowledge/docs/{id}` | Kèm `content` + `ETag`. |
| `brief_create` | `title`, `angle?`, `target_duration_sec=780`, `language="vi"`, `approval_mode="hard"`, `source_links?` | `ingest` | `POST /briefs` | **Không có tham số `critic`** — critic do owner gán riêng. |
| `brief_assign_critic` | `brief_id`, `critic` | `admin` + identity `owner` | `POST /briefs/{id}/critic` | Critic trùng author → `422 critic_is_author`. |
| `brief_approve` | `brief_id` | `admin` + identity `owner` | `POST /briefs/{id}/approve` | Brief phải `draft`. |
| `agreement_read` | `brief_id` | `read` | `GET /briefs/{id}/agreement` | Nguồn sự thật cho `next_action`. |
| `proposal_submit` | `brief_id`, `shots`, `script_text?`, `parent_id?`, `idempotency_key?` | `ingest` | `POST /script-proposals` | Tự sinh `Idempotency-Key` (uuid hex) nếu thiếu. |
| `finding_write` | `proposal_id`, `category`, `severity`, `evidence?`, `blocking=false` | `ingest` | `POST /script-proposals/{id}/findings` | Chỉ **critic hoặc owner**; author tự ghi → `422 self_review`. Không có idempotency → đọc findings trước khi gửi lại. |
| `proposal_sign` | `proposal_id`, `verdict`, `note?` | `ingest` | `POST /script-proposals/{id}/sign` | `verdict`: `pass` \| `block`. Trả `agreed: bool`. |

`shots[]` mỗi phần tử: `{index?, narration, visual_prompt?, shot_type?, source_url?}`.
`category`: `fact` `structure` `style` `duration`. `severity`: `low` `medium` `high`.

---

## 5. Chọn danh tính — một process, một cách

| Việc | Danh tính nên dùng |
|---|---|
| `brief_create`, `brief_assign_critic`, `brief_approve` | **PIN** (`VIDFAC_PIN`) — session cho identity `owner` |
| `proposal_submit`, `finding_write`, `proposal_sign` | key `ingest` của từng agent (vd `muse-writer`, `muse-critic`) |
| `knowledge_search`, `knowledge_read`, `agreement_read` | key `read` (hoặc key `ingest` — scope cao hơn bao gồm read) |

Prod chỉ có key `sk-video-agent-prod:ingest` và `sk-video-admin-prod:admin`; **không
có key nào mang identity `owner`**. Vì `brief_assign_critic` / `brief_approve` đòi
identity đúng bằng `owner`, process owner phải đăng nhập bằng `VIDFAC_PIN` (session
cookie), không dùng key. `brief_create` về kỹ thuật chỉ cần `ingest`, nhưng giữ luồng
brief ở process owner để owner là người duyệt duy nhất.

> **Quan trọng:** bridge đọc danh tính từ env **một lần lúc spawn**. Một process MCP
> chỉ mang **một** danh tính: key **hoặc** PIN, không trộn. Muốn agent đóng cả vai
> writer và critic trong cùng phiên thì phải chạy **2 process với 2 key khác nhau**
> (2 entry `mcpServers`). Không có cách đổi danh tính theo từng tool call.

Lý do: backend bắt buộc **2 chữ ký từ 2 identity khác nhau** (author + critic), chặn
critic trùng người tạo (`422 critic_is_author`) và chặn author tự ghi finding
(`422 self_review`). Một danh tính chỉ tạo được một identity → dùng chung sẽ khoá luồng.

---

## 6. Ví dụ một lượt

### Writer (key `muse-writer`, scope `ingest`)

```json
{"name": "agreement_read", "arguments": {"brief_id": "brf_01H..."}}
```

```json
{
  "name": "proposal_submit",
  "arguments": {
    "brief_id": "brf_01H...",
    "script_text": "Toàn văn nếu có",
    "shots": [
      {"index": 1, "narration": "Mở đầu...", "visual_prompt": "wide establishing shot", "shot_type": "scene"},
      {"index": 2, "narration": "Diễn biến...", "visual_prompt": "close-up", "shot_type": "scene"}
    ]
  }
}
```

Đọc `validation.ok`. Nếu `false` → sửa theo `validation.errors[].code`, nộp version
mới với `parent_id`. Nếu `true`:

```json
{"name": "proposal_sign", "arguments": {"proposal_id": "prp_01H...", "verdict": "pass"}}
```

### Critic (key `muse-critic`, scope `ingest`)

```json
{"name": "agreement_read", "arguments": {"brief_id": "brf_01H..."}}
```

```json
{
  "name": "finding_write",
  "arguments": {
    "proposal_id": "prp_01H...",
    "category": "style",
    "severity": "low",
    "evidence": "Câu 3 lặp từ 'tuy nhiên' hai lần",
    "blocking": false
  }
}
```

```json
{"name": "proposal_sign", "arguments": {"proposal_id": "prp_01H...", "verdict": "pass", "note": "đã đối chiếu nguồn"}}
```

Chỉ `blocking=true` khi sai sự thật hoặc lệch thời lượng nặng. Tối đa 3 finding/lượt.

---

## 7. Lỗi thường gặp & cách phản xạ

Bridge biến HTTP lỗi thành `ToolError` dạng `HTTP <status> <code>: <detail>`. Message
**không bao giờ** chứa API key hay PIN.

| Triệu chứng | Nghĩa | Phản xạ |
|---|---|---|
| `Thiếu VIDFAC_API_KEY hoặc VIDFAC_PIN` | Process không có env key lẫn PIN. | Set `VIDFAC_API_KEY` (writer/critic/read) hoặc `VIDFAC_PIN` (owner) rồi **spawn lại** process MCP. Không retry tool. |
| `HTTP 401: PIN login failed` | `VIDFAC_PIN` sai hoặc hết hiệu lực. | Kiểm tra PIN 6 số; spawn lại process. Message không chứa PIN. |
| `HTTP 403 owner_only` | Danh tính không phải `owner` nhưng gọi `brief_assign_critic` / `brief_approve`. | Đổi sang process owner dùng `VIDFAC_PIN`. Đừng thử lại bằng key agent. |
| `HTTP 403 critic_owner_only` | Gửi `critic` khi tạo brief bằng key không phải owner. | Bỏ field `critic`; owner gọi `brief_assign_critic` sau. (Tool `brief_create` không có tham số này, nên lỗi này chỉ gặp khi gọi API trực tiếp.) |
| `HTTP 403 not_critic` / `HTTP 409 critic_not_assigned` | Key hiện tại không phải critic của brief / brief chưa có critic. | Kiểm tra `brief.critic`; báo owner gán critic. Không retry. |
| `HTTP 409 proposal_locked` | Proposal đã `agreed`/`superseded`. | Dừng; đọc `agreement_read`. Muốn mở lại phải qua owner decision. |
| `HTTP 409 idempotency_in_progress` | Request trước cùng key còn chạy. | Backoff 5–10s, retry **một** lần. |
| `HTTP 412 hash_mismatch` | `If-Match`/ETag cũ khi ghi Knowledge. | Đọc lại bản mới, hợp nhất, gửi lại. **Không retry y nguyên.** Bridge hiện chỉ đọc Knowledge nên lỗi này chỉ gặp khi gọi API trực tiếp. |
| `HTTP 422 self_review` | Tự ghi finding cho proposal của mình. | Đổi sang key của critic/owner (process thứ hai). Ký bài mình thì được phép. |
| `HTTP 422 validation_error` | Payload sai field. | Đọc `detail`/`errors`, sửa payload. Không retry nguyên bản. |

`ToolError` là lỗi trả về cho agent — coi như kết quả tool, không phải crash process.

---

## 8. Bảo mật

- **Không** đưa key hay PIN vào prompt, log, hay nội dung tool call. Chúng chỉ nằm
  trong env của process MCP.
- Mỗi agent một danh tính riêng, quyền tối thiểu: writer/critic `ingest`, reader
  `read`; owner dùng **PIN** (session cookie, identity `owner`, scope `admin`).
  Dùng chung danh tính làm mất audit trail (`created_by`, `signer`, `reviewer`) và
  có thể khoá luồng 2 người.
- Không có key `owner` trên prod. Process owner đăng nhập bằng `VIDFAC_PIN`; bridge
  gọi `POST /auth/login` một lần khi tạo client và giữ cookie session. Nếu agent cần
  owner decision, để owner chạy process riêng với PIN.
- Không log `Authorization`, `Cookie`, `VIDFAC_API_KEY` hay `VIDFAC_PIN`. Bridge đã
  lọc key/PIN khỏi message lỗi; đừng tự thêm log làm rò rỉ.
