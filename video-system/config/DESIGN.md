# DESIGN.md — UI Style Lock (nguồn duy nhất cho giao diện)

> Tham chiếu cấu trúc: Linear (grid dày, sidebar, bảng số). KHÔNG copy font/vỏ.
> Không xoay theme. Mọi thành phần UI phải conform file này; skill `ui-review`
> dùng file này làm checklist audit.

## 1. Nền tảng

- **Loại artifact:** dashboard/pro tool — mật độ CAO là đúng, khoảng trắng lan man là sai.
- **Signature move:** hàng Held là công dân hạng nhất — luôn hiển thị với lý do
  tiếng người + nút hành động ngay tại chỗ, không bao giờ ẩn sau tooltip.
- **Cấm badge/progress hardcode:** mọi số hiển thị phải đến từ API. UI render
  chuỗi "0 lỗi" tĩnh = bug.

## 2. Token

### Font
- UI: **IBM Plex Sans** (400/500/600)
- Số/timecode/duration: **IBM Plex Mono** + `font-variant-numeric: tabular-nums`
- CẤM làm font chính: Inter, Roboto, Arial, system-ui.

### Màu (light theme duy nhất cho v1)
| Token | Giá trị | Dùng |
|---|---|---|
| --bg | #FAFAF9 | nền trang |
| --surface | #FFFFFF | card, bảng |
| --fg | #1C1917 | chữ chính |
| --fg-muted | #78716C | chữ phụ |
| --border | #E7E5E4 | đường kẻ |
| --accent | #2563EB | hành động chính |
| --accent-fg | #FFFFFF | chữ trên accent |
| --ok | #16A34A | appended / done |
| --run | #D97706 | generating / running |
| --held | #DC2626 | held — màu ĐỘC QUYỀN của held |
| --ghost | #78716C | superseded |

### Spacing & shape
- Base unit 4px. Trong nhóm: 8px; giữa section: 24px.
- Radius: 6px (control), 10px (card). Không radius >10px.
- Shadow: 1 kiểu duy nhất — border 1px + shadow `0 1px 2px rgba(0,0,0,.05)`.
  KHÔNG hairline + diffuse shadow chồng nhau. KHÔNG glassmorphism, KHÔNG
  gradient tím, KHÔNG gradient text.

## 3. Trạng thái shot → màu (map 1-1 API)

| API status | Màu | Label VI |
|---|---|---|
| pending | ghost | Chờ |
| keyframe_pending | ghost | Chờ keyframe |
| keyframe_approved | accent | Đã duyệt KF |
| generating | run | Đang gen |
| generated | run | Đã gen |
| qc_passed | ok-nhạt (border ok) | Đạt — chờ duyệt batch |
| appended | ok | Đã nối |
| held | held | Giữ lỗi |
| failed | held | Hỏng |
| retry_later | run | Hẹn retry |
| superseded | ghost | Bị thay |

## 4. Bắt buộc chuyên môn (domain)

- Số dùng `tabular-nums`: progress `33/169`, duration `194.4s / 900.0s`, timecode.
- Progress theo AUDIO: `shots appended/total • seconds appended/total • held count`.
- Waveform + word-highlight là hero của tab Kịch bản & Audio (v1: placeholder
  waveform phải honest — hiện "chưa có audio preview", không vẽ giả).
- Timeline tích lũy: đoạn appended tô `--ok`, held gap tô `--held` + ghi chú
  enum → tiếng người, click-jump tới shot.
- Lỗi provider/quota: hiện lỗi gì, tài khoản nào, bao giờ retry (`next_retry_at`),
  shot nào ảnh hưởng. Cấm "Đã có lỗi xảy ra" chung chung.
- Empty state có hướng dẫn hành động. Lỗi là tiếng người + nút, không mã thô.

## 5. Motion & a11y

- ≤1 transition 150ms ease-out mỗi tương tác; chỉ transform/opacity;
  tôn trọng `prefers-reduced-motion`.
- WCAG 2.2 AA: focus visible, keyboard được, target ≥24px, không chỉ dựa màu
  (held có icon + text, không chỉ nền đỏ).
- Bảng: text căn trái, số căn phải tabular. Header sticky.

## 6. Self-audit checklist (dùng trước khi ship UI)

1. Nhìn "lần đầu": có chi tiết nào mặc định AI không (gradient tím, 3-card grid,
   icon-tile, eyebrow trên mọi heading)? → xóa.
2. Mọi số có tabular không?
3. Held có lý do tiếng người + nút hành động không?
4. Có chỉ số nào hardcode không?
5. Bare-structure check: che CSS đi, layout còn đúng chức năng dashboard không?
