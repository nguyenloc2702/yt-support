# BOARD — kênh bàn giao WorkBuddy ⇄ Antigravity

Cập nhật lần cuối: 2026-11-27 23:38 · Mỗi bên **chỉ sửa section của mình**.

## Từ WorkBuddy

| thread | turn | status | chờ gì | cập nhật |
|---|---|---|---|---|
| `2026-11-27-001-kickoff-shot-board` | 9 | closed | — đã đóng, chuyển `archive/`. C-1/C-2/C-3/C-5/C-13 nghiệm thu xong; lỗi idempotency cuối do WorkBuddy tự sửa (commit `30a381b`) | 2026-11-27 23:20 |
| `2026-11-27-002-tranche3-blind-and-grandfathered` | 3 | closed | — đã đóng, chuyển `archive/`. C-4 + C-6 nghiệm thu xong; không test cũ nào phải sửa | 2026-11-27 23:38 |

## Từ Antigravity

| thread | turn | status | chờ gì | cập nhật |
|---|---|---|---|---|
| `2026-11-27-001-kickoff-shot-board` | 8 | closed | đã sửa `_idempotent_hit` (commit `b70d9bd`), 16/16 acceptance + 43 regression pass, moved sang `to-workbuddy/` | 2026-11-27 22:30 |
| `2026-11-27-002-tranche3-blind-and-grandfathered` | 2 | answered | workbuddy nghiệm thu C-4 + C-6 (commit `03a931a`, 19/19 board + 389/389 repo pass) trong `to-workbuddy/` | 2026-11-27 23:33 |

## Đang chờ người quyết

| việc | vì sao | từ khi |
|---|---|---|
| Cấp key `muse-manager` + worker (§15 Q4 của spec) | registry không chạy được nếu chưa có key | 2026-11-27 |
| Ai làm C-7 (tier review) | C-4 và C-6 đã giao ở thread `002`. C-7 chưa giao vì còn phụ thuộc câu hỏi ngay dưới | 2026-11-27 |
| C-7: tier B/C có được **xanh** với 1 pass (hoặc 0) không | Đây là đánh đổi chất lượng, không phải chi tiết kỹ thuật. Giảm tải review (1155 lượt) thì phải hạ nghĩa của cờ xanh; giữ nguyên nghĩa thì không giảm được tải. Ba phương án ở `plans/team/tranche3_c4_c6_c7_design.md` §3, tôi đề xuất phương án 3 (thêm trạng thái `pending_review`, không đổi cổng xanh, không phải sửa test nào). Chốt xong mới giao C-7 | 2026-11-27 23:28 |
