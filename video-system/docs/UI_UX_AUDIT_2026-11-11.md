# UI/UX Audit toàn hệ thống — `video-system`

> **Ngày audit:** 2026-11-11  
> **Phạm vi:** đánh giá UI/UX, information architecture, nội dung hiển thị, responsive, accessibility, consistency và maintainability ảnh hưởng trực tiếp đến giao diện.  
> **Không nằm trong phạm vi:** sửa code, thay đổi business logic, thay đổi API/schema, thay đổi workflow nghiệp vụ.  
> **Trạng thái:** audit và đề xuất; chưa chỉnh sửa source code.

---

## 1. Tóm tắt điều hành

### Kết luận ngắn

Giao diện hiện tại đã vượt qua mức prototype thuần túy và có nền tảng khá tốt cho một dashboard/pro tool: theme sáng nhất quán, token màu rõ, có trạng thái loading/error/empty, có hỗ trợ keyboard ở nhiều custom control, có reduced-motion, có hash routing, có responsive breakpoint và có quy ước status thân thiện người dùng.

Tuy nhiên, trải nghiệm hiện tại đang bị kéo xuống bởi ba nhóm vấn đề lớn:

1. **Mật độ thông tin và số lớp điều hướng quá cao.** Người dùng đồng thời phải hiểu top navigation, breadcrumb, item selector, previous/next, quick jump, sáu detail tabs, các card có thể click và các action nằm trong card.
2. **Ngôn ngữ giao diện chưa thống nhất.** Tiếng Việt, tiếng Anh, thuật ngữ kỹ thuật, viết tắt và emoji đang trộn trong cùng một cấp độ hiển thị. Một số label mô tả implementation thay vì mô tả nhiệm vụ người dùng.
3. **Hệ thống tương tác chưa có một ngôn ngữ visual duy nhất.** Một phần dùng button/inline SVG, một phần dùng emoji, một phần dùng native `alert()`/`confirm()`, một phần dùng toast/banner/modal. Điều này làm trải nghiệm có cảm giác được ghép từ nhiều giai đoạn phát triển.

### Mức độ tổng thể

| Hạng mục | Đánh giá | Nhận xét |
|---|---:|---|
| Nền tảng visual | **Khá** | Token light theme, font, radius, border và shadow có chủ đích. |
| Information architecture | **Trung bình** | Bao phủ nhiều workflow nhưng đang chồng nhiều lớp điều hướng. |
| Copy và naming | **Trung bình–yếu** | Nhiều label dài, trộn ngôn ngữ, viết tắt và thuật ngữ nội bộ. |
| Interaction consistency | **Trung bình** | Có custom feedback tốt nhưng vẫn lẫn native dialogs và emoji affordance. |
| Responsive | **Khá về kỹ thuật, cần kiểm chứng thực tế** | Có breakpoint từ desktop đến ultra-narrow; rủi ro chính là density và thứ tự nội dung trên mobile. |
| Accessibility | **Khá, chưa đạt mức hoàn thiện** | Focus và keyboard đã được quan tâm; còn rủi ro với nested interactive, modal focus và tab semantics. |
| Maintainability ảnh hưởng UI | **Yếu** | Một file [`frontend/index.html`](../frontend/index.html) khoảng 7.668 dòng khiến consistency khó giữ lâu dài. |

### Ưu tiên cao nhất

- Thay emoji đang đóng vai trò icon/action bằng icon system nhất quán; giữ emoji chỉ khi thật sự là nội dung.
- Gom và đơn giản hóa navigation/detail tabs; người dùng cần biết “đang ở đâu, tiếp theo làm gì” mà không phải đọc sáu label dài.
- Chuẩn hóa copy theo một glossary tiếng Việt, trong đó thuật ngữ kỹ thuật chỉ xuất hiện ở tooltip/help text khi cần.
- Loại bỏ native `alert()`/`confirm()` khỏi các flow chính, chuyển sang dialog/toast/banner thống nhất.
- Rà soát lại các card `role="button"` có nested buttons; ưu tiên semantic HTML rõ ràng hơn là phủ thêm keyboard delegate.
- Tạo một visual/interaction inventory để các màn hình Todo và Short Studio không tiếp tục tiến hóa thành hai sản phẩm khác nhau.

---

## 2. Phạm vi và phương pháp audit

### Đã kiểm tra

- Source UI trong [`frontend/index.html`](../frontend/index.html), gồm CSS, markup, routing, rendering, loading states, dialogs, modals, Todo flow và Short Studio.
- Design contract trong [`config/DESIGN.md`](../config/DESIGN.md).
- Product context trong [`README.md`](../README.md).
- Lịch sử review trong [`docs/UI_REVIEW.md`](UI_REVIEW.md).
- Các route/page/tab, label, button, icon, status, empty/error state, modal, responsive rule, ARIA và keyboard handler.
- Một số nguồn tham khảo cộng đồng GitHub; tham khảo theo hướng pattern và nguyên tắc, không sao chép visual nguyên xi.

### Không thực hiện

- Không sửa [`frontend/index.html`](../frontend/index.html).
- Không sửa backend, API, schema hoặc data.
- Không thay đổi business logic.
- Không khẳng định pixel-perfect trên mọi thiết bị thật vì chưa có session usability test và chưa có ảnh chụp ở toàn bộ breakpoint.

### Cách đọc kết quả

- **Blocker:** gây cản trở rõ rệt cho việc hiểu hoặc hoàn thành workflow.
- **High:** ảnh hưởng thường xuyên đến usability, consistency hoặc accessibility; nên xử lý sớm.
- **Medium:** không chặn workflow nhưng làm UI khó hiểu, kém gọn hoặc khó mở rộng.
- **Polish:** cải thiện chất lượng cảm nhận sau khi nền tảng đã ổn.

---

## 3. Design contract và 5 cửa UI Review

Nguồn chuẩn là [`config/DESIGN.md`](../config/DESIGN.md). Audit không đánh giá theo gu cá nhân nếu vấn đề đã được design lock quy định rõ.

