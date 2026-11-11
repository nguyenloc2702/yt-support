# Stitch UI Fix Map — Sửa đúng chỗ để đạt 9.5+/10

> Dùng tài liệu này cùng bundle tại:
> `C:\Users\admin\Downloads\stitch_video_production_workflow_dashboard\stitch_video_production_workflow_dashboard`
>
> Mục tiêu: **harden bundle hiện tại**, không gen lại dashboard khác, không đổi business logic, không đổi API contract. Chỉ sửa UI, copy, token, responsive, accessibility và consistency.
>
> Nguồn luật duy nhất của project là [`config/DESIGN.md`](../config/DESIGN.md:1). Nếu file `studio_production_engine/DESIGN.md` trong bundle Stitch mâu thuẫn với file này, phải ưu tiên [`config/DESIGN.md`](../config/DESIGN.md:1).

---

## Prompt gửi nguyên văn cho Stitch

Bạn phải sửa trực tiếp bundle UI hiện tại tại thư mục được cung cấp. Không tạo một dashboard mới, không đổi layout thành landing page, không đổi nghiệp vụ, không đổi API contract. Hãy thực hiện đúng từng mục trong file này và trả lại toàn bộ các file HTML đã sửa.

Mục tiêu chất lượng cuối: **ít nhất 9.5/10** theo design lock của repository. Không được tự đánh giá đạt nếu còn bất kỳ P0 blocker nào.

---

# 1. Danh sách file bắt buộc phải sửa

Sửa các file sau trong bundle Stitch:

1. `danh_s_ch_c_ng_vi_c_s_n_xu_t_video/code.html`
2. `danh_s_ch_c_ng_vi_c_mobile/code.html`
3. `chi_ti_t_c_ng_vi_c_teaser_ra_m_t_s_n_ph_m_v2/code.html`
4. `chi_ti_t_c_ng_vi_c_mobile/code.html`
5. `short_studio_bi_n_t_p_s_n_xu_t_video_ng_n_9_16/code.html`
6. `short_studio_mobile/code.html`
7. `video_th_nh_ph_m_th_vi_n_t_li_u_tham_chi_u/code.html`
8. `video_th_nh_ph_m_th_vi_n_mobile/code.html`
9. `studio_production_engine/DESIGN.md`

Nếu có file khác chứa cùng pattern, áp dụng cùng quy tắc. Không chỉ sửa một screen mẫu rồi bỏ các screen còn lại.

---

# 2. P0 — Token và typography: phải sửa toàn bộ

## 2.1. Xóa palette Stitch cũ

Trong tất cả `tailwind.config` và style token, xóa hoặc thay các giá trị sau:

```text
#fff8f5
#faf2ee
#f4ece8
#eee7e3
#e9e1dd
#1e1b19
#434655
#c3c6d7
#004ac6
#904d00
#006329
#ba1a1a
#ffdad6
```

Thay bằng đúng token:

```css
--bg: #FAFAF9;
--surface: #FFFFFF;
--fg: #1C1917;
--fg-muted: #78716C;
--border: #E7E5E4;
--accent: #2563EB;
--accent-fg: #FFFFFF;
--ok: #16A34A;
--run: #D97706;
--held: #DC2626;
--ghost: #78716C;
```

Không để palette cũ tồn tại trong frontmatter, Tailwind config, inline style hoặc class arbitrary color.

## 2.2. Thay Space Mono

Tìm và thay mọi:

```text
Space Mono
```

bằng:

```text
IBM Plex Mono
```

Áp dụng trong:

- Google Fonts URL.
- Tailwind `fontFamily`.
- CSS `.font-mono-tech`.
- `data-mono-sm`, `data-mono-md`, `data-mono-lg`.
- Inline fallback.

Font URL phải có cả IBM Plex Sans và IBM Plex Mono. Không giữ `Space Mono` như fallback.

## 2.3. Bổ sung tabular numerals

Mọi class dùng cho counter, count, progress, duration, timecode, IDs, FPS, bitrate, quota và percentage phải có:

```css
font-variant-numeric: tabular-nums;
```

Có thể tạo class dùng chung:

```css
.font-mono-tabular {
  font-family: 'IBM Plex Mono', monospace;
  font-variant-numeric: tabular-nums;
}
```

## 2.4. Sửa design file Stitch

Trong `studio_production_engine/DESIGN.md`:

- Thay toàn bộ frontmatter palette cũ bằng palette repository.
- Thay `Space Mono` bằng `IBM Plex Mono`.
- Xóa các token `rounded.full: 9999px` khỏi hướng dẫn sử dụng chính.
- Ghi rõ `config/DESIGN.md` là source of truth.
- Không để prose nói token repository nhưng frontmatter dùng token khác.

---

# 3. P0 — Mobile viewport và layout: sửa đúng từng file

## 3.1. Sửa viewport ở 4 file mobile

Trong các file:

- `danh_s_ch_c_ng_vi_c_mobile/code.html`
- `chi_ti_t_c_ng_vi_c_mobile/code.html`
- `short_studio_mobile/code.html`
- `video_th_nh_ph_m_th_vi_n_mobile/code.html`

Thay:

```html
<meta content="width=390, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" name="viewport"/>
```

bằng:

```html
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
```

Không được dùng lại `maximum-scale` hoặc `user-scalable=no`.

## 3.2. Xóa mock system status bar

Trong `danh_s_ch_c_ng_vi_c_mobile/code.html`, xóa block hiển thị:

```text
09:41
5G
100%
```

Trong các mobile screen khác, nếu có system status bar giả, cũng xóa. Web app chỉ hiển thị app header thật.

## 3.3. Xóa mobile shell giả trong production layout

Không dùng:

```text
max-w-[390px]
border-x
shadow-sm
```

để giả lập điện thoại trong layout production. Thay bằng:

```html
<body class="min-h-screen bg-[#FAFAF9]">
```

Mobile screenshot vẫn có thể dùng viewport 390px, nhưng HTML không được tự vẽ khung điện thoại.

## 3.4. Fixed action không được che nội dung

Với bottom navigation hoặc fixed action bar:

- Thêm padding-bottom cho main bằng chiều cao thực tế của bar + 16px.
- Không đặt hai fixed bar chồng lên nhau.
- Không để FAB che primary action hoặc card cuối.
- Không dùng `max-w-[390px]` cho fixed bar.
- Đảm bảo focus keyboard không bị che.

---

# 4. P0 — Shape, shadow và glassmorphism

## 4.1. Xóa `rounded-full` sai mục đích

Tìm toàn bundle và thay `rounded-full` trong các loại sau:

- Primary button.
- Provider alert/banner.
- Toolbar.
- Table/data container.
- Status badge lớn.
- FAB tạo job.
- Tab container.
- Navigation container.

Quy tắc thay:

- Control/input/button: `rounded-[6px]`.
- Card/panel/modal/data container: `rounded-[10px]`.
- Badge nhỏ: `rounded-[4px]` hoặc `rounded-[6px]`.
- Chỉ giữ hình tròn cho avatar, dot trạng thái hoặc play button thật sự cần hình tròn.

Progress bar có thể dùng `rounded-[2px]`; không dùng pill styling cho toàn container.

## 4.2. Xóa `backdrop-blur`

Tìm và xóa:

```text
backdrop-blur-sm
backdrop-blur-md
backdrop-blur
```

Thay bằng nền đặc và border:

```html
bg-white border border-[#E7E5E4]
```

Áp dụng chắc chắn cho:

- `chi_ti_t_c_ng_vi_c_mobile/code.html` header và bottom bar.
- Video overlays nếu không cần blur thật sự.

## 4.3. Chuẩn hóa shadow

Chỉ dùng:

```css
box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
```

Hoặc đúng một biến tương đương:

```css
--shadow-technical: 0 1px 2px rgba(0, 0, 0, 0.05);
```

Xóa `shadow-md`, `shadow-lg`, shadow diffuse và các shadow khác nếu không phải control đặc biệt cần nổi.

---

# 5. P0 — Motion: xóa các pattern gây fail

Tìm toàn bộ bundle và xử lý:

## 5.1. Xóa animation trạng thái

Xóa:

```text
animate-pulse
animate-ping
```

Không làm dot “nhấp nháy” cho trạng thái render, held, syncing hoặc online nếu trạng thái đó không phải live telemetry thực sự.

Thay bằng dot tĩnh + icon + text.

## 5.2. Xóa transition sai

Thay mọi:

```text
transition-all
```

bằng transition cụ thể, hoặc bỏ transition:

```text
transition-colors duration-150 ease-out
```

Tuy nhiên theo design lock, chỉ cho phép animate `transform` hoặc `opacity`. Vì vậy ưu tiên:

- Hover màu: thay bằng state CSS tức thời nếu có thể.
- Focus: border/outline tức thời.
- Motion thật: `transition-opacity duration-150 ease-out` hoặc `transition-transform duration-150 ease-out`.

Xóa `duration-300`, `duration-500` và các duration dài.

