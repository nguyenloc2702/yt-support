# Senior Dev — đánh giá `muse-vidfac-contact-architecture.md`

## Verdict

- **Phân cấp — ACCEPT WITH CHANGES.** `agent_registry` đúng, nhưng `role` là thuộc tính người dùng, **không** thay scope của key; enforce phải sau seed + shadow, chỉ ở claim/review/mention.
- **Mention — ACCEPT WITH CHANGES.** Cần bảng `chat_mentions` để query; `mentions_json` chỉ để render. Regex fallback phải whitelist theo registry, không parse mù.
- **Wake — ACCEPT WITH CHANGES.** Cursor phải server-side (`chat_read_state`), state file phía Muse chỉ là cache; thêm `agent_wake_state` cho dedupe/backoff.

## 6 câu

**1. Mention parsing.** Contract: client gửi `mentions:["muse-manager"]` (≤10, unique); server validate theo `agent_registry.status='active'` + quyền đọc thread, sai → **422 `unknown_mention`** (không 403 — đây là lỗi body). Lưu `chat_mentions(agent, thread_id, seq)` + `mentions_json`. Agent text-only: regex `(?<![\w@/])@([a-z0-9][a-z0-9-]{1,31})\b`, bỏ fenced code block và inline code, strip `.,;:!?`, **chỉ nhận token khớp registry** (không khớp = bỏ, không 422). Không alias: đổi tên = agent mới, tên cũ `retired` → không nhập nhằng. Chưa có endpoint sửa tin; nếu thêm phải re-validate + `message.edited`, giữ `seq`.

**2. Cursor.** Server-side mới đúng: `chat_read_state` là nguồn sự thật, state file chỉ cache restart. `?light=1` trả `{server_time, max_seq, unread_threads, mentions_count, next_actions_count}`; hook so `max_seq` local chỉ để khỏi gọi wake thừa, dedupe thật dựa `ack_seq`. "Watermark trước wake" = at-most-once → mất mention khi wake fail. Thay bằng `agent_wake_state(agent PK, watermark_seq, last_wake_at, attempts)`: ghi intent trước, `attempts` chỉ tăng, >2 lần chưa ack → escalation. **`?light=1` phải miễn cập nhật `agent_presence`**, nếu không hook 60s khiến mọi agent "alive" 24/7 và suspect vô nghĩa.

**3. ACL manager.** Không auto-add manager vào mọi thread (phá 403 hiện tại, lộ 1-1). Dùng role: manager đọc thread có `item_id` (room theo job), **không** đọc 1-1 không item. Post vào thread nào thì ghi `chat_participants(role='manager')` — sau đó ACL cũ áp nguyên, không cần nhánh read riêng. Không thêm scope `manager` vào auth. Test mới: manager đọc item-room 200, đọc 1-1 403, mention ngoài quyền 422.

**4. Backward compat.** (1) P1 tạo `agent_registry`, seed toàn bộ key hiện có: `owner`=owner, `muse-manager`=manager, `ku-li-agent-*` + `sk-video-agent-prod`=worker/active; (2) shadow 7 ngày, `VIDEO_REGISTRY_ENFORCE=0`, log `registry.miss` thay vì chặn; (3) bật enforce theo endpoint — claims trước, reviews sau — khi 7 ngày 0 miss; (4) **không bao giờ** enforce trên 7-step/todos/upload. Test: chạy full 7-step bằng `sk-video-agent-prod` khi enforce=1 → 200; key lạ claim 403, GET todos 200.

**5. Tải.** 5 agent = 0.083 req/s; 50 agent = 0.83 req/s — thấp hơn 1 tab UI đang poll (0.67 req/s). Mỗi request 1 aggregate trên `chat_read_state`+`chat_mentions` (index), ~1ms. Vẫn nên rate limit riêng: **2 req/s/key, burst 10** → 429 (hook lỗi vòng 1s thành 5× tải). Payload ≤1KB (dưới `minimum_size=1000` của GZip nên không nén): `server_time, max_seq, unread_threads, mentions_count, next_actions_count, paused[]` — không body.

**6. Schema/phase.** P1: `agent_registry(agent PK, role, manager, display_name, status, joined_at, note)` + idx(role,status), idx(manager); `chat_mentions(agent, thread_id, seq) PK(agent,seq)` + idx(agent, acked_at); `chat_messages.mentions_json` NULL; `agent_wake_state`. P1 phải migrate 2 thread live sang `chat_participants` **và** seed registry trong cùng migration — nếu không, thread đang chạy mất quyền đọc. P2 claims đọc role; P3 reviews; P4 escalation mention 30′. Trước P0 chỉ cần `mentions_json` nullable.

## 3 xung đột với bản đã chốt

1. **Cursor:** owner §5 "cursor lưu phía Muse (state file)" vs `agent_comms_decision.md` §4.1 `chat_read_state` server-side. Server thắng; state file là cache.
2. **Registry:** owner §3 "không có registry → không claim/review" vs §10 test 10 "agent cũ chạy không đổi hành vi" + `sk-video-agent-prod` đang live. Chỉ enforce sau seed+shadow, đúng 2 endpoint.
3. **Blocking:** owner §7 "manager xác nhận trước khi block có hiệu lực" vs §5.3 "fail+blocking → held ngay". Bỏ bước xác nhận cho shot (thêm 1 điểm chờ, trễ escalation 15′); chỉ item-freeze cần manager.

## 3 rủi ro

1. **Wake storm/trùng** → `agent_wake_state.attempts` >2/giờ/agent, mention chưa ack >30′, tỷ lệ wake:mention >3:1.
2. **Mention sai** → event `mention.parse_miss` (text có `@`, mentions rỗng) >0.5%, 422 `unknown_mention` >5/ngày.
3. **Enforce gãy 7-step** → 403 theo key/endpoint, `registry.miss`, tỷ lệ 7-step fail tăng.

## 3 câu hỏi mở của Owner

1. **Tên key + bàn giao:** `muse-manager`; bàn giao = update registry + event + seed `chat_read_state` của manager mới = `ack_seq` manager cũ (cursor per-agent nên không chuyển trực tiếp được).
2. **Auth `?light=1`:** full bearer key, không read-token nhẹ (thêm đường auth thứ 2 = thêm bề mặt lộ); key trong env/file 0600, rate limit 2/s.
3. **SLA ack:** 30′ ngoài cửa sổ, **10′** trong 16:00–18:00 (mất slot là mất doanh thu ngày).
