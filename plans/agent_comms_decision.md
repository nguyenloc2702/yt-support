# Quyết định kiến trúc — Kênh giao tiếp & phối hợp giữa các agent (production)

**Ngày:** 2026-11-23 · **Trạng thái:** APPROVED (Architect + PO/BA + Senior Dev + Owner)
**Version:** 1.1 — addendum "lớp liên lạc Muse" (hierarchy + @mention + wake), xem §14.
Bản 1.1 kế thừa toàn bộ §0–§13; chỉ sửa 3 điều khoản bị xung đột (đánh dấu **[v1.1]**).
**Phạm vi:** `video-system` trên `https://vidfac.gacdero.vn` · **Không phá Agent API v3**

---

## 0. Chốt một câu

> **Chat là lớp hội thoại, không phải lớp điều phối.** Giữ REST pull-first trên SQLite
> WAL, thêm **inbox cursor/ack lưu server + work protocol có cấu trúc** (claim,
> review gắn hash, presence suy ra từ API call, escalation Telegram).
> **Không** SSE/WebSocket/broker ở giai đoạn này. Agent Muse được đối xử như client
> ngoài, không tin cậy, có thể one-shot và có thể im lặng rất lâu.

---

## 1. Ràng buộc đã đổi so với giả định ban đầu

| Giả định cũ | Thực tế (owner xác nhận + đo prod) | Hệ quả thiết kế |
|---|---|---|
| Agent chạy được heartbeat nền 30s | **Muse AI** — runtime ngoài, prompt-driven, không cắm code | Không có heartbeat chủ động; presence suy ra từ **mọi API call có auth** |
| Gen 1 shot ~2–3 phút | p99 442s / 1.033s / 3.532s tuỳ item (117 shot/item) | **Không auto-steal**; lease chỉ là tín hiệu mềm |
| Agent tự sửa script của nhau | Owner chốt: **reviewer chỉ đề xuất, author sửa** | Review là record advisory + blocking, không phải quyền edit |
| Takeover tự động khi agent chết | Owner chốt: **suspect + owner duyệt** | Không bao giờ gen trùng; chậm nhưng an toàn tiền GPU |

---

## 2. Kiến trúc 4 lớp

```
┌───────────────────────────── Agent Muse A / B (ngoài, one-shot) ─────────────────────────────┐
│  poll inbox 10–15s → claim → làm việc → upload → review → ack                                │
└───────────────────────────────────────┬─────────────────────────────────────────────────────┘
                                        │ HTTPS REST + Bearer key riêng (identity = tên key)
┌───────────────────────────────────────▼─────────────────────────────────────────────────────┐
│ FastAPI (1 process, 127.0.0.1:8090 sau nginx)                                               │
│                                                                                             │
│  L1 Hội thoại      chat_threads / chat_messages / chat_participants / chat_read_state       │
│  L2 Công việc      work_claims (advisory lease) · shot_reviews (hash-bound)                 │
│  L3 Nhịp & cảnh báo agent_presence (last_seen từ auth) · scheduler 30s · Telegram           │
│  L4 Audit           events (append-only) · approvals (owner override)                        │
│                                                                                             │
│  Nguồn sự thật duy nhất: SQLite WAL — /opt/video-system/data/video_system.sqlite3           │
└───────────────────────────────────────┬─────────────────────────────────────────────────────┘
                                        │
                          Owner: SPA #/chat + Telegram + PIN session
```

**Nguyên tắc:** mọi trạng thái phối hợp nằm trong bảng có khóa & index; chat chỉ là
nơi giải thích lý do. Không có quyết định nghiệp vụ nào chỉ tồn tại trong tin nhắn.

---

## 3. Vì sao không SSE / WebSocket / broker

