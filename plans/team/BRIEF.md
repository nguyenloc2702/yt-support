# BRIEF — Kênh giao tiếp giữa các agent trên production (2026-11-23)

> Tài liệu dùng chung cho PO/BA, Senior Dev và Architect. Mọi số liệu dưới đây đã
> được verify từ code + prod, không phải giả định. Nếu cần kiểm chứng thêm, đọc
> đúng file được dẫn.

## 1. Mục tiêu nghiệp vụ (owner đặt ra)

Sau này **2 agent sẽ tự nói chuyện với nhau**, cùng:
1. tự lên kịch bản video cho owner,
2. gen các shot video,
3. **tự bắt bẻ / review chéo** nhau,
4. nhắc nhở nhau việc cần làm,
5. cùng quản lý (vận hành) hệ thống video này.

Yêu cầu: chọn **hướng công nghệ phù hợp cho chức năng giao tiếp giữa các agent
bên trong hệ thống, chạy trơn tru trên production**. Không được làm gãy Agent API
v3 đang có khách/agent dùng.

## 2. Hiện trạng production (verified)

- Domain `https://vidfac.gacdero.vn` → Nginx → `127.0.0.1:8090`, systemd service
  `video-system`, code ở `/opt/video-system`, DB
  `/opt/video-system/data/video_system.sqlite3`.
- Backend: FastAPI 1 process (`uvicorn backend.app.main:app --port 8000`, prod qua
  8090), **SQLite WAL**, `Store` abstraction (`backend/app/store.py`) — mọi write
  đi qua service, CAS bằng `cas_update`.
- `GZipMiddleware(minimum_size=1000, compresslevel=5)` bật toàn cục
  (`backend/app/main.py:74`).
- Frontend SPA nhúng trong cùng app (`frontend/index.html`), auth bằng PIN session
  cookie; agent dùng `Authorization: Bearer <key>`.
- `VIDEO_API_KEYS` hiện có: `owner:admin`, `sk-video-agent-prod:ingest`,
  `ku-li-agent-1:ingest`, `ku-li-agent-2:ingest`. **Identity = phần tên của key**
  (`backend/app/auth.py:41-64`). Key chung ⇒ mọi tin cùng 1 sender.
- GET trên prod hiện **đọc mở** (read scope), POST/PATCH/DELETE cần key.
- Agent ngoài đang chạy workflow 7 bước qua REST: lấy todo → tải audio/script/ref
  → feedback → upload keyframe → upload video tuần tự → confirm-assemble
  (`video-system/AGENT_API_GUIDE.md`).
- Đã có sẵn các primitive: `items`, `todos`, `shots` (state machine + CAS),
  `events` (audit log), `todo_feedbacks`, digest submission flow có **critic pass
  bằng LLM độc lập** (Phụ lục H), Telegram bot long-poll để human-in-the-loop.

## 3. Bảng chat agent hiện tại (mới, 2026-11-23)

File: `video-system/backend/app/service_chat.py`; routes:
`backend/app/main.py:1776-1847`; UI: `frontend/index.html:7397+`;
test: `video-system/tests/test_chat_board.py` (10 case).

- Mô hình **thread 1-1, tối đa 2 participant**: `created_by` + `participant_b`
  (slot trống claim bằng CAS `UPDATE ... WHERE participant_b IS NULL`).
- Delivery: **REST polling + cursor `since_id`** (seq AUTOINCREMENT). UI poll 3s;
  agent được dặn poll 10–15s (`read_me/05_agent_chat_prompt.md`).
- **Không** long-poll / SSE / WebSocket / broker — quyết định có chủ đích
  (Phụ lục I, `plans/de_xuat_he_thong_video_v3_1_hop_nhat.md`): agent là script
  HTTP, long-poll sync đậu anyio threadpool, SSE gãy với GZip + nginx mặc định,
  <10 agent ~1 msg/phút không cần broker.
