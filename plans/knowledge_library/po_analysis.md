# Knowledge Library — Phân tích nghiệp vụ (PO/BA)

> Trạng thái: DRAFT để review 3 bên · Ngày: 2026-11-24 · Phạm vi: repo `support-video-edit`, SPA `video-system/frontend/index.html`, API `/api/v1/read-me`.

## 1. Vấn đề thực sự

Không phải "thiếu trang đọc Markdown". Vấn đề là **thiếu tầng tuyển chọn (curation) và truy xuất nguồn (provenance)** — tầng lưu trữ đã có.

- **Phân mảnh**: ~135 file `.md` ở 10 gốc (`read_me/` 8, `docs/` 23, `skills/` 10, `plans/` 34, `plans/team/` 15, `config/DESIGN.md`, root 4). `skills/` và `video-system/skills/` trùng tên nhưng **nội dung đã lệch** (chỉ `style_bible_template.md` giống). Agent không biết bản nào chuẩn.
- **Hai nguồn sự thật**: `read_me/` là nơi agent được ghi (kể cả append log vào `03_system_notes.md`), còn contract/guide/skill thật nằm ngoài.
- **Không metadata**: không owner, trạng thái sống/chết, phiên bản, tag. 11 plan chết trong `plans/archive/` vẫn đọc được; `title` chỉ là dòng đầu file.
- **Ghi là ghi đè**: PUT ghi đè toàn bộ, PATCH `overwrite` không kiểm tra phiên bản, DELETE xóa cứng, **không audit** (bảng `events` có sẵn nhưng read-me không dùng). Một agent `ingest` xóa được `02_api_contracts.md` mà owner không biết ai làm.
- **Onboard đắt**: phải đọc 8 file `read_me` + `AGENT_API_GUIDE.md` (20KB); không có "đường đọc tối thiểu".
- **Rủi ro triển khai**: app chạy ở `/opt/video-system`; `plans/`, root `*.md`, `skills/` nằm ở `/opt/support-video-edit`. Index gốc ngoài app = đọc **bản copy có thể cũ**, và sửa ở đó sẽ bị sync sau ghi đè.

→ Vấn đề thật: *"Không biết tài liệu nào là chuẩn, còn sống, và ai được đổi nó."*

## 2. Personas & Jobs-to-be-Done

| Persona | JTBD |
|---|---|
| **Owner** | Biết cái gì là chuẩn; duyệt thay đổi vào tài liệu ảnh hưởng agent; dọn tài liệu chết; tìm lại quyết định cũ. |
| **Muse writer** | Trước khi viết: lấy đúng protocol/brief/style bible. Sau: ghi finding gắn brief, không sửa manual. |
| **Muse critic** | Đối chiếu checklist/QA, ghi verdict kèm đường dẫn tài liệu nguồn. |
| **Dev** | Đọc contract trước khi sửa API; cập nhật doc trong cùng thay đổi để doc không lệch code. |
| **Agent mới onboard** | Trong một phiên lấy đúng bộ tối thiểu (≤3 file), biết được ghi ở đâu, không phá tài liệu vận hành. |

## 3. Phạm vi

**MVP (P0 — giá trị cao/chi phí thấp)**
1. Index **read-only** toàn bộ `.md` trong gốc cho phép, có `source_root` + `scope` (canonical/draft/deprecated/archived) suy ra từ vị trí + front-matter.
2. Tìm theo tên, path, nội dung; lọc theo gốc/loại/trạng thái; kết quả nêu rõ đang đọc bản nào.
3. Viewer có mục lục heading + copy path.
4. Ghi/sửa/xóa **chỉ trong `read_me/`** (giữ hợp đồng cũ), thêm **soft delete + audit**.
5. Trang thứ 6 "Knowledge": list + filter + viewer + editor.
6. Báo cáo trùng lặp (sha256 + tiêu đề gần giống) — chỉ đọc, không tự xóa.

**Phase 2**: tag/collection; version + diff + restore; publish workflow (draft→review→published); FTS5; link-check; "reading path" onboarding; agent đề xuất sửa `docs/skills` qua proposal chờ duyệt.

**Non-goals**: không thành wiki/CMS; không upload binary; không phân quyền per-document nhiều cấp; không đồng bộ hai chiều git; không sửa `plans/archive/`; không thay git làm nguồn lịch sử.

## 4. Luồng nghiệp vụ

