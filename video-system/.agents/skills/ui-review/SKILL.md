---
name: ui-review
description: Rà soát giao diện video-system (và dashboard nói chung) theo DESIGN.md khóa — chống AI-slop, kiểm thẩm mỹ + thân thiện người dùng. Dùng khi tạo/sửa frontend/index.html hoặc bất kỳ màn hình nào của hệ thống Item.
---

# UI Review Skill — video-system

> Tổng hợp từ `frontend-design-deslop` (samber/cc-skills v1.2.2, MIT),
> `hallmark` (Nutlope/hallmark, MIT — chế độ audit), và chuẩn dashboard từ
> awesome-design-md (Linear reference). Theme KHÓA DUY NHẤT: không xoay.

## Khi nào dùng

- Trước khi ship bất kỳ thay đổi nào của `frontend/index.html`.
- Khi thêm tab/màn hình mới cho hệ thống Item.
- Khi cảm thấy UI "được gen ra" thay vì "được làm ra".

## Quy trình audit (5 cửa, tất cả phải đậu)

### Cửa 1 — DESIGN.md là luật (đọc `config/DESIGN.md` trước)

- Token đúng: màu từ bảng token, IBM Plex Sans/Mono, KHÔNG Inter/Roboto/
  system-ui làm font chính, radius ≤10px, 1 kiểu shadow duy nhất.
- Cấm tuyệt đối (anti-slop): gradient tím/indigo, gradient text, glassmorphism,
  icon-tile-trên-heading, eyebrow trên mọi heading, 3-card grid đối xứng,
  hairline+diffuse shadow chồng nhau, bounce easing, pulsing dot trên nội dung
  không live, emoji thay icon.

### Cửa 2 — Số và dashboard-first

- Mọi số dùng `font-variant-numeric: tabular-nums` (IBM Plex Mono): progress,
  timecode, duration, đếm shot.
- Mọi chỉ số ĐỘNG từ API. Có bất kỳ chuỗi hardcode kiểu "0 lỗi", "✓ Khớp"
  → FAIL ngay (di chứng `07_export.py:944-970` của repo cũ).
- Mật độ dashboard: bảng dày, khoảng trắng có mục đích; landing-page air là sai.

### Cửa 3 — Held là công dân hạng nhất (signature của hệ thống)

- Shot held: màu riêng `--held` + icon + **lý do tiếng người** từ
  `HELD_LABELS_VI` (không hiện enum thô một mình) + nút hành động ngay tại chỗ
  (Retry từ keyframe / Giữ lại).
- Held gap trên timeline tích lũy: tô đỏ + chú thích, KHÔNG phải khoảng trống
  im lặng khó hiểu.
- Lỗi provider/quota: hiện lỗi gì, tài khoản nào, bao giờ retry
  (`next_retry_at`), shot nào ảnh hưởng. Cấm "Đã có lỗi xảy ra" chung chung.

### Cửa 4 — Thân thiện người dùng (hallmark floor)

- Floor — không bao giờ ship: contrast AA, focus visible, keyboard được,
  `prefers-reduced-motion` tôn trọng, target ≥24px, không chỉ dựa vào màu
  (held có icon+text, không chỉ nền đỏ).
- Empty state có hướng dẫn hành động ("dùng POST /items/{id}/shots/import…"),
  không để trống trơn.
- Lỗi hiển thị tiếng người + nút, không mã lỗi thô.
- Motion ≤1 transition 150ms ease-out mỗi tương tác, chỉ transform/opacity.

### Cửa 5 — Bare-structure check (deslop tự-audit)

Che CSS đi, đọc DOM: layout còn đúng chức năng dashboard không (bảng có header,
trạng thái đọc được, hành động tìm được)? Nếu chỉ đẹp khi có CSS mà cấu trúc
vô nghĩa → redesign cấu trúc trước, trang trí sau.

## Báo cáo bắt buộc

Kết thúc audit, xuất bảng: `Cửa | Đậu/Trượt | Bằng chứng (selector/dòng) | Fix đề xuất`.
Mọi thất bại Cửa 1-3 là blocker; Cửa 4-5 blocker nếu là floor item.

## Tham chiếu ngoài (khi cần đào sâu)

- samber/cc-skills `frontend-design-deslop` — strategy-first, token table, slop checklist.
- Nutlope/hallmark — floor/reflex/finish gate tiers, `sloplint` mindset.
- awesome-design-md — tham chiếu token Linear/Stripe (lấy nhịp, không lấy vỏ).
