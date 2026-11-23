# PO/BA — Round 2: Phản hồi Senior Dev

## Trả lời 5 câu hỏi

**Q1 — Phạm vi `blocking`:** Chặn **3 cửa**: claim shot bị block, upload clip của shot đó, và `confirm-assemble` của item. Block ở cấp shot thì các shot khác vẫn gen được; block ở cấp script/item thì chặn claim mọi shot của item. Lý do: dừng cả item khi 1 shot lỗi là đốt 2 giờ cửa sổ, nhưng cho gen tiếp trên script sai là đốt tiền GPU.

**Q2 — Thread là phòng job:** Thread gắn `item_id`, sống tới khi item `completed` + 7 ngày. Cap **200** + `paused` chấp nhận được, nhưng `paused` phải: (a) hiện UI + báo Telegram ngay, (b) owner resume được, (c) cho mở thread mới cùng item (tối đa 2 active/item). Lý do: job 60–80 shot cần trao đổi nhiều ngày; 30 tin quá ngắn.

**Q3 — Bất đồng kịch bản:** Mặc định **bên nói "fail" thắng** (an toàn trước); creator không được override. Owner là trọng tài cuối. SLA: báo owner ≤60s khi có blocking; nhắc lần 2 sau **30 phút**; **60 phút** không giải quyết → freeze item (không claim/upload); **17:30** chưa chốt → bỏ đăng ngày, ghi `digest.failed`.

**Q4 — Trần chi phí:** Chốt **3 tầng**: (1) per item/ngày: tối đa **1.5× số shot** được gen (dư 50% regen), **40 tin auto**, 2 vòng critic; (2) per agent/ngày: **5 item**, **3M token**, **300 tin auto**; (3) per thread: 12 auto liên tiếp → paused. Lý do: đơn vị doanh thu là video/item, không phải thread.

**Q5 — 3+ agent:** **Có, ngay P1.** Phòng job bắt buộc có owner + 2 agent = **3 participant** (owner phải thấy và chốt trong cùng phòng). Vì vậy dùng `chat_participants` từ P1, không chờ 6 tháng.

## Đồng ý / Phản đối

**1. Cap 30→200 + `paused` — ĐỒNG Ý.** 200 đủ cho job 2 ngày; `paused` thay `locked` đúng hướng. Bổ sung: paused phải notify Telegram + owner resume, không im lặng.

**2. Lease TTL 15 phút + heartbeat throttle 20s — PHẢN ĐỐI.** 15 phút quá dài cho cửa sổ 16:00–18:00: agent chết 16:10 thì 16:25 mới takeover, mất 12% cửa sổ. Đề xuất **TTL 300s, heartbeat 30s** (2 agent × 2 write/phút, không đáng kể với WAL), phát hiện chết ≤ **330s**; vẫn giữ throttle 20s làm trần.

**3. `blocking` chỉ chặn ráp, không đổi `shots.status` — PHẢN ĐỐI.** Không đổi `shots.status` thì đúng (giữ CAS), nhưng chỉ chặn ráp là sai: agent vẫn gen 40 shot trên script đã bị đánh fail. Đề xuất thêm `items.blocked_reason` + `blocked_at`; claim/upload/review trả **409** khi item bị block; chỉ owner resolve mới gỡ.

## Chỉnh yêu cầu của tôi

- **Lên must:** idempotency scoping; cursor/ack server-side; claim/lease; review gắn hash + gate 3 cửa; escalation scheduler; **room 3 participant**; loop control (12 auto/cooldown/body_hash); agent đang giữ lease phải gọi inbox `wait=10–20`.
- **Hạ ưu tiên:** presence UI realtime (đủ badge + `last_seen`); budget P4 → chỉ cần đếm auto-message, chưa cần `agent_usage` đầy đủ.
- **Bỏ khỏi MVP:** auto-rebalance khi agent chết, token budget theo thread, SSE/WS/broker.
- **MVP prod = P0 + P1 + P2** (idempotency, ack/inbox, presence, claim/lease, review/gate, escalation, room 3 người). P3 long-poll chỉ bật nếu load test đạt; không đạt thì poll 5s vẫn thoả SLA.

## Tiêu chí nghiệm thu (final)

- **A1** Việc mới hiện trong inbox ≤ **15s p95** (agent đang giữ lease, `wait=10–20`); agent one-shot theo lịch ≤ **60s**.
- **A2** Cùng `Idempotency-Key` retry 5 lần → đúng **1** bản ghi; 2 agent trùng key → **2** bản ghi.
- **A3** 2 claim đồng thời 1 shot → **1×201 + 1×409** kèm holder; **0** shot gen trùng trong 7 ngày.
- **A4** Lease TTL **300s**, heartbeat ≤30s; agent chết → steal được ≤ **330s**, có event `claim.stolen`.
- **A5** `ack` không giảm; unread đúng sau 100 tin và sau restart; **0** tin mất/7 ngày.
- **A6** Blocking review → claim/upload/assemble trả **409 trong 1 request**; owner resolve → mở ≤1 request.
- **A7** Blocking >15 phút → Telegram ≤ **60s**; claim hết hạn >5 phút → Telegram ≤60s; tin chưa trả lời >30 phút → Telegram.
- **A8** 12 auto liên tiếp → **423 paused**; cooldown 20s → **429**; body trùng trong 5 tin → **409**; **40** auto/item/ngày.
- **A9** 100% publish có review record gắn đúng hash artifact; hash lệch → **409**.
- **A10** 1 digest xong trong 16:00–18:00; ≥2 item song song; p95 API < **300ms** ở 4 agent × 1 msg/2s + 20 long-poll; WAL < **20MB**.
- **A11** 1 thread job có owner + 2 agent; người thứ 3 đọc/post được; non-participant **403**.

## Rủi ro còn lại

1. **Lease 15 phút** (nếu giữ) → chết 16:10, takeover 16:25, trễ giờ đăng → mất doanh thu ngày + uy tín kênh.
2. **Blocking chỉ chặn ráp** → đốt GPU 40 shot trên script sai, tốn tiền và trễ slot.
3. **`paused` không hiện UI** → 2 agent kẹt im lặng giữa job, owner chỉ biết qua Telegram muộn.

## 2 điều cần Architect quyết

1. **Phạm vi blocking:** nếu Dev giữ "chỉ chặn ráp", Architect chốt có thêm `items.blocked` chặn claim/upload không. Nghiệp vụ yêu cầu **có**.
2. **Lease TTL 15 vs 5 phút:** nếu Dev lo write amplification, Architect chốt TTL cuối và có cần đo write/s trước khi hạ không.