## 5.3. Xóa scale interaction

Xóa:

```text
active:scale-95
active:scale-[0.99]
hover:scale-105
```

Không dùng scale để giả feedback. Dùng focus-visible, border hoặc background state.

## 5.4. Thêm reduced motion

Mỗi file cần có:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

---

# 6. P0 — Copy Held và Provider error

## 6.1. Job list desktop

Trong `danh_s_ch_c_ng_vi_c_s_n_xu_t_video/code.html`, giữ provider banner nhưng đảm bảo thứ tự:

1. Headline tiếng người.
2. Provider và account ở metadata.
3. Lý do cụ thể.
4. Retry time.
5. Shot bị ảnh hưởng.
6. Một primary action.
7. Các action phụ.

Không để HTTP code làm headline hoặc badge chính.

## 6.2. Job list mobile

Trong `danh_s_ch_c_ng_vi_c_mobile/code.html`, thay:

```text
Cảnh báo Provider: Hết Quota
HTTP 429
Tạm dừng pipeline
```

bằng:

```text
Chưa thể tạo video lúc này
Tài khoản Team-Pro-04 đã hết hạn mức tạo video hôm nay.
Hệ thống sẽ thử lại lúc 14:30.
Ảnh hưởng: Shot 04, Shot 07
```

Actions:

- Primary: `Đổi tài khoản` hoặc `Thử lại ngay` tùy trạng thái API.
- Secondary: `Dùng video tải lên`.

Không đặt hai primary action ngang hàng.

## 6.3. Job detail desktop/mobile

Tìm toàn bộ:

```text
HELD (THIẾU REF)
```

thay bằng:

```text
Đang chờ ảnh tham chiếu
```

Bổ sung ngay bên dưới:

```text
Shot này chưa thể render vì thiếu ảnh tham chiếu.
```

Action ngay tại chỗ:

```text
Thêm ảnh tham chiếu
```

Không chỉ dùng chữ viết tắt `REF` hoặc `HELD` làm thông tin duy nhất.

## 6.4. Giữ mã kỹ thuật ở vùng phụ

Nếu cần hiển thị `HTTP 429`, provider ID hoặc enum, đặt trong disclosure:

```text
Chi tiết kỹ thuật
```

Không dùng trong headline, card title hoặc primary status.

---

# 7. P1 — Sửa semantics và accessibility

## 7.1. Filter không phải tab

Các control như:

- Tất cả công việc.
- Đang xử lý.
- Đang chờ xử lý.
- Hoàn thành.

nếu chỉ lọc bảng thì đổi từ:

```html
role="tab"
aria-selected="true"
```

sang:

```html
<button type="button" aria-pressed="true">...</button>
```

Dùng `role="tab"` chỉ khi thực sự đổi `tabpanel`.

## 7.2. Nếu là tab thật

Mỗi tab thật phải có:

```html
<div role="tablist" aria-label="...">
  <button role="tab" id="tab-overview" aria-controls="panel-overview" aria-selected="true" tabindex="0">...</button>
</div>
<section role="tabpanel" id="panel-overview" aria-labelledby="tab-overview">...</section>
```

Bổ sung arrow-left/right, Home, End và Enter/Space behavior.

## 7.3. Focus visible

Thêm global style:

```css
:where(button, a, input, select, textarea, [tabindex]):focus-visible {
  outline: 2px solid #2563EB;
  outline-offset: 2px;
}
```

Không dùng `outline-none` nếu không có focus replacement.

## 7.4. Accessible names

Mọi icon-only button phải có `aria-label` rõ ràng bằng tiếng Việt:

- Mở thông báo.
- Mở nhật ký terminal.
- Phát hoặc tạm dừng preview.
- Shot trước.
- Shot sau.
- Tải xuống video.
- Sao chép liên kết.
- Mở menu thao tác.

## 7.5. Contrast và color independence

Held, failed, running và success phải luôn có:

- Icon.
- Text label.
- Border hoặc visual marker.
- Không chỉ chấm màu.

---

# 8. P1 — Sửa riêng từng nhóm màn hình

## 8.1. `danh_s_ch_c_ng_vi_c_s_n_xu_t_video/code.html`

Giữ lại:

- Provider banner.
- Dense production table.
- Filter bar.
- Một primary action `Tạo video mới`.

Sửa:

- Thay toàn bộ font data sang IBM Plex Mono.
- Xóa palette cũ còn trong Tailwind config.
- Thay `demo: 28` bằng nhãn rõ `Dữ liệu mẫu` hoặc loại khỏi bản production.
- Bổ sung `aria-controls` nếu filter là tab thật; nếu là filter thì dùng `aria-pressed`.
- Thêm `:focus-visible`.
- Giảm số action ngang hàng trong provider banner; chọn một action chính.
- Kiểm tra table header sticky.
- Số liệu căn phải và tabular.

## 8.2. `danh_s_ch_c_ng_vi_c_mobile/code.html`

Bắt buộc sửa:

- Viewport.
- Xóa system status bar giả.
- Xóa `Space Mono`.
- Xóa palette Material cũ.
- Xóa `animate-pulse`.
- Xóa `rounded-full` ở FAB.
- Đổi `HTTP 429`, `Hết Quota`, `Tạm dừng pipeline` thành copy tiếng người.
- Xóa fixed shell `max-w-[390px]`.
- Không dùng 5 nav item nếu chúng tạo overload; giữ tối đa 4–5 item với label ngắn.
- Bổ sung padding tránh bottom nav che card cuối.

## 8.3. `chi_ti_t_c_ng_vi_c_teaser_ra_m_t_s_n_ph_m_v2/code.html`

Sửa:

- `HELD (THIẾU REF)` → `Đang chờ ảnh tham chiếu`.
- Bổ sung action `Thêm ảnh tham chiếu` ngay trong shot card.
- Xóa `animate-pulse` ở held badge.
- Xóa `backdrop-blur-sm` ở video overlay.
- Thay `transition-all` bằng transition cụ thể hoặc bỏ.
- Đảm bảo tabs có panel liên kết rõ.
- Giữ preview, shot list, audio và references nhưng giảm action cạnh tranh.

## 8.4. `chi_ti_t_c_ng_vi_c_mobile/code.html`

Sửa:

- Viewport và `Space Mono`.
- `rounded-xl` primary upload button → `rounded-[6px]`.
- `backdrop-blur-sm` header/timecode/bottom bar → nền đặc.
- `rounded-full` status/tabs/count → token radius.
- Xóa `animate-pulse`, `animate-ping`, `transition-all`, scale.
- Xóa `shadow-lg` bottom bar.
- Đổi `HELD (THIẾU REF)` thành copy tiếng người + action.
- Không để sticky bottom action che nội dung hoặc focus.

## 8.5. `short_studio_bi_n_t_p_s_n_xu_t_video_ng_n_9_16/code.html`

Sửa:

- `Space Mono` → IBM Plex Mono.
- `transition-all` ở sidebar → transition cụ thể.
- `SHORT STUDIO (9:16)` nên là page title hoặc context nhỏ, không phải status badge.
- `Safe Zone TikTok / Reels` giữ được vì là functional visualization nhưng phải có label tiếng Việt dễ hiểu.
- Không dùng dark gradient overlay trang trí; chỉ giữ overlay cần cho text readability trong preview video.
- Cấu trúc ba panel phải có collapse/drawer cho tablet.
- Primary action `Xuất bản Short này` phải nổi bật duy nhất.

## 8.6. `short_studio_mobile/code.html`

Sửa:

- Viewport.
- `Space Mono`.
- Xóa `animate-pulse`, `animate-ping`.
- `SYNCING` → `Đang đồng bộ`.
- `TTS Ducking` → `Tự giảm nhạc nền khi có lời thoại`.
- `BGM` → `Nhạc nền`.
- `AI GEN • 60 FPS` → `Video preview · 60 fps` nếu thực sự cần.
- Xóa `active:scale`, `transition-all`.
- Play button có thể tròn vì là media control, nhưng giữ kích thước và focus rõ.
- Safe Zone overlay được giữ như functional overlay, không dùng như decoration.
- Bổ sung `tabpanel` cho Kịch bản/Timeline nếu đây là tabs thật.

## 8.7. `video_th_nh_ph_m_th_vi_n_t_li_u_tham_chi_u/code.html`

Sửa:

- `Space Mono`.
- `animate-pulse`, `animate-ping`.
- `transition-all`.
- `rounded-xl` card/dropzone → `rounded-[10px]`.
- `rounded-full` count/status → radius 4–6px.
- `LOCAL CLOUD GATEWAY: 10 GbE LAN` → `Cổng lưu trữ nội bộ · 10 GbE`.
- `AUTO TAGGING ON`, `QA 5/5 PASSED` → copy tiếng Việt nhất quán.
- Asset card actions phải có label hoặc aria-label.
- Upload empty state phải hướng dẫn rõ thao tác.

