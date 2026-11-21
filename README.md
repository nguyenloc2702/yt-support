# support-video-edit

Workspace sản xuất video AI. Repo hiện có **2 hệ thống sống** + 1 service biên:

| Thành phần | Vai trò | Entry point |
|---|---|---|
| **video-system** (`video-system/`) | **Canonical production** — EN explainer pipeline, Short Studio, publish YouTube/Instagram, Agent API | `uvicorn backend.app.main:app --port 8000` |
| **Streamlit Studio** (root) | Workbench biên tập offline (rough-cut, Whisper, timeline, EBU R128) — **freeze feature mới, chỉ bugfix** | `streamlit run app.py --server.port=8501` |
| Muse Job Inbox (`services/job_inbox_api.py`) | Service biên nhận job từ Muse | `uvicorn services.job_inbox_api:app --port 8787` — **rà quyết định migration: 2026-12-15** |

## Tombstone — Automated Factory (RETIRED 2026-11-21)

Factory Telegram (`python -m core.worker`, điều khiển qua Telegram, gen qua cookie
Gemini/Veo web) đã **ngừng hoạt động và bị gỡ khỏi repo** trong đợt dọn 3 bên
PO–BA–Dev: 34 module + 36 test + Dockerfile + docker-compose.prod.yml +
requirements-worker.txt + CI worker job đã bị xóa. Tài sản đáng giá của nó
(HeavyLockLease `services/job_queue.py:60-110`, 4-gate manifest integrity
`services/approval_state_machine.py:301-339` + `core/worker.py:214-240` hiện đã
gỡ) cần port sang video-system khi có ≥2 worker vật lý — tìm lại bằng commit
hash của đợt dọn này trong `git log`.

External Video Agent API guide (chuẩn v3.x): `video-system/AGENT_API_GUIDE.md`
— serve trực tiếp tại `https://vidfac.gacdero.vn/api/v1/agent-guide`.

Chi tiết thiết kế & lộ trình: `plans/de_xuat_he_thong_video_v3_1_hop_nhat.md`,
`plans/README.md` (bảng trạng thái các plan).