| Gate | Kết quả | Bằng chứng | Nhận định | Đề xuất |
|---|---|---|---|---|
| 1. Design tokens / anti-AI-slop | **Pass có điều kiện** | Token ở [`frontend/index.html`](../frontend/index.html:12); IBM Plex ở [`frontend/index.html`](../frontend/index.html:36) | Light theme, màu, radius và shadow bám contract. Emoji, inline styles và một số transition làm visual language chưa sạch. | Tách icon system; giảm inline style; kiểm tra mọi transition chỉ animate transform/opacity. |
| 2. Dashboard/API-first | **Pass có điều kiện** | KPI ở [`frontend/index.html`](../frontend/index.html:3003); loading/list ở [`frontend/index.html`](../frontend/index.html:3052) | Có dashboard metrics, loading và dữ liệu động. Một số text/empty affordance vẫn có vẻ implementation-first. | Viết copy theo nhiệm vụ; phân biệt “chưa có dữ liệu” với “đang tải” và “lỗi tải”. |
| 3. Held-first | **Chưa đủ bằng chứng để gọi pass tuyệt đối** | Status map trong [`frontend/index.html`](../frontend/index.html:3210); historical review ở [`docs/UI_REVIEW.md`](UI_REVIEW.md:19) | Status đã được humanize, nhưng cần kiểm chứng mọi held/provider/quota state có reason, retry time, affected shots và action tại chỗ hay chưa. | Lập ma trận state × reason × action; không để held chỉ còn màu/pill. |
| 4. Accessibility/usability floor | **Pass có điều kiện** | Focus ở [`frontend/index.html`](../frontend/index.html:210); reduced-motion ở [`frontend/index.html`](../frontend/index.html:237); custom keyboard ở [`frontend/index.html`](../frontend/index.html:6570) | Có nền tảng tốt. Nested interactive, tab keyboard model, modal focus trap và native dialogs còn rủi ro. | Dùng semantic control thật; test screen reader, keyboard-only, 200% zoom và touch. |
| 5. Bare-structure correctness | **Pass có điều kiện** | Các page container ở [`frontend/index.html`](../frontend/index.html:2996) | DOM có cấu trúc và label khá rõ, nhưng nhiều hành vi phụ thuộc inline handler/JS render và các custom role. | Giảm `role="button"` giả; giữ heading hierarchy, landmark và form label độc lập với CSS. |

### Các điểm phù hợp với design lock

- Nền light, foreground ấm, border nhẹ, accent xanh và status colors có mapping rõ.
- Font UI ưu tiên IBM Plex Sans; số/timecode dùng tabular numerals.
- Radius và shadow có tính tiết chế, không rơi vào glassmorphism/gradient marketing.
- Dashboard có density cao phù hợp domain production tool.
- Có `prefers-reduced-motion` và focus-visible.
- Có human-readable status mapping thay vì phơi raw enum ở nhiều vị trí.

### Các điểm lệch hoặc có nguy cơ lệch

- Emoji xuất hiện dày ở topbar, tab, action và empty state; điều này đi ngược nguyên tắc không dùng emoji thay icon có nghĩa.
- Inline style xuất hiện trong markup, ví dụ ở brand và loading panel; làm token hóa và kiểm soát consistency khó hơn.
- Một số transition có `background`, `border` hoặc `all`, trong khi motion rule chỉ cho transform/opacity.
- Fallback font có `Roboto`/system font dù IBM Plex vẫn là font chính; cần thống nhất policy nếu muốn design lock thật chặt.
- Một số label như “lazy load”, “infinite scroll”, “BGM”, “Ref”, “KF”, “Agent” có thể làm lộ implementation hoặc đòi hỏi kiến thức nội bộ.

---

## 4. Inventory màn hình, route và navigation

### 4.1 Page-level

Hiện có ba vùng page chính:

1. `pageTodoList`: danh sách công việc, KPI, filter, search, sort.
2. `pageTodoDetail`: detail một item, overview và các flow xử lý.
3. `pageShortStudio`: workflow tạo/quản lý short dọc.

Markup page container nằm tại [`frontend/index.html`](../frontend/index.html:3001). Top navigation gồm “Điều phối Shot” và “Short Studio” tại [`frontend/index.html`](../frontend/index.html:2984).

**Nhận xét:** cấu trúc ba page là hợp lý ở cấp sản phẩm, nhưng tên “Điều phối Shot” có thể không bao quát đúng list chứa cả item/job/video/audio/checklist. Nếu page thực tế là “Công việc” hoặc “Video Jobs”, tên hiện tại khiến người mới kỳ vọng đây chỉ là màn hình điều phối shot.

### 4.2 Route-level

Các hash route chính:

- `#/todos`
- `#/todos/{itemId}?tab={tab}`
- `#/shorts`
- `#/shorts/{shortId}`

Ưu điểm:

- Có thể bookmark/back-forward.
- Tab detail được phản ánh vào URL.
- Có đường đi rõ cho item và short.

Rủi ro:

- Hash URL dài và có nhiều trạng thái ẩn trong query.
- Back navigation có thể trở nên khó đoán nếu mỗi đổi tab đều push state thay vì replace state.
- Người dùng có thể mở một URL tab cũ không còn phù hợp sau khi flow thay đổi; normalization đã có nhưng cần thông báo nếu tab bị fallback.

### 4.3 Detail tabs

Sáu tab hiện tại được định nghĩa ở [`frontend/index.html`](../frontend/index.html:4070):

1. Tổng quan & Kịch bản
2. Video Thành Phẩm
3. Lưới Shot Video
4. Bảng Checklist
5. Ảnh Keyframe & Tư Liệu
6. Tải lên & Tinh chỉnh Audio

**Vấn đề chính:** đây là sáu bước xử lý khác nhau nhưng đang được trình bày như một tablist ngang. Tab là mô hình phù hợp khi nội dung song song; workflow này lại có tính tuần tự và phụ thuộc. Vì vậy người dùng có thể không biết:

- Tab nào bắt buộc trước.
- Tab nào chỉ là kho tài liệu.
- Tab nào là hành động, tab nào là kiểm tra.
- “Video Thành Phẩm” nên xem trước hay sau Checklist.
- “Tải lên & Tinh chỉnh Audio” là input, correction hay finalization.

**Đề xuất IA:** giữ URL và code flow hiện tại trong giai đoạn đầu, nhưng trình bày lại bằng nhóm chức năng:

- **Hiểu công việc:** Tổng quan, Kịch bản.
- **Tạo và kiểm tra:** Shot, Checklist, Audio.
- **Tài sản và xuất bản:** Ảnh/Tư liệu, Video thành phẩm.

Có thể vẫn dùng tab nhưng thêm step label ngắn, progress/context và trạng thái hoàn thành; không nên để số thứ tự tạo cảm giác mọi người phải đi tuần tự nếu thực tế không bắt buộc.

### 4.4 Navigation overload

Các lớp điều hướng cùng tồn tại:

- Top navigation.
- Brand click về list.
- Breadcrumb/back.
- Previous/next item.
- Job selector hoặc quick directory.
- Sáu detail tabs.
- Quick-jump cards trong overview.
- Button action trong từng card.

Đây là dấu hiệu **navigation entropy** cao. Không phải control nào cũng sai, nhưng tổng số lựa chọn cùng xuất hiện khiến người dùng phải liên tục xây mental model.