| Tiêu chí | Poll 10–15s (chọn) | SSE | WS | Redis/NATS |
|---|---|---|---|---|
| Agent Muse one-shot | ✅ chạy xong thoát | ❌ mất stream | ❌ mất stream | ⚠️ cần consumer |
| Hạ tầng hiện tại | ✅ 0 thay đổi | ❌ GZip + nginx buffer | ❌ upgrade/reconnect | ❌ +1 service trên 1 VPS |
| Độ trễ cần cho nghiệp vụ | 10–15s đủ (SLA 30s) | <1s (không cần) | <1s (không cần) | ~ms (không cần) |
| Rủi ro prod | Thấp | Cao | Cao | Trung bình |
| Chi phí vận hành | 0 | Thấp | TB | Cao |

**Điều kiện xem lại:** >20 client đồng thời, hoặc cần đẩy cảnh báo <2s cho UI.
Khi đó thêm **SSE một chiều** (`GET /api/v1/stream?since_seq=`), route được miễn GZip
và nginx `proxy_buffering off; X-Accel-Buffering: no`. Không dùng WS.

---

## 4. Data model (additive, không FK cứng, migrate idempotent)

### 4.1 Bảng mới

| Bảng | Cột chính | Khóa / index | Ghi chú |
|---|---|---|---|
| `chat_participants` | thread_id, agent, role(owner/agent), joined_at | PK(thread_id, agent) | Room theo `item_id`; migrate 2 thread cũ từ `created_by`/`participant_b` |
| `chat_read_state` | thread_id, agent, last_read_seq, ack_seq, ack_state | PK(thread_id, agent) | Cursor **lưu server** → one-shot resume được |
| `work_claims` | id, scope_type(item/shot), scope_id, holder, status, lease_until, suspect_at, released_reason | PK(id); **UNIQUE(scope_type, scope_id) WHERE status='active'**; idx(lease_until) | Claim nguyên tử bằng `INSERT` trong `BEGIN IMMEDIATE` |
| `shot_reviews` | id, shot_id, artifact_type, artifact_hash, reviewer, round, verdict(pass/fail), blocking, evidence_json, status(open/passed/failed/stale/overridden) | PK(id); UNIQUE(shot_id, reviewer, round); idx(shot_id, status) | Gắn **hash**, không gắn version number |
| `agent_presence` | agent, last_seen_at, last_endpoint, last_item_id | PK(agent) | Ghi **throttle 1 lần/60s/agent** từ middleware auth; không cần endpoint heartbeat riêng |
| `agent_usage` | agent, day_vn, item_id, auto_msgs, tokens, critic_rounds | PK(agent, day_vn, item_id) | Đếm theo **ngày VN**, không UTC |

### 4.2 Thay đổi bảng cũ (an toàn)

- `chat_messages`: `+ body_hash`, `+ auto INTEGER DEFAULT 0`, `+ kind(text/review/system)`.
- `chat_threads`: `+ item_id` (đã có), `+ last_body`, `+ last_sender`, `+ paused_reason`.
- `events`: `+ idx_events_type_at(type, at)`.
- `shots`: dùng nguyên `held_reason` / `held_detail`; **thêm value** `REVIEW_BLOCKED`
  vào `HeldReason` + map group `held_review` (bắt buộc, nếu không `retry_held` sẽ
  cho agent retry xuyên qua block — `service_shots.py:215`).

---

## 5. API contract (additive dưới `/api/v1`)

### 5.1 Inbox & chat

```http
GET  /agent/inbox?wait=0
→ { unread_threads, my_claims, reviews_waiting, blocked_shots, next_actions[], server_time }
```
- `wait=0` mặc định → trả ngay, không giữ connection (an toàn cho Muse one-shot).
- `wait=1..25` chỉ bật ở P5, route phải `async def` + semaphore 20.
- SLA pickup **[v1.1]**: agent đang trong phiên thấy việc mới ≤15s p95; agent one-shot
  theo lịch ≤**3 phút trong cửa sổ 16:00–18:00**, ≤10 phút ngoài cửa sổ (mốc ≤60s cũ
  bị bỏ vì bất khả thi với Muse — xem §14.6). Poll khuyến nghị 10–15s (không dồn).

```http
GET  /chat/threads/{id}/messages?since_id=<seq>   # shape giữ nguyên
POST /chat/threads/{id}/ack   {seq, state: seen|working|done}
POST /chat/threads/{id}/messages  {body}          # + header Idempotency-Key
```
- Ack chỉ tăng (`max()`), không rewind. Unread tính server-side.
- `paused` chỉ nhận tin của owner; agent gửi → 423 kèm `paused_reason`.

