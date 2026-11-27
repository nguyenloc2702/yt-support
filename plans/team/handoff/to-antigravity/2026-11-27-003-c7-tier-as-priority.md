```
thread: 2026-11-27-003-c7-tier-as-priority
opened_by: workbuddy
to: antigravity
turn: 1
status: open
needs: action
type: task
```

# C-7 (phạm vi thu hẹp) — `tier` là thứ tự ưu tiên, KHÔNG phải cổng

Owner đã chốt: **cờ xanh luôn cần 2 agent khác nhau chấm đạt và kí.** Tier không được hạ số review ở bất kỳ mức nào. Vì vậy C-7 không còn là công cụ giảm tải — 1155 lượt review với 385 shot là cố định, và cách duy nhất để giảm là C-6 `grandfathered` (đã xong).

Phần còn lại của C-7, giao đợt này:

## Việc

1. **Server tính `shot.tier`** (A/B/C) từ dữ liệu đã có, trả trong response shot. **Read-only**, không có endpoint sửa.

   | Tier | Điều kiện |
   |---|---|
   | A | prompt có mặt người / kính phản chiếu / chữ hoặc số hiện trên hình, hoặc `camera_move ≠ STATIC` |
   | B | STATIC, không mặt người, có chi tiết máy móc |
   | C | STATIC, vật thể đơn, không chữ, < 3s |

   `camera_move` suy ra từ `index mod 5` như spec §6 đã mô tả. Nếu không suy được thì mặc định **B**, đừng đoán A.

2. **Dùng `tier` làm khoá sắp xếp thứ cấp** trong bucket `waiting_review` của `shots/next`: tier A trước, rồi B, rồi C; cùng tier thì `index_num` tăng dần.

   Lý do: theo chính bảng 4.2, lỗi nặng (mặt phản chiếu, sai hướng camera, subject ra khung) tập trung ở tier A. 385 shot không chấm cùng lúc, nên chấm A trước là cách duy nhất để rủi ro giảm sớm.

3. **Hiện tier trong `board_summary`** dưới dạng đếm theo tier, để người điều phối biết còn bao nhiêu việc nặng.

## Không được làm

- **Không đụng `_board_status`.** Xanh vẫn là "≥ 2 review pass từ 2 reviewer khác nhau, không ai là người gen/upload". Nếu bạn thấy mình đang sửa điều kiện này, dừng lại — sai hướng.
- **Không thêm trạng thái `pending_review`.** `generated` đã đóng vai đó.
- **Không cho sửa tier.** Nếu cho sửa, agent sẽ hạ tier để né bị soi.
- Không đổi shape của `reviews[]` hay `scores_summary` (C-4 vừa chốt).

## Không được vỡ

```
VIDEO_BOARD_ACCEPTANCE=1 pytest tests/test_board_acceptance.py tests/test_board_tranche3.py -q   ->  20 passed
pytest tests/ -q                                                                                 ->  389 passed, 20 skipped
```

## Báo lại

1. Commit hash.
2. Hai output pytest ở trên.
3. Một ví dụ `shots/next` trên job 4 shot cho thấy tier A được trả trước tier C khi cả hai cùng `waiting_review`.
4. Nếu `camera_move` không suy được từ dữ liệu hiện có ở đâu đó, nói rõ chỗ nào — đừng im lặng mặc định hết thành B.

Xong thì `status: answered`, `to: workbuddy`, move sang `to-workbuddy/`. Trần 8 lượt.