**Nguyên tắc cần áp dụng:** tại mỗi viewport và mỗi page, chỉ nên có một primary navigation, một secondary navigation và một primary action. Các jump links còn lại nên gộp vào một “Đi đến…” hoặc để trong overview.

---

## 5. Visual hierarchy và layout

### 5.1 Topbar

Topbar đang chứa brand, page navigation, hướng dẫn Agent, storage status, refresh và khóa phiên. Các action này có mức độ quan trọng rất khác nhau nhưng cùng nằm trong một hàng.

**Rủi ro:**

- “Xem Hướng Dẫn Agent” có thể cạnh tranh thị giác với “Làm mới dữ liệu”.
- Storage status vừa là trạng thái vừa là button; người dùng có thể không biết click để làm gì.
- “Khóa phiên” là security action nhưng nếu đặt ngang hàng với refresh dễ bị xem như action thường.
- Emoji prefix làm mỗi button có visual weight khác nhau tùy hệ điều hành.

**Đề xuất:**

- Brand + page switch ở trái.
- Refresh là action chính duy nhất ở phải.
- Storage và session đưa vào account/system menu hoặc một status cluster rõ ràng.
- Hướng dẫn đưa vào help menu, không chiếm vị trí primary.
- Dùng icon SVG cùng kích thước và accessible name thống nhất.

### 5.2 KPI bar

Bốn stat card ở [`frontend/index.html`](../frontend/index.html:3003) phù hợp với dashboard, nhưng cần kiểm tra hierarchy giữa:

- Số lượng tổng.
- Số lượng đang xử lý.
- Held/error.
- Số lượng sẵn sàng.

Nếu tất cả card có cùng kích thước, cùng màu và cùng độ nhấn, người dùng khó biết metric nào cần hành động. Held/error nên có reason/action chứ không chỉ là số màu đỏ.

**Đề xuất:**

- Chỉ một metric primary; các metric còn lại secondary.
- Clickable KPI phải có affordance rõ và filter tương ứng.
- Label dùng cùng cấu trúc: `[Noun] · [meaning]`, không trộn câu đầy đủ với noun phrase.
- Luôn hiển thị last updated hoặc freshness khi dữ liệu có thể stale.

### 5.3 Toolbar/filter

Toolbar có status tabs, search và sort. Đây là pattern đúng cho list, nhưng các label dài như “Đủ thời lượng (Chờ ráp)” và “Cần ưu tiên gen (Thiếu nhiều)” dễ gây wrapping trên tablet/mobile.

**Đề xuất copy ngắn hơn:**

| Hiện tại | Gợi ý label | Tooltip/helper |
|---|---|---|
| Tất cả | Tất cả | — |
| Đang chờ video | Chờ video | Chưa đủ video cho các shot |
| Đủ thời lượng (Chờ ráp) | Chờ ráp | Đã đủ thời lượng, chưa ghép |
| Đã ráp thành phẩm | Đã ráp | — |
| Cần ưu tiên gen (Thiếu nhiều) | Thiếu nhiều nhất | Ưu tiên item thiếu nhiều thời lượng |

Không nên dùng tooltip để cứu một label vốn có thể viết ngắn hơn; tooltip chỉ dành cho context bổ sung.

### 5.4 Card density

Todo card là một composite component với title, id, status, progress, duration, held count, quick actions và click toàn card. Mật độ cao là hợp lý cho tool, nhưng cần phân cấp:

- Header: title + status.
- Primary metric: tiến độ audio/video.
- Risk: held/error.
- Actions: một action chính, các action phụ trong overflow.

Nếu mọi dòng đều có icon/label/badge, card sẽ trở thành “wall of metadata”. Dữ liệu ít quan trọng nên nằm trong hover/detail hoặc condensed line.

### 5.5 Shot grid

Desktop dùng bốn cột tại [`frontend/index.html`](../frontend/index.html:1470), sau đó giảm xuống ba, hai và một cột theo breakpoint. Bốn cột phù hợp cho overview nhanh, nhưng với video/image review có thể làm thumbnail quá nhỏ, đặc biệt khi card chứa narration, duration, status và nhiều button.

**Khuyến nghị:**

- Xác định một minimum card width dựa trên nội dung, không chỉ dựa trên số cột.
- Cho phép user chuyển giữa “compact grid” và “review grid” nếu số shot lớn.
- Ở màn hình khoảng 900–1200px, ưu tiên ba cột nếu bốn cột làm text wrap quá nhiều.
- Giữ action chính visible; action hiếm dùng đưa vào menu.

### 5.6 Checklist

Việc biến table thành card trên mobile là quyết định tốt. Tuy vậy, khi header bị ẩn, `data-label` phải đủ rõ và thứ tự đọc phải tự nhiên. Các trường dài như narration, status, video/audio control có thể làm card rất cao và khó scan.

**Cần kiểm chứng:**

- Card có heading/identifier rõ trước các field không.
- Mỗi field có label lặp lại khi screen reader đọc.
- Button không bị dồn sát nhau ở 320–359px.
- Long narration có line clamp + “xem thêm” hay không.
- Các status/action không chỉ phân biệt bằng màu.

### 5.7 Short Studio

Layout hai cột ở desktop và một cột từ 900px là hợp lý về kỹ thuật. Về UX, mobile cần quyết định rõ thứ tự:

1. Chọn news/short.
2. Chỉnh script.
3. Xem preview.
4. Kiểm tra timeline/audio.
5. Export/publish.

Nếu preview phone bị đẩy xuống sau toàn bộ editor, người dùng khó nhận feedback sớm. Nếu preview đặt trước editor nhưng chưa chọn short, nó chiếm nhiều không gian với empty state.

**Đề xuất:** trên mobile đặt một preview compact sticky/collapsible gần header của short đang chọn; các panel dài còn lại nằm sau.

---

## 6. Naming, copy và text clarity

### 6.1 Nguyên tắc copy nên áp dụng

- Một label chỉ nên trả lời một câu hỏi: “Đây là gì?” hoặc “Bấm để làm gì?”.
- Dùng động từ cho action: “Làm mới”, “Tải lên”, “Xem video”, “Ráp video”.
- Dùng danh từ cho navigation: “Tổng quan”, “Shots”, “Checklist”, “Tư liệu”.
- Không dùng implementation detail làm tên chức năng: “Lazy load”, “Infinite scroll”, “Tab 6”.
- Viết hoa nhất quán; tránh Title Case kiểu tiếng Anh trong câu tiếng Việt.
- Không trộn “gen”, “render”, “assemble”, “publish”, “upload” nếu người dùng không cần phân biệt kỹ thuật.

### 6.2 Các label cần chuẩn hóa