- Luật: body ≤ 2000 ký tự; 20 tin/5 phút/sender (429); 30 tin/thread → tự
  `locked`; retention 30 ngày; nội dung chỉ 2 participant + owner/admin đọc;
  metadata list public (nhưng list trả cả `last_body`).
- Đã live: 2 thread thật (`thr_e4de7987aa88` owner↔sk-video-agent-prod,
  `thr_5a569a511901` owner, slot trống). `GET .../messages` không phải
  participant → 403.

## 4. Khoảng trống so với mục tiêu (tự đọc và bổ sung)

Chat hiện tại là **kênh hội thoại**, chưa phải **lớp phối hợp công việc**:
- Không có presence/heartbeat ⇒ không biết agent còn sống hay đã chết.
- Không có inbox/ack ⇒ tin nhắn không có trạng thái "đã nhận / đang xử lý".
- Không có task assignment/claim/lease ⇒ 2 agent có thể cùng làm 1 shot.
- Không có review record có cấu trúc (verdict, evidence, blocking) ⇒ "bắt bẻ
  nhau" bằng chat trôi mất, không chặn được publish.
- Không có reminder/SLA/escalation ⇒ việc rơi vào quên lãng.
- Không có chống loop: 2 agent tự trả lời nhau vô hạn ⇒ đốt token/tiền.
- Không có idempotency ⇒ retry HTTP tạo tin trùng / gen trùng.
- Thread 1-1 cứng 2 người ⇒ không mở rộng được cho 3+ agent hoặc room theo job.

## 5. Ràng buộc (không thương lượng)

1. Không phá Agent API v3 và workflow 7 bước đang chạy.
2. Hạ tầng nhỏ: 1 VPS, SQLite WAL, 1 process; **không thêm Redis/NATS/Kafka**
   trừ khi chứng minh được lợi ích > chi phí vận hành.
3. Agent có thể là **script one-shot** (chạy xong thoát), không giả định luôn có
   daemon thường trú ⇒ push thuần tuý không đủ, phải có pull/inbox.
4. Mọi thứ phải **audit được** (ai làm gì, khi nào, trên item/shot nào).
5. Human vẫn là người chốt cuối (owner PIN + Telegram), agent không được tự
   publish vượt gate.
6. Backward compatible: agent cũ không biết tính năng mới vẫn phải chạy được.
7. Chi phí/token phải có trần (rate limit, max turns, budget).

## 6. Câu hỏi kiến trúc phải trả lời

- **Q1 Delivery**: polling tiếp, long-poll async, SSE, hay WebSocket? Trên nền
  FastAPI + GZip + nginx + SQLite, phương án nào rẻ và bền nhất? Có cần tách
  route khỏi GZip không?
- **Q2 Protocol**: chat free-text đủ chưa, hay cần **structured work protocol**
  (task/claim/review/ack) đặt cạnh chat? Ranh giới giữa "nói chuyện" và "giao
  việc" nằm ở đâu?
- **Q3 Data model**: bảng nào thêm, khóa nào, index nào, giữ SQLite được bao
  lâu, ngưỡng nào thì phải sang Postgres?
- **Q4 Authority**: 2 agent bất đồng thì ai thắng? Auto-escalate lên owner sau
  bao lâu? Ai được đánh dấu blocking?
- **Q5 Loop/cost control**: chặn agent trả lời nhau vô hạn bằng gì (max turns,
  cooldown, budget theo item)?
- **Q6 Rollout**: ship theo pha nào để không rủi ro prod, rollback ra sao, test
  gì trước khi bật cho 2 agent thật?

## 7. Định dạng đầu ra cho mỗi memo

- ≤ 1200 từ, tiếng Việt, kết luận trước.
- Nêu rõ: đề xuất, lý do, đánh đổi, rủi ro, **câu hỏi cho người còn lại** (3–5
  câu, đánh số).
- Ghi file đúng đường dẫn được giao; không sửa file khác.
