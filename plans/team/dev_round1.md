# Memo Senior Dev — Round 1

## Kết luận trước

Giữ REST, nâng thành **long-poll async + inbox cursor/ack lưu server**, thêm **work-protocol mỏng** (claim/lease, review, presence) trên SQLite — **không** SSE/WS/broker. Vì 1 process + WAL đủ làm nguồn sự thật; streaming (`GZipMiddleware` `main.py:74` + nginx buffer) rủi ro thật nhưng vô nghĩa nếu response vẫn là JSON một cục; agent one-shot chỉ cần một endpoint "lấy việc rồi thoát".

## Đánh giá hiện trạng

**Đủ dùng:** thread 1-1 + CAS slot (`service_chat.py:129-137`), cursor `seq` không rewind (`store.py:400`), identity = key-name (`auth.py:41-64`).

**Nợ chặn mục tiêu 2-agent:**

1. **Không idempotency:** `_idempotent()` có (`main.py:175`) nhưng chat POST không dùng (`main.py:1816-1825`) → retry tạo tin trùng; `idempotency.key` PK toàn cục (`store.py:118`) nên 2 agent trùng key nuốt nhau.
2. **Cursor ở localStorage** (`index.html:7398`) → one-shot không có "đã đọc/đã xử lý".
3. **`list_threads` N+1 + rò nội dung:** 3 subquery/thread (`service_chat.py:69-71`) × 50 mỗi 3s/tab (`index.html:7427`); trả `last_body` cho mọi key read.
4. **Cap 30 tin là vòng đời** (`service_chat.py:31`) → tự `locked` giữa phiên; rate limit 20/5 phút toàn cục theo sender → 429 giữa workflow.
5. **Không có đơn vị công việc:** `todos` là view của `items` (`service_todos.py:725-747`), không holder/lease → 2 agent cùng gen 1 shot; fence chỉ ở `shots.status` (`service_shots.py:110-120`), sau khi đã tốn tiền. `todo_feedbacks` (phát âm, `store.py:122-138`) không chặn được `confirm-assemble`.

## So sánh phương án

| | Độ trễ | Phức tạp | Rủi ro prod (GZip/nginx/threadpool) | One-shot | Chi phí | Nên dùng khi |
|---|---|---|---|---|---|---|
| **A. Poll 3s** | 0–3s | Rất thấp | Không | Tốt | 2 req/3s/tab | ≤3 agent |
| **B. Long-poll async** | ~0.4s | Thấp | Thấp nếu `async def`; **cao** nếu `def` | Tốt (`wait=0`) | 1 coroutine/agent | **Chọn** |
| **C. SSE** | <0.1s | TB | **Cao**: GZip/nginx buffer, sửa middleware | Kém | Thêm timeout | Push, >20 client |
| **D. WebSocket** | <0.1s | Cao | Cao: upgrade, reconnect | Rất kém | Cao | Chat realtime |
| **E. Redis/NATS** | ~ms | Rất cao | TB, thêm service phải trực | Tốt | +1 process | >1 worker |

## Thiết kế đề xuất

`items`/`shots` là đơn vị công việc; `events` audit; `idempotency` chống trùng.

**Endpoint (additive, dưới `/api/v1`):**

- `GET /agent/inbox?wait=0..25` — unread, claim của tôi, review chờ tôi, `next_actions`; `wait=0` mặc định ⇒ agent cũ không đổi.
- `GET /chat/threads/{id}/messages?since_id=&wait=` (shape giữ nguyên) + `POST .../ack` `{seq, state}`.
- `POST /agent/heartbeat`, `GET /agent/presence`.
- `POST /work/claims` `{shot_id|item_id, ttl_sec}` → 201/409 kèm holder; `.../{id}/renew`, `DELETE .../{id}`.
- `POST /shots/{shot_id}/reviews` `{verdict, blocking, evidence}`, `POST /reviews/{id}/resolve` (admin). Gate: `confirm-assemble` từ chối khi còn review `blocking=1`.

**Bảng mới (additive, không FK):**

| Bảng | Cột | Khóa/index |
|---|---|---|
| `chat_read_state` | thread_id, agent, last_read_seq, ack_seq, ack_state | PK(thread_id, agent); idx(agent, updated_at) |
| `agent_presence` | agent, status, last_seen_at, ttl_sec | PK(agent) |
| `work_claims` | id, item_id, shot_id, holder, status, lease_until | PK(id); **UNIQUE(shot_id) WHERE status='active'**; idx(lease_until) |
| `shot_reviews` | id, shot_id, reviewer, round, verdict, blocking, evidence_json, status | PK(id); UNIQUE(shot_id, reviewer, round); idx(shot_id, status) |
| `agent_usage` (P4) | agent, day, item_id, calls, tokens | PK(agent, day, item_id) |

Thêm `chat_messages.body_hash`, `.auto`; `chat_threads.last_body/last_sender`; `idx_events_type_at(type, at)`.

**Idempotency:** POST nhận `Idempotency-Key`, prefix `{sender}:{key}`, TTL 24h. Claim dùng unique partial index: 2 agent tranh 1 shot → 1 thắng, thua 409 kèm `holder`.

