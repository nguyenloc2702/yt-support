# Memo Senior Dev — Round 2 (phản hồi PO)

## Trả lời 5 câu hỏi

**Q1 — Long-poll 25s và threadpool.** Anyio mặc định **40 token**; route `def` giữ 1 token suốt 25s, mà upload video cũng giành threadpool ⇒ chỉ ~10–12 long-poll sync đồng thời trước khi p95 tăng. Vì vậy long-poll phải `async def`: 1 coroutine ~4KB, **0 token**, 0.4s/lần read (2.5 q/s/agent). Semaphore **20 waiter** → 503 + `Retry-After`. One-shot giữ 15s (client timeout 35s), UI 25s. Đo: `asyncio.all_tasks()` + histogram middleware; cảnh báo p95 `/health` >300ms hoặc idle token <8.

**Q2 — Trần write SQLite.** WAL + `synchronous=NORMAL`, row nhỏ trên SSD: **300–800 commit/s** bền, p99 <5ms, nhưng **1 writer**. Tải thật: heartbeat 1/60s/agent + claim/ack/review ~1–2 w/s + UI ~0.1 w/s ⇒ **<5 w/s**, cách trần 2 bậc. Ngưỡng lên Postgres: p95 write >50ms, lock-wait p95 >200ms, >50 w/s bền, DB >5GB, hoặc cần process thứ 2. Đo: timer quanh `store.execute`, đếm `database is locked`, expose `db_write_p95_ms`/`wal_size_mb` ở `/api/v1/health`, log `wal_checkpoint`.

**Q3 — `cas_update` có đủ?** Không đủ cho **claim**, đủ cho **renew/steal**. Claim cần `INSERT` trong `BEGIN IMMEDIATE` + `UNIQUE(scope_type, scope_id) WHERE status='active'`: 2 agent cùng shot → 1×201, 1×409, không lost-update (WAL serialize writer). `UPDATE ... WHERE holder IS NULL` dính ABA khi lease bị steal. Renew/steal CAS guard `holder=? AND lease_until=?`, sai holder → 409.

**Q4 — Hash/version đã có.** `scripts.hash` + `scripts.shots_hash` (`store.py:41-46`), `assets.sha256`, `shots.keyframe_hash`, `todo_videos.sha256`, `items.cumulative_manifest_hash`. Review lưu `artifact_hash` (không chỉ version); `confirm-assemble` so hash hiện tại: lệch → 409 `stale_review` (assembler đã đọc đủ bảng). Owner override ghi vào `approvals` (`store.py:102-105`, gate=`review_override`, version, decision, approver, reason) + event ⇒ audit không vỡ.

**Q5 — Retry.** Chat POST hiện **tạo bản trùng** (`main.py:1816-1825` không gọi `_idempotent`). Upload an toàn nhờ `UNIQUE(item_id,kind,seq_num)`/`UNIQUE(item_id,seq_num)` (`store.py:77-97`) — retry cùng seq bị 409, nhưng agent tự chọn seq. Idempotency-Key **client gửi**, server prefix `{sender}:{key}` (vì `idempotency.key` là PK toàn cục, `store.py:118`), TTL 24h. `since_id` chỉ **at-least-once**: crash sau xử lý, trước khi lưu cursor ⇒ xử lý lại; consumer idempotent theo `seq` (ack `max()`).

## 3 must của PO

**M1 claim/lease — ĐỒNG Ý CÓ ĐIỀU KIỆN.** (a) Unique index trên **active** claim, không trên lịch sử: gen lại sau QC fail là claim mới, nên "0 shot bị 2 agent gen" đo **khoảng active chồng lấn**. (b) Lease advisory; fence tiền vẫn là CAS `shots.status` (`service_shots.py:74`). (c) Item claim tuỳ chọn, shot claim độc lập, không bắt buộc cha–con. (d) Takeover chỉ sau `lease_until + 60s`, bắt buộc event `claim.takeover`. (e) **Hạ TTL 15→10 phút, heartbeat 5 phút→60s**: G3 "phát hiện ≤30 phút" làm mất cả cửa sổ 16:00–18:00; 10 phút + 60s cho phát hiện ≤11 phút, write vẫn 0.03 w/s.

