# _archive/ — Khu tiêu hủy có kiểm soát

> **Ngày rà tiêu hủy: 2027-03-01.** Đến ngày này, toàn bộ nội dung dưới đây
> phải được DỌC HẾT (xóa trắng hoặc xác nhận tái định danh). Archive không có
> ngày chết là bãi rác tái xuất với tên đẹp hơn.

Đã dọn 2026-11-21 trong buổi họp 3 bên PO–BA–Dev. Nội dung:

| Thư mục / file | Gốc | Bản chất | Chú ý |
|---|---|---|---|
| `2026-11-21/automated_runs/` | `data/automated_runs` (2.9G) | Output factory đã upload GDrive | Factory đã retire; DB `data/factory_jobs.sqlite3` vẫn giữ ở chỗ cũ |
| `2026-11-21/products/` | `products/` (2.4G) | Final renders đã giao theo sản phẩm | Không phải dữ liệu nguồn |
| `2026-11-21/bgm_finds/` | `bgm_finds/` (2.1G) | Kho BGM candidates | Chưa chọn — chọn thì copy vào `assets/music/bgm/` trước khi tiêu hủy |
| `2026-11-21/scratch/` | `scratch/` (1G) | Scratch thử nghiệm | |
| `2026-11-21/verification_artifacts/` | `verification_artifacts/` (385M) | Artifact chạy E2E | |
| `2026-11-21/tmp_digest/`, `tmp_tiktok_ref/` | root (175M) | Frame cache intermediate | |
| `2026-11-21/job_dario_amodei_gigachad/` | `plans/` | Payload artifacts one-off | |
| `2026-11-21/demo_finventure_hook60/` | **KHÔNG di chuyển được** — file bị process khóa (Permission denied) | Demo one-off | Đóng process rồi `mv` vào đây |
| `docs_history/` | 10 MD root | Docs chết / lịch sử (factory, upgrade log, spec cũ) | Git history vẫn giữ bản tracked |

Quy tắc sau dọn: thư mục này **không bao giờ được import từ code sống**
(đã verify grep 2026-11-21); thêm vào `.gitignore`; mọi thứ ngoài bảng trên
rơi vào đây phải cập nhật bảng này.