### 5.2 Claim (advisory lease)

```http
POST   /work/claims        {scope_type, scope_id, ttl_sec?}   → 201 {claim_id, lease_until}
POST   /work/claims/{id}/extend {ttl_sec}                     → 200
POST   /work/claims/{id}/suspect                              → 200 (scheduler tự gọi)
DELETE /work/claims/{id}   {reason: done|failed|released}
POST   /work/claims/{id}/takeover  {approved_by:"owner"}       → 200 (chỉ owner)
```
- Hai agent tranh 1 shot: 1×201, 1×409 kèm `holder` — unique partial index bảo đảm.
- Hết lease → `suspect` (không tự nhả). Owner duyệt takeover → claim cũ `taken_over`,
  ghi `events`, claim mới `active`. **Không auto-steal.**
- TTL mặc định **1200s (20 phút)**, `VIDEO_CLAIM_TTL_SEC` override.
- Claim cấp item là **advisory**; chỉ chặn claim shot khi khai báo
  `exclusive=true` (1 agent nhận trọn job). Claim cấp shot luôn bắt buộc.

### 5.3 Review

```http
POST /shots/{shot_id}/reviews {verdict, blocking, evidence_json, artifact_hash}
POST /reviews/{id}/resolve    {action: accept|override}   # owner/admin
GET  /items/{item_id}/reviews?status=open
```
- `blocking=true` + `fail` → shot `status=held`, `held_reason=REVIEW_BLOCKED`,
  item `HELD_REVIEW`. Chặn claim/upload shot đó + chặn `confirm-assemble`.
- **[v1.1] Block có hiệu lực ngay (fail-safe).** Không có bước "quản lý xác nhận
  trước". Manager chỉ được **gỡ block trong 10 phút** kể từ lúc block (ghi
  `approvals` + event); quá 10 phút chỉ Owner gỡ. Owner nhận Telegram ngay khi block.
- `artifact_hash` lệch hash hiện tại → 409 `stale_review`; verdict cũ → `stale`.
- Reviewer **không có** endpoint sửa script. Muốn sửa → `POST /scripts` tạo version
  mới với `author=<reviewer>`? **Không** — owner chốt: author sửa. Reviewer chỉ
  `evidence_json` + đề xuất.

### 5.4 Presence

Không endpoint riêng. Middleware auth ghi `agent_presence.last_seen_at`
(throttle 60s). `alive = last_seen < 20 phút`. UI hiện chấm xanh/xám.

---

## 6. State machine

**Claim:** `active → suspect → (takeover | released) → completed`
**Review:** `open → passed | failed → (fixed → open vòng mới) | stale | overridden`
**Thread:** `open → paused (cap/loop) → open (owner resume) → locked (200 tin) → closed`
**Shot (bổ sung):** `* → held(REVIEW_BLOCKED) → author fix → re-review → *`

Mọi transition ghi `events` với `actor` = tên key, `shot_id`, `artifact_hash`.

---

## 7. Guardrails chống loop & đốt tiền

| Cơ chế | Ngưỡng mặc định | Hành vi |
|---|---|---|
| Auto turn liên tiếp | 12 (1-1) / 20 (job room) **[v1.1]** | thread `paused`, Telegram owner |
| Cooldown 2 tin auto | 20s | 429 `retry_after` |
| Trùng nội dung | `body_hash` xuất hiện trong 5 tin gần nhất | 409 `duplicate_body` |
| Tin/thread | 200 | `locked` |
| Auto msg/item/ngày | `max(40, 3 × total_shots)`, trần 600 | `paused` |
| Critic rounds/item | 2 | dừng, escalate owner |
| Body / evidence | 2000 ký tự / 4KB | 422 |
| Budget token | env `VIDEO_AGENT_TOKEN_BUDGET` | 403 `budget_exceeded` + Telegram |

Tất cả đọc từ env tại runtime — đổi không cần restart.

---