## 8.8. `video_th_nh_ph_m_th_vi_n_mobile/code.html`

Sửa:

- Viewport.
- `Space Mono`.
- Palette cũ.
- `transition-all`.
- `onclick` inline nếu không cần cho mockup.
- Progress hardcode `98.2%` phải gắn nhãn `Dữ liệu mẫu` hoặc thay bằng placeholder API.
- Tabs master/reference phải có semantics rõ.
- Không dùng khung điện thoại giả.

---

# 9. P1 — Navigation và copy consistency

Dùng một bảng thuật ngữ duy nhất:

| Không dùng làm label chính | Dùng thay thế |
|---|---|
| Shot Tracker | Theo dõi shot |
| Render Queue | Hàng đợi render |
| Storage / CDN | Kho tư liệu |
| Error / Problem | Cảnh báo lỗi |
| Syncing | Đang đồng bộ |
| BGM | Nhạc nền |
| TTS Ducking | Tự giảm nhạc nền khi có lời thoại |
| HTTP 429 | Đã hết hạn mức tạo video |
| HELD (THIẾU REF) | Đang chờ ảnh tham chiếu |
| NODE_CLUSTER | Hệ thống render |

Nếu người dùng chuyên môn bắt buộc cần thuật ngữ tiếng Anh, đặt tiếng Anh trong metadata phụ, không đặt làm headline chính.

---

# 10. P2 — Prototype hygiene

Các điểm sau không nhất thiết chặn visual score, nhưng phải ghi rõ trong output:

- Tailwind CDN chỉ là prototype.
- Google Fonts CDN chỉ là prototype.
- Remote generated image URL chỉ là placeholder.
- Hardcoded count/progress/status không phải dữ liệu thật.
- Inline style cần gom thành class/token.
- Inline `onclick` cần chuyển thành semantic event khi tích hợp app thật.

Không được nói “production-ready” nếu các điểm này vẫn còn.

---

# 11. Checklist bắt buộc trước khi trả bundle

Chạy search toàn bundle. Kết quả bắt buộc:

```text
Space Mono                 = 0
user-scalable=no           = 0
maximum-scale              = 0
backdrop-blur              = 0
transition-all             = 0
animate-pulse              = 0
animate-ping               = 0
active:scale               = 0
hover:scale                = 0
HELD (THIẾU REF)           = 0 ở headline/status chính
HTTP 429                   = 0 ở headline/status chính
```

`rounded-full` chỉ được phép tồn tại cho:

- Avatar.
- Status dot.
- Play button/media control thật sự cần tròn.

Tất cả screen phải đạt:

- IBM Plex Sans + IBM Plex Mono.
- Đúng token repository.
- Radius ≤10px.
- Một shadow duy nhất.
- Focus-visible.
- Keyboard flow.
- Reduced-motion.
- No zoom lock.
- Held có lý do người dùng hiểu + action.
- Provider error có reason + account + retry + affected shots.
- Mobile không có mock system bar.
- Mobile không có fixed shell giả.
- Không có navigation cạnh tranh hoặc trùng chức năng.

---

# 12. Output Stitch phải trả về

Sau khi sửa, Stitch phải trả về:

1. Danh sách chính xác file đã sửa.
2. Screenshot desktop và mobile cho từng nhóm màn hình.
3. Bảng kiểm tra trước/sau.
4. Kết quả search các pattern cấm ở mục 11.
5. Danh sách hardcoded/demo data còn lại.
6. Ghi rõ điểm nào chưa thể implement vì chỉ là static prototype.
7. Không đổi business logic, API contract hoặc domain behavior.

Không được trả lời chung chung kiểu “đã tối ưu accessibility”. Phải chỉ rõ file, pattern và kết quả kiểm tra.

---

# 13. Tiêu chí chấm điểm sau khi sửa

| Hạng mục | Trọng số |
|---|---:|
| Token/font/shape consistency | 20% |
| Visual quality và hierarchy | 20% |
| Dashboard density và information architecture | 15% |
| Held/provider error UX | 15% |
| Responsive/mobile | 10% |
| Accessibility/keyboard/focus | 10% |
| Motion discipline | 5% |
| Prototype hygiene và consistency giữa các screen | 5% |

Chỉ được gọi là **9.5+** khi:

- Không còn P0.
- 5 cửa UI Review đều pass.
- Mobile và desktop dùng cùng token/copy contract.
- Search checklist không còn pattern cấm.
- Held/provider error đạt đầy đủ reason + action.
- Accessibility không còn zoom lock và có focus/keyboard/reduced-motion.
