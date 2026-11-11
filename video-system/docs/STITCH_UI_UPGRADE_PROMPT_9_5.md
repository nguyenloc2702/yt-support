# Stitch UI Upgrade Brief — Mục tiêu 9.5+/10

> Tài liệu này là prompt/specification để đưa vào Stitch nhằm **chỉnh sửa lại toàn bộ UI bundle hiện tại**, không thay đổi nghiệp vụ. Mục tiêu là nâng điểm visual/UI/UX lên tối thiểu **9.5/10 theo design lock của video-system**.
>
> **Nguồn luật duy nhất:** `config/DESIGN.md` của repository. Không được tự tạo một palette hoặc design system khác.

---

## Prompt gửi cho Stitch

Bạn đang chỉnh sửa một bộ giao diện web hiện có cho **Video Production Operations Platform**. Hãy nâng cấp toàn bộ UI hiện tại lên mức production-grade, đạt tối thiểu **9.5/10 về visual quality, UX, responsive và accessibility**.

Đây là một **dashboard/pro tool vận hành sản xuất video**, không phải landing page, không phải marketing dashboard, không phải SaaS template chung chung. Giữ lại domain và các workflow có giá trị hiện tại: job list, job detail, shot tracker, render queue, asset/reference library và Short Studio 9:16. Chỉ cải thiện presentation, hierarchy, copy, responsive behavior, accessibility và consistency; không tự ý đổi business logic hoặc tạo thêm nghiệp vụ mới.

## 1. Design lock — bắt buộc tuyệt đối

Chỉ dùng light theme và các token sau:

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

- UI text: `IBM Plex Sans`, weights 400/500/600.
- Numbers, counters, duration, progress, timecode, IDs: `IBM Plex Mono`.
- Mọi số phải dùng tabular numerals.
- Không dùng `Space Mono`, Inter, Roboto, Arial hoặc system-ui làm font chính.

Shape and elevation:

- Control/input/button: radius 6px.
- Card/panel/modal: radius 10px.
- Không dùng radius lớn hơn 10px.
- Dùng duy nhất một kiểu shadow: `0 1px 2px rgba(0,0,0,.05)` kết hợp border 1px.
- Không dùng `rounded-full` cho primary button, alert, toolbar, table, data container hoặc panel.
- Không dùng glassmorphism, `backdrop-blur`, gradient tím/indigo, gradient text, diffuse shadow, decorative icon tile hoặc 3-card marketing grid.

## 2. Visual direction

Giữ giao diện:

- Sạch, sáng, kỹ thuật, đáng tin cậy.
- Mật độ cao nhưng có thứ bậc rõ ràng.
- Ít trang trí, nhiều thông tin hữu ích.
- Có cảm giác do một product designer hiểu quy trình video làm ra, không giống UI sinh tự động.
- Border và spacing tạo cấu trúc; không dùng shadow để làm mọi thứ nổi lên.
- Accent blue chỉ dành cho action và selection quan trọng.
- Trạng thái phải dùng icon + text + màu, không dựa vào màu một mình.

Không làm:

- Không thêm hero section, slogan, illustration trang trí hoặc card KPI vô nghĩa.
- Không thêm emoji.
- Không biến mọi label thành uppercase.
- Không đặt eyebrow label trên mọi heading.
- Không tạo các badge/pill dày đặc làm giao diện giống marketing.

## 3. Information architecture

Giảm cognitive load và phân biệt rõ 3 lớp:

1. **Global navigation:** Công việc, Thư viện tư liệu, Cài đặt render.
2. **Section navigation:** Pipeline, Shot Tracker, Render Queue, Storage/CDN, Cảnh báo lỗi.
3. **Local tabs:** chỉ dành cho nội dung của trang hiện tại.

Không hiển thị đồng thời quá nhiều navigation layer nếu chúng trùng chức năng. Desktop dùng top navigation + side rail có chủ đích. Mobile dùng bottom navigation tối đa 4–5 mục, label ngắn, nhất quán tiếng Việt.

Đặt primary action duy nhất cho mỗi màn hình. Các action nguy hiểm hoặc phụ phải hạ cấp rõ ràng.

## 4. Copy và thuật ngữ

Ưu tiên tiếng Việt dễ hiểu. Giữ thuật ngữ kỹ thuật chỉ khi thật sự cần và giải thích bằng copy người dùng hiểu được.

Thay các chuỗi kiểu:

- `HTTP 429` → `Tài khoản đã hết hạn mức tạo video hôm nay`.
- `HELD (THIẾU REF)` → `Đang chờ ảnh tham chiếu`.
- `SYNCING` → `Đang đồng bộ`.
- `TTS Ducking` → `Tự giảm nhạc nền khi có lời thoại`.
- `BGM` → `Nhạc nền`.
- `Render Queue` → `Hàng đợi render` nếu không cần giữ tiếng Anh.
- `NODE_CLUSTER: 12 ONLINE` → `Hệ thống render: 12 node đang hoạt động`.

Mã kỹ thuật, provider ID và HTTP code chỉ xuất hiện trong vùng “Chi tiết kỹ thuật”, không nằm trong headline chính.

## 5. Held và provider error — signature của sản phẩm

Mọi shot held phải có đủ:

- Icon cảnh báo hoặc trạng thái.
- Label tiếng Việt dễ hiểu.
- Lý do cụ thể, không chỉ enum hoặc viết tắt.
- Shot hoặc tài nguyên bị ảnh hưởng.
- Thời điểm retry nếu có.
- Một primary action ngay tại chỗ.
- Một secondary action hợp lý.

Ví dụ:

```text
Đang chờ ảnh tham chiếu
Shot 04 chưa thể render vì thiếu ảnh đầu vào.
Ảnh hưởng: Shot 04, Shot 07
Hành động chính: Thêm ảnh tham chiếu
Hành động phụ: Giữ lại để xử lý sau
```

Provider/quota error phải nhất quán trên desktop và mobile: provider, account, lý do tiếng người, retry time, affected shots và action chính.

## 6. Dashboard data rules

- Không hardcode KPI, count, progress, status, duration hoặc “0 lỗi” trong UI production.
- Giữ placeholder data chỉ khi cần cho mockup, nhưng đánh dấu rõ là demo data và không làm nó giống dữ liệu live.
- Progress phải hiển thị theo ngữ cảnh: `shots appended/total`, `seconds appended/total`, `held count`.
- Số và timecode căn phải, dùng IBM Plex Mono và tabular numerals.
- Table header sticky; text căn trái, numeric columns căn phải.
- Empty state phải có hướng dẫn hành động cụ thể.
- Error state phải có lý do và nút xử lý, không dùng “Đã có lỗi xảy ra”.

## 7. Responsive rules

Desktop:

- Tối ưu cho viewport từ 1280px.
- Giữ layout production workstation: navigation/section tree, content canvas, inspector khi phù hợp.
- Không để top bar, side rail và local tabs tranh nhau làm primary navigation.

Tablet:

- Inspector chuyển thành drawer hoặc panel có thể đóng.
- Bảng có horizontal scroll có chủ đích, giữ column quan trọng.

Mobile:

- Single-column list/card workflow.
- Không dùng khung giả cố định `390px` trong layout production.
- Không thêm system status bar giả như `09:41`, `5G`, `100%`.
- Không dùng `maximum-scale=1.0` hoặc `user-scalable=no`.
- Không che nội dung bằng bottom navigation, FAB hoặc fixed action bar.
- Touch target tối thiểu 44px cho action chính, tối thiểu 24px cho control phụ.
- Alert/error phải giữ đủ thông tin quan trọng như desktop.
- Cho phép zoom trình duyệt.

## 8. Accessibility và interaction

Bắt buộc:

- WCAG 2.2 AA contrast.
- `:focus-visible` rõ ràng cho mọi button, link, input, tab và menu.
- Keyboard navigation đầy đủ.
- Icon-only control phải có accessible name.
- Tabs thật phải có `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls`, `tabpanel` và arrow-key navigation.
- Nếu chỉ là filter thì dùng button với `aria-pressed`, không giả tab.
- Modal/drawer phải có focus management và Escape close.
- Không dùng màu làm tín hiệu duy nhất.
- Thêm `prefers-reduced-motion`.
- Motion tối đa một transition 150ms ease-out mỗi interaction; chỉ animate `transform` hoặc `opacity`.
- Không dùng `transition-all`, `animate-pulse`, `animate-ping`, bounce hoặc scale effect không cần thiết.

## 9. Short Studio

Giữ preview 9:16, shot navigation, script/timeline, audio/caption layers và safe zone vì đây là phần có giá trị nhất.

Cải thiện:

- Preview là vùng hero chức năng, không phải decoration.
- Safe Zone overlay được xem là functional visualization, không phải decorative gradient.
- Timeline phải cho thấy appended segment, held gap và chú thích dễ hiểu.
- Audio chưa có preview thì ghi rõ `Chưa có audio preview`, không vẽ waveform giả.
- Mobile phải ưu tiên preview, play/pause, shot navigation và action xuất bản.
- Không để các panel kỹ thuật phụ lấn át việc xem và kiểm tra video.

## 10. Implementation hygiene cho prototype

Nếu vẫn xuất static HTML prototype:

- Có thể dùng Tailwind để preview, nhưng class phải nhất quán với token.
- Không dùng Google Fonts khác IBM Plex Sans/Mono.
- Không dùng remote image không ổn định nếu không cần thiết.
- Không dùng inline style lặp lại cho cùng một pattern.
- Không dùng `onclick` inline cho flow quan trọng nếu có thể mô tả bằng semantic button.
- Giữ code dễ chuyển sang component thật.

## 11. Definition of Done — tự kiểm trước khi trả kết quả

Chỉ trả kết quả khi tất cả điều kiện sau đạt:

- Không còn palette xung đột với `config/DESIGN.md`.
- Không còn `Space Mono`.
- Không còn `rounded-full` trên primary control, alert, toolbar, table hoặc data container.
- Không còn `backdrop-blur`.
- Không còn `transition-all`, `animate-pulse`, `animate-ping` cho UI state thông thường.
- Không còn `user-scalable=no` hoặc `maximum-scale=1.0`.
- Held có lý do tiếng người + action ngay tại chỗ.
- Provider error có provider + account + reason + retry time + affected shots.
- Mỗi page có một primary action rõ ràng.
- Navigation không bị lặp hoặc cạnh tranh.
- Mobile và desktop giữ cùng mức thông tin quan trọng.
- Có focus-visible, keyboard flow, reduced-motion và semantic tabs/filter đúng loại.
- Mọi số/timecode dùng IBM Plex Mono + tabular numerals.
- Giao diện vẫn có cấu trúc dashboard khi tắt CSS.

## Output cần trả về

Hãy trả về:

1. Các file/màn hình đã cập nhật.
2. Một screenshot desktop và mobile cho từng nhóm màn hình chính.
3. Checklist Definition of Done với trạng thái pass/fail.
4. Danh sách những điểm còn là mockup hoặc hardcoded data.
5. Không thay đổi business logic, API contract hoặc domain behavior.

Ưu tiên chất lượng và consistency hơn việc thêm nhiều component mới. Không gen lại một dashboard khác; hãy harden bộ UI hiện tại thành một hệ thống nhất quán, thân thiện và production-ready.