## 8. Escalation (scheduler 30s, không phải 60s)

| Tình huống | Ngưỡng | Hành động |
|---|---|---|
| Claim `suspect` | ngay khi hết TTL | Telegram owner + badge UI |
| Review `blocking` chưa xử lý | 15 phút | Telegram |
| Thread `paused` | ngay | Telegram + nút Resume trên UI |
| Tin chưa trả lời | 30 phút | Telegram nhắc agent còn lại |
| Item chưa claim trong cửa sổ 16:00–18:00 | 10 phút | Telegram owner |
| Dedupe cảnh báo | key `(entity, state)`, im 30 phút | tránh spam; trần 1 Telegram/10s |

---

## 9. Ship theo pha

| Pha | Nội dung | Effort | Rollback |
|---|---|---|---|
| **P0** | Idempotency scoping cho chat POST; `body_hash`; cap 30→200; `chat_read_state` + ack; `/agent/inbox` (wait=0); presence từ auth | S | env `VIDEO_AGENT_PROTOCOL=0` |
| **P1** | `chat_participants` (owner + 2 agent, room theo item); migrate 2 thread cũ; UI badge/paused/resume | S | giữ cột cũ, đọc dual |
| **P2** | `work_claims` + unique index + suspect + owner-approved takeover + UI cảnh báo | M | tắt route, bảng để nguyên |
| **P3** | `shot_reviews` + `REVIEW_BLOCKED` + gate `confirm-assemble` + owner override qua `approvals` | M | gate sau env flag |
| **P4** | Escalation scheduler + `agent_usage` + budget + loop guard | S | tắt scheduler |
| **P5** (tuỳ chọn) | Long-poll `wait≤25` async + semaphore 20 + load test 4 agent/20 waiter | S | bỏ param `wait` |

**Không làm:** SSE/WS/broker, auto-steal, Postgres, multi-worker. Xem lại khi
>20 client, >3 worker, hoặc p99 write >50ms.

---

## 10. Test bắt buộc trước khi bật cho agent thật

1. Retry cùng `Idempotency-Key` → 1 tin; 2 agent cùng key → 2 tin.
2. 2 agent claim cùng shot song song → đúng 1 winner, 1 nhận 409 kèm holder.
3. Hết lease → `suspect`, **không** nhả claim; takeover không có owner → 403.
4. Ack lùi seq không rewind; unread server-side khớp UI.
5. `blocking` review → claim/upload shot đó 409, `confirm-assemble` 409; shot khác vẫn chạy.
6. Sửa script làm hash đổi → verdict cũ `stale`, không chặn nhầm bản mới.
7. Vượt auto-turn cap (12 ở 1-1 / 20 ở job room) → 423; trùng body → 409; vượt 200 tin → locked.
8. Agent im 21 phút → presence xám + Telegram, claim vẫn `suspect` (không mất việc).
9. Load nhỏ: 4 agent × 1 msg/2s trong 60s + 20 long-poll (P5) → p95 <300ms, WAL <20MB.
10. Agent cũ chỉ biết 7-step chạy nguyên workflow không đổi hành vi.

---

## 11. Rủi ro còn lại & cách phát hiện sớm

| Rủi ro | Phát hiện | Giảm thiểu |
|---|---|---|
| Agent sống nhưng bị `suspect` (im lâu do gen/retry) | đếm `claim.suspect` không có takeover | suspect không phá gì; chỉ owner mới takeover; nâng TTL nếu tỷ lệ >20%/ngày |
| Owner không on-call 16:00–18:00 → mất slot đăng | đếm `paused`/`held` chưa xử lý lúc 17:30 | Telegram + auto-digest cảnh báo; cân nhắc auto-resume 1 lần (chưa bật) |
| Review advisory bị bỏ qua | tỷ lệ `fail` không có version mới trong 30' | escalation + chặn assemble |
| SQLite write contention | p99 write, `busy` errors | `busy_timeout=5s`, heartbeat throttle, WAL checkpoint ngày |
| Nợ idempotency ở upload video (ngoài chat) | đếm video trùng `sha256`/shot | P0 mở rộng `_idempotent()` cho upload ở pha sau |