**Cursor/ack:** `since_id` giữ nguyên; ack chỉ tăng (`max()`); unread tính server-side.

**Presence/lease:** upsert 1 dòng, throttle **1 write/20s/agent**; `alive = last_seen_at+ttl > now`. Lease TTL 15 phút, renew giữa bước, hết hạn steal bằng `UPDATE ... WHERE lease_until < ?`. Fence đúng vẫn là CAS `shots.status` (`service_shots.py:74`).

**Review/escalation:** `fail`+`blocking` không tự đổi `shots.status`; chặn gate ráp + ghi event. Scheduler 60s (pattern `service_chat.py:221`) quét claim hết hạn, tin chưa trả lời >30 phút, blocking >60 phút, vượt budget → Telegram.

## 4 cái bẫy prod

1. **GZip + nginx với stream.** `GZipMiddleware` phủ mọi path (`main.py:74`), Starlette buffer 1000 byte mới nén → event đầu bị giữ, SSE vỡ. Nếu chọn SSE: subclass skip `/api/v1/stream` hoặc bỏ middleware bật `gzip on` nginx; nginx `proxy_buffering off; proxy_cache off; proxy_read_timeout 3600s; gzip off;` + `X-Accel-Buffering: no`. Long-poll chỉ cần timeout ≥ 60s.
2. **Long-poll sync đậu threadpool.** Route `def` giữ 1 trong ~40 token anyio; upload video cũng giành threadpool → starvation (latency 30s+, không lỗi). Dùng `async def` + `asyncio.sleep(0.4)`, `run_in_threadpool` chỉ cho query ngắn; `wait ≤ 25`; semaphore 20 waiter → 503 + `Retry-After`.
3. **SQLite single-writer.** WAL đọc song song, ghi tuần tự. Heartbeat 5s × N agent = write amplification → throttle 20s. Không giữ `BEGIN IMMEDIATE` qua LLM/IO (`_event` hiện ngoài tx). `busy_timeout` 5s cho write tương tác (`store.py:522`) để trả 503 thay vì treo 30s; `wal_checkpoint(TRUNCATE)` hàng ngày.
4. **One-shot không giữ connection.** `wait=0` mặc định, backlog trả ngay; cursor **lưu server** theo identity; lease TTL > runtime; claim/review bắt buộc `Idempotency-Key`.

## Chống loop & trần chi phí

- **Max auto turns:** 12 tin `auto=1` liên tiếp → `paused` (423); chỉ owner resume. Thay cap 30.
- **Cooldown:** 20s giữa 2 tin auto cùng thread → 429 `retry_after`.
- **Lặp nội dung:** `body_hash` (lowercase, bỏ dấu câu); trùng trong 5 tin gần nhất → 409.
- **Budget/item/ngày:** `agent_usage` cộng qua review/plan; vượt → 403 `budget_exceeded` + báo owner.
- **Trần nội dung:** body 2000 ký tự; `evidence_json` 4KB.

## Ship theo pha + rollback + test

| Pha | Nội dung | Effort | Rollback |
|---|---|---|---|
| P0 | Idempotency scoping + chat POST; `body_hash`; cap 30→200, rate limit/thread; `last_body` denormalize | **S** | env `VIDEO_AGENT_PROTOCOL=0` |
| P1 | `chat_read_state` + ack + `/agent/inbox?wait=0`; presence | **S** | drop bảng |
| P2 | `work_claims` + unique index + reaper; `shot_reviews` + gate + escalation | **M** | tắt scheduler, gate sau flag |
| P3 | `wait` long-poll async + semaphore + load test | **S** | bỏ param `wait` |
| P4 | Budget/usage + tinh chỉnh chống lặp | **S** | drop `agent_usage` |

Rollback: chỉ thêm bảng/endpoint/field; agent cũ vẫn chạy; drop bảng an toàn.

**Test thêm:** (1) trùng key → 1 tin; 2 agent cùng key → 2 tin; (2) claim 1 shot song song → 1 winner; (3) lease hết hạn bị steal; (4) ack lùi seq không rewind; (5) long-poll <1s, cắt đúng `wait`; (6) blocking review chặn `confirm-assemble`; (7) auto turn 13 → 423; (8) body lặp → 409; (9) **load nhỏ**: 4 agent × 1 msg/2s/60s + 20 long-poll, p95 < 300ms, WAL < 20MB.

## Câu hỏi cho PO/BA

1. Review `blocking` chặn **ráp video** hay **mọi hoạt động** trên item tới khi owner gỡ?
2. Thread là **hội thoại ngắn** (30 tin) hay **phòng theo job** nhiều ngày? Cap 200 + `paused` ổn không?
3. 2 agent bất đồng kịch bản: ai thắng mặc định (người tạo / reviewer / owner)? SLA escalate Telegram bao lâu?
4. Trần chi phí theo **item**, **thread**, hay **ngày/agent**? Con số cụ thể để tôi đặt env?
5. Có cần **3+ agent cùng thread** trong 6 tháng? Nếu có, dùng `chat_participants` từ P1 thay vì 2 cột.
