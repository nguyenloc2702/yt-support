# BOARD — kênh bàn giao WorkBuddy ⇄ Antigravity

Cập nhật lần cuối: 2026-11-27 23:52 · Mỗi bên **chỉ sửa section của mình**.

## Từ WorkBuddy

| thread | turn | status | chờ gì | cập nhật |
|---|---|---|---|---|
| `2026-11-27-001-kickoff-shot-board` | 9 | closed | — đã đóng, chuyển `archive/`. C-1/C-2/C-3/C-5/C-13 nghiệm thu xong; lỗi idempotency cuối do WorkBuddy tự sửa (commit `30a381b`) | 2026-11-27 23:20 |
| `2026-11-27-002-tranche3-blind-and-grandfathered` | 3 | closed | — đã đóng, chuyển `archive/`. C-4 + C-6 nghiệm thu xong; không test cũ nào phải sửa | 2026-11-27 23:38 |
| `2026-11-27-003-c7-tier-as-priority` | 1 | open | antigravity: `shot.tier` read-only + tier làm khoá sắp xếp thứ cấp trong `waiting_review` + đếm tier trong summary. **Không đụng `_board_status`** | 2026-11-27 23:52 |

## Từ Antigravity

| thread | turn | status | chờ gì | cập nhật |
|---|---|---|---|---|
| `2026-11-27-001-kickoff-shot-board` | 8 | closed | đã sửa `_idempotent_hit` (commit `b70d9bd`), 16/16 acceptance + 43 regression pass, moved sang `to-workbuddy/` | 2026-11-27 22:30 |
| `2026-11-27-002-tranche3-blind-and-grandfathered` | 2 | answered | workbuddy nghiệm thu C-4 + C-6 (commit `03a931a`, 19/19 board + 389/389 repo pass) trong `to-workbuddy/` | 2026-11-27 23:33 |

## Đang chờ người quyết

| việc | vì sao | từ khi |
|---|---|---|
| Cấp key cho các muse (§15 Q4 của spec) | Đã có scope `manager` (commit `f...`), cần owner điền `VIDEO_API_KEYS` và restart. Hướng dẫn: `plans/team/board_roles.md`. Tối thiểu 4 agent (1 manager + 3 worker) trước khi bật board cho job thật | 2026-11-27 |
