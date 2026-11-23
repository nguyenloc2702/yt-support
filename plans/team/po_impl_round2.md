# PO/BA — Chốt 11 phán quyết

| R | Chấp nhận/Phản đối | Điều kiện (≤25 từ) |
|---|---|---|
| R1 | CHẤP NHẬN | Retry 1 lần khi lock; hết retry mới 409. |
| R2 | CHẤP NHẬN | Replay chỉ khi hash khớp; TTL 24h; 422 kèm key gốc. |
| R3 | CHẤP NHẬN | Critic claim tường minh + event, không gán ngầm lúc ký; owner đổi được trước chữ ký đầu. |
| R4 | CHẤP NHẬN | Mở lại phải tạo version mới, không sửa bản agreed; bản agreed giữ nguyên. |
| R5 | CHẤP NHẬN | Đếm vòng hiện trong `next_action`; chỉ decision của owner vượt trần. |
| R6 | CHẤP NHẬN | Finding trên invalid vẫn ghi nhưng không mở ký; cần version valid mới. |
| R7 | CHẤP NHẬN | CAS fail trả 409 kèm status hiện tại, không trả brief như thành công. |
| R8 | CHẤP NHẬN | Scheduler chỉ duyệt trong giờ làm việc; Telegram owner ngay, không chỉ event. |
| R9 | CHẤP NHẬN | Agreement owner force đánh dấu `owner_forced`; publish gate phân biệt với 2 chữ ký. |
| R10 | CHẤP NHẬN | Đổi WPM không được hồi tố proposal đã agreed; validation snapshot giữ nguyên. |
| R11 | CHẤP NHẬN | Test scheduler không duyệt ngoài giờ; agreement `owner_forced` hiển thị đúng. |

## 2 điều cho Owner

1. Owner là người duy nhất `force_agree`/mở lại tới P1.5 — Owner có cam kết xử lý deadlock trong 15 phút, kể cả ngoài cửa sổ, hay muốn manager quyết từ P1?
2. Brief `calendar` chỉ Owner tạo — Owner duyệt trước calendar tuần để agent khỏi chờ từng brief, hay chấp nhận chờ từng brief?
