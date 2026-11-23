# Senior Dev — chốt 11 phán quyết

| R | Kết luận | Điều kiện kỹ thuật |
|---|---|---|
| R1 | CHẤP NHẬN | Dup-check vào cùng transaction; retry 1 lần khi `busy`, không chỉ UNIQUE. |
| R2 | CHẤP NHẬN | Row cũ `request_hash` NULL = legacy, không 422; thêm index `created_at` + job dọn. |
| R3 | CHẤP NHẬN | Claim critic check registry `active` + role worker/manager; chưa có registry thì owner set. |
| R4 | CHẤP NHẬN | Chặn `pass` khi `agreed` (trừ idempotent cùng hash); decision lưu `content_hash`. |
| R5 | CHẤP NHẬN | Vòng = proposal valid có finding/chữ ký; v3 chỉ mở khi có decision. |
| R6 | CHẤP NHẬN | `under_review` chỉ từ `draft`; `invalid` giữ nguyên tới version mới. |
| R7 | CHẤP NHẬN | `cancel` cũng CAS trạng thái, tránh đè approve đồng thời. |
| R8 | CHẤP NHẬN | Sweep CAS `status='draft'` 08:00–18:00; lazy-read chỉ đọc. |
| R9 | CHẤP NHẬN | `force_agree` atomic: `agreed` + signature owner/pass + decision cùng transaction. |
| R10 | CHẤP NHẬN | Language lạ fallback WPM en + warning; env đọc runtime. |
| R11 | CHẤP NHẬN | Test migration schema cũ (thiếu `request_hash`) + legacy row NULL. |

## Cách test race

2 thread, mỗi thread connection riêng (store đã thread-local), `Barrier(2)` + monkeypatch `validate` để cả hai đọc `MAX(version)` xong mới đi; assert 1×201 + 1×409 `version_conflict`, không 500. Lặp 50 vòng, thêm biến thể 4 thread.

## Rủi ro còn lại

**Claim critic để treo bài:** key nhanh tay chiếm slot rồi `block` bừa. Phát hiện: `critic.claimed` bởi key không được phân công, block rate theo critic >50%, proposal có critic nhưng 0 chữ ký sau 20′.
