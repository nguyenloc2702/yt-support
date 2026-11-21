# plans/ — Bảng điều hành

> **Quyết định owner 2026-11-21 (phiên đánh giá affiliate):**
> 1. **Chiến lược chọn sản phẩm affiliate: hoa hồng cao trước** (ClickBank
>    marketplace lọc commission 50–75%; eBay EPN 1–4% chỉ là kênh phụ) — kèm 2
>    bộ lọc bắt buộc: tỉ lệ refund thấp + niche không vi phạm policy TikTok/IG
>    (health claims, crypto, get-rich-quick → bị ban kênh).
> 2. **KPI giai đoạn đầu: KHÔNG chạy ritual tuần** — bảng KPI 3 số dời xuống
>    backlog. Giữ nguyên đường ống đo (affiliate_clicks, IG insights job, pull
>    retention) trong P1 để khi cần nhìn số thì số có sẵn.
> 3. **Pháp lý payout (W-8BEN, thuế, FTC): owner tự ký và chịu trách nhiệm.**
>    Hệ quả: dòng disclosure "We may earn a commission…" trên bio page +
>    YouTube description là bắt buộc (P1), không phải tùy chọn.

> **Quy tắc sống của thư mục này** (chốt 3 bên PO–BA–Dev 2026-11-21):
> `plans/` chỉ chứa plan **đang sống**. Plan hoàn thành / bỏ rơi / shelved →
> `plans/archive/` (tìm lại bằng git history + bảng dưới). Feature ngoài lõi
> đóng băng của `de_xuat_he_thong_video_v3_1` phải kèm một Phụ lục đánh số
> trong cùng PR (tham khảo tiền lệ Phụ lục G — Short Studio).

## Plan đang sống (5)

| Plan | Trạng thái | Ghi chú |
|---|---|---|
| `de_xuat_he_thong_video_v3_1_hop_nhat.md` | **NỀN KIẾN TRÚC** | Đóng băng lõi; feature mới = Phụ lục đánh số (H, I,…) trong cùng PR |
| `muse_video_generation_migration_plan.md` | CHỜ DUYỆT | Rà quyết định Muse inbox: **2026-12-15** — nếu không duyệt thì Muse inbox retire kèm factory |
| `visual_standard.md` | ĐANG SỐNG | Chuẩn đo benchmark visual; tooling: `core/smoothness` + `tools/reference_bench.py` |
| `visual_standard_implementation_plan.md` | ĐANG SỐNG | Cặp đôi với visual_standard.md |
| `motion_smoothness_architecture.md` | TREO | Tooling đo đã có; quyết định ship khi rà visual_standard |

## Plan đã archive (11) — xem `plans/archive/`

| Plan | Lý do archive |
|---|---|
| `ke_hoach_youtube_publish_v1_1.md` | Đã code xong: `video-system/backend/app/service_youtube.py`, `main.py` |
| `ke_hoach_instagram_reels_affiliate_v1.md` | Đã code xong: `service_instagram.py` — **còn thiếu insights job (P1)** trước khi kill criteria chạy được |
| `workflow_5_steps_plan.md` | Đã code xong: `pages/00_workflow.py` |
| `four_gate_approval_test_plan.md` | Đã code xong (thuộc factory đã retire — tài sản 4-gate giữ lại bằng git history, xem tombstone README) |
| `ke_hoach_tiktok_reels_affiliate_us_v1.md` | **SHELVED tới 2027-01**; điều kiện rà: số liệu IG từ P1 insights job |
| `agent_deployment_plan.md` | Cũ — mô hình agent đã thay bằng Agent API v3.x |
| `bottleneck_fix_plan.md` | Cũ — đã xử lý / không còn ngữ cảnh |
| `video_reverse_engineering_plan.md` | Cũ — chức năng reverse-engineering nằm trong pipeline phân tích hiện tại |
| `script_quality_upgrade_plan_vi_en.md` | Cũ — thay bằng 2-stage Story Architect (pipeline_en) |
| `vlm_custom_provider_plan.md` | Cũ — provider được resolve qua `settings_service.py` hiện tại |
| `word_budget_gate_fix_plan.md` | Cũ — budget WPM quản tại `video-system/config/short_config.yaml` |
