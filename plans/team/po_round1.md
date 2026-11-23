# PO/BA — Round 1: Kênh giao tiếp giữa 2 agent trên production

## Kết luận trước

- Chat hiện tại (REST polling + cursor) **đủ cho hội thoại, không đủ cho vận hành prod**. Cần thêm **lớp phối hợp công việc có state** (claim/lease/ack/review/reminder) đặt cạnh chat, không thay chat.
- Ba thứ bắt buộc: (1) **ai giữ việc gì** (lease + heartbeat), (2) **review có cấu trúc gắn version/hash** (verdict + evidence + blocking), (3) **nhắc & escalate SLA** tới owner qua Telegram.
- Không cần broker. Ở quy mô <10 agent, ~1 msg/phút, SQLite WAL + 1 process đủ **nếu** write path được kiểm soát; broker là chi phí vận hành không đổi lại được giá trị.
- Agent one-shot là ràng buộc số 1: **pull/inbox bắt buộc**; push thuần tuý sẽ mất việc.
- Mọi thứ phải audit được và **owner vẫn là gate cuối**; agent không được publish/confirm-assemble vượt owner.

## Jobs-to-be-done

### 1. Soạn kịch bản chung
- Ai: Agent A (writer) + Agent B (critic).
- Input: digest list 8–15 tin owner đã chốt + summary nguồn. Output: `script_en.json` + `shots[]` đã qua critic.
- Tần suất: 1 lần/ngày (daily digest).
- Thất bại: script sai số/trùng tin → critic fail 2 lần → không render → **mất slot đăng ngày**.

### 2. Điều phối gen shot
- Ai: A và B chia dải shot (A shot 1–6, B 7–12…) hoặc chia item.
- Input: `shots[]` + `duration_sec` + reference images. Output: clip theo `seq_num` + `covered_shot_indexes`, `shortfall_sec == 0`.
- Tần suất: mỗi job, hàng chục shot (audio ~1000s ⇒ ~60–80 macro-shot 12–18s).
- Thất bại: gen trùng = đốt tiền GPU + xung đột seq; thiếu shot = shortfall > 0.5s → không assemble được.

### 3. Review chéo
- Ai: người không gen ra artifact (B review A, ngược lại).
- Input: artifact + version/hash + tiêu chí (style lock v3, duration, thumbnail). Output: verdict pass/fail + evidence + blocking + đề xuất sửa.
- Tần suất: mỗi artifact (keyframe/clip/script), nhiều lần/ngày.
- Thất bại: review trôi trong chat, không chặn được publish; hoặc review áp lên bản cũ đã sửa.

### 4. Nhắc việc & SLA
- Ai: hệ thống nhắc agent; agent nhắc owner khi bị chặn.
- Input: trạng thái job/shot + deadline 18:00. Output: reminder, escalate Telegram.
- Tần suất: trong cửa sổ 16:00–18:00.
- Thất bại: job kẹt im lặng, owner không biết, mất ngày.

### 5. Cùng quản trị hệ thống
- Ai: cả 2 agent + owner.
- Input: queue, quota YouTube (1600 units/video, ~6/ngày), BGM library, disk, read_me log. Output: cảnh báo, đề xuất, log.
- Tần suất: mỗi ngày / khi có sự cố.
- Thất bại: hết quota/đầy disk → pipeline chết mà không ai biết.

## Kịch bản prod cụ thể

**S1 — A chết giữa lúc gen shot 12.** A đã claim shot 12 nhưng process chết. Không có heartbeat/lease: B không biết nên chờ hay làm tiếp. B gen lại → trùng, tốn tiền, sai seq; B chờ → kẹt hết ngày. **Cần lease có TTL + takeover có audit.**

**S2 — cả 2 cùng nhận 1 todo.** Cả 2 poll cùng lúc, cùng gen 1 item → double cost, `seq_num` đụng nhau, `covered_shot_indexes` chồng lấn. **Cần claim nguyên tử ở cấp item/shot, không chỉ "ai nhanh tay".**

**S3 — review lệch version.** A review script v1 của B; B sửa v2 (hash đổi) trước khi verdict tới → verdict pass áp lên bản đã chết, false approve. **Cần review record gắn hash/version, verdict cũ tự stale.**

