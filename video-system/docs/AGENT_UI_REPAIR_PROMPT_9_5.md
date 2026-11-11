# PROMPT CHO AGENT — FULL UI/UX REPAIR VÀ REVIEW BUNDLE STITCH LÊN 9.5+

Bạn là senior frontend/UI engineer. Hãy sửa trực tiếp bundle Stitch hiện tại trong thư mục đang được giao. Mục tiêu là nâng chất lượng UI/UX lên tối thiểu 9.5/10 theo design lock, không chỉ sửa cho pass search.

## 1. PHẠM VI VÀ GIỚI HẠN

Sửa UI/UX trực tiếp trong 8 màn hình HTML hiện có:

1. Production Job List — Desktop
2. Production Job List — Mobile
3. Job Detail: Product Launch Teaser — Desktop
4. Job Detail: Product Launch Teaser — Mobile
5. Short Studio 9:16 — Desktop
6. Short Studio 9:16 — Mobile
7. Finished Video & Reference Asset Library — Desktop
8. Finished Video & Reference Asset Library — Mobile

Bắt buộc:

- Giữ nguyên domain, workflow, nội dung nghiệp vụ, tên màn hình, shot, timeline, audio, reference asset và provider error workflow.
- Không đổi business logic.
- Không đổi API contract.
- Không xóa màn hình hoặc chức năng hiện có.
- Không tạo landing page, hero marketing page, dashboard mới hoặc visual style khác.
- Chỉ sửa presentation, responsive layout, copy hiển thị, accessibility, interaction states và prototype hygiene.
- Không tuyên bố hoàn tất nếu chưa chụp screenshot và review từng màn hình.

## 2. DESIGN LOCK BẮT BUỘC

Dùng light theme duy nhất:

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

Typography:

- UI: IBM Plex Sans.
- Numbers, timecode, duration, progress, shot count: IBM Plex Mono.
- Numerical values phải dùng `font-variant-numeric: tabular-nums`.
- Không dùng Inter, Roboto, Space Mono hoặc fallback tương đương làm font chính.

Shape:

- Control/button/input: radius 6px.
- Card/panel/modal/table container: radius 10px.
- Small badge: radius 4px hoặc 6px.
- Chỉ avatar, status dot và media play control mới được tròn.
- Không dùng pill cho primary button, alert, filter container, table, data container hoặc FAB.
- Không dùng radius lớn hơn 10px.

Elevation:

- Chỉ dùng một technical shadow:

```css
box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
```

- Không glassmorphism.
- Không gradient.
- Không gradient text.
- Không decorative icon tile.
- Không emoji thay icon.

## 3. UTF-8 VÀ TIẾNG VIỆT — P0 BLOCKER

Trước mọi thay đổi UI, kiểm tra encoding toàn bộ HTML.

- Tất cả file phải là UTF-8.
- Không được có `?`, `�`, mojibake hoặc tiếng Việt mất dấu trong title, heading, label, button, status, helper text, aria-label, shot copy và tooltip.
- Kiểm tra các chuỗi bắt buộc:

```text
Đang chờ ảnh tham chiếu
Shot này chưa thể render vì thiếu ảnh tham chiếu.
Thêm ảnh tham chiếu
Chưa thể tạo video lúc này
Đang đồng bộ
Tự giảm nhạc nền khi có lời thoại
Nhạc nền
Video preview · 60 fps
Vùng an toàn 9:16
```

Nếu có lỗi encoding, sửa trước rồi mới tiếp tục. Sau khi sửa phải mở browser và đọc trực tiếp từng màn hình để xác nhận tiếng Việt hiển thị đúng.

## 4. MOBILE VÀ RESPONSIVE

Trên cả 4 màn hình mobile:

- Viewport chỉ dùng:

```html
<meta name="viewport" content="width=device-width, initial-scale=1.0">
```

- Xóa `maximum-scale` và `user-scalable`.
- Không khóa zoom.
- Không giả lập system status bar như `09:41`, `5G`, `100%`.
- Không đóng khung production app bằng phone shell cố định 390px.
- Không dùng `max-w-[390px]` để giả điện thoại.
- Nội dung không bị fixed bottom navigation hoặc fixed action che.
- Tất cả control tương tác có target tối thiểu 44px trên mobile.
- Không để horizontal overflow ngoài vùng có chủ đích như filter row hoặc timeline.
- Job list mobile phải đọc được provider error ngay ở đầu màn hình.
- Job detail mobile phải giữ được hierarchy: job header → held reason → action → shot list → preview/audio/reference.
- Short Studio mobile phải giữ được preview, tabs, timeline và primary export action.
- Asset library mobile phải giữ được filter, search, asset card và action chính.

