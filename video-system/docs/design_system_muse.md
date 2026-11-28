# Design system — Dark MUSE (Phase 2)

Nguồn: 4 mockup owner gửi 2026-11-28 (MUSE Shotboard, MUSE Studio, Muse Orchestrator, Analytics).
Mọi agent làm Phase 2 đọc file này trước khi sửa CSS/JS. Không tự đặt palette khác.

## 1. Token (đã có trong `frontend/css/theme.css` — giữ tên biến, chỉ tinh chỉnh giá trị)

| token | giá trị | dùng cho |
|---|---|---|
| `--bg` | `#0B1220` | nền trang |
| `--surface` | `#111A2C` | panel/card chính |
| `--surface-muted` | `#0E1626` | panel lồng, header bảng |
| `--surface-2` (thêm nếu cần) | `#16223A` | card hover / elevated |
| `--fg` | `#E6EDF6` | chữ chính |
| `--fg-muted` | `#8CA0B8` | chữ phụ, label |
| `--border` | `#1E2A42` | viền 1px mặc định |
| `--border-strong` | `#2A3A58` | viền card nhấn |
| `--accent` | `#2DD4BF` | teal chính (mockup dùng #2DD4BF, không phải #14B8A6) |
| `--accent-soft` | `rgba(45,212,191,.12)` | nền pill/active |
| `--ok` | `#22C55E` · soft `rgba(34,197,94,.15)` | pass/duyệt |
| `--run` | `#F59E0B` · soft `rgba(245,158,11,.15)` | đang chạy/chờ |
| `--held` | `#EF4444` · soft `rgba(239,68,68,.15)` | lỗi/từ chối |
| `--r` | `10px` | control nhỏ |
| `--r-lg` | `14px` | card/panel |
| `--shadow` | `0 1px 3px rgba(0,0,0,.4)` | card |

Quy ước bổ sung:
- Chữ trên nền teal **phải là màu tối** `#062A28` (mockup dùng chữ navy đậm trên nút teal). Trắng trên `#2DD4BF` chỉ đạt ~2.4:1 — cấm.
- Không dùng `#FFFFFF` làm nền bất kỳ khối nào. Nền sáng duy nhất được phép: avatar/logo.
- Số liệu (duration, score, count) dùng `font-family: var(--font-mono)` + `font-variant-numeric: tabular-nums`.

## 2. Hình khối

- Card: `background: var(--surface)`, `border: 1px solid var(--border)`, `border-radius: var(--r-lg)`, padding 14–16px. Hover: `border-color: var(--border-strong)`, `background: var(--surface-2)`.
- Panel header: label nhỏ in hoa 11px, letter-spacing .04em, màu `--fg-muted`, kèm icon 14px.
- Pill/badge: `border-radius: 999px`, padding `3px 9px`, font 11–12px, `background: <màu>-soft`, `color: <màu>`, không viền hoặc viền cùng màu 30% opacity.
- Nút primary: nền `--accent`, chữ `#062A28`, font-weight 600, radius `--r`. Hover: `filter: brightness(1.08)`.
- Nút secondary/ghost: nền trong suốt, viền `--border-strong`, chữ `--fg`.
- Nút destructive: viền `--held`, chữ `--held`, nền `--held-soft`.
- Bảng: header `--surface-muted`, chữ 11px in hoa muted; row viền dưới `--border`; row hover `--surface-2`.
- Input/select/textarea: nền `--surface-muted`, viền `--border`, radius `--r`, focus `border-color: var(--accent)` + `box-shadow: 0 0 0 3px var(--accent-soft)`.
- Scrollbar: thumb `#24344F`, track trong suốt.
- Progress bar: track `rgba(255,255,255,.08)`, fill `--accent`, radius 999px, cao 6px.

## 3. Bố cục shell

- Topbar: cao 56px, nền `rgba(11,18,32,.92)` + `backdrop-filter: blur(8px)`, viền dưới `--border`. Brand mark: ô 32px radius 10px, **nền gradient teal** `linear-gradient(135deg,#2DD4BF,#0EA5A4)`, chữ `#062A28` — không dùng nền sáng `--fg` như hiện tại.
- Nav item: chữ 13px `--fg-muted`, active `--fg` + gạch chân 2px `--accent` (giống mockup Shotboard) hoặc pill `--accent-soft`.
- KPI strip: 4 card cùng hàng, mỗi card có label in hoa 11px + số lớn 24–28px + sub 12px muted. Số lớn dùng mono/tabular. Viền trái 3px màu trạng thái khi card biểu diễn trạng thái.
- Khoảng cách: section cách nhau 16px, trong card 12px, gutter lưới 12px.

## 4. Trạng thái (map màu cố định, không tự đổi)

| trạng thái | màu | ghi chú |
|---|---|---|
| pass / duyệt / xong / ready | `--ok` | pill xanh |
| đang gen / đang chạy / chờ ráp | `--run` | pill amber |
| lỗi / từ chối / thiếu | `--held` | pill đỏ |
| chưa bắt đầu / trống | `--fg-muted` | pill xám |
| nhấn / active / link | `--accent` | teal |

## 5. Việc cấm

- Không đổi tên class, id, hay cấu trúc DOM — JS render bằng string template, đổi tên là gãy.
- Không sửa `frontend/index.html` (file cũ, đã archive).
- Không thêm build step, không thêm thư viện CSS ngoài (giữ plain CSS).
- Không hardcode màu mới ngoài bảng token; nếu buộc phải dùng, thêm biến vào `:root`.
- Không dùng `!important` để đè — sửa đúng selector.

## 6. Nghiệm thu Phase 2

1. `node --check` từng file JS.
2. `pytest tests/ -q` không giảm số pass so với baseline 403 passed / 56 skipped.
3. `tools/ui_smoke.py` trên 6 trang: 0 console error, 0 pageerror, 0 request 4xx/5xx,
   **0 surface sáng** (trừ avatar/logo), **0 text contrast < 3.0**.
4. Screenshot 6 trang đối chiếu mockup: nền tối liền mạch, teal là accent duy nhất, card bo tròn có viền, pill màu đúng bảng trạng thái.
