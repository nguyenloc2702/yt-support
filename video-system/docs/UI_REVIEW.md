# UI Review — video-system/frontend/index.html

- Audit theo: `.agents/skills/ui-review/SKILL.md` (5 cửa) × `config/DESIGN.md`

## Audit lần 2 — 2026-11-10 (file dựng lại 2026-11-06, 6.578+ dòng)

> Audit lần 1 (2026-11-05) bên dưới đã lỗi thời về line-ref (file đã được dựng
> lại 6.578 dòng ngay sau audit). Lần 2 audit lại trực tiếp file hiện tại;
> blocker cũ được kiểm tra lại từng điểm.

| Cửa | Kết quả | Bằng chứng | Fix đã áp dụng (2026-11-10) | Mức |
|---|---|---|---|---|
| 1 — Token DESIGN.md | **ĐẬU** | Toàn bộ token `:root` khớp DESIGN.md; IBM Plex Sans/Mono; radius 6/10; 1 shadow; không gradient/glassmorphism. Di sản: emoji tiền tố nhãn (part of text) — giữ; emoji **thay icon thật** đã chuyển | `lock-badge` 🔒 → inline SVG padlock; toast bỏ icon emoji; status-banner icon → `.status-dot` màu token (held/run/ok) | non-blocker (di sản) |
| 2 — Số tabular + API-first | **ĐẬU** | `body` + `.mono/.num` tabular-nums; mọi số từ `/api/v1/todos` + `/api/v1/shorts`; không chuỗi "0 lỗi" hardcode | — | — |
| 3 — Held first-class + nhãn VI | **ĐẬU** (sau fix) | Trước fix: `renderTodoCard` render `pill-${item.status}` với fallback **enum thô** cho `held_review/generating/analyzing/…` (cùng nhóm lỗi audit lần 1); feedback pill hiện `fb.status` thô | `STATUS_LABELS` đủ 15 trạng thái Item VI; `statusPillClass()` — held/failed/cancelled → `--held` (pill-failed, không chỉ màu: có nhãn VI); `FEEDBACK_STATUS_LABELS`; Short Studio dùng `status_label` từ API (`SHORT_LABELS_VI` backend) | blocker (đã xong) |
| 4 — a11y floor | **ĐẦU** (sau fix) | (a) Card click không keyboard (`todo-summary-card`, `detail-flow-jump-card`, `img-card`, brand) → thêm `role="button" tabindex=0 data-kb-click` + delegate Enter/Space toàn document; (b) **thiếu `prefers-reduced-motion`** trên file mới → thêm media query; (c) `.btn-xs` ~21px < 24px floor → `min-height:24px`; (d) focus-visible → outline accent cho button/[role=button]/input/summary; (e) `seg-tab` thêm `aria-pressed` | Khối CSS "A11y Floor (DESIGN.md §5)" + JS keydown delegate | **blocker** (đã xong) |
| 5 — Bare-structure | **ĐẬU** | DOM là dashboard khi che CSS; **không còn poller `setInterval`** → lỗi race poller của lần 1 không tồn tại trên file hiện tại | — | — |

### Trang Short Studio mới (Phụ lục G) — dựng theo 5 cửa ngay từ đầu

- Nhãn trạng thái qua `status_label` (API) + map pill; empty-state có hướng dẫn
  hành động (dán tin / sync RSS / tạo kịch bản đầu tiên); lỗi tiếng người
  (toast + banner `error_detail` + nút "Thử lại" tại chỗ khi produce fail).
- Timeline scene từ audio thật (mốc giây tabular), ảnh scene lazy, khung điện
  thoại 9:16 honest (CHƯA CHỌN SHORT / trạng thái chữ, không spinner giả).
- Bằng chứng: `docs/screenshots/03_short_detail_ready.png`,
  `04_short_detail_scenes.png`, frame video `short_frame_karaoke_v2.png`.

## Bug đã biết (lần 1) — trạng thái hiện tại

- `held_count` "—": **ĐÃ FIX SẴN** tại `backend/app/service_items.py:72-75`
  (`recompute_from_shots` ghi `held_count`). Đóng.
- Timeline trống (Cửa 3 lần 1): UI hiện tại (todo-flow) không render timeline
  tích lũy đó — đóng theo trạng thái hiện tại; timeline scene của Short Studio
  làm đúng luật (tô đủ + nhãn + số tabular).

---

## ARCHIVE — Audit lần 1 (2026-11-05, file ~200 dòng trước khi dựng lại)

