# Tranche 3 — C-4, C-6, C-7: đề bài trước khi code

Người viết: WorkBuddy (Reviewer C) · 2026-11-27
Nguồn: `review_c_shot_board_v1.md` §C-4, §C-6, §C-7 (cả ba đã có quyết định ✅)
Trạng thái: **chờ owner chốt 1 điểm ở §3**, sau đó mở thread `2026-11-27-002` giao Antigravity.

---

## 0. Vì sao tranh này không thể làm chung với tranh 1–2

Tranh 1–2 đã chốt: review chỉ tick `defect_codes`, server tính `score`/`verdict` (C-1); 2 pass mới xanh (luật gốc §4.1). Tranh 3 đụng đúng hai chỗ đó:

- **C-4** làm `reviews[]` trả về khác nhau **theo caller** — tức `GET /shots/{index}` không còn là hàm thuần của state.
- **C-7** đổi **số review cần để xanh** theo tier — tức `_board_status()` không còn là "đếm pass >= 2".

Vì vậy phải làm sau, và phải làm cùng nhau: C-7 đổi luật xanh, C-6 quyết định shot cũ có được tính là xanh hay không, C-4 quyết định ai được thấy điểm. Làm lẻ từng cái sẽ phải sửa 2–3 lần.

---

## 1. C-4 — Chấm mù phải chặn ở tầng API, không chỉ ở UI

**Vấn đề:** `GET /shots/{index}` trả full `reviews[]` kèm `score`. Agent đọc detail trước khi chấm là thấy hết neo điểm. "Mù" hiện chỉ đúng với người bấm UI.

**Contract:**

| trường | caller chưa chấm version hiện tại | caller đã chấm | manager/owner |
|---|---|---|---|
| `reviews[].score` | `null` | số thật | số thật |
| `reviews[].defect_codes` | `null` | mảng thật | mảng thật |
| `reviews[].verdict` | giữ nguyên | giữ nguyên | giữ nguyên |
| `reviews[].regen_brief` | **giữ nguyên** | giữ nguyên | giữ nguyên |
| `scores_summary` (lưới) | ẩn | hiện | hiện |

Ba quyết định đã cân nhắc, ghi lại để khỏi hỏi lại:

1. **Không redact `verdict`.** Ẩn nó không cứu được gì (shot đỏ thì ai cũng thấy đỏ) mà làm UI phải xử lý thêm một trạng thái "không biết".
2. **Không redact `regen_brief`.** Đây là thứ generator **cần** để sửa shot đỏ (C-2), mà generator không phải reviewer. Redact nó là bẻ gãy vòng regen.
3. **`?reveal=1` chỉ có tác dụng với manager/owner.** Agent thường gọi kèm `reveal=1` phải bị bỏ qua im lặng, không trả 403 — vì 403 xác nhận "có gì đó để reveal".

**Bẫy triển khai:** `_format_shot()` hiện không nhận caller. Đừng redact ở tầng route (dễ sót endpoint mới); redact trong `_format_shot(shot, caller=..., privileged=...)` để mọi đường đi qua đó đều được bảo vệ.

---

## 2. C-6 — `grandfathered`: shot cũ không chặn assemble, nhưng không được tính là xanh

**Vấn đề:** 201 shot cũ vào board ở `generated`, mà `generated` không chặn assemble. Với đúng job ISS, board **không gác gì cả** — trừ khi có đỏ, mà đỏ cần 402 lượt review mới sinh ra.

**Contract:**

- Field `grandfathered: bool` trên shot record, set **một lần** lúc migration cho video có trước ngày bật board. Có trong history, không sửa tay được.
- `grandfathered=true` → **không chặn assemble**, **không tính là xanh**. Header đếm riêng: `Xanh n · Cũ (chưa chấm) n · Đỏ n`.
- Chiến dịch review có mục tiêu: chỉ tier A của 201 shot cũ (~60–80 shot thay vì 201).
- Từ ngày bật board: mọi video mới **bắt buộc** qua review mới assemble được. Nếu không làm được điều này thì bỏ luôn cổng xanh, đừng giữ hình thức.