---

## 12. Quyết định của owner (đã chốt)

1. Agent là **Muse AI** → thiết kế không phụ thuộc heartbeat/stream phía agent.
2. Lease hết hạn → **suspect + owner duyệt takeover**, không auto-steal.
3. Reviewer **chỉ đề xuất**, author sửa; owner là trọng tài cuối.

## 13. Việc tiếp theo (đề xuất)

- P0 + P1 trong 1 PR nhỏ (idempotency, read_state, participants, inbox wait=0).
- Viết `read_me/06_agent_protocol_v4.md` cho Muse agent: inbox → claim → ack → review.
- Đo 3 ngày: `claim.suspect`, `paused`, p99 claim→upload; chỉnh TTL/caps bằng env.

---

## 14. Addendum v1.1 — Lớp liên lạc Muse (hierarchy + @mention + wake)

**Nguồn:** `muse-vidfac-contact-architecture.md` (Owner, 2026-11-23).
**Review:** PO/BA + Senior Dev độc lập, 2 vòng phản biện.
**Verdict:** cả 3 điểm mới **ACCEPT WITH CHANGES**; 3 xung đột với v1.0 đã xử lý (§14.1).

### 14.1 Ba xung đột đã xử lý

| Xung đột | Đề xuất Owner | Quyết định v1.1 | Lý do |
|---|---|---|---|
| Blocking review | Manager xác nhận trước, block mới có hiệu lực | **Block tức thì**; manager chỉ gỡ trong 10' | Tránh fail-open: manager ngủ ⇒ GPU vẫn cháy trên script fail |
| Cursor | Lưu state file phía Muse | **Server-side `chat_read_state` là nguồn sự thật**; state file chỉ cache | Restart-safe, không wake trùng, không mất mention |
| SLA one-shot | Cron L2 10 phút | **3 phút trong cửa sổ 16:00–18:00**, 10 phút ngoài | 10 phút = 25% cửa sổ đăng bài; ≤60s cũ bất khả thi |

### 14.2 Phân cấp — ngoại lệ, không phải đường đi mặc định

- **Worker tự làm** mọi việc trong "luật chơi" (standing instructions) do manager
  ban hành. Chỉ hỏi manager khi: ngoài luật / việc mới / tốn tiền / bất đồng.
- Luật chơi phải phủ **≥95%** tình huống thường. Trần **5 câu hỏi/item/ngày**
  (`agent_usage.questions`); câu thứ 6 bị chặn ở service (422) **trước khi ghi tin**
  để không tốn lượt — rồi escalate Owner, không pause item.
- Manager là **router + advisor**, không phải cổng an toàn: không duyệt trước block,
  không thay Owner ở publish/`confirm-assemble`/takeover/override/budget.
- Mọi lời khuyên của manager phải thành record (`claim`/`review`/`approval`/`event`);
  chat không phải quyết định.
- **Auto-turn cap [v1.1]:** job room 20 lượt auto liên tiếp, 1-1 giữ 12; vượt → `paused`.
  Lý do: hierarchy thêm vòng hỏi–đáp, cap 12 sẽ pause giữa job 60–117 shot.

### 14.3 `agent_registry` + rollout mềm

| Cột | Ý nghĩa |
|---|---|
| `agent` PK | = tên key (identity hiện tại) |
| `role` | `owner` / `manager` / `worker` |
| `manager` | worker → `muse-manager`; manager → `owner` |
| `display_name`, `status`, `joined_at`, `note` | UI @tag, `active/suspended/retired`, audit |

- **Seed trước** toàn bộ key đang sống: `owner`, `muse-manager`, `ku-li-agent-1/2`,
  `sk-video-agent-prod`. Sau đó **shadow 7 ngày**: mọi miss ghi `events` type
  `registry.miss` (queryable, không chỉ log file).
- Chỉ enforce khi 7 ngày có **0 `registry.miss`**, và chỉ trên **claim / review /
  mention** — tuyệt đối không enforce trên 7-step, todos, upload.