| Cửa | Kết quả | Bằng chứng | Fix đề xuất | Mức |
|---|---|---|---|---|
| 1 — Token DESIGN.md | **ĐẬU** (có 1 ngoại lệ nhỏ) | `index.html:11-15` khai báo đúng toàn bộ token màu (`--bg #FAFAF9 … --held #DC2626`); IBM Plex Sans/Mono (`:7-8, :18-19`); radius 6/10 (`:25, :32, :40`); đúng 1 shadow `0 1px 2px rgba(0,0,0,.05)` (`:26`). Anti-slop: ảnh 1-2 không có gradient tím, gradient text, glassmorphism, icon-tile, eyebrow, 3-card grid. Ngoại lệ: map trạng thái → **nhãn VI** trong DESIGN.md §3 không được dùng — `pill()` ở `index.html:97` render enum thô (`held_review`, `keyframe_pending`…), tức là nửa "màu" của §3 conform, nửa "label VI" thì không (đếm ở Cửa 4). | Thêm map `STATUS_LABELS_VI` theo §3 và render `pill()` bằng nhãn VI. | non-blocker (label) |
| 2 — Số tabular + API-first | **ĐẬU** | `index.html:19` `.mono,.num{font-family:"IBM Plex Mono";font-variant-numeric:tabular-nums}` áp cho tiến độ `0/4 shot · 0.0s` (ảnh 1), audio timecode `0.0→8.0` (ảnh 2), cột `td.num,th.num` căn phải (`:31`). Không thấy chuỗi hardcode kiểu "0 lỗi"/"✓ Khớp": tiến độ từ `/cumulative` (`:139, :171`), held count từ filter shots (`:141`), segments từ API (`:144`). | — | — |
| 3 — Held first-class + Timeline | **TRƯỢT** (timeline) | Đậu phần lý do + nút: ảnh 2 có `heldbox` "Keyframe sai style" (`HELD_LABELS` tại `index.html:76-84`, render `:156-157`) + nút "Retry từ keyframe" tại hàng (`:158`) + hộp tổng "1 shot đang held / Retry tất cả held" (`:176-178`). **Trượt timeline**: `index.html:145-148` tính width `Math.max(w/10, 2)px` — với segment 8s chỉ ra **0.8px → kẹp 2px**, cả 4 shot (~32s audio) chỉ chiếm ~8px trong box ~800px → ảnh 2 timeline gần như **trống rỗng**, khoảng held chỉ là vệt đỏ ~2px không nhìn ra, không có chú thích hiển thị (chỉ `title` tooltip — vi phạm DESIGN.md §4 "tô đỏ + ghi chú, KHÔNG khoảng trống im lặng"), và không có click-jump tới shot. | Scale segment theo tổng duration: `width = (w/dur)*100%` thay vì px cố định; segment held thêm label/giá trị hiển thị; thêm onclick jump `detailView` scroll tới hàng shot. | **blocker** |
| 4 — a11y + tiếng người | **TRƯỢT** | (a) **Enum thô**: ảnh 1 và 2 hiện nguyên văn `held_review`, `keyframe_pending`, `keyframe_approved`, `held` trong pill (`index.html:97` không map nhãn) — đây KHÔNG phải tiếng người; "held" trong `1 held` (`:171`) cũng là tiếng Anh. (b) **Keyboard**: hàng bảng click được nhưng không có `tabindex`/`role`/handler phím (`:103`) — floor "keyboard được" trượt với thao tác mở Item. (c) **Lỗi thô**: `createItem` → `alert('Tạo thất bại: '+r.status)` (`:131`) lộ mã HTTP; `retryShot/retryAllHeld` (`:192-200`) không bắt lỗi, im lặng khi fail; `detailView` không xử lý fetch lỗi → trang trắng. Đậu: focus-visible (`:43`), `prefers-reduced-motion` (`:61`), button min-height 24 (`:40`), held không dựa màu đơn thuần (icon-viền + text VI + nền `#FEF2F2`), empty state có hướng dẫn hành động (`:118, :122, :186`). | (a) map nhãn VI §3 cho mọi pill; (b) `tabindex=0` + Enter/Space trên hàng click; (c) gói lỗi thành thông điệp VI + nút, thêm try/catch cho retry và detailView. | **blocker** (a, c là floor; b floor) |
| 5 — Bare-structure / nội dung nhân đôi | **ĐẬU** (artifact) | Ảnh 2 nội dung bị nhân đôi dọc + ngang. **Kết luận: artifact của full-page screenshot, không phải lỗi DOM.** Code: `detailView` gán `app.innerHTML=...` (`:163`) — replace toàn bộ, không thể chồng lớp; `setInterval` 5s gọi lại `detailView` (`:207`) chỉ ghi đè, không append. Body không có overflow ẩn; `main max-width:1200px` (`:24`). Ảnh 1280px rộng nhưng layout bị cắt ~1140px rồi lặp lại bên phải + lặp dọc = máy chụp stitch theo viewport thấp hơn kích thước render, đúng hành vi screenshot tool. **Tuy nhiên poller có lỗi thật kế phát**: 2 lần `detailView` chạy chồng nhau (fetch async 4 request/lần) có thể ghi phản hồi cũ đè phản hồi mới (race), và re-render mỗi 5s xoá selection/scroll của user đang đọc. | Giữ poller nhưng thêm cờ `inFlight` (bỏ qua lần gọi mới nếu lần trước chưa xong) + so sánh `updated_at`/etag trước khi ghi `innerHTML`, và không poll khi `document.hidden`. | non-blocker |

### Bug lần 1: cột Held = "—" dù status `held_review`

**Nguyên nhân (đã fix trong code hiện tại):** `recompute_from_shots` đếm held
chỉ để đổi status, không ghi `held_count` → fix 1 dòng
`UPDATE items SET held_count=?` tại `backend/app/service_items.py:72-75`.

### Kết luận lần 1

- Blocker: (1) timeline tích lũy gần như trống; (2) enum thô + lỗi thô + hàng
  bảng không keyboard-accessible.
- Non-blocker: nhãn VI cho pill, poller race, bug `held_count` (đã fix).
- Cửa 1, 2, 5 đậu về token/số/cấu trúc.