- **Ingest**: chọn gốc → path → validate ext/2MB/scope → kiểm trùng → `draft`; trong `read_me/agents/<identity>/` auto-publish; ngoài `read_me` phải owner promote.
- **Tìm/đọc**: lọc gốc + trạng thái → search → viewer → thấy ngay "canonical?" và "cập nhật lúc nào".
- **Sửa**: editor → gửi kèm `sha256` đã đọc → lệch thì `409` (không ghi mù) → lưu → audit + snapshot.
- **Xóa**: chỉ owner; chuyển `.trash/<ngày>/`; restore 30 ngày; purge thủ công.
- **Phân loại**: 4 loại (contract/manual, plan, skill, design-guide) × 4 trạng thái; trạng thái là dữ liệu, không suy đoán từ tên file.
- **Trùng lặp**: báo cặp → owner chọn canonical → bản kia `deprecated` + trỏ canonical (không xóa ngay).
- **Tài liệu agent tạo**: bắt buộc front-matter (`author`, `scope`, `status=draft`, `source`, `expires_at`); chỉ ghi trong namespace của mình; không sửa contract/skill.

## 5. Quy tắc & phân quyền

| Hành động | Ai | Điều kiện |
|---|---|---|
| Đọc | mọi scope `read`+ (kể cả agent mới) | Ẩn file khớp mẫu secret (key, PIN, token) |
| Tạo | `ingest` | Chỉ `read_me/`, bắt buộc front-matter |
| Sửa | `ingest` trong `read_me/agents/<self>/`; owner cho phần còn lại | Kèm `sha256`; audit bắt buộc |
| Xóa | owner/admin | Soft delete + restore 30 ngày |
| Publish/promote canonical | **owner duyệt bắt buộc** | Với contract, skill, onboarding |

## 6. Acceptance criteria (MVP)

1. 100% file `.md` trong gốc cho phép có `source_root`, `scope`, `updated_at`, `sha256`; không file ngoài danh sách lọt vào (test path traversal).
2. Tìm chuỗi có trong nội dung trả kết quả **< 1s** với kho hiện tại; zero-result gợi ý nới bộ lọc.
3. Danh sách phân biệt canonical vs deprecated; không cặp trùng nào hiển thị mà không cảnh báo.
4. Sửa bằng `sha256` cũ trả `409` và **không đổi file**.
5. Xóa đưa vào trash, restore được trong 30 ngày; không có đường xóa cứng từ UI.
6. Mọi thay đổi (create/update/delete/restore) sinh audit có actor + path + thời điểm.
7. Agent `ingest` tạo file ngoài `read_me/agents/<self>/` bị `403`.
8. Agent `ingest` không sửa được `02_api_contracts.md` / `07_agent_onboarding.md`.
9. Trang Knowledge mở từ nav; deep-link `#/knowledge/<path>` mở đúng tài liệu; back/forward hoạt động.
10. Bộ "onboarding tối thiểu" trả ≤3 tài liệu canonical, mở bằng một click.
11. Không có secret (API key, PIN, token) hiển thị trong viewer.
12. `/api/v1/read-me` cũ vẫn đúng như trước (regression test xanh).

## 7. Rủi ro & metric

**Rủi ro**: (1) hai nguồn sự thật — sửa trên VPS bị sync/deploy ghi đè; (2) agent ghi rác → kho phình, ghi đè contract; (3) index file ngoài app đọc **bản cũ** khiến agent làm theo tài liệu sai; (4) mất dữ liệu do hard delete/không audit; (5) scope creep thành wiki.

**Metric**: % tài liệu canonical có owner + trạng thái (mục tiêu 100%); số cặp trùng còn mở; thời gian agent mới lấy đủ bộ đúng (<5 phút, ≤3 file); % thay đổi có audit (100%); tỷ lệ `409` (đo tranh chấp ghi); tỷ lệ search zero-result.

## 8. Khuyến nghị API

**Tạo bounded context mới `/api/v1/knowledge`**, giữ `/api/v1/read-me` làm bề mặt tương thích (deprecated dần, không xóa).

Lý do nghiệp vụ:
- **Ngữ nghĩa khác**: `read-me` là "thư mục log/manual"; knowledge là "kho có metadata, trạng thái, phiên bản, thùng rác". Nhồi vào `prefix`/`q` không diễn đạt được lọc theo trạng thái/gốc/loại.
- **Quyền khác**: đọc toàn kho nhưng ghi rất hẹp. Gộp endpoint sẽ dính "đọc mọi thứ" với "ghi mọi thứ" — đúng lỗi hiện tại.
- **Cần trạng thái**: index, snapshot, audit, trash cần lưu trữ (SQLite), khác CRUD filesystem stateless.
- **Không phá agent đang chạy**: agent đã dùng `read-me` trong tài liệu onboarding.
- **Migrate rõ**: knowledge root `read_me` map 1-1 hành vi cũ; thêm gốc mới chỉ là cấu hình.
