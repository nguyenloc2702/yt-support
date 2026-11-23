# Memo Senior Dev — Round 3 (chốt)

| Phán quyết | Kết luận | Điều kiện kỹ thuật / lý do |
|---|---|---|
| R1 — Delivery REST + inbox, long-poll P5 | CHẤP NHẬN | Chỉ bật P5 khi load test 4 agent + 20 waiter đạt p95 <300ms; không đạt thì giữ poll 5s. |
| R2 — Blocking dùng `held`/`HELD_REVIEW` | CHẤP NHẬN | `HeldReason` là enum đóng, `group` mặc định "workflow": phải thêm value `REVIEW_BLOCKED`, map `_HOLD_GROUP_RULES` và nhánh `retry_held` (`service_shots.py:215`), nếu không agent retry vòng block. |
| R3 — Lease TTL 360s, heartbeat 60s | PHẢN ĐỐI KÈM ĐIỀU KIỆN | 360s chỉ an toàn nếu call gen chạy nền và renew được; nếu blocking, cần TTL 600s hoặc p99 gen <240s. |
| R4 — `chat_participants` P1, claim shot bắt buộc | CHẤP NHẬN | Owner là row thật trong `chat_participants`; migrate 2 thread cũ idempotent, giữ cột cũ để endpoint không đổi shape. |
| R5 — Trần 200/12/40/2, env | CHẤP NHẬN | `paused` chỉ owner post được và bật Telegram ngay; đếm 40 auto theo ngày local VN, không UTC. |

**Rủi ro còn lại — steal giữa call gen dài.** Agent gọi API gen 5–8 phút thì không renew được; lease 360s hết hạn, agent kia steal rồi gen lại cùng shot → đốt GPU, vi phạm A3. Phát hiện: đếm `claim.stolen`/ngày, cặp upload cùng `shot_id` từ 2 holder trong 30 phút, và p99 (claim → video upload) lấy từ `events`; alert khi p99 >0.7×TTL.

**Câu cần Architect chốt với owner:** `LEASE_TTL_SEC` mặc định 360 hay 600, dựa trên p99 thời gian gen một clip đo trong 3 ngày đầu?