- Hai env flag riêng: `VIDEO_REGISTRY_ENFORCE_CLAIM`, `VIDEO_REGISTRY_ENFORCE_REVIEW`.
- Manager ACL: chỉ đọc thread có `item_id` (job room); **không** auto-join mọi thread;
  không thêm auth scope mới — check ở service (`item_id IS NOT NULL` + role).
- Key `muse-manager` là **role ổn định**, xoay key bằng cách đổi value trong
  `VIDEO_API_KEYS`; registry giữ nguyên row. Không đổi tên ⇒ @tag/audit không gãy.

### 14.4 @mention có cấu trúc

- UI gửi `mentions: []` tường minh (≤10, unique). Server validate theo registry
  `active` + ACL thread → 422 `unknown_mention` / `mention_not_participant`.
- Lưu **bảng `chat_mentions`** (queryable) + `chat_messages.mentions_json` (chỉ render).
- Agent text-only: fallback regex `(?<![\w@/])@([a-z0-9][a-z0-9-]{1,31})\b`,
  bỏ fenced/inline code, chỉ nhận token khớp registry (không alias).
- Worker tag Owner phải kèm `escalation_id` trỏ tới `escalations` row **đang mở**,
  cùng `item_id`; tối đa **1 lần/giờ/(sender,item)**; thiếu/đóng → 422.
- Mention **không phải lệnh làm việc**, không nhân đôi lượt (1 tin tag nhiều người
  vẫn 1 lượt của sender).

### 14.5 Wake 3 lớp (không đốt token)

| Lớp | Cơ chế | Chi phí | Ghi chú |
|---|---|---|---|
| 0 | Poll inbox 10–15s khi đang trong phiên | 0 token thêm | SLA ≤15s p95 |
| 1 | Hook script ~60s gọi `GET /agent/inbox?light=1` | 0 token | Silent nếu không có gì; wake 1 lần/mention |
| 2 | Cron dự phòng 3' (trong cửa sổ) / 10' (ngoài) | Trung bình | Chỉ bật khi job active |
| 3 | Webhook VPS + Telegram (tuỳ chọn) | Thấp | Chỉ làm khi có số liệu cần <2s |

**`?light=1` bắt buộc:**
- Payload <1KB: `{cursor, unread, mentions_count, next_actions_count, server_time}`.
- **Không cập nhật `agent_presence`** — nếu không, hook 60s làm mọi agent "sống" 24/7
  và `suspect` vô nghĩa.
- Auth = bearer key của chính agent; rate limit **2 req/s/key** (burst 10); hook coi
  429 là im lặng, không retry dồn.
- Cursor server-side (`chat_read_state`); state file phía Muse chỉ cache.
- Dedupe bằng `agent_wake_state(agent, watermark_seq, last_wake_at, attempts)`;
  `attempts` reset khi ack; **2 lần thử thất bại → escalate**, không mất mention.
  Khoá dedupe `(agent, watermark_seq)` để 2 hook chạy song song không wake đôi.
- Manager vắng >20 phút hoặc `status != active` → worker được escalate Owner ngay,
  không phải chờ 30 phút.

### 14.6 SLA liên lạc (thay A1 cũ)

| Tình huống | Trong 16:00–18:00 | Ngoài cửa sổ |
|---|---|---|
| Agent đang phiên (L0) | ≤15s p95 | ≤15s p95 |
| Agent ngủ, có @tag (L1) | wake ≤2 phút | ≤5 phút |
| One-shot theo lịch (L2) | **≤3 phút** | ≤10 phút |
| Manager ack câu hỏi | ≤5 phút | ≤30 phút |
| Escalate Owner nếu không ack | ≤10 phút | ≤30 phút |
| Mention không ack | 30 phút → Telegram (giữ nguyên) | 30 phút |

Đo từ **"work claimable" → API call đầu tiên**, không đo từ lúc wake.

### 14.7 Bàn giao manager

- `manager_handovers(id, from_agent, to_agent, snapshot_json, sha256, acked_at,
  approved_by, created_at)`.
- Snapshot **7 mục**: claims mở, reviews mở, câu hỏi chưa trả lời, thread `paused`,
  cursor, approval chờ, budget còn lại — build trong **1 transaction** + hash.
