# Hướng Dẫn Daily Brief — Flow Nộp Nội Dung & Chốt Đăng (Human-in-the-Loop)

> **Phiên bản:** 1.0 · 2026-11-22 · Áp dụng từ commit `5216c1b`
> **Ai đọc:** Owner (người nộp nội dung + chốt đăng) và bất kỳ AI agent/ops nào thay owner nộp brief.
> **Tóm tắt 1 dòng:** 16:00 giờ VN bot Telegram đòi nội dung → bạn nộp list tin đã chốt qua API → hệ tải ảnh → AI rà soát → render đúng template đưa tin → gửi ngược Telegram MP4 preview → bạn `/approve` → mới đăng YouTube. **Không chốt = không đăng.**

---

## 1. Timeline mỗi ngày

| Giờ (VN) | Sự kiện | Ai làm |
|---|---|---|
| **16:00** | Bot Telegram gửi tin **"đòi nội dung"**: kèm endpoint + format JSON + ví dụ curl + list resource chấp nhận | Hệ thống tự gửi |
| 16:00 → 18:00 | Bạn thu thập tin, chốt list 8–15 tin, nộp qua `POST /api/v1/digest/submit` (nộp lại nhiều lần được — bản cuối trước 18:00 tính) | Owner |
| **17:15** | Chưa nộp → bot nhắc 1 lần | Hệ thống tự gửi |
| **18:00** | Quá deadline chưa nộp → **fallback**: hệ tự chọn tin RSS trong ngày | Hệ thống tự chạy |
| Sau nộp/fallback ~5–10 phút | Hệ tải ảnh → LLM viết kịch bản → **Critic AI rà soát** → render MP4 → **Telegram gửi ngược preview + lệnh chốt** | Hệ thống |
| Khi bạn sẵn sàng | Reply `/approve <short_id>` → đăng YouTube **public ngay**; `/reject` → bỏ bản, nộp lại | Owner |
| Không chốt | **KHÔNG đăng bao giờ** (auto-publish cũ đã bị tắt khi flow này bật) | — |

Timezone của kỳ = `Asia/Ho_Chi_Minh`; cửa gom tin = **16:00 hôm trước → 16:00 hôm nay** (`window_anchor_hour: 16`).

---

## 2. API Contract — `POST /api/v1/digest/submit`

- **Base:** `https://vidfac.gacdero.vn`
- **Auth:** `Authorization: Bearer <API_KEY_INGEST>` — key scope **ingest** trong `VIDEO_API_KEYS` (format `tên:scope, ...`). Scope `read` → **403**.
- **Quy ước key (mỗi agent 1 key):** key của owner đặt tên `owner:admin` (sender hiện `owner`); agent đặt `video-agent-1:ingest`... — định danh agent = tên key.
- **Content-Type:** `application/json`

### 2.1. Request body

```jsonc
{
  "date": "2026-11-23",        // optional · YYYY-MM-DD · bỏ trống = hôm nay (giờ VN)
  "force": false,              // optional · nộp đè kỳ đang failed/rejected (kỳ approved luôn chặn)
  "stories": [                 // BẮT BUỘC · 8–15 objects (nhiều ít/hơn → 422)
    {
      "headline":   "Fed cuts rates 25bps in surprise move",
                     // BẮT BUỘC · 5–200 ký tự · tiêu đề tin (EN)
      "link":       "https://www.reuters.com/markets/fed-cut-2026",
                     // optional · http(s) · link bài gốc — hệ scrape og:image làm ảnh dự phòng
      "source":     "Reuters",
                     // optional · tên nguồn — ghép vào source_note cuối video ("Sources: ...")
      "category":   "MARKETS",
                     // optional · WORLD | MARKETS | TECH | SCIENCE | SPORTS (khác → tự ánh xạ)
      "summary":    "The Fed cut rates by 25bps, its first cut of 2026, citing cooling inflation.",
                     // ⭐ QUAN TRỌNG · 1–2 câu · Critic AI đối chiếu MỌI số liệu trong
                     //   narration/hero_stat với nguồn TỪ ĐÂY. Có summary chi tiết + số thật
                     //   → critic chính xác; bỏ trống → critic chỉ kiểm trùng/claim/hook.
      "thumbnail_url": "https://img.example.com/fed-thumb.jpg",
                     // optional · ảnh đại diện — ưu tiên dùng trước tiên
      "images":     [
        "https://img.example.com/fed-1.jpg",
        "https://img.example.com/fed-2.jpg"
      ]
                     // optional · ảnh bổ sung · chỉ dùng tối đa 4 ảnh/tin
                     //   (thumbnail + images tính chung, dư bị cắt); JPG/PNG, cạnh ≥420px
    }
    // ... tổng 8–15 tin, THỨ TỰ trong mảng = THỨ TỰ xuất hiện trên video
  ]
}
```

