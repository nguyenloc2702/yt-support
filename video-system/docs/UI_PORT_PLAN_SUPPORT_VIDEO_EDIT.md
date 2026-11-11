# UI Port Plan — support-video-edit

## Phạm vi

Mục tiêu là port **visual system và UX fixes** đã audit trong `video-system` sang source production `support-video-edit`, nhưng **không thay thế Streamlit bằng static HTML** và không đụng business logic, database, auth, pipeline, API hoặc deployment.

Source production đã xác định:

- GitHub: `https://github.com/nguyenloc2702/yt-support.git`
- Local checkout: `C:\app\work\support-video-edit`
- Entry point: `app.py`
- Shared CSS: `assets/style.css`
- Shared UI helpers: `utils/ui.py`
- Screens: `pages/*.py`

## Kết luận audit nhanh

| Cửa | Trạng thái | Bằng chứng | Kết luận/việc cần làm |
|---|---|---|---|
| 1. Design lock | **Trượt** | `assets/style.css:6-17` dùng Inter, radius 16px, nhiều shadow; `utils/ui.py:12-57` có dark theme và teal accent | Chuẩn hóa về light-only, IBM Plex Sans/Mono, token DESIGN.md, radius tối đa 10px, một shadow |
| 2. Dashboard-first | **Trượt một phần** | `app.py:87-113` render danh sách dự án dạng các cột lặp; `pages/07_export.py` có nhiều HTML inline màu dark | Giữ dữ liệu động; gom layout vào shared primitives; chuẩn hóa số/timecode tabular |
| 3. Held/error first-class | **Chưa đạt đủ bằng chứng** | Streamlit pages có status/error riêng lẻ; chưa thấy một component dùng chung cho held reason + action | Audit từng workflow/status và tạo pattern dùng chung, không chỉ đổi màu |
| 4. Accessibility/friendly | **Trượt một phần** | Emoji được dùng như icon trong `app.py:54-64`; transition/shadow hiện rải rác; dark/light toggle trái UI lock | Bổ sung focus-visible, reduced-motion, target tối thiểu, icon + text, empty/error action |
| 5. Bare structure | **Đạt nền tảng** | App vẫn dùng Streamlit controls, forms, tabs, columns và workflow thật | Giữ nguyên semantic Streamlit; chỉ thay presentation và cấu trúc các vùng UI quá inline |

## Những gì đã làm đúng và cần giữ

- Streamlit multipage là kiến trúc runtime hiện tại; không thay bằng HTML Stitch.
- Các service/database/pipeline hiện tại phải giữ nguyên.
- `inject_pro_css()` là điểm tiêm CSS chung nên có thể làm migration theo tầng.
- `render_studio_header()` và `render_sidebar_hardware_status()` là các primitive phù hợp để chuẩn hóa trước.
- Các trang workflow có dữ liệu động và hành động thật; không được biến thành mockup.

## Blocker P0

### P0.1 — Hai hệ token/theme đang xung đột

`assets/style.css` dùng biến `--bg-app`, `--text-main`, `--accent`..., trong khi `utils/ui.py` cung cấp light/dark theme khác nhau. `config/DESIGN.md` yêu cầu light theme duy nhất và token cố định.

Kế hoạch:

1. Định nghĩa một block token production duy nhất trong `utils/ui.py` hoặc CSS shared.
2. Xóa/khóa dark theme toggle khỏi flow UI sau khi xác nhận không có nghiệp vụ phụ thuộc.
3. Map toàn bộ biến cũ về token:
   - `--bg-app` → `--bg`
   - `--bg-card` → `--surface`
   - `--text-main` → `--fg`
   - `--text-muted` → `--fg-muted`
   - `--accent` → `--accent`
4. Giữ alias tạm thời trong một migration step để không làm vỡ các page cũ.

### P0.2 — Font sai design lock