**M3 review gắn hash + blocking — ĐỒNG Ý CÓ ĐIỀU KIỆN.** Bắt buộc `artifact_hash` + `round`; verdict stale khi hash đổi (không xoá, đánh `status='stale'`). `blocking` chỉ chặn **confirm-assemble**, không đổi `shots.status`. Override qua `approvals` + event, không được "im lặng bỏ qua".

**M4 reminder/escalation — ĐỒNG Ý CÓ ĐIỀU KIỆN.** Tick 60s không bảo đảm "≤60s" khi blocked 15 phút (worst case 16 phút). Dùng tick **20s** (query indexed ~1ms) hoặc trigger ngay khi ghi `review.blocking`. Dedupe theo `(task, state)`, im lặng 30 phút; trần 1 Telegram/10s; ngoài 16:00–18:00 gộp digest.

## Chỉnh thiết kế + MVP

**Thêm:** `work_claims.scope_type/scope_id`; `shot_reviews.artifact_hash/round/status`; `chat_read_state`; `chat_messages.body_hash/auto`; tái dùng `approvals`.
**Bỏ khỏi MVP:** long-poll (G1 đã đạt bằng poll 10–15s), `agent_usage` (để P4; MVP dùng max turns + cooldown).
**Đổi thứ tự:** claim (P1) trước review (P2) — S1/S2 đốt tiền ngay.

**MVP prod — 4 bảng:** `work_claims`, `shot_reviews`, `chat_read_state`, `agent_presence`; **11 endpoint:** `POST/GET/DELETE /work/claims`, `POST /work/claims/{id}/renew`, `GET /agent/inbox`, `POST /chat/threads/{id}/ack`, `POST/GET /shots/{id}/reviews`, `POST /reviews/{id}/resolve`, `POST /agent/heartbeat`, `GET /agent/presence`.

## Bảng pha final

| Pha | Nội dung | Effort | Rollback | Test bắt buộc |
|---|---|---|---|---|
| P0 | Idempotency scoping + chat POST; `body_hash/auto`; cap 30→200, rate limit/thread; `read_state`+ack+inbox | **S** | env `VIDEO_AGENT_PROTOCOL=0` | trùng key → 1 tin; ack lùi không rewind; unread đúng |
| P1 | `work_claims` + presence/heartbeat 60s + takeover + event | **M** | tắt 4 endpoint claim | 20 claim song song → 1 winner; takeover sau TTL+60s; renew sai holder → 409 |
| P2 | `shot_reviews` + `artifact_hash` + gate + `approvals` override | **M** | gate sau flag | hash lệch → 409; override có record; 100% publish có review |
| P3 | Escalation tick 20s + Telegram dedupe | **S** | tắt scheduler | blocked 15 phút → đúng 1 tin; 30 phút không lặp |
| P4 | Budget `agent_usage` + tinh chỉnh chống lặp | **S** | drop bảng | vượt trần → 403; 6 lượt/giờ → pause |
| P5 | Long-poll async + semaphore 20 | **S** | bỏ `wait` | 20 waiter, p95 `/health` <300ms |

## 3 rủi ro lớn nhất

1. **Starve threadpool/event-loop khi long-poll + upload trùng thời điểm** (P5). Phát hiện: p95 `/health` >300ms, idle token <8, tỉ lệ 503 semaphore >1% trong 5 phút.
2. **Write lock SQLite do heartbeat/claim storm.** Phát hiện: p95 write >50ms, log `database is locked`, `wal_size_mb` >50.
3. **Lease mồ côi / gen trùng âm thầm.** Phát hiện: đếm active claim có `shots.updated_at` cũ hơn TTL, số event `claim.takeover`/ngày, và `covered_shot_indexes` chồng lấn trong `todo_videos`.

## 2 điểm cần Architect quyết

1. **SLA phát hiện agent chết:** chấp nhận 10 phút TTL/60s heartbeat (phát hiện ≤11 phút, write không đáng kể) hay siết <5 phút (TTL 4 phút, agent phải renew mỗi bước, thêm complexity)?
2. **Mô hình phạm vi:** item claim **bắt buộc** hay tuỳ chọn, và có mở room 3+ agent ngay (đổi sang `chat_participants`) hay giữ 1-1 tới hết quý?