## 5. MOTION VÀ REDUCED MOTION

Xóa toàn bộ các pattern sau khỏi 8 màn hình:

```text
transition-all
transition-colors
animate-pulse
animate-ping
active:scale
hover:scale
backdrop-blur
```

Chỉ dùng transition cho opacity hoặc transform, tối đa 150ms ease-out:

```text
transition-opacity duration-150 ease-out
transition-transform duration-150 ease-out
```

Không animate màu, width, layout hoặc `all`.

Mỗi màn hình phải có:

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

Không làm status dot nhấp nháy nếu đó không phải dữ liệu live thực sự.

## 6. COPY VÀ TRẠNG THÁI HELD

Không dùng enum kỹ thuật làm headline.

Thay mọi biến thể như `HELD`, `HELD (THIẾU REF)`, `THIẾU REF` trong UI chính bằng:

```text
Đang chờ ảnh tham chiếu
```

Shot held bắt buộc có đủ 3 phần:

1. Icon trạng thái.
2. Lý do tiếng người:

```text
Shot này chưa thể render vì thiếu ảnh tham chiếu.
```

3. Action tại chỗ:

```text
Thêm ảnh tham chiếu
```

Provider/quota error bắt buộc hiển thị:

- Provider hoặc hệ thống nào lỗi.
- Account nào bị ảnh hưởng.
- Lý do bằng tiếng người.
- Thời gian retry kế tiếp.
- Shot nào bị ảnh hưởng.

Không dùng các chuỗi sau làm headline/status chính:

```text
HTTP 429
Hết Quota
Tạm dừng pipeline
Đã có lỗi xảy ra
```

Technical code/provider ID nếu cần giữ phải đặt trong vùng phụ có nhãn `Chi tiết kỹ thuật`.

## 7. SCREEN-SPECIFIC REQUIREMENTS

### Production Job List — Desktop

- Giữ table, sticky header, provider banner, filter và job rows.
- Table header rõ ràng; text căn trái, số/timecode căn phải.
- Filter dữ liệu dùng `role="group"` và `aria-pressed`.
- Provider banner chỉ có một primary action.
- Không dùng KPI card trang trí hoặc số hardcode gây hiểu là dữ liệu live.
- Trạng thái phải có icon + text, không phụ thuộc màu.

### Production Job List — Mobile

- Provider error nằm gần đầu màn hình.
- Giữ primary `Đổi tài khoản` và secondary `Dùng video tải lên` nếu đang có trong workflow.
- Card held phải có lý do và action.
- Bottom nav không che card cuối.
- Không dùng pill FAB.

### Job Detail: Product Launch Teaser — Desktop

- Giữ preview, shot list, audio, reference và QA checklist.
- Held state dùng copy tiếng người thống nhất với mobile.
- Không pulse status hoặc dùng blur decoration.
- Shot timeline phải cho thấy held gap có lý do, không để khoảng trống im lặng.

### Job Detail: Product Launch Teaser — Mobile

- Giữ cùng content contract với desktop.
- Primary action radius 6px.
- Fixed bottom action không che shot cuối.
- Shot card phải đọc được reason và action mà không cần hover.

### Short Studio 9:16 — Desktop

- Giữ preview video functional, script/timeline/inspector/audio.
- `SYNCING` → `Đang đồng bộ`.
- `TTS Ducking` → `Tự giảm nhạc nền khi có lời thoại`.
- `BGM` → `Nhạc nền`.
- `AI GEN • 60 FPS` → `Video preview · 60 fps`.
- `Safe Zone` → `Vùng an toàn 9:16`.
- Một primary action duy nhất: `Xuất bản Short này`.
- Tablet chuyển inspector thành drawer, không làm tràn layout.

### Short Studio 9:16 — Mobile

- Tabs `Kịch bản` và `Timeline` phải có semantics thật.
- Play button được tròn vì là media control và phải có accessible name.
- Timecode dùng IBM Plex Mono + tabular numerals.
- Preview không bị che bởi timeline hoặc bottom action.

### Finished Video & Reference Asset Library — Desktop

- Giữ hai vùng Master Exports và Reference Asset Library.
- Card container radius 10px; control radius 6px.
- Không hover scale ảnh.
- Upload empty state phải có hướng dẫn hành động.
- Mọi icon-only action có accessible name.
- Đổi copy kỹ thuật khó hiểu thành:

```text
Hệ thống render: 12 node đang hoạt động
Cổng lưu trữ nội bộ · 10 GbE
```

