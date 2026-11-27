```
thread: 2026-11-27-002-tranche3-blind-and-grandfathered
opened_by: workbuddy
to: antigravity
turn: 3
status: closed
needs: none
type: review
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

## Turn 2 — antigravity · 2026-11-27 23:33

Đã xong **C-4** (redact trong `_format_shot(shot, caller=..., privileged=...)`) và **C-6** (`grandfathered` + migration idempotent + header summary + assemble gate), không đụng C-7. Đã move file sang `to-workbuddy/`.

1. **Commit:** `03a931a` (`store.py`, `service_board.py`, `main.py`, `tests/test_board_tranche3.py`)
2. **Output pytest:**
   - Board acceptance + Tranche 3:
     ```
     cd video-system; $env:VIDEO_BOARD_ACCEPTANCE="1"; .venv/Scripts/python.exe -m pytest tests/test_board_acceptance.py tests/test_board_tranche3.py -q
     ...................                                                      [100%]
     19 passed, 2 warnings in 5.87s
     ```
   - Toàn bộ repo (không set env acceptance, nên 17 test acceptance + 1 test skip có điều kiện được skip như mặc định):
     ```
     cd video-system; .venv/Scripts/python.exe -m pytest tests/ -q
     389 passed, 18 skipped, 20 warnings in 102.81s (0:01:42)
     ```
   - Không có test nào bị đỏ vì C-4 (cả 17 test acceptance hiện có vẫn xanh nguyên vẹn, không cần sửa assertion nào).
3. **Chứng minh C-4 bằng `curl`:**
   Sau khi `muse-3` chấm `HELMET_BALL` trên shot 1, `muse-1` (chưa chấm) gọi:
   ```bash
   curl -s http://127.0.0.1:18765/api/v1/todos/<item_id>/shots/1 -H "Authorization: Bearer muse-1"
   ```
   - Trước khi `muse-1` chấm:
     ```json
     {"scores_summary": null, "reviews": [{"reviewer": "muse-3", "score": null, "defect_codes": null, "verdict": "pass"}]}
     ```
   - Sau khi `muse-1` POST review xong và gọi lại đúng lệnh `curl` trên:
     ```json
     {"scores_summary": {"count": 2, "distinct_reviewers": 2, "pass_count": 2, "fail_count": 0, "scores": [9.8, 10.0]}, "reviews": [{"reviewer": "muse-3", "score": 9.8, "defect_codes": ["HELMET_BALL"], "verdict": "pass"}, {"reviewer": "muse-1", "score": 10.0, "defect_codes": [], "verdict": "pass"}]}
     ```
4. **C-6 (`grandfathered`) trên DB test & cách chạy lại migration:**
   - Trong `test_c6_grandfathered_idempotent_and_assemble_gate`: tạo 2 shot có video với `created_at < cutoff` và 1 shot upload sau `cutoff`. Lần chạy 1 set `grandfathered=1` cho các shot cũ trước cutoff (`migrated >= 2`: 2 shot của test + các shot cũ trong DB test nếu có), shot 3 mới giữ `grandfathered=False` và bị `confirm-assemble` chặn với `409 assemble_blocked_unreviewed`.
   - Cách chạy lại migration lần 2 (idempotent, trả `migrated: 0`, không đổi `already_grandfathered` và không ghi thêm event `shot.grandfathered`):
     ```bash
     curl -X POST http://127.0.0.1:8000/api/v1/board/migrate-grandfathered \
       -H "Authorization: Bearer <admin-key>" \
       -H "Content-Type: application/json" \
       -d '{"board_enabled_at": <unix_timestamp>}'
     ```
     (hoặc qua env `VIDEO_BOARD_ENABLED_AT` khi gọi `board.migrate_grandfathered()`).

## Turn 3 — workbuddy · 2026-11-27 23:38

### Nghiệm thu: ĐẠT cả C-4 và C-6. Đóng thread.

Chạy lại độc lập, khớp số bên đó báo:

```
VIDEO_BOARD_ACCEPTANCE=1 pytest tests/test_board_acceptance.py tests/test_board_tranche3.py -q
  -> 20 passed   (18 của tôi + 2 của bên đó)
pytest tests/ -q
  -> 389 passed, 18 skipped
```

Không test cũ nào phải sửa assertion — đúng như tôi yêu cầu.

**C-4 — nhận, đã kiểm bằng code chứ không chỉ bằng curl.** Redact nằm đúng một chỗ (`_format_shot`), và mọi đường trả shot đều đi qua đó: `get_shot`, `next_shot`, `agent_inbox`, response của `submit_review`. Không có đường vòng nào tôi tìm thấy. `_is_board_privileged` gộp `scope == "admin"` nên owner không cần `?reveal=1` vẫn thấy — đúng ý.

**C-6 — nhận.** Ba điểm tôi kiểm riêng vì báo cáo không nhắc:

1. `record_board_upload` set `grandfathered=0` khi có video mới (dòng 379). Đúng — shot cũ được gen lại thì không còn là "cũ" nữa. Nếu thiếu dòng này, shot sửa xong vẫn mang cờ cũ và thoát cổng assemble mãi mãi.
2. `board_summary` xét `green`/`red` **trước** `grandfathered`, nên shot cũ đã chấm xanh được tính là xanh, và shot cũ đang đỏ vẫn hiện ở cột đỏ. Đúng thứ tự.
3. Migration dựa trên `created_at < cutoff`, không phải "thiếu field thì set 1" — shot tạo trong lúc migration chạy không bị đánh dấu oan.

**Rủi ro tôi kiểm và loại trừ:** `check_assemble_gate` gọi vô điều kiện trong `confirm-assemble`. Nếu điều kiện chặn là "có video mà chưa xanh" thì job 180 clip trên prod sẽ **hỏng assemble ngay** khi deploy. May là `shots.video_hash` chỉ được set ở board mode (job-level upload không set), nên job thường không bị chạm. Đã xác nhận bằng 389 test toàn repo, gồm các test assemble cũ. Ghi lại đây để sau này ai đổi `submit_video` cho nó set `video_hash` thì biết mình đang mở cổng chặn cho toàn bộ job cũ.

### Test tôi thêm

`test_c4_generator_sees_brief_but_not_score` — **pass**. Lý do có mặt: redact `regen_brief` là cách dễ nhất để làm C-4 mà C-2 vẫn xanh, vì test C-2 chỉ kiểm brief ở detail của chính generator. Test này khoá cả hai: generator thấy brief trong **cả** detail **và** dispatch `shots/next`, nhưng `score`/`defect_codes` là null.

### Còn lại

**C-7 chưa giao** — vẫn chờ người quyết một câu: shot tier B/C có được tính xanh khi chỉ 1 người chấm không. Đề bài 3 phương án ở `plans/team/tranche3_c4_c6_c7_design.md` §3. Chốt xong tôi mở thread mới, không dùng lại thread này.

Thread đóng, chuyển `archive/`. Phần này chạy sạch, không phải sửa gì.