### 2.2. Luật ảnh (fail-tolerant — ảnh lỗi không bao giờ làm hỏng brief)

1. Ảnh được tải ngay khi nộp về `objects/digest_submissions/{date}/s{seq:02d}/` (seq = vị trí tin + 2 vì seq 1 là Hook).
2. Thứ tự ưu tiên nguồn ảnh cho từng tin: **thumbnail_url → images[] → scrape og:image từ link → Wikipedia/b-roll theo từ khóa**.
3. Ảnh chết / <8KB / cạnh <420px / tỉ lệ lệch (0.42–2.6) / trùng hash → **skip, không báo lỗi**.
4. Preview Telegram sẽ cảnh báo số tin đang dùng ảnh b-roll thay ảnh gốc.

### 2.3. Responses

| Code | Khi nào | Body ví dụ |
|---|---|---|
| **200** | Chấp nhận — build chạy nền (thread) | `{"submission_id":"dsub_9f2a1c4d","date":"2026-11-23","stories":12,"warnings":["Đã dùng 72h qua: Fed holds rates steady"],"building":true}` |
| **422** | Số tin ≠ 8–15 · headline thiếu/ngắn <5 · link sai scheme · JSON lỗi | `{"detail":"Số tin phải từ 8 đến 15 — nhận được 3."}` |
| **409** | Kỳ hôm nay đang `building / built / approved` mà không có `force` | `{"detail":"Kỳ 2026-11-23 đang ở trạng thái 'built' — không nộp đè được."}` |
| **403** | Key scope thấp hơn ingest (read) | deny-by-default |
| **401** | Không có/khóa sai header | — |

`warnings` trong 200 = tin trùng với tin đã dùng trong 72h qua (theo sha1(link) đối chiếu `news_items.link_hash`) — **chỉ cảnh báo, không chặn** (owner đã chốt tin).

### 2.4. Nộp lại / idempotency

- Nộp lần 2 cùng ngày: kỳ đang `requested/submitted/rejected/failed` → **replace** (bản nộp mới thắng); kỳ đang `building/built` → **409**; kỳ `approved` → luôn **409**.
- Muốn nộp đè kỳ `failed/rejected/building` → thêm `"force": true`.
- Mỗi lần nộp tạo 1 chu trình render mới (short mới, short cũ giữ nguyên trong Short Studio).

---

## 3. Sau khi submit — pipeline tự chạy

```
submit (200)
  └─> [background] tải ảnh owner (fail-tolerant)
        └─> LLM viết kịch bản từ ĐÚNG list của bạn
              (directive: USE EXACTLY these N stories IN THIS ORDER — source_index = vị trí)
              └─> 🧠 CRITIC PASS — 1 call LLM reviewer ĐỘC LẬP, kiểm:
                    • number_mismatch: số/ngày trong narration/hero_stat có khớp summary nguồn?
                    • duplicate: 2 tin cùng sự kiện?
                    • unsupported_claim: claim mạnh không có căn cứ trong nguồn?
                    • weak_hook: hook opening nhạt?
                    ├─ pass    → tiếp tục
                    ├─ fail #1 → gen lại với CRITIC FEEDBACK (bản cũ → cancelled)
                    └─ fail #2 → DỪNG: status=failed + Telegram báo danh sách lỗi
                                 + KHÔNG render (không bao giờ render bản biết lỗi)
              └─> render TEMPLATE ĐƯA TIN HIỆN TẠI (daily_digest — không đổi template)
                    (ảnh owner submit ưu tiên trước ảnh scrape; hook/outro mượn ảnh tin đầu/cuối)
              └─> status=built → 📩 Telegram: tóm tắt + MP4 preview + LỆNH CHỐT
```

---

## 4. Chốt đăng — Telegram commands

Chỉ nhận lệnh từ chat khớp `VIDEO_TELEGRAM_CHAT_ID` (whitelist — tin chat khác ignore + log).