## Yêu cầu chức năng

**Must (MVP prod):**
- M1 Claim/lease theo item + shot, TTL, heartbeat, takeover khi hết hạn, mọi thay đổi ghi `events`.
- M2 Inbox/ack: hộp thư đến việc được giao; trạng thái `assigned → acked → in_progress → done/failed`.
- M3 Review record có cấu trúc: `artifact_type, artifact_id, version/hash, verdict, severity, blocking, evidence, reviewer`.
- M4 Reminder + escalation theo SLA, đẩy owner qua Telegram; không spam.
- M5 Loop/cost control: max turns/thread, cooldown, budget token/item, giữ rate limit hiện có.
- M6 Idempotency cho post/upload (retry không tạo tin/clip trùng).
- M7 Giữ nguyên 7-step API + chat cũ; agent cũ vẫn chạy.

**Should:**
- S1 Presence: agent sống/chết theo heartbeat, hiển thị trên UI.
- S2 Room theo job (không chỉ 1-1) để 3+ agent/owner cùng thấy.
- S3 Review blocking chặn `confirm-assemble` ở tầng service.

**Could:**
- C1 Digest tiến độ job cho owner.
- C2 Tự phân công lại khi agent chết (auto-rebalance).

## Tiêu chí nghiệm thu đo được

- G1 pickup: việc mới hiện trong inbox agent ≤ 30s (P95), với poll 10–15s.
- G2 lost message: 0 tin mất trên 7 ngày chạy thật; retry trùng bị dedupe theo idempotency key.
- G3 lease: TTL 15 phút, heartbeat 5 phút; agent chết phát hiện ≤ 2×TTL (30 phút).
- G4 escalate: task chưa claim > 10 phút hoặc blocked > 15 phút → owner nhận Telegram ≤ 60s.
- G5 duplicate work: 0 shot bị 2 agent gen trong 7 ngày (đo qua `events`).
- G6 loop: 1 thread/item ≤ 6 lượt trao đổi/giờ; vượt → dừng + báo owner.
- G7 review: 100% publish có review record gắn hash đúng version, hoặc owner override có log.
- G8 throughput: 1 digest/ngày xong trong 16:00–18:00; chịu ≥2 job song song.

## Non-functional

- **Audit:** mọi state change vào `events` (actor = tên key, item/shot, hash, thời điểm).
- **Bảo mật:** không dán key vào tin; nội dung review (chứa script) cùng ACL participants + owner.
- **Human gate:** agent không được confirm-assemble/publish nếu chưa owner approve.
- **One-shot:** pull/inbox + cursor resume bắt buộc; không giả định daemon.
- **Chi phí:** trần token/item, rate limit, không broker; ưu tiên tận dụng SQLite hiện có.
- **Backward compat:** agent cũ chỉ biết 7-step vẫn chạy; tính năng mới opt-in.

## Rủi ro nếu chọn sai

- Chat-only không state → tranh chấp "ai làm gì", gen trùng, không chứng minh được.
- Push-only (WS/SSE) → agent one-shot mất việc, phải thêm hàng đợi phức tạp.
- Broker (Redis/NATS) → thêm 1 hệ thống vận hành trên 1 VPS, thêm failure mode, không giải quyết claim/review.
- Structured protocol quá nặng → agent không implement nổi, adoption thất bại.
- Không loop control → đốt token, hóa đơn tăng không kiểm soát.

## Câu hỏi cho Senior Dev

1. Agent one-shot có giữ được kết nối 30s không? Nếu long-poll 25s, anyio threadpool ~40 thread chịu được bao nhiêu agent đồng thời?
2. SQLite WAL 1 process chịu được bao nhiêu write/s khi có heartbeat 10s/agent + claim + ack? Đo ở đâu, ngưỡng nào phải sang Postgres?
3. `cas_update` hiện có đủ cho claim/lease nguyên tử không, hay cần bảng + transaction riêng? 2 agent claim cùng lúc có lost-update không?
4. `shots`/`items` đã có cột version/hash chưa? Bind review verdict vào hash và chặn assemble khi hash đổi làm được ở service layer không?
5. Retry POST tin/upload có tạo bản trùng không? Idempotency key đặt ở client hay server, và cursor `since_id` có đảm bảo at-least-once?
