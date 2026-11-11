# PROMPT DÁN THẲNG VÀO STITCH — SỬA BUNDLE HIỆN TẠI LÊN 9.5+

Bạn đang chỉnh sửa **chính bundle hiện tại**, không được gen lại từ đầu và không được tạo một dashboard khác.

Bundle hiện tại là version đã được sửa một phần. Hãy giữ nguyên domain, layout chính, nội dung workflow và các màn hình hiện có; chỉ sửa đúng các lỗi còn tồn tại dưới đây để đạt **9.5+/10 theo design lock**.

## QUY TẮC TUYỆT ĐỐI

## GATE BẮT BUỘC — UTF-8 VÀ TIẾNG VIỆT

Trước khi sửa giao diện, kiểm tra toàn bộ bundle đang mở đang dùng UTF-8. Không được để tiếng Việt bị biến thành `?`, `�`, chuỗi mất dấu hoặc mojibake trong title, heading, label, button, status, helper text, aria-label và nội dung shot. Nếu phát hiện lỗi encoding, sửa encoding trước rồi mới đánh giá UI. Sau khi sửa, đọc lại toàn bộ màn hình ở browser và xác nhận tiếng Việt hiển thị đúng dấu. Đây là blocker P0; nếu còn lỗi encoding thì không được tuyên bố đạt 9.5+.

- Không đổi business logic.
- Không đổi API contract.
- Không đổi tên workflow/domain nếu không được yêu cầu bên dưới.
- Không thêm landing page, hero marketing, gradient tím, glassmorphism, card KPI trang trí hoặc emoji.
- Không xóa job list, job detail, asset library, Short Studio, shot list, timeline, audio và provider error workflow.
- Không trả lời “đã sửa” chung chung. Phải sửa trực tiếp file và trả checklist kiểm chứng.

Nguồn thiết kế duy nhất là **Studio Production Engine Design Specification** trong bundle hiện tại, đối chiếu với các token chuẩn dưới đây. Nếu bất kỳ màn hình hoặc tài liệu thiết kế nào dùng token khác, phải sửa theo token này.

---

# A. SỬA ĐÚNG TỪNG MÀN HÌNH TRONG BUNDLE HIỆN TẠI

Áp dụng thay đổi cho toàn bộ bundle đang mở, không phụ thuộc tên thư mục, tên file hoặc tên ZIP. Nhận diện màn hình bằng title, heading hiển thị, layout và chức năng:

1. **Production Job List — Desktop**
2. **Production Job List — Mobile**
3. **Job Detail: Product Launch Teaser — Desktop**
4. **Job Detail: Product Launch Teaser — Mobile**
5. **Short Studio 9:16 — Desktop**
6. **Short Studio 9:16 — Mobile**
7. **Finished Video & Reference Asset Library — Desktop**
8. **Finished Video & Reference Asset Library — Mobile**
9. **Studio Production Engine Design Specification**

Nếu một pattern tồn tại ở màn hình hoặc tài liệu khác, cũng phải sửa màn hình/tài liệu đó. Không tìm màn hình theo đường dẫn cố định.

---

# B. TOKEN BẮT BUỘC — XÓA HỆ TOKEN CŨ

Chỉ dùng các token sau:

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

Tìm và xóa/thay toàn bộ các màu cũ sau, kể cả trong Tailwind config, frontmatter, inline style và arbitrary class:

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

Không được để hai design system song song trong cùng bundle.

---

# C. FONT — KHÔNG CÒN SPACE MONO

Tìm toàn bộ bundle và thay mọi chuỗi:

```text
Space Mono
```

bằng:

```text
IBM Plex Mono
```

Phải sửa tất cả nơi sau:

- Google Fonts URL.
- `tailwind.config`.
- `fontFamily`.
- `.font-mono-tech`.
- `data-mono-sm`, `data-mono-md`, `data-mono-lg`.
- Fallback font.
- Comment mô tả font.
- **Studio Production Engine Design Specification**.

Font data bắt buộc:

```css
.font-mono-tabular {
  font-family: 'IBM Plex Mono', monospace;
  font-variant-numeric: tabular-nums;
}
```