| Nhóm | Vấn đề | Hướng chuẩn hóa |
|---|---|---|
| `Shot` / `Video` / `Item` / `Todo` / `Job` | Nhiều noun chỉ cùng hoặc gần cùng một đối tượng | Chọn glossary: “Công việc”, “Shot”, “Video thành phẩm”; chỉ dùng “Item” trong code/API. |
| `gen` | Từ nội bộ, không phải người dùng nào cũng hiểu | “Tạo video” hoặc “Sinh video”; có thể giữ “gen” trong tooltip kỹ thuật. |
| `assemble` / `ráp` | Trộn tiếng Anh và tiếng Việt | Chọn “Ráp video” xuyên suốt UI. |
| `Short Studio` | Tên product có thể giữ, nhưng page subtitle cần giải thích | “Short Studio · Tạo video dọc” ở lần đầu. |
| `BGM` | Viết tắt | “Nhạc nền (BGM)” lần đầu, sau đó “Nhạc nền”. |
| `Ref`, `KF` | Viết tắt khó đoán | “Ảnh tham chiếu”, “Keyframe”; chỉ dùng viết tắt trong metadata compact. |
| `Agent` | Có thể là khái niệm nội bộ | “Hướng dẫn trợ lý” nếu user không trực tiếp làm việc với agent. |
| “Tải lên & Tinh chỉnh Audio” | Dài, trộn ngôn ngữ | “Audio” hoặc “Âm thanh”; ví dụ “Tải lên âm thanh”. |
| “Video Thành Phẩm” | Viết hoa không nhất quán | “Video thành phẩm”. |
| “Lưới Shot Video” | Dịch word-by-word | “Danh sách shot” hoặc “Các shot video”. |
| “Bảng Checklist” | Redundant | “Checklist” hoặc “Kiểm tra”. |

### 6.3 Emoji và icon text

Các label như `📊`, `🏆`, `🎬`, `📋`, `🖼️`, `📤`, `🔄`, `🔒`, `☁️` đang tạo cảm giác thân thiện nhưng có các nhược điểm:

- Render khác nhau giữa Windows, macOS, browser và font fallback.
- Baseline, màu và kích thước không ổn định.
- Emoji có thể bị đọc bằng tên dài bởi screen reader.
- Không thể hiện cùng một icon grammar.
- Một số emoji mang nghĩa cảm xúc khác với tác vụ.
- Khi UI đã có màu status và nhiều metadata, emoji làm hierarchy nhiễu hơn.

**Đề xuất:**

- Icon UI: inline SVG hoặc icon font có stroke/fill thống nhất.
- Icon decorative: `aria-hidden="true"`.
- Icon-only button: có accessible name và tooltip.
- Không đặt emoji trong heading/tab trừ khi đó là nội dung thương hiệu có chủ đích.
- Nếu cần phân biệt tab bằng icon, icon phải phụ trợ; text vẫn là nguồn nghĩa chính.

### 6.4 Error copy

`extractApiError()` đã cố gắng lấy message human-readable, đây là điểm tốt. Tuy nhiên việc nối `(HTTP 400)`/`(HTTP 500)` vào message người dùng thường không giúp họ xử lý lỗi.

Nên tách:

- Message chính: người dùng cần biết chuyện gì xảy ra.
- Next action: thử lại, kiểm tra tài khoản, liên hệ admin.
- Technical detail: ẩn trong expandable “Chi tiết kỹ thuật” hoặc copy-to-clipboard.

Ví dụ:

> Không tải được video cho shot 03. Tài khoản VideoGen đã hết quota. Có thể thử lại sau 14:30 hoặc đổi provider.
>
> Chi tiết kỹ thuật: HTTP 429 · request id …

---

## 7. Buttons và interaction patterns

### 7.1 Button hierarchy

Cần duy trì tối đa ba cấp:

1. **Primary:** một hành động chính trong một vùng.
2. **Secondary:** hành động hỗ trợ.
3. **Tertiary/ghost:** điều hướng hoặc hành động ít rủi ro.

Hiện tại nhiều action nằm cạnh nhau trong card và topbar. Nếu button nào cũng có border/background, tất cả sẽ cạnh tranh.

**Đề xuất:**

- Mỗi card chỉ có một primary action.
- Xóa, hủy, reset dùng destructive style nhưng không cần màu đỏ toàn diện; đặt trong confirmation dialog.
- Action hiếm dùng trong `…` menu.
- Loading button cần giữ label và đổi thành trạng thái “Đang xử lý…”, không chỉ thay icon.

### 7.2 Touch target

Design lock yêu cầu tối thiểu 24px, và `.btn-xs` đã được tăng lên mức này theo lịch sử review. Tuy nhiên 24px là floor accessibility, không phải target lý tưởng cho touch.

Khuyến nghị thực tế:

- Button chính/touch: 40–44px.
- Icon button: vùng hit 36–44px dù icon chỉ 16–20px.
- Khoảng cách giữa hai destructive/adjacent actions: ít nhất 8px.
- Không đặt icon-only buttons sát cạnh thumbnail/video controls.

### 7.3 Native `alert()` và `confirm()`

Các call ở [`frontend/index.html`](../frontend/index.html:5310) và các vùng upload/delete/assemble tạo ra hai hệ thống feedback song song.

Nhược điểm:

- Không giữ được visual context của app.
- Không có title, affected item, retry hoặc undo.
- Chặn main thread và gây gián đoạn.
- Trên mobile, native dialog có thể che nội dung quan trọng.
- Không thể thống nhất với toast/banner/modal hiện có.

**Phân loại nên dùng:**

| Tình huống | Pattern |
|---|---|
| Success nhỏ, không cần quyết định | Toast |
| Warning cần đọc nhưng không chặn | Inline banner |
| Xóa/reset có hậu quả | Modal confirm có tên object + consequence + cancel |
| Lỗi cần sửa ở form | Inline field error + summary |
| Lỗi hệ thống/ảnh hưởng nhiều item | Persistent banner hoặc error panel |

### 7.4 Clickable card và nested button

Todo card dùng `article role="button" tabindex="0"` nhưng bên trong có nhiều button. Đây là điểm đáng ưu tiên vì:

- Một `article` không nên giả lập button nếu chứa interactive descendant.
- Screen reader có thể đọc card như một button lớn rồi gặp button lồng bên trong.
- Enter/Space trên card có thể cạnh tranh với button bên trong.
- `event.stopPropagation()` giải quyết click bubbling nhưng không giải quyết hoàn toàn semantic model.
- Focus order có thể dài và khó đoán.

**Pattern an toàn hơn:**