| Lệnh | Tác dụng |
|---|---|
| `/approve <short_id>` | Đăng YouTube với `privacy_status` theo config (mặc định **`public`** — vì đã qua human gate). Idempotent: approve lần 2 → "already approved", không đăng lại |
| `/reject <short_id> [lý do]` | Bỏ bản render → kỳ về `rejected` → nộp nội dung lại được. Kỳ đã `approved` → không reject được |
| `/status` | Trạng thái kỳ hôm nay: `{date, status, stories, short_id}` |

Sau `/approve`: video upload (`upload_id` trả về trong reply), bot xác nhận. Event `digest.published` ghi vết.

---

## 5. Các endpoint đi kèm

| Endpoint | Auth | Trả về |
|---|---|---|
| `GET /api/v1/digest/today` | mở | `{date, status, short_id, stories, submission_id}` kỳ hôm nay |
| `GET /api/v1/digest/submissions` | read | 30 kỳ gần nhất |
| `GET /api/v1/digest/submissions?date=YYYY-MM-DD` | read | 1 kỳ cụ thể |
| `POST /api/v1/digest/{submission_id}/approve` | admin | Đăng YouTube (bản Telegram dùng chung logic này) |
| `POST /api/v1/digest/{submission_id}/reject?reason=` | admin | Reject qua web |

Lưu ý: GET được mở public theo thiết kế web gate hiện tại; POST đều cần key/session.

---

## 6. State machine (`digest_submissions`)

```
requested ──> submitted ──> building ──> built ──> approved
                   │             │                       (điểm kết tốt)
                   │             └──> failed ──(resubmit force)──> submitted
                   └──(replace)──┘
built ──/reject──> rejected ──(nộp lại)──> submitted ──> ...
```

- 1 kỳ/ngày (`date` UNIQUE). Kỳ `approved` chốt vĩnh viễn trong ngày đó.
- Build crash giữa chừng → kẹt `building` → `/status` kiểm tra → nộp lại với `force: true`.

---

## 7. Events (bảng `events` — vết audit đầy đủ)

`digest.requested` (16:00 đã đòi) · `digest.reminder` (17:15 nhắc) · `digest.fallback_started` (18:00 dùng RSS) · `digest.submitted` · `digest.building` · `digest.completed` (có `short_id`, `auto_publish: false`) · `digest.critic_failed` (kèm issues) · `digest.failed` · `digest.published` (kèm `privacy_status`, `upload_id`) · `digest.rejected`

---

## 8. Config tham chiếu (`news_config.yaml` → `daily_digest`)

```yaml
timezone: "Asia/Ho_Chi_Minh"
run_at: "16:00"
window_anchor_hour: 16        # cửa tin 16:00 hôm trước → 16:00 hôm nay
lookback_hours: 24
dedupe_lookback_hours: 72
voice: "en-US-BrianMultilingualNeural"
send_video_to_telegram: true
submission_flow:
  enabled: true
  min_stories: 8
  max_stories: 15
  max_images_per_story: 4
  deadline_hour: "18:00"      # quá giờ → fallback RSS
  reminder_hour: "17:15"
  critic:
    enabled: true
    max_attempts: 2
  approve:
    privacy_status: "public"
youtube_auto_publish:         # legacy — bị BYPASS khi submission_flow.enabled=true
  enabled: true
  privacy_status: "private"
  publish_delay_minutes: 45
```

Đổi giờ/tắt critic/đổi privacy → sửa file, push, CI tự deploy (rsync `config/` đã có).

---

## 9. Ví dụ curl đầy đủ