`assets/style.css:6-10` đang import và dùng Inter. Cần thay bằng IBM Plex Sans cho UI và IBM Plex Mono cho số, duration, timecode, mã kỹ thuật.

Không dùng `system-ui`, Roboto hoặc Arial làm font chính. Các inline HTML trong pages phải dần chuyển sang class shared để tránh font/color hardcode.

### P0.3 — Shape/elevation vượt lock

Hiện có `--radius-lg: 16px`, `--radius-full`, `--shadow-card`, `--shadow-card-hover`, và hover transform. Design lock chỉ cho radius 6px controls, 10px cards, một shadow `0 1px 2px rgba(0,0,0,.05)`, motion tối đa 150ms.

Kế hoạch:

- Control: 6px.
- Card/table/panel: 10px.
- Không dùng pill cho mọi badge; chỉ dùng khi status thật sự phù hợp.
- Loại shadow hover khuếch tán và giảm motion về transform/opacity tối đa 150ms.
- Thêm `prefers-reduced-motion: reduce`.

### P0.4 — Encoding tiếng Việt

Output của [`app.py`](../support-video-edit/app.py) hiện đã hiển thị dấu hiệu mojibake ở các chuỗi sidebar (`?? Workflow 5 bu?c`). Trước khi screenshot hoặc review UI phải kiểm tra toàn bộ source là UTF-8 và sửa text lỗi ở nơi có trách nhiệm.

## Kế hoạch port theo tầng

### Phase 0 — Baseline và an toàn

Không sửa code trong phase này.

- Ghi nhận `git status --short --branch` của `support-video-edit`.
- Không reset hoặc stash các thay đổi local hiện có.
- Chụp baseline các màn hình chính ở desktop/mobile.
- Lưu danh sách lỗi encoding, inline style, dark token, emoji/icon và hardcoded status.
- Chốt test command và cách chạy Streamlit trước khi bắt đầu.

### Phase 1 — Shared design system

File chính:

- `assets/style.css`
- `utils/ui.py`
- `app.py`

Việc làm:

1. Light-only token theo `config/DESIGN.md`.
2. IBM Plex Sans/Mono và tabular numerals.
3. Control/card radius và single shadow.
4. Focus-visible, reduced-motion, responsive width, overflow an toàn.
5. Chuẩn hóa header/sidebar/footer.
6. Thay emoji trang trí bằng icon không phụ thuộc emoji; icon phải đi kèm text khi truyền trạng thái.
7. Không thay đổi callback, `st.session_state`, `st.switch_page`, auth hoặc service calls.

Definition of done:

- Không còn Inter làm font chính.
- Không còn radius >10px trong shared CSS.
- Không còn dark theme là mặc định hoặc toggle nếu design lock vẫn light-only.
- CSS có reduced-motion và focus-visible.

### Phase 2 — Dashboard shell và navigation

File chính:

- `app.py`
- `utils/ui.py`
- có thể liên quan các page config.

Việc làm:

- Header có title, context và trạng thái hệ thống rõ ràng.
- Sidebar phân cấp theo workflow 5 bước; page legacy không làm nhiễu người dùng.
- Project list dùng cấu trúc hàng/bảng dày, không phải các cột rời khó quét.
- Empty state có hành động cụ thể.
- Status không chỉ dựa vào màu.
- Nút mở/xóa có label, focus và target đủ lớn.

### Phase 3 — Workflow pages

Ưu tiên:

1. `pages/00_workflow.py`
2. `pages/01_projects.py`
3. `pages/02_analysis.py`
4. `pages/03_workflow_report.py`
5. `pages/04_transcript.py`
6. `pages/05_script_review.py`
7. `pages/05_media_analysis.py`
8. `pages/06_audio_delivery.py`
9. `pages/06_timeline.py`
10. `pages/07_export.py`
11. `pages/08_analysis_io.py`

Mỗi page phải được port theo pattern:

- page header
- context/status row
- dynamic metrics
- primary action
- content table/list
- error/empty state
- secondary actions