**Bẫy triển khai:** migration phải idempotent — chạy lại không được reset `grandfathered` về 0 cho shot đã set. Dùng mốc thời gian (`created_at < BOARD_ENABLED_AT`) chứ đừng dùng "thiếu field thì set 1", nếu không shot mới tạo trong lúc migration chạy sẽ bị đánh dấu cũ.

---

## 3. C-7 — Tier review: **điểm cần owner chốt**

**Vấn đề:** 385 shot × (1 gen + 2 review) ≈ 1155 lượt. Với 3 agent, mỗi shot chỉ có 2 người đủ điều kiện chấm, dư phòng = 0 → một agent nghỉ 20 phút là board đứng.

Phân tier (server tính, read-only, trả trong `shot.tier`):

| Tier | Điều kiện | Review cần | Ước tính |
|---|---|---|---|
| A | có mặt người/kính phản chiếu, `camera_move ≠ STATIC`, hoặc prompt có chữ/số | 2 | ~35% |
| B | STATIC, không mặt người, có chi tiết máy móc | 1 | ~45% |
| C | STATIC, vật thể đơn, không chữ, < 3s | 0 + audit ngẫu nhiên 20% | ~20% |

### Điểm phải chốt: tier B/C có được **xanh** với 1 pass (hoặc 0) không?

Đây không phải chi tiết kỹ thuật, nó là đánh đổi chất lượng:

- **Phương án 1 — tier quyết định số review để XANH.** B: 1 pass là xanh, C: 0 review là xanh. Giảm tải đúng như bảng trên, nhưng **hạ cổng chất lượng thật**: shot C chưa ai xem vẫn được coi là đạt. Rủi ro: lỗi nặng ở shot "trông đơn giản" đi thẳng vào bản cuối.
- **Phương án 2 — tier chỉ quyết định SỐ REVIEW ĐƯỢC YÊU CẦU, xanh vẫn cần 2 pass.** Không hạ cổng, nhưng gần như không giảm tải — vô nghĩa.
- **Phương án 3 (tôi đề xuất) — tier quyết định số review, nhưng shot chưa đủ review thì mang trạng thái `pending_review`, KHÔNG phải xanh và KHÔNG chặn assemble.** Cổng xanh giữ nguyên nghĩa "đã được 2 người xác nhận"; shot tier B/C chỉ đơn giản là không bao giờ đạt xanh nếu không ai chấm, và người assemble nhìn thấy con số đó trước khi bấm.

Phương án 3 là phương án duy nhất không nói dối về chất lượng mà vẫn cho chạy. Nhưng nó **không giảm được 1155 lượt** — nó chỉ làm con số đó hiện ra thay vì bị che. Nếu mục tiêu thật là giảm tải thì phải chọn 1, và chấp nhận hạ cổng.

**Cần owner trả lời đúng một câu:** giảm tải review, hay giữ nguyên nghĩa của cờ xanh?

---

## 4. Điều kiện tiên quyết (§13, sửa luôn)

Bật board chỉ khi có **≥ 4 agent active** (1 manager + 3 worker). Nếu < 4, luật xanh hạ xuống 1 pass cho tier C.

---

## 5. Ảnh hưởng tới bộ test hiện có

17 test đang xanh giả định `2 pass = xanh` và `GET /shots/{index}` trả điểm đầy đủ. Nếu chọn phương án 1 hoặc 2 ở §3, các test sau **sẽ phải sửa** — sửa test theo contract mới là hợp lệ, nhưng phải ghi rõ lý do trong thread:

- `test_c1_light_codes_arithmetic` — đọc `score` từ response detail
- `test_c3_*` — dựa trên `board_status` sau 0/1 review
- `test_c2_*` — dựng state "đỏ" bằng 1 fail review

Nếu chọn phương án 3: **không test nào phải sửa**, chỉ thêm test mới. Đây là lý do thứ hai tôi nghiêng về phương án 3.