- Manager cũ phải ack; manager mới ack + seed `chat_read_state` trong cùng request.
- Quá 15 phút không ack → Owner được **force handover** (ghi override).
- Owner duyệt là gate cuối; mất việc do bàn giao sót = lỗi Owner (đã ghi nhận).

### 14.8 Schema bổ sung (additive)

```sql
agent_registry(agent PK, role, manager, display_name, status, joined_at, note)
  idx(role,status), idx(manager)
chat_mentions(agent, thread_id, seq, acked_at)  PK(agent,seq), idx(agent,acked_at)
chat_messages.mentions_json
escalations(id, item_id, opened_by, status, opened_at)  idx(item_id,status)
agent_wake_state(agent PK, watermark_seq, last_wake_at, attempts)
manager_handovers(id, from_agent, to_agent, snapshot_json, sha256,
                  acked_at, approved_by, created_at)  idx(to_agent,created_at)
agent_usage.questions
```

### 14.9 Pha triển khai (nối vào §9)

| Bước | Nội dung | Pha |
|---|---|---|
| 1 | `agent_registry` + seed + shadow + cấp key `muse-manager` | P1 |
| 2 | Mention: UI gợi ý, `mentions[]`, `chat_mentions`, inbox `mentions[]` + `?light=1` | P1 |
| 3 | `agent_wake_state` + tài liệu hook + `read_me/07_muse_contact_protocol.md` | P2 |
| 4 | Cron L2 (3'/10') + `manager_handovers` | P2 |
| 5 | Escalation mention quá hạn + `escalations` + `agent_usage.questions` | P4 |
| 6 | Webhook VPS (tuỳ chọn) | P5 |

### 14.10 Nghiệm thu bổ sung

1. `@` trong UI gợi ý đúng registry `active`; tag sai → 422, không ghi tin.
2. Mention `@muse-manager` chỉ vào inbox manager, không lọt worker khác.
3. Hook: 10 vòng không có gì → **0 wake**; 1 mention → wake **đúng 1 lần**; restart
   giữa chừng không wake lại; 2 hook song song không wake đôi.
4. `?light=1` **không** làm `last_seen` tươi; agent chỉ chạy hook vẫn bị `suspect`
   sau 20 phút.
5. Worker tag Owner không có `escalation_id` → 422; có escalation mở → hợp lệ.
6. Manager gỡ block trong 10' → có `approvals` + event; quá 10' → 403.
7. Registry shadow: `registry.miss = 0` trong 7 ngày mới được bật enforce; tắt flag
   là rollback tức thì.
8. 7-step của `sk-video-agent-prod` chạy nguyên vẹn khi registry enforce bật.

### 14.11 Chỉ số hiệu quả (đo sau 2 tuần)

| Chỉ số | Mục tiêu |
|---|---|
| Autonomy rate (việc không phải hỏi) | ≥90% |
| Câu hỏi/item | ≤5 |
| Manager ack p95 (trong cửa sổ) | ≤5 phút |
| Owner interruptions/ngày | ≤3 |
| Wake:mention ratio | <3:1 (cao = wake trùng) |
| `registry.miss` | 0 trước khi enforce |
| `claim.suspect` không takeover | <20%/ngày |

### 14.12 Trả lời 3 câu hỏi mở của Owner

1. **Tên key + bàn giao:** `muse-manager` là role ổn định; xoay key bằng value trong
   `VIDEO_API_KEYS`, registry không đổi. Bàn giao theo §14.7 (snapshot + hash + 2 ack
   + Owner duyệt).
2. **Auth `?light=1`:** dùng bearer key đầy đủ của chính agent, không tạo read-token
   riêng — đơn giản hơn, tránh thêm một loại secret phải quản lý. Bù lại: rate limit
   2 req/s/key + payload <1KB + không cập nhật presence.
3. **SLA ack:** **kép** — 5 phút trong cửa sổ 16:00–18:00, 30 phút ngoài; escalate
   Owner ở 10 phút / 30 phút tương ứng.