Không copy nguyên HTML từ Stitch. Chỉ chuyển cấu trúc, token, spacing, state treatment và responsive behavior vào Streamlit primitives/custom classes.

### Phase 4 — Domain state và error UX

Đây là phần quan trọng nhất để đạt 9.5+, không chỉ làm đẹp:

- Held/failed phải có icon + label tiếng người + lý do + action.
- Provider/quota error phải hiện provider/account, thời điểm retry và shot/block ảnh hưởng nếu dữ liệu có.
- Progress phải lấy từ dữ liệu runtime, không hardcode.
- Timeline gap phải có giải thích, không để khoảng trống im lặng.
- Empty state phải nói bước tiếp theo.
- Không expose raw exception/code cho người dùng cuối; log kỹ thuật giữ ở log.

### Phase 5 — Responsive và accessibility

- Kiểm tra viewport desktop và mobile.
- Không để bảng/columns ép tràn ngang trên mobile; chuyển thành stacked rows khi cần.
- Tất cả control quan trọng keyboard reachable.
- Focus visible.
- Contrast WCAG 2.2 AA.
- `prefers-reduced-motion`.
- Target tương tác tối thiểu 24px; ưu tiên 44px cho action chính.
- Không dùng emoji làm thông tin duy nhất.

### Phase 6 — Verification và release gate

Chạy:

1. Unit/smoke tests hiện có của `support-video-edit`.
2. Streamlit startup smoke test.
3. Screenshot từng page ở desktop/mobile.
4. Search gate:
   - Inter/Roboto/system-ui làm font chính.
   - radius trên 10px.
   - gradient/glassmorphism.
   - hardcoded metrics.
   - emoji-only state.
   - generic error copy.
5. Manual review theo 5 cửa UI Review.
6. Chỉ commit khi diff chỉ chứa UI/UX và test/documentation liên quan.

## Mapping bộ fix cũ sang source production

| Bộ fix trong `video-system` | Nơi port vào `support-video-edit` | Cách port |
|---|---|---|
| Token light, IBM fonts | `utils/ui.py`, `assets/style.css` | Chuyển thành shared CSS/token |
| Header/sidebar | `utils/ui.py`, `app.py` | Giữ Streamlit actions, thay presentation |
| Card/table/metric density | `assets/style.css`, từng `pages/*.py` | Dùng class chung, giảm inline style |
| Held/error UX | workflow/report/timeline/export pages | Tạo helper/state pattern dùng chung |
| Responsive/mobile | `assets/style.css`, các page layout | CSS + tránh columns cố định |
| Reduced motion/focus | `assets/style.css` | Global accessibility layer |
| Stitch screens | Không copy nguyên HTML | Dùng làm reference thị giác בלבד |

## Rủi ro cần tránh

- Không sửa trực tiếp khi chưa tạo baseline diff.
- Không `git reset`, `git clean`, `stash` vì repo production đang có thay đổi local.
- Không commit API key, session secret, database, media hoặc verification artifacts.
- Không push hoặc deploy nếu chưa có user approval.
- Không đổi tên page hoặc route làm hỏng Streamlit navigation.
- Không đổi service/API contract trong một task UI.
- Không tuyên bố 9.5+ chỉ dựa trên grep; phải có screenshot và accessibility review.

## Đề xuất thứ tự thực thi

1. Baseline screenshot + test + diff inventory.
2. Chuẩn hóa `utils/ui.py` và `assets/style.css`.
3. Sửa shell trong `app.py`.
4. Port dashboard/projects/workflow.
5. Port analysis/review/timeline/export.
6. Chuẩn hóa held/error/empty states.
7. Responsive + accessibility.
8. Screenshot audit 5 cửa.
9. Review diff, test, commit riêng UI.

## Phạm vi hiện tại

Tài liệu này chỉ là audit và kế hoạch. **Chưa sửa file nào trong `support-video-edit`, chưa commit và chưa push.**