- `article` là container semantic.
- Title là link tới detail, chiếm vùng click chính.
- Các action là button thật bên cạnh.
- Nếu muốn click cả card, dùng overlay link có accessible name nhưng phải tránh che các button.
- Không dùng global handler để bù cho HTML semantics nếu có thể dùng native link/button.

Điểm tương tự áp dụng cho `detail-flow-jump-card`.

### 7.5 Tooltips

Icon-only hoặc label rút gọn cần tooltip, nhưng tooltip không nên là nơi duy nhất chứa tên action quan trọng. Tooltip cần:

- Hiện bằng hover và keyboard focus.
- Không bị cắt ở viewport.
- Có delay ngắn, không gây lag.
- Có tên rõ ràng, không lặp icon.

---

## 8. Status, held state và domain visibility

### 8.1 Điểm tốt

`STATUS_LABELS` và `statusPillClass()` đã chuyển nhiều raw enum thành label tiếng người. Đây là cải thiện quan trọng được ghi nhận trong [`docs/UI_REVIEW.md`](UI_REVIEW.md:29).

### 8.2 Ma trận status cần kiểm tra

Mỗi status ở item, shot, provider và export nên có đủ:

| Thành phần | Câu hỏi UX |
|---|---|
| Label | Người dùng có hiểu tình trạng hiện tại không? |
| Reason | Vì sao nó ở trạng thái này? |
| Impact | Việc gì đang bị ảnh hưởng? |
| Next action | Bấm gì để tiếp tục? |
| Owner | Người dùng hay hệ thống cần xử lý? |
| Freshness | Trạng thái cập nhật lúc nào? |
| Recovery | Retry, đổi provider, upload lại hay bỏ qua? |

### 8.3 Held không được chỉ là màu đỏ

Held cần xuất hiện như một object có lý do tiếng người và action ngay tại chỗ. Ví dụ tốt:

> Shot 03 đang giữ vì thiếu 4.2 giây video. Tạo lại video / Tải video lên / Bỏ qua shot.

Không nên chỉ hiển thị:

> `held_review` · màu đỏ · —

Cần rà soát các vùng:

- KPI held.
- Todo card.
- Shot card.
- Timeline gap.
- Checklist row.
- Provider/quota banner.
- Final assembly state.

### 8.4 Màu và text

Màu đỏ/cam/xanh phải đi cùng label, icon hoặc reason. Status pill chỉ bằng màu không đủ cho color-blind users và không đủ khi nhìn ở grayscale.

---

## 9. Loading, empty, success và error states

### 9.1 Loading

Loading panel hiện có, nhưng cần phân biệt:

- Initial page loading.
- Refresh toàn bộ.
- Lazy load một tab.
- Infinite scroll đang tải thêm.
- Action riêng đang chạy trên một shot.

Mỗi state nên giữ context. Không nên biến cả page thành spinner khi chỉ một card đang upload.

**Khuyến nghị:** dùng skeleton cho layout ổn định ở initial list; dùng inline spinner cho action cục bộ; dùng progress cho upload/render có thời lượng.

### 9.2 Empty state

Empty state nên trả lời ba câu:

1. Không có gì vì sao?
2. Người dùng có cần làm gì không?
3. Button chính là gì?

Ví dụ:

- Chưa chọn Short: “Chọn một Short trong danh sách để xem preview.” — tốt về hướng dẫn, nhưng có thể thêm “Hoặc tạo Short mới”.
- Chưa có ảnh: cần phân biệt “chưa upload”, “API chưa trả về”, “không có ảnh cho shot này”.
- Chưa có video: cần cho biết thiếu video do chưa tạo, đang xử lý hay bị held.

### 9.3 Error state

Không nên dùng một error text chung cho mọi layer. Error panel cần scope rõ: page, tab, card hay field.

Mỗi error nên có:

- Scope/object bị lỗi.
- Lý do ngắn.
- Nút retry phù hợp.
- Link/help nếu cần.
- Technical detail ẩn.

### 9.4 Success state

Success sau upload/assemble nên cho biết kết quả và next step. Toast biến mất quá nhanh không phù hợp với action quan trọng như ráp thành phẩm hoặc publish package.

---

## 10. Responsive audit theo breakpoint

Các breakpoint được khai báo ở [`frontend/index.html`](../frontend/index.html:1140), gồm 1200px, 900px, 768px, 560px và 359px.

### Desktop > 1200px

**Ưu điểm:** đủ không gian cho KPI, toolbar, grid và detail columns.

**Rủi ro:**

- Quá nhiều vùng cùng cạnh tranh theo chiều ngang.
- Shot grid 4 cột có thể khiến review media nhỏ.
- Topbar có thể chứa quá nhiều action mà không lộ vấn đề overflow.
- Khoảng trống lớn giữa content nếu layout ưu tiên card đều nhau.

**Cần kiểm tra:** max-width content, line length của narration, sticky header và vị trí action khi scroll.

### 900–1200px

Đây thường là vùng tablet landscape/small laptop dễ bị bỏ quên. Các panel còn đủ để nằm cạnh nhau nhưng text bắt đầu wrap.

**Rủi ro cao:**

- Label filter thành hai dòng, làm toolbar cao bất thường.
- Topbar action bị co hoặc wrap.
- Grid bốn cột quá hẹp.
- Short Studio hai cột vẫn còn nhưng preview/editor mỗi bên thiếu không gian.

**Đề xuất:** xem 1024px là breakpoint thiết kế thực tế, không chỉ breakpoint CSS. Cho phép wrap có chủ ý và giảm secondary labels.

### 768–900px

Nhiều layout chuyển đổi tại 900px và 768px. Cần tránh trạng thái “lưng chừng” nơi sidebar đã xuống dưới nhưng toolbar vẫn cố giữ một hàng.

**Đề xuất:**

- Chuyển toolbar thành stack có thứ tự: status → search → sort.
- Giữ filter đang chọn visible sau khi scroll.
- Đưa quick navigation vào select/menu thay vì sáu nút ngang.

### 560–768px

Đây là mobile landscape và tablet portrait. Hai cột checklist/card có thể vẫn dày.

**Rủi ro:**

- Buttons dài bị xuống dòng.
- Detail tabs ngang gây overflow hoặc cần scroll mà không có cue.
- Modal rộng gần bằng viewport, nội dung khó đọc.
- Video/phone preview cao, đẩy action xuống.

### 359–560px

Đây là vùng mobile chính. Breakpoint 359px cho thấy đã nghĩ đến màn hình hẹp, nhưng cần kiểm tra layout thật.

**Checklist kiểm thử:**

- Không có horizontal scroll ngoài vùng tabs/gallery có chủ ý.
- Text tiếng Việt có dấu không bị cắt.
- Button destructive vẫn đọc đủ label.
- Input/select không vượt viewport.
- Modal có thể đóng và scroll nội dung.
- Touch target không bị co dưới 36px dù CSS floor là 24px.
- Card title, status và ID có hierarchy rõ.