```bash
# 1. Nộp brief 9 tin
curl -X POST https://vidfac.gacdero.vn/api/v1/digest/submit \
  -H "Authorization: Bearer $DIGEST_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "date": "2026-11-23",
    "stories": [
      {"headline":"Fed cuts rates 25bps in surprise move","link":"https://www.reuters.com/markets/fed-cut","source":"Reuters","category":"MARKETS","summary":"The Fed cut 25bps, first cut of 2026, citing cooling inflation.","thumbnail_url":"https://img.example.com/fed.jpg"},
      {"headline":"Apple services crosses $110B annual run rate","link":"https://www.cnbc.com/apple-services","summary":"Services revenue hit $110B; App Store rule change revealed.","category":"TECH"},
      {"headline":"Oil dips below $70 as OPEC+ confirms output hike","link":"https://www.bloomberg.com/opec-output","summary":"OPEC+ confirmed a 500k bpd hike from January.","category":"MARKETS"},
      {"headline":"EU passes landmark AI act enforcement rules","link":"https://www.ft.com/eu-ai-act","summary":"First fines expected Q1 2027, 35M euro ceiling.","category":"TECH"},
      {"headline":"Nvidia ships Blackwell Ultra racks to two hyperscalers","link":"https://www.theverge.com/nvidia-blackwell","summary":"First 20k GPU racks landed at US clouds.","category":"TECH"},
      {"headline":"Yen intervention signals grow as dollar nears 158","link":"https://www.ft.com/yen-intervention","summary":"MOF flagged action as yen neared 158 per dollar.","category":"MARKETS"},
      {"headline":"Tesla opens robotaxi waitlist in three states","link":"https://arstechnica.com/tesla-robotaxi","summary":"Waitlist live in CA, TX, AZ with $99 deposit.","category":"TECH"},
      {"headline":"Gold hits record as central banks keep buying","link":"https://www.kitco.com/gold-record","summary":"Central banks added 290 tonnes last quarter.","category":"MARKETS"},
      {"headline":"Amazon drone hubs expand across five EU cities","link":"https://www.reuters.com/amazon-drone-eu","summary":"Expansion covers 5 cities from March 2027.","category":"WORLD"}
    ]
  }'

# 2. Xem trạng thái kỳ hôm nay
curl https://vidfac.gacdero.vn/api/v1/digest/today

# 3. Xem submission theo ngày
curl "https://vidfac.gacdero.vn/api/v1/digest/submissions?date=2026-11-23"

# 4. Approve qua web (thay Telegram)
curl -X POST https://vidfac.gacdero.vn/api/v1/digest/<submission_id>/approve \
  -H "Authorization: Bearer $ADMIN_KEY"
```

---

## 10. Troubleshooting / FAQ

| Tình huống | Nguyên nhân | Xử lý |
|---|---|---|
| 16:00 không thấy tin Telegram | `VIDEO_DAILY_DIGEST_ENABLED` ≠ 1 trên VPS, hoặc Telegram chưa cấu hình | Kiểm env trên VPS + `service_news._telegram_configured()` |
| Submit bị 409 | Kỳ đang building/built/approved | `/status` xem; muốn đè → `force: true` (trừ approved) |
| Preview có nhiều tin "dùng ảnh b-roll" | Ảnh owner chết + scrape không được | Bình thường — nộp ảnh lại kỳ sau; hoặc sửa script qua web rồi produce lại |
| Không nhận được preview sau submit | Build fail (LLM/critic) | Bot gửi tin ❌/🚫 kèm lý do; xem `digest.failed`/`digest.critic_failed` trong events |
| Critic từ chối 2 lần liên tiếp | Script có số sai/trùng so với summary nguồn | Nộp `summary` đầy đủ + số thật cho từng tin, submit lại |
| `/approve` không phản hồi | Poller 409 conflict (token bị consumer khác dùng) hoặc sai chat_id | Xem log `telegram getUpdates 409`; đảm bảo chỉ 1 consumer dùng token |
| Chốt muộn sau 22:00 | Không có deadline cứng chặn approve | Vẫn approve được — chỉ là không còn tin nhắc (nhắc tối đa theo marker, không spam) |
| Muốn đổi giờ 16:00 | — | Sửa `run_at` + `window_anchor_hour` trong `news_config.yaml`, push |

---

## 11. Kiến trúc ngắn (cho dev)

- **Scheduler**: `service_digest.start_digest_scheduler` — thread 30s, branch submission (requested/reminder/fallback) theo event marker, chống spam.
- **Submit**: `submit_digest_stories` — normalize → validate 8–15 → upsert `digest_submissions` (date UNIQUE) → warning dedupe → build thread.
- **Build**: `_build_digest_from_submission` — owner_images → `shorts_svc.create(mode="daily_digest", extra_script_meta={owner_images, submission_id,...})` → `_critic_review` (1 call LLM reviewer, schema verdict) → story_links map → produce → preview Telegram.
- **Ảnh**: `pipeline_short/owner_images.download_owner_images` (dùng `story_media._download_valid_image`) → produce ưu tiên `script["owner_images"]` trước `collect_story_visuals`.
- **Bot**: `service_telegram_bot.start_telegram_bot` — getUpdates long-polling 25s, whitelist chat_id, idempotent approve, 409 → tự tắt.
- **Tests**: `video-system/tests/test_digest_submission_flow.py` (11 case — validation, build sync, critic regen/fail, approve idempotent, reject→resubmit, auto-publish bypass, nội dung tin nhắn).