### Finished Video & Reference Asset Library — Mobile

- Tabs Master/Reference dùng semantics phù hợp.
- Không fake phone shell.
- Không che progress/card bằng bottom navigation.
- Dữ liệu demo phải ghi rõ `Dữ liệu mẫu` hoặc dùng placeholder.
- Không dùng inline handler cho interaction quan trọng nếu có thể tránh.

## 8. ACCESSIBILITY

Global focus rule:

```css
:where(button, a, input, select, textarea, [tabindex]):focus-visible {
  outline: 2px solid #2563EB;
  outline-offset: 2px;
}
```

- Không để `focus-visible:outline-none` nếu không có replacement.
- Icon-only button phải có `aria-label` tiếng Việt rõ ràng.
- Status luôn dùng icon + text.
- Filter không có panel dùng `role="group"` + `aria-pressed`.
- Tab thật phải có `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls` và `tabpanel` tương ứng.
- Tab thật phải hỗ trợ ArrowLeft/ArrowRight, Home, End, Enter/Space.
- Kiểm tra contrast tối thiểu WCAG AA.
- Không dùng màu là tín hiệu duy nhất.
- Keyboard tab order phải theo thứ tự đọc tự nhiên.

## 9. SELF-AUDIT BẮT BUỘC

Sau khi sửa, search toàn bộ HTML và tài liệu thiết kế. Các count sau phải bằng 0:

```text
Space Mono
maximum-scale
user-scalable
backdrop-blur
transition-all
transition-colors
animate-pulse
animate-ping
active:scale
hover:scale
shadow-md
shadow-lg
rounded-full
HTTP 429 ở headline/status chính
HELD (THIẾU REF)
THIẾU REF ở UI chính
ký tự replacement �
```

`rounded-full` chỉ được phép nếu thực sự là avatar, status dot hoặc play/media control. Nếu có ngoại lệ, báo rõ từng vị trí.

Kiểm tra thêm:

- 8/8 HTML có reduced motion.
- 8/8 HTML có viewport đúng.
- 8/8 HTML không có fake mobile status bar.
- 8/8 HTML không có phone shell giả.
- 8/8 HTML có focus-visible thật.
- 8/8 HTML giữ IBM Plex Sans/Mono.
- 8/8 HTML dùng tabular numerals cho số liệu.
- 8/8 HTML không có horizontal overflow ngoài vùng chủ đích.

## 10. CHỤP ẢNH VÀ REVIEW BẰNG CHỨNG

Bắt buộc dùng browser automation hoặc browser thật để chụp lại sau khi sửa:

- Desktop viewport: 1440×1000.
- Mobile viewport: 390×844.
- Chụp full-page từng màn hình.
- Ghi đè hoặc cập nhật `screen.png` trong thư mục tương ứng.
- Phải có đủ 8 screenshot, không được bỏ qua mobile.

Sau khi chụp, review thủ công từng ảnh theo checklist:

1. Không có text bị cắt hoặc ký tự lỗi.
2. Không có card/button bị tràn.
3. Không có fixed bar che nội dung.
4. Hierarchy title → status → data → action rõ ràng.
5. Held/error state nhìn thấy ngay và hiểu được.
6. Table/timeline/preview giữ đúng chức năng.
7. Mobile không bị ép vào phone shell.
8. Khoảng cách và mật độ phù hợp dashboard/pro-tool.
9. Không có component thừa do sửa token hàng loạt.
10. Không có visual regression rõ ràng so với workflow ban đầu.

Nếu có lỗi trong screenshot, sửa lại HTML rồi chụp lại. Không được chỉ báo cáo lỗi mà để nguyên.

## 11. OUTPUT BẮT BUỘC

Trả về báo cáo cuối gồm:

1. Danh sách 8 màn hình đã sửa theo tên màn hình.
2. Danh sách thay đổi UI/UX theo từng màn hình.
3. Bảng search count trước/sau.
4. Danh sách screenshot đã cập nhật.
5. Kết quả review từng screenshot: PASS/FAIL + bằng chứng.
6. Danh sách hardcoded/demo data còn lại.
7. Danh sách interaction còn là mockup.
8. Xác nhận business logic/API không bị thay đổi.
9. Các điểm chưa thể xác nhận nếu môi trường không chạy được browser.

Chỉ được tuyên bố đạt 9.5+ khi:

- Tất cả P0 pass.
- Encoding tiếng Việt pass.
- Search checklist pass.
- Đủ 8 screenshot.
- Đã review thủ công đủ 8 screenshot.
- Không có visual regression nghiêm trọng.
- Accessibility floor pass.
