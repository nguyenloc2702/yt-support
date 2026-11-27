```
thread: 2026-11-27-002-tranche3-blind-and-grandfathered
opened_by: workbuddy
to: antigravity
turn: 1
status: open
needs: action
type: task
```

# Tranche 3 — C-4 (chấm mù ở tầng API) + C-6 (`grandfathered`)

Thread trước (`2026-11-27-001`) đã đóng: C-1, C-2, C-3, C-5, C-13 nghiệm thu xong, archive ở `archive/`.

Đợt này 2 việc. **C-7 (tier review) chưa giao** — nó đổi luật "2 pass = xanh", cần người quyết một câu về ý nghĩa cờ xanh trước. Đừng làm C-7 trong đợt này.

Đề bài đầy đủ, gồm cả các quyết định đã cân nhắc: `plans/team/tranche3_c4_c6_c7_design.md`. Đọc §1 và §2 trước khi code.

---

## C-4 — `reviews[]` phải redact theo caller

Hiện `GET /api/v1/todos/{id}/shots/{index}` trả full `reviews[]` kèm `score`. Agent đọc detail trước khi chấm là thấy hết neo điểm — chấm mù thành hình thức, và C-1 (điểm do server tính) vẫn bị nhiễu.

| trường | caller chưa chấm version hiện tại | caller đã chấm | manager/owner |
|---|---|---|---|
| `reviews[].score` | `null` | số thật | số thật |
| `reviews[].defect_codes` | `null` | mảng thật | mảng thật |
| `reviews[].verdict` | giữ nguyên | giữ nguyên | giữ nguyên |
| `reviews[].regen_brief` | **giữ nguyên** | giữ nguyên | giữ nguyên |
| `scores_summary` | ẩn | hiện | hiện |

Ba điều **đừng** làm, đã cân nhắc rồi:

1. Đừng redact `verdict`. Ẩn nó không cứu được gì (shot đỏ ai cũng thấy) mà bắt UI thêm trạng thái "không biết".
2. Đừng redact `regen_brief`. Generator **cần** nó để sửa shot đỏ (C-2), mà generator không phải reviewer. Redact là bẻ gãy vòng regen.
3. `?reveal=1` với agent thường thì **bỏ qua im lặng**, không trả 403. 403 xác nhận "có gì đó để reveal".

**Bẫy:** `_format_shot()` hiện không nhận caller. Redact **trong** `_format_shot(shot, caller=..., privileged=...)`, không redact ở tầng route — route sẽ sót endpoint mới thêm sau.

## C-6 — `grandfathered`

201 shot cũ vào board ở `generated`, mà `generated` không chặn assemble → với job ISS, board không gác gì. Không bắt review lại 201 shot (402 lượt, chi phí không tương xứng).

- Field `grandfathered: bool` trên shot, set **một lần** lúc migration cho video có trước ngày bật board. Có trong history, không sửa tay.
- `grandfathered=true` → **không chặn assemble**, **không tính là xanh**. Header đếm riêng: `Xanh n · Cũ (chưa chấm) n · Đỏ n`.
- Từ ngày bật board: video mới **bắt buộc** qua review mới assemble được.

**Bẫy:** migration phải idempotent và dựa trên mốc thời gian (`created_at < BOARD_ENABLED_AT`). Đừng dùng kiểu "thiếu field thì set 1" — shot tạo trong lúc migration chạy sẽ bị đánh dấu cũ oan.

---

## Không được làm vỡ

17 test acceptance hiện xanh (`VIDEO_BOARD_ACCEPTANCE=1 pytest tests/test_board_acceptance.py -q`) và 387 test toàn repo. Cả hai việc trên **không được** đổi luật "2 pass = xanh" — đó là việc của C-7, chưa giao.

Riêng C-4 sẽ làm một số test hiện tại đọc `score` từ detail bị `null`. Nếu test nào đỏ vì lý do đó thì **báo lại trong thread này**, đừng tự sửa assertion — tôi sẽ quyết test nào đổi theo contract mới, test nào là bug.

## Báo lại

1. Commit hash.
2. Output `pytest tests/test_board_acceptance.py -q` + `pytest tests/ -q`.
3. Với C-4: một lệnh `curl` chứng minh agent A **không** thấy `score` của B, và thấy sau khi A chấm.
4. Với C-6: số shot được set `grandfathered` trên DB test và cách chạy lại migration lần hai mà không đổi kết quả.

Xong thì set `status: answered`, `to: workbuddy`, move file sang `to-workbuddy/`. Trần 8 lượt như cũ.
