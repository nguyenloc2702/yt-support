# PO/BA — Round 3: Chốt 5 phán quyết

| Phán quyết | Chấp nhận/Phản đối | Điều kiện hoặc lý do (≤25 từ) |
|---|---|---|
| **R1 — Delivery** | CHẤP NHẬN | A1 đo lại theo poll 10–15s: ≤15s p95 cho agent đang giữ việc, ≤60s cho one-shot theo lịch. |
| **R2 — Blocking review** | CHẤP NHẬN | `held_reason` phải namespace; `confirm-assemble` chỉ chặn `held_review`; agent cũ nhận 409 kèm lý do, không silent. |
| **R3 — Lease** | CHẤP NHẬN | Agent phải pre-extend lease phủ thời lượng gen dự kiến trước call dài, nếu không bị steal khi còn sống. |
| **R4 — Scope model** | CHẤP NHẬN | Item claim mặc định advisory; chỉ chặn shot claim khi khai `exclusive=true`, tránh khoá chéo. |
| **R5 — Trần chi phí** | CHẤP NHẬN | `paused` phải báo Telegram + hiện UI ngay; trần đọc runtime, đổi env không cần restart. |

## Rủi ro nghiệp vụ còn lại

Trần **40 auto msg/item/ngày** quá thấp cho job 60–80 shot review chéo từng shot: thread `paused` giữa 17:00, owner không kịp resume, mất slot đăng ngày.

## Câu cần Architect nói rõ với owner trước khi code

Owner có cam kết trực trong 16:00–18:00 để bấm resume/override ngay khi `paused` hoặc `held` không — vì mọi trạng thái đó đều đồng nghĩa "không đăng hôm nay" cho tới khi owner thao tác?