Không chấp nhận kết quả có chữ `Space Mono` dù chỉ ở fallback hoặc comment.

---

# D. MOBILE — SỬA ĐÍCH DANH 4 MÀN HÌNH

Sửa chính xác 4 màn hình:

- **Production Job List — Mobile**
- **Job Detail: Product Launch Teaser — Mobile**
- **Short Studio 9:16 — Mobile**
- **Finished Video & Reference Asset Library — Mobile**

Thay mọi viewport dạng:

```html
<meta content="width=390, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" name="viewport">
```

hoặc:

```html
<meta content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" name="viewport">
```

bằng:

```html
<meta content="width=device-width, initial-scale=1.0" name="viewport">
```

Không được còn `maximum-scale` hoặc `user-scalable` ở bất kỳ file nào.

Trong tất cả mobile screen:

- Xóa mock system status bar như `09:41`, `5G`, `100%`.
- Xóa phone shell giả `max-w-[390px]`, `border-x`, `shadow-sm` nếu dùng để giả lập điện thoại.
- Giữ responsive layout thật, không đóng khung app vào 390px.
- Giữ padding bottom đủ để bottom navigation/fixed action không che nội dung.
- Không để hai fixed bar chồng nhau.

---

# E. SHAPE VÀ SHADOW — SỬA ĐÚNG TOKEN

Quy tắc:

- Button/input/control: `rounded-[6px]`.
- Card/panel/modal/table container: `rounded-[10px]`.
- Badge nhỏ: `rounded-[4px]` hoặc `rounded-[6px]`.
- Avatar, status dot và play control mới được tròn.
- Không dùng `rounded-full` cho primary button, alert, toolbar, table, data container, tab container, FAB hoặc status badge lớn.
- Không dùng radius lớn hơn 10px.

Xóa hoặc thay toàn bộ `rounded-full` sai mục đích.

Chỉ dùng một shadow:

```css
box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
```

Xóa `shadow-md`, `shadow-lg`, shadow diffuse và shadow không đồng nhất.

Xóa mọi:

```text
backdrop-blur
backdrop-blur-sm
backdrop-blur-md
```

Thay bằng nền đặc, border và layer rõ ràng.

---

# F. MOTION — DỌN HẾT PATTERN CÒN SÓT

Tìm toàn bộ bundle và xóa:

```text
animate-pulse
animate-ping
transition-all
active:scale-95
active:scale-[0.99]
hover:scale-102
hover:scale-105
duration-200
duration-300
duration-500
```

Không làm status dot nhấp nháy. Status live vẫn phải có icon + text; chỉ được animate khi thật sự biểu diễn dữ liệu live.

Nếu cần transition, chỉ dùng:

```text
transition-opacity duration-150 ease-out
```

hoặc:

```text
transition-transform duration-150 ease-out
```

Không animate màu, layout, width hoặc `all`.

Mỗi file phải có reduced motion:

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

# G. COPY — SỬA ĐÚNG CHUỖI CÒN LẠI

## 1. Mobile job list

Trong màn hình **Production Job List — Mobile**, giữ copy hiện tại:

```text
Chưa thể tạo video lúc này
Tài khoản Team-Pro-04 đã hết hạn mức tạo video hôm nay. Hệ thống sẽ thử lại lúc 14:30. Ảnh hưởng: Shot 04, Shot 07
```

Không đưa lại `HTTP 429`, `Hết Quota` hoặc `Tạm dừng pipeline` vào headline.

## 2. Held status

Tìm mọi:

```text
HELD (THIẾU REF)
THIẾU REF
```

Ở headline/status chính, thay bằng:

```text
Đang chờ ảnh tham chiếu
```

Bổ sung ngay trong shot card:

```text
Shot này chưa thể render vì thiếu ảnh tham chiếu.
```

Và action:

```text
Thêm ảnh tham chiếu
```

Desktop và mobile phải dùng cùng copy.

## 3. Short Studio

Thay:

```text
SYNCING
```

bằng:

```text
Đang đồng bộ
```

Thay:

```text
TTS Ducking
```

bằng:

```text
Tự giảm nhạc nền khi có lời thoại
```

Thay label chính:

```text
BGM
```

