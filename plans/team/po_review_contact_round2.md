# PO/BA — Chốt 9 phán quyết

| R | Chấp nhận/Phản đối | Điều kiện (≤25 từ) |
|---|---|---|
| R1 | CHẤP NHẬN | Đếm 5 câu/item/ngày; escalate không chặn các shot khác. |
| R2 | CHẤP NHẬN | Mỗi lần gỡ block ghi lý do + giữ evidence; Owner nhận Telegram ngay. |
| R3 | CHẤP NHẬN | Đo ≤3 phút từ lúc claimable, không phải từ wake; cron 3 phút chỉ khi job active. |
| R4 | CHẤP NHẬN | `escalation_id` phải đang mở, đúng item; đã đóng → 422. |
| R5 | CHẤP NHẬN | `attempts` chỉ reset khi ack; escalation kèm `seq` mention bị sót. |
| R6 | CHẤP NHẬN | UI phân biệt "hook-only 60s" với "chết"; presence chỉ tính call không-light. |
| R7 | CHẤP NHẬN | Enforce chỉ sau 7 ngày 0 `registry.miss`, có kill-switch riêng từng endpoint. |
| R8 | CHẤP NHẬN | Quản lý thấy item chưa có thread qua danh sách item, không qua thread. |
| R9 | CHẤP NHẬN | Quản lý cũ cũng phải ack; quá 15 phút Owner được force handover có ghi đè. |

## Acceptance

A1: one-shot **≤3 phút trong cửa sổ, ≤10 phút ngoài**, đo từ lúc claimable tới API call đầu tiên; bỏ mốc ≤60s.

## Câu cho Owner

Owner có đồng ý mỗi lần quản lý gỡ block đều hiện UI + Telegram, và Owner có 15 phút phản đối?