### ≤359px

Đây là edge case nhưng nên giữ graceful degradation:

- Ẩn label phụ, giữ icon + accessible name.
- Stack toàn bộ actions.
- Không ép hai field nằm cùng hàng nếu text bị cắt.
- Cho phép detail tabs scroll ngang có mask/gradient cue nhẹ hoặc menu thay thế.
- Không để `overflow-x: hidden` che mất content quan trọng.

### Responsive không chỉ là số cột

Cần đánh giá thêm:

- Thứ tự đọc khi DOM stack.
- Sticky/fixed element có che input không.
- Focus ring có bị clipped bởi overflow không.
- Keyboard viewport khi mobile browser mở bàn phím.
- Video ratio và thumbnail crop.
- Long title/narration.
- Locale tiếng Việt với từ dài và dấu.

---

## 11. Accessibility audit

### 11.1 Điểm đã làm tốt

- Có focus-visible rõ ở [`frontend/index.html`](../frontend/index.html:210).
- Có reduced-motion ở [`frontend/index.html`](../frontend/index.html:237).
- Nhiều button có `type="button"`.
- Tabs có `role="tablist"`, `role="tab"`, `aria-selected`.
- Form input có label/aria-label ở nhiều nơi.
- Custom clickable cards đã có keyboard Enter/Space.
- `lang="vi"` được khai báo ở document root.

### 11.2 Tab pattern

Nếu dùng ARIA tab thật, cần đảm bảo:

- Arrow Left/Right di chuyển giữa tabs.
- Home/End hoạt động nếu tablist dài.
- `aria-controls` trỏ tới đúng panel.
- Panel có `role="tabpanel"`, `aria-labelledby`.
- Tab inactive có semantics đúng.
- Focus model nhất quán: automatic activation hay manual activation.

Hiện evidence cho thấy có `role="tab"` và `aria-selected`; cần kiểm tra đầy đủ các phần còn lại. `aria-label="6 tab lazy load xử lý công việc"` cũng là label implementation-heavy và không tự nhiên với user/screen reader.

Gợi ý: `aria-label="Các phần của công việc"`.

### 11.3 Modal

Các modal có `role="dialog"`, `aria-modal` và `aria-labelledby`, đây là nền tảng đúng. Cần kiểm tra thêm:

- Focus được chuyển vào modal khi mở.
- Focus quay lại trigger khi đóng.
- Escape đóng được nếu không có lý do ngăn.
- Tab không thoát ra background.
- Body scroll lock không làm layout nhảy.
- Close button có accessible name.
- Dialog destructive có heading và consequence rõ.

### 11.4 Focus và visual states

Focus ring xanh trên nền trắng phù hợp, nhưng cần kiểm tra contrast ở mọi component, đặc biệt trên status background và thumbnail overlay. Hover không được là cách duy nhất để nhận biết clickable.

### 11.5 Screen reader và live updates

Loading, toast, upload progress và status change nên dùng `aria-live` phù hợp, tránh đọc quá nhiều nội dung. Không nên render lại một container lớn khiến screen reader đọc lại toàn bộ card list sau mỗi action.

### 11.6 Zoom và text resize

Cần test 200% zoom và browser text-only zoom. Những vùng có fixed height, absolute overlay, phone preview và toolbar nhiều control là ứng viên rủi ro.

### 11.7 Motion

Reduced motion đã có. Cần rà soát transition để đảm bảo không animate layout/background/border không cần thiết; giữ motion ngắn, có mục đích và không làm status change khó nhận biết.

---

## 12. Short Studio: consistency review

Short Studio là page lớn thứ ba, có news/decode mode, news inbox, script editor, short list, phone preview, timeline, audio, download và publish package. Đây là một workflow có giá trị nhưng có nguy cơ trở thành “ứng dụng con” với ngôn ngữ riêng.

### Điểm cần đồng bộ với Todo flow

| Chủ đề | Rủi ro | Đề xuất |
|---|---|---|
| Navigation | Short có page/detail flow khác Todo | Dùng cùng pattern page header, breadcrumb/back và selected state. |
| Status | Short có draft/render/published nhưng có thể khác pill conventions | Dùng cùng status taxonomy hoặc glossary riêng được ghi rõ. |
| Empty state | Phone preview có empty message riêng | Dùng cùng layout: reason + next action + optional help. |
| Buttons | Download/publish/refresh có thể khác cấp độ visual | Dùng chung primary/secondary/destructive hierarchy. |
| Copy | “news”, “decode”, “hook”, “beat”, “loop” | Hiển thị tiếng Việt trước; thuật ngữ chuyên môn trong hint. |
| Preview | Phone frame có tính presentation cao | Giữ preview là công cụ kiểm tra, không để lấn át editor. |
| Mobile | Two-column desktop thành vertical dài | Có preview compact/collapsible và sticky action. |

### Mode tabs

`Tin nóng` và `Giải mã tiền bạc` là cách đặt dễ hiểu. Phần hint dài như “25–40s · 80–110 từ · hook…” hữu ích cho expert nhưng có thể làm tab/card cao. Nên tách:

- Label ngắn.
- Một dòng goal.
- Chi tiết target trong helper text hoặc info popover.

### Publish package

Publish là action có hậu quả và có thể tạo nhiều file. Modal nên hiển thị checklist đầu ra:

- MP4.
- Subtitle SRT/ASS.
- Metadata/caption.
- Thumbnail nếu có.
- Trạng thái từng file.

Không nên chỉ hiện success chung nếu một artifact thất bại.

---

## 13. Kiến trúc file và tác động đến UI consistency

[`frontend/index.html`](../frontend/index.html) hiện chứa khoảng 7.668 dòng gồm:

- Design tokens và toàn bộ CSS.
- Static markup.
- State.
- Routing.
- API calls.
- Template rendering.
- Modal behavior.
- Todo flow.
- Short Studio.

Đây không phải bug UX trực tiếp, nhưng là **UX risk dài hạn**:

- Cùng một button pattern dễ bị copy thành nhiều biến thể.
- Một label sửa ở page này không được sửa ở page kia.
- CSS selector dễ bị override ngoài ý muốn.
- Inline style làm component không có source of truth.
- Accessibility fix khó áp dụng đồng bộ.
- Review visual diff khó vì toàn bộ giao diện nằm trong một file.

**Đề xuất kiến trúc, chưa yêu cầu triển khai ngay:**