bằng:

```text
Nhạc nền
```

Thay:

```text
AI GEN • 60 FPS
```

bằng:

```text
Video preview · 60 fps
```

Giữ `Safe Zone` vì đây là chức năng video, nhưng hiển thị dễ hiểu:

```text
Vùng an toàn 9:16
```

## 4. Technical details

Nếu cần giữ HTTP code/provider ID/enum, đưa vào vùng phụ có tiêu đề:

```text
Chi tiết kỹ thuật
```

Không dùng mã kỹ thuật làm headline, status chính hoặc primary action.

---

# H. SEMANTICS VÀ ACCESSIBILITY

## 1. Filter status

Các bộ lọc `Tất cả`, `Đang xử lý`, `Đang chờ xử lý`, `Hoàn thành` chỉ là filter dữ liệu. Dùng:

```html
role="group"
```

và mỗi button:

```html
aria-pressed="true|false"
```

Không dùng `role="tab"` cho filter không có panel.

## 2. Tabs thật

Chỉ dùng `role="tab"` nếu có panel tương ứng. Mỗi tab thật phải có:

```html
role="tablist"
role="tab"
aria-selected="true|false"
aria-controls="panel-id"
```

Panel phải có:

```html
role="tabpanel"
id="panel-id"
aria-labelledby="tab-id"
```

Bổ sung keyboard ArrowLeft/ArrowRight, Home, End, Enter/Space.

## 3. Focus

Tìm và xóa `focus-visible:outline-none` nếu không có replacement. Thêm vào mỗi file:

```css
:where(button, a, input, select, textarea, [tabindex]):focus-visible {
  outline: 2px solid #2563EB;
  outline-offset: 2px;
}
```

Không được để control mất focus indicator.

## 4. Icon-only controls

Mọi icon-only button phải có `aria-label` tiếng Việt rõ ràng. Ví dụ:

- `Mở nhật ký terminal`.
- `Mở thông báo`.
- `Phát hoặc tạm dừng xem trước`.
- `Chuyển sang shot trước`.
- `Chuyển sang shot sau`.
- `Tải xuống video`.
- `Sao chép liên kết CDN`.
- `Mở menu thao tác`.

---

# I. SỬA ĐÍCH DANH THEO TỪNG MÀN HÌNH

## 1. Production Job List — Desktop

- Giữ table, provider banner và filter bar.
- Thay toàn bộ `Space Mono`.
- Xóa palette cũ trong config.
- Đảm bảo provider banner có provider, account, reason, retry time, affected shots.
- Chỉ một action chính trong banner.
- Số liệu dùng IBM Plex Mono + tabular numerals.
- Header table sticky.
- Filter dùng `aria-pressed` nếu chỉ lọc dữ liệu.
- Thêm focus-visible thật.

## 2. Production Job List — Mobile

- Sửa viewport.
- Xóa palette cũ.
- Xóa `Space Mono`.
- Xóa phone shell giả.
- Giữ copy provider đã được sửa.
- Giữ primary `Đổi tài khoản`, secondary `Dùng video tải lên`.
- Không thêm lại `HTTP 429`.
- Không dùng pill FAB.
- Không để bottom nav che card cuối.

## 3. Job Detail: Product Launch Teaser — Desktop

- `HELD (THIẾU REF)` → `Đang chờ ảnh tham chiếu`.
- Thêm `Thêm ảnh tham chiếu` trong shot held.
- Xóa `animate-pulse`.
- Xóa `backdrop-blur-sm`.
- Xóa `transition-all`.
- Đồng bộ copy với mobile.
- Giữ preview, shot, audio, reference và checklist.

## 4. Job Detail: Product Launch Teaser — Mobile

- Sửa viewport.
- Xóa `Space Mono`.
- Xóa backdrop blur.
- Xóa `transition-all`, ping, pulse, scale.
- Primary button radius 6px, không dùng `rounded-xl`.
- Held copy + action phải giống desktop.
- Fixed bottom bar không được che nội dung và dùng shadow duy nhất.

## 5. Short Studio 9:16 — Desktop

