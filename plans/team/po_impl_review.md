# PO/BA — Review Phase 0–1: brief → script duel

## Verdict: SHIP WITH CHANGES

Nền đúng hướng, additive, 216 test xanh, không chạm 7-step. Nhưng cổng duyệt và quyền ký đang hở, vòng sửa không có trần, deadlock không có đường ra. Merge sau flag được; **không bật agent thật** tới khi xong 5 must-fix dưới.

## 5 must-fix

| # | Vấn đề | Hành vi mong muốn | Mức |
|---|---|---|---|
| M1 | `MAX_ROUNDS` không dùng; v3, v4… vô hạn | Tạo version >2 phải có decision record; không thì 409 `round_limit`, proposal `deadlock`, escalate. | Chặn |
| M2 | `script_decisions` không có endpoint/status | `POST /briefs/{id}/decisions` cho manager/owner: `force_agree\|reject\|cancel` + lý do bắt buộc; force_agree gắn hash cụ thể, ghi actor + event + Telegram. | Chặn |
| M3 | Duyệt lazy; ai cũng tạo brief `calendar` | Chỉ owner/manager tạo `calendar`; auto-approve chạy theo scheduler, không theo lượt đọc; mọi approve phát event + Telegram; objection chỉ tính giờ làm việc. | Chặn |
| M4 | Mọi key `ingest` đều ký/block được | Chỉ author + critic được phân công (manager thay khi critic vắng); key khác 403; `block` bắt buộc evidence; lưu role trong signature. | Chặn |
| M5 | Brief `hard`/deadlock/1 chữ ký treo im lặng | SLA: draft/hard >15', deadlock >15', 1 chữ ký >20' → Telegram + badge; cutoff 17:00 phải cancel/force, không treo qua ngày. | Chặn |

## Trả lời 7 câu

**1. Cổng duyệt lazy:** Có, phải có scheduler. Auto-approve theo lượt đọc nghĩa là ai mở dashboard thì brief được duyệt, `list()` có thể duyệt hàng loạt; không ai đọc thì treo `draft` vĩnh viễn. Chạy sweep 60s theo `due_at`, ghi event + Telegram; objection window chỉ 08:00–18:00.

**2. 2 vòng sửa:** Hậu quả: 2 agent đấu vô hạn, đốt token, trễ 18:00, và không bao giờ chạm deadlock. Doc hứa "tối đa 2 vòng" nên tranh chấp không có chuẩn phân xử. Chặn v3 khi chưa có decision; chuyển `deadlock` + escalate.

**3. Deadlock:** Thiếu cả chuỗi: cách phát hiện (v3, 2 block, hoặc 2 chữ ký không hội tụ), status `deadlock`, endpoint quyết định, hiệu lực (force_agree tạo agreement trên hash; reject huỷ brief), event + Telegram. `script_decisions` hiện là bảng chết.

**4. Quyền ký:** Chỉ author + critic được phân công; manager ký thay khi critic vắng; owner override qua decision. Mọi key `ingest` khác — kể cả `sk-video-agent-prod` — phải 403. `block` phải kèm evidence và chỉ critic/manager được block.

**5. Freeze:** Hơi cứng. Cho **1 lần amendment trước build**: chưa build, chưa shot nào claim → author nộp v+1, đúng 2 người cũ ký lại; sau đó đóng băng thật. Nếu không, một typo tốn cả brief + TTS + 30 phút.

**6. Owner vắng:** Trước khi bật agent thật phải có: escalate draft/hard/deadlock/1-chữ-ký; trong cửa sổ 15 phút, cutoff 17:00 (cancel hoặc force decision); ngoài cửa sổ dồn 09:00 hôm sau. Auto-approve không được chạy qua đêm.

**7. 3 metric:** (a) brief→agreed p95 ≤30 phút, ≥90% chốt ở v1/v2, 0 agreement sau v3 không có decision; (b) 0 brief duyệt bởi non-owner/manager, 0 agreement thiếu chữ ký critic được phân công, 0 self-review; (c) 0 stall im lặng — mọi brief/agreement treo >30 phút đều có event + Telegram.

## 3 câu hỏi cho Senior Dev

1. `_maybe_auto_approve` chạy trong `list()`: nếu owner mở dashboard, brief quá hạn tự duyệt hàng loạt — có tách approve khỏi read path bằng scheduler/sweep được không?
2. `script_signatures` không lưu role/assignment: server biết ai là "critic được phân công" từ đâu — registry, `work_assignments`, hay thêm cột?
3. Deadlock phát hiện theo tiêu chí nào — đếm version, đếm block signature, hay 2 chữ ký không hội tụ — và có cần status `deadlock` riêng không?