1. Giữ nguyên behavior hiện tại, lập component inventory trước.
2. Tách token/theme, primitives, shared components và page-specific styles.
3. Tạo glossary copy trong một file hoặc object duy nhất.
4. Tạo icon registry.
5. Tách rendering theo page/feature sau khi có test snapshot hoặc smoke test.
6. Không refactor business logic trong đợt UI polish đầu tiên.

---

## 14. GitHub/reference tham khảo

Các nguồn sau được dùng như reference về nguyên tắc/pattern, không phải nguồn để copy nguyên visual:

- [Refactoring UI](https://github.com/refactoringui) — tư duy hierarchy, spacing, typography và visual prioritization.
- [Every Layout](https://every-layout.dev/) — layout responsive theo intrinsic behavior thay vì breakpoint-only thinking.
- [Inclusive Components](https://github.com/Heydon/inclusive-components) — pattern accessible cho tabs, dialogs, menus và content structure.
- [Radix Primitives](https://github.com/radix-ui/primitives) — tham khảo interaction/accessibility contract cho dialog, tabs, tooltip, dropdown.
- [shadcn/ui](https://github.com/shadcn-ui/ui) — tham khảo cách tổ chức primitive và variant; không nên copy nguyên style nếu design lock yêu cầu khác.
- [IBM Carbon Design System](https://github.com/carbon-design-system/carbon) — dashboard density, data table, status, notification và enterprise workflow.
- [GitHub Primer](https://github.com/primer/design) — conventions cho action hierarchy, navigation, feedback và accessibility.
- [Material Design](https://github.com/material-components/material-components-web) — tham khảo touch target, dialog, responsive behavior; cần chọn lọc vì visual language khác design lock.
- [awesome-selfhosted](https://github.com/awesome-selfhosted/awesome-selfhosted) — nguồn khám phá các ứng dụng self-hosted/dashboard; không phải UI guideline trực tiếp.

### Cách áp dụng reference vào dự án

- Lấy **nguyên tắc** từ Carbon/Primer: scanability, status semantics, data density.
- Lấy **interaction contract** từ Radix/Inclusive Components: focus, keyboard, modal, tab.
- Lấy **layout reasoning** từ Every Layout: min width, wrapping, intrinsic layout.
- Không nhập các pattern marketing như gradient hero, glassmorphism, symmetric card grid; chúng trái với [`config/DESIGN.md`](../config/DESIGN.md).

---

## 15. Danh sách vấn đề ưu tiên

### Blocker / P0

| ID | Vấn đề | Tác động | Bằng chứng | Đề xuất |
|---|---|---|---|---|
| P0-01 | Nested interactive: card `role="button"` chứa buttons | Keyboard/screen reader semantics khó đoán | [`frontend/index.html`](../frontend/index.html:3520) | Chuyển title/card navigation thành link; card là container semantic; action tách riêng. |
| P0-02 | Held có nguy cơ chưa luôn có reason + action tại chỗ | Người dùng không biết vì sao bị chặn và xử lý thế nào | Status mapping [`frontend/index.html`](../frontend/index.html:3210) | Audit state matrix toàn hệ thống; reason/action/impact bắt buộc. |
| P0-03 | Tab semantics chưa đủ evidence | Screen reader có thể không điều hướng tab đúng | Tablist [`frontend/index.html`](../frontend/index.html:4070) | Bổ sung/kiểm tra tabpanel, aria-controls, arrow key, focus model. |

### High / P1

| ID | Vấn đề | Tác động | Đề xuất |
|---|---|---|---|
| P1-01 | Emoji dùng làm icon/action quá rộng | Visual không ổn định, thiếu professional consistency | Icon registry SVG, emoji chỉ là content. |
| P1-02 | Native alert/confirm trộn với toast/modal | Workflow bị gián đoạn, feedback không nhất quán | Custom dialog/banner/toast theo severity. |
| P1-03 | Navigation quá nhiều lớp | Tăng cognitive load, khó biết context | Giảm quick-jump; nhóm tabs; một primary nav mỗi page. |
| P1-04 | Copy trộn Việt/Anh/viết tắt | Người mới khó hiểu, labels wrap | Chuẩn hóa glossary và copy matrix. |
| P1-05 | Error lộ HTTP status trong message chính | Technical detail lấn át next action | Tách user message và technical detail. |
| P1-06 | Short Studio có nguy cơ visual language riêng | Sản phẩm thiếu cảm giác thống nhất | Dùng shared primitives, status và empty/error patterns. |
| P1-07 | Responsive vùng 900–1200px chưa chắc đủ thoải mái | Tablet/small laptop dễ bị wrap/density | Test theo content width, ưu tiên intrinsic min-width. |

### Medium / P2

| ID | Vấn đề | Đề xuất |
|---|---|---|
| P2-01 | Inline styles rải trong markup | Đưa vào class/token có tên nghĩa. |
| P2-02 | Detail tab label dài và có số thứ tự | Label ngắn; step/context hiển thị riêng. |
| P2-03 | “Lazy load”, “Infinite scroll” xuất hiện trong UI semantics | Đây là implementation, không phải user goal. |
| P2-04 | KPI chưa chắc phân cấp action | Một primary metric, held có action. |
| P2-05 | Shot grid bốn cột có thể quá nhỏ | Có min card width/review mode. |
| P2-06 | Checklist mobile card có nguy cơ quá cao | Kiểm tra order, clamp narration, action grouping. |
| P2-07 | Modal focus/return focus cần kiểm chứng | Viết keyboard test checklist. |
| P2-08 | Fallback font có Roboto/system | Quyết định rõ policy theo design lock. |

### Polish / P3

| ID | Vấn đề | Đề xuất |
|---|---|---|
| P3-01 | Viết hoa không thống nhất | Chọn sentence case tiếng Việt. |
| P3-02 | Tooltip/icon affordance chưa có grammar chung | Chuẩn hóa size, stroke, placement. |
| P3-03 | Freshness của dashboard chưa nổi bật | Hiển thị cập nhật lúc nào khi cần. |
| P3-04 | Success của export/publish có thể quá ngắn | Dùng result panel có artifact checklist. |
| P3-05 | Empty state có thể thiếu alternative action | Luôn có next step hoặc lý do rõ. |

---

## 16. Backlog đề xuất theo giai đoạn

### Phase 0 — Không đổi business logic

- Chốt glossary: Công việc, Shot, Video thành phẩm, Âm thanh, Ráp video, Tạo video.
- Lập danh sách toàn bộ emoji/icon và phân loại decorative/action/status.
- Lập inventory button variants, status variants, modal, toast, empty và error.
- Chụp baseline ở 1440, 1280, 1024, 900, 768, 560, 390 và 320px.
- Viết accessibility smoke checklist.

### Phase 1 — Accessibility và consistency

- Sửa semantic card/link/button.
- Hoàn thiện tab keyboard and ARIA contract.
- Chuẩn hóa modal focus và destructive confirmation.
- Thay native dialogs trong flow chính.
- Đưa error technical detail ra khỏi message chính.

### Phase 2 — Information architecture và copy

- Rút ngắn detail tabs.
- Gom navigation phụ.
- Đồng bộ Short Studio với Todo flow.
- Chuẩn hóa status/held presentation.
- Tối ưu KPI/filter toolbar.

### Phase 3 — Visual polish và responsive

- Thay emoji bằng icon system.
- Giảm inline styles.
- Kiểm tra shot card min width và grid mode.
- Tối ưu mobile preview/editor order.
- Kiểm tra animation/motion theo design lock.

### Phase 4 — Maintainability

- Tách shared primitives và token layers.
- Tách page-specific rendering.
- Thêm visual regression/screenshot tests.
- Tạo copy glossary và UI component contract.

---

## 17. QA checklist trước khi coi UI hoàn tất

### Visual

- [ ] Không có gradient/glassmorphism/decorative icon tile trái design lock.
- [ ] Icon UI dùng một hệ thống, size/stroke nhất quán.
- [ ] Primary action nổi bật hơn secondary action.
- [ ] Status không chỉ phụ thuộc màu.
- [ ] Không còn inline style cho pattern lặp lại.
- [ ] Typography, radius, border, shadow đúng token.

### Copy

- [ ] Mỗi page có một tên rõ ràng.
- [ ] Mỗi tab có label ngắn và mô tả đúng user goal.
- [ ] Không lộ “lazy load”, “infinite scroll”, “tab 6” nếu không cần.
- [ ] Không trộn viết hoa tùy tiện.
- [ ] Thuật ngữ Anh/viết tắt có glossary hoặc helper text.
- [ ] Error message có next action, không đặt HTTP code ở foreground.

### Interaction

- [ ] Mỗi vùng chỉ có một primary action.
- [ ] Card không chứa nested interactive semantics mơ hồ.
- [ ] Delete/reset/assemble có custom confirmation.
- [ ] Loading cục bộ không khóa cả page.
- [ ] Toast/banner/modal dùng đúng severity.
- [ ] Tab có arrow navigation, panel association và focus behavior.

### Responsive

- [ ] 1440px: content không quá loãng.
- [ ] 1024px: toolbar và grid không vỡ.
- [ ] 768px: navigation phụ không chồng.
- [ ] 560px: form/button đọc được.
- [ ] 390px: không horizontal overflow ngoài chủ đích.
- [ ] 320px: không cắt action quan trọng.
- [ ] Mobile keyboard không che input/action.

### Accessibility

- [ ] Keyboard-only hoàn thành được các workflow chính.
- [ ] Focus visible đủ contrast và không bị clip.
- [ ] Screen reader hiểu page, heading, tab, dialog và status.
- [ ] Modal trap/return focus đúng.
- [ ] 200% zoom không mất chức năng.
- [ ] Reduced motion không làm mất feedback.
- [ ] Touch target đạt floor và có spacing đủ.

---

## 18. Bảng evidence tổng hợp

| Khu vực | Evidence | Mức | Ghi chú |
|---|---|---:|---|
| Tokens | [`frontend/index.html`](../frontend/index.html:12) | Tốt | Bám design lock. |
| Font | [`frontend/index.html`](../frontend/index.html:36) | Cần rà | IBM Plex đúng, fallback cần policy rõ. |
| Focus | [`frontend/index.html`](../frontend/index.html:210) | Tốt | Có focus-visible tổng quát. |
| Reduced motion | [`frontend/index.html`](../frontend/index.html:237) | Tốt | Đã có baseline đúng. |
| Page structure | [`frontend/index.html`](../frontend/index.html:2996) | Tốt | Ba page rõ, nhưng top-level naming cần review. |
| KPI | [`frontend/index.html`](../frontend/index.html:3003) | Cần rà | Cần phân cấp metric/action. |
| Toolbar | [`frontend/index.html`](../frontend/index.html:3027) | Cần rà | Label dài, responsive risk. |
| Detail tabs | [`frontend/index.html`](../frontend/index.html:4070) | High | Quá nhiều nội dung khác loại trong tablist. |
| Status labels | [`frontend/index.html`](../frontend/index.html:3210) | Tốt có điều kiện | Humanized, cần kiểm held matrix. |
| Custom keyboard | [`frontend/index.html`](../frontend/index.html:6570) | Cần rà | Bù được interaction nhưng không thay semantic HTML. |
| Native dialogs | [`frontend/index.html`](../frontend/index.html:5310) | High | Không đồng nhất feedback system. |
| Modal | [`frontend/index.html`](../frontend/index.html:3081) | Cần test | Có ARIA nền tảng; cần focus trap/return focus. |
| Responsive | [`frontend/index.html`](../frontend/index.html:1140) | Khá | Có đủ breakpoint, cần test content-driven. |
| Checklist mobile | [`frontend/index.html`](../frontend/index.html:1900) | Cần test | Card transformation tốt nhưng có density risk. |
| Short Studio | [`frontend/index.html`](../frontend/index.html:6750) | High | Cần đồng bộ IA/visual language. |
| Architecture | [`frontend/index.html`](../frontend/index.html:1) | Medium | Monolith làm tăng consistency risk. |
| Historical audit | [`docs/UI_REVIEW.md`](UI_REVIEW.md:5) | Tham khảo | Không thay thế audit mới vì file hiện tại đã mở rộng. |

---

## 19. Kết luận

Giao diện hiện tại có nền tảng tốt và đã xử lý nhiều lỗi nền tảng thường gặp: token rõ, light theme có chủ ý, status mapping thân thiện hơn, responsive rules tồn tại, focus-visible và reduced-motion đã được bổ sung. Vì vậy đây không phải trường hợp cần “đập đi làm lại”.

Hướng phù hợp là **polish có hệ thống, không rewrite nghiệp vụ**:

1. Giữ layout dashboard/pro tool và density cao.
2. Giảm cognitive load bằng cách rút gọn navigation và tab labels.
3. Chuẩn hóa copy và terminology trước khi chỉnh màu/spacing.
4. Đưa toàn bộ icon/action về một visual grammar.
5. Sửa semantic interaction và modal/accessibility trước khi polish.
6. Kiểm chứng held state ở mọi cấp, vì đây là signature behavior của sản phẩm.
7. Dùng cùng component language cho Todo và Short Studio.
8. Chỉ sau đó mới tối ưu các chi tiết responsive, grid density và animation.

**Trạng thái source:** audit này chỉ ghi nhận và đề xuất. Không có source code, business logic, API hoặc data nào được sửa trong quá trình audit.