- Xóa `Space Mono`.
- Xóa pulse.
- Xóa transition-all.
- Chuyển `TTS Ducking`, `BGM`, `Safe Zone` sang copy đã quy định.
- Preview vẫn là functional video preview, không thêm decoration.
- Tablet phải cho phép inspector chuyển thành drawer.
- Một primary action duy nhất là `Xuất bản Short này`.

## 6. Short Studio 9:16 — Mobile

- Sửa viewport.
- Xóa toàn bộ Space Mono khỏi config và fallback.
- Giữ IBM Plex Mono cho timecode.
- Xóa `backdrop-blur-none`.
- Xóa pulse/ping/scale/transition-all.
- `SYNCING` → `Đang đồng bộ`.
- `TTS Ducking` → copy tiếng Việt.
- `BGM` → `Nhạc nền`.
- Tabs Kịch bản/Timeline phải có semantics thật.
- Play button được tròn vì là media control, nhưng phải có focus rõ.

## 7. Finished Video & Reference Asset Library — Desktop

- Xóa Space Mono.
- Xóa pulse/ping/transition-all.
- Xóa hover scale ảnh.
- Card radius 10px, control radius 6px.
- Chỉ dùng shadow technical duy nhất.
- `NODE_CLUSTER: 12 ONLINE` → `Hệ thống render: 12 node đang hoạt động`.
- `LOCAL CLOUD GATEWAY: 10 GbE LAN` → `Cổng lưu trữ nội bộ · 10 GbE`.
- Upload empty state có hướng dẫn.
- Asset actions có accessible name.

## 8. Finished Video & Reference Asset Library — Mobile

- Sửa viewport.
- Xóa Space Mono khỏi config.
- Xóa `transition-all`.
- Xóa phone shell nếu có.
- Tabs Master/Reference có semantics đúng.
- Progress hardcode phải ghi `Dữ liệu mẫu` hoặc để placeholder.
- Không dùng inline handler cho interaction quan trọng nếu có thể tránh.

## 9. Studio Production Engine Design Specification

- Sửa toàn bộ frontmatter về token repository.
- `Space Mono` → `IBM Plex Mono` ở mọi dòng, kể cả dòng specialized component.
- Ghi rõ `Studio Production Engine Design Specification` là nguồn duy nhất.
- Xóa mô tả token cũ gây conflict.

---

# J. DEFINITION OF DONE — TỰ SEARCH TRƯỚC KHI TRẢ KẾT QUẢ

Trước khi trả bundle, search toàn bộ nội dung của các màn hình và Studio Production Engine Design Specification. Kết quả bắt buộc:

```text
Space Mono              = 0
maximum-scale           = 0
user-scalable           = 0
backdrop-blur           = 0
transition-all          = 0
animate-pulse           = 0
animate-ping            = 0
active:scale            = 0
hover:scale             = 0
HELD (THIẾU REF)        = 0 ở UI chính
THIẾU REF               = 0 ở UI chính
HTTP 429                = 0 ở headline/status chính
```

`rounded-full` chỉ được phép cho avatar, status dot và play button.

Mọi screen phải đạt:

- Đúng palette repository.
- IBM Plex Sans + IBM Plex Mono.
- Radius tối đa 10px.
- Một shadow duy nhất.
- Focus-visible thật.
- Keyboard flow.
- Reduced motion.
- Zoom mobile hoạt động.
- Held có lý do tiếng người và action.
- Provider error có provider, account, reason, retry time, affected shots.
- Desktop/mobile dùng cùng content contract.
- Không có mock system bar.
- Không có phone shell giả.

## OUTPUT BẮT BUỘC

Sau khi sửa, trả về:

1. Báo cáo thay đổi theo tên màn hình, không dùng đường dẫn file.
2. Screenshot desktop/mobile của từng nhóm màn hình.
3. Kết quả search các pattern cấm ở mục J.
4. Danh sách hardcoded/demo data còn lại.
5. Danh sách các interaction chỉ là mockup.
6. Xác nhận không đổi business logic/API.

Chỉ được tuyên bố đạt 9.5+ nếu tất cả P0 pass và search checklist không còn pattern cấm. Nếu còn lỗi, phải ghi rõ lỗi và không được tự chấm 9.5+.
