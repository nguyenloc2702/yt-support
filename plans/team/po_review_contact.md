# PO/BA — Đánh giá đề xuất "Muse contact architecture"

## Verdict 3 điểm

- **Phân cấp: ACCEPT WITH CHANGES.** Chỉ dùng cho việc ngoài luật và điều phối, không gate từng shot; bắt buộc luật chơi phủ ≥95%, block mặc định bật, SLA kép trong/ngoài cửa sổ.
- **Mention: ACCEPT WITH CHANGES.** Là tín hiệu ưu tiên, không phải lệnh; worker→Owner phải kèm `escalation_id`, không có thì 422, trần 1 mention Owner/item/giờ.
- **Wake: ACCEPT WITH CHANGES.** 3 lớp đúng hướng và tiết kiệm token; nhưng L2 trong 16:00–18:00 phải 3 phút, ngoài cửa sổ mới 10 phút.

## Trả lời 6 câu

**1. Phân cấp có làm chậm cửa sổ?** Có, nếu mỗi shot đều hỏi. Số câu hỏi = 117 × (1 − độ phủ luật). Phủ 95% → ~6 câu × 2,5–3 phút/vòng (wake 60s + quản lý xử lý) = **+15–18 phút**, tức 12–15% cửa sổ, chấp nhận được. Phủ 80% → ~23 câu → **+58–69 phút**, gần nửa cửa sổ, gãy. Vì vậy luật chơi phải phủ ≥95% và tối đa 5 câu/item: (a) auto-regen 1 lần khi fail style/duration; (b) ngưỡng shortfall/thumbnail; (c) map severity feedback audio; (d) dải shot được claim; (e) định nghĩa "tốn tiền" (>2 lần gen/shot mới cần phép); (f) danh sách ngoại lệ đã duyệt trước.

**2. Single point of failure?** 30 phút không phù hợp: bằng 25% cửa sổ, và quản lý đã ngủ từ trước đó. SLA kép: trong 16:00–18:00 **ack ≤5 phút, escalate Owner ≤10 phút**, đồng thời bật quản lý dự phòng đã đăng ký; ngoài cửa sổ 30 phút. Thêm kiểm tra presence lúc **15:45**: quản lý không alive → Owner nhận cảnh báo và chuyển quyền tạm cho deputy ngay, không chờ.

**3. Block qua 2 lớp duyệt có phải lỗ hổng?** Có, đây là **fail-open**: quản lý ngủ thì block vô hiệu, worker vẫn đốt GPU trên script sai. Đảo mặc định: reviewer fail + blocking → shot `held` **ngay** (tạm), quản lý có 10 phút trong cửa sổ (30 phút ngoài) để xác nhận hoặc gỡ; hết hạn → block tự chính thức + escalate Owner. Quản lý chỉ có quyền **gỡ**, không có quyền "bật" block.

**4. @mention có thành kênh cấp cứu tuỳ tiện?** Quy ước không đủ. Phải là kiểm tra quyền: worker tag Owner chỉ hợp lệ khi kèm `escalation_id` đã mở (do SLA timeout hoặc quản lý mở); không có → 422. Thêm trần 1 mention Owner/item/giờ, dedupe 30 phút, Owner nhận digest thay vì tin lẻ. Vậy vừa chặn spam vừa giữ đường cấp cứu thật.

**5. Bàn giao quản lý cần record gì?** Snapshot 7 mục: (1) claim đang mở + holder; (2) review open/blocking; (3) câu hỏi chưa trả lời; (4) thread `paused` + lý do; (5) cursor `chat_read_state` của vai quản lý; (6) approval/escalation treo; (7) budget đã dùng. Ghi `manager.handover` + hash + ack của quản lý mới, overlap 15 phút. Trách nhiệm: quản lý cũ chịu nội dung, quản lý mới chịu ack, Owner chịu phê duyệt — sót thì Owner là người cuối vì chỉ Owner đổi được registry.

**6. 3 chỉ số đo hiệu quả?** (1) Tỷ lệ tự chủ **≥90%** shot xong không phải hỏi, và **≤5 câu/item**; (2) độ trễ quản lý **p95 ≤5 phút** ack trong cửa sổ, tổng thời gian worker chờ **≤15% cửa sổ**; (3) Owner bị ngắt **≤3 lần/ngày** và **0 việc mất** khi bàn giao. Nếu (1) thấp hoặc (2) cao → luật chơi chưa đủ, không phải "chạy được".

## 3 xung đột với bản đã chốt

1. **Blocking hiệu lực ngay:** `agent_comms_decision.md` §5.3 và test #5 quy định fail + blocking → shot `held` ngay, chặn claim/upload/assemble. Đề xuất Owner §7 bắt quản lý xác nhận trước → mâu thuẫn trực tiếp, tạo fail-open.
2. **Chuỗi thẩm quyền:** decision §12.3 "reviewer chỉ đề xuất, author sửa, Owner trọng tài cuối" và trần 2 vòng critic (§7). Owner thêm "quản lý review vòng 1" + quản lý duyệt sửa → quản lý thành trọng tài giữa, vượt trần 2 vòng.
3. **SLA one-shot:** decision §5.1 cam kết **≤60s**; Owner §5 đặt L2 **≤10 phút** → nới 10 lần, phá cam kết A1 đã chốt.

## 3 rủi ro nghiệp vụ

1. **Quản lý thành nút thắt:** 117 shot × hỏi = mất cửa sổ, trễ/mất slot đăng → mất doanh thu ngày và uy tín kênh.
2. **Fail-open block:** quản lý ngủ → GPU đốt trên script sai, video lỗi tới Owner → hoàn tiền/uy tín.
3. **Bàn giao sót:** claim/review treo không ai biết, item kẹt qua ngày → mất slot, Owner mất niềm tin vào mô hình.

## Trả lời 3 câu hỏi mở của Owner

1. **Tên key + bàn giao:** `muse-manager`, một key một người giữ; bàn giao qua registry + snapshot 7 mục + event `manager.handover` + ack, Owner duyệt.
2. **Auth `?light=1`:** dùng đầy đủ Bearer key riêng của Muse; nếu cần nhẹ, thêm scope `hook` chỉ đọc `?light=1`, không đọc body, không claim. Không dùng token chung.
3. **SLA ack:** theo cửa sổ — trong 16:00–18:00 ack ≤5 phút, trả lời ≤10 phút, escalate ≤10 phút; ngoài cửa sổ 30 phút.
