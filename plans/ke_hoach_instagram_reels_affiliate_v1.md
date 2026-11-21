# Kế hoạch v1.0: Đăng Instagram Reels tự động (Graph API trực tiếp) + Hạ tầng Affiliate link

> Ngày lập: 2026-11-18 · Bám mẫu [ke_hoach_youtube_publish_v1_1.md](ke_hoach_youtube_publish_v1_1.md)
> Phạm vi: **không làm TikTok trong v1** (né rủi ro audit "tool nội bộ") — chỉ Instagram Reels + affiliate link + trang bio đo click.

## 0. Quyết định kiến trúc đã chốt

| Vấn đề | Quyết định | Lý do |
|---|---|---|
| Đăng IG qua API trung gian (Upload-Post $24/mo) hay **trực tiếp Graph API**? | **Trực tiếp Graph API** | Lý do duy nhất phải trả tiền vendor là audit TikTok — đã bỏ. Tài khoản IG của chính bạn là admin/developer của Meta app → chạy được ngay ở **Development Mode, không cần App Review**, không tốn phí, toàn quyền kiểm soát, tái dùng 100% pattern OAuth/Fernet/state machine của `service_youtube.py` |
| Biến thể token | **Instagram Login** (`instagram_business_basic` + `instagram_business_content_publish`, host `graph.instagram.com`) | Không cần link Facebook Page, ít permission hơn biến thể Facebook Login, token long-lived 60 ngày tự refresh được |
| Link affiliate để đâu | **Trang bio + redirect tự host** trên `vidfac.gacdero.vn` | Đo click theo từng video (subid), đổi link tức thì, không phụ thuộc Linktree; caption IG không click được |
| Mạng affiliate phase 1 | **ClickBank + eBay Partner Network** | Link **tự dựng URL** — không cần duyệt API, vào chương trình là dùng được ngay |

---

## 1. Mục tiêu

1. **Đăng Reels tự động** từ job todo/shorts (file `exports/*.mp4`) lên tài khoản Instagram professional của bạn qua Meta Graph API: chọn video → caption (auto `#ad` + CTA "link in bio") → đăng ngay hoặc hẹn giờ → theo dõi trạng thái + permalink, chặn đăng trùng.
2. **Gắn affiliate link không thủ công**: chọn sản phẩm cho từng job → hệ sinh deeplink (ClickBank/eBay) + slug `/b/{slug}` → chèn CTA vào caption tự động.
3. **Đo hiệu quả**: click theo video/nền tảng, biết video nào ra đơn.
4. compliance mặc định: `#ad` bắt buộc khi có link affiliate, `is_ai_generated=true` cho nội dung AI.

---

## 2. Thiết kế kiến trúc

### 2.1 Database ([store.py](../video-system/backend/app/store.py) — pattern `CREATE TABLE IF NOT EXISTS` + `_ensure_columns`)

```sql
CREATE TABLE IF NOT EXISTS ig_accounts (
  id TEXT PRIMARY KEY,
  ig_user_id TEXT UNIQUE NOT NULL,          -- graph API id (dạng số)
  username TEXT, account_type TEXT,          -- BUSINESS/CREATOR
  media_count INTEGER,
  access_token_enc TEXT NOT NULL,            -- Fernet long-lived (60 ngày)
  token_expires_at REAL,
  is_default INTEGER DEFAULT 1,
  connected_at REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS ig_publishes (   -- state machine như youtube_uploads
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,                    -- todo job / short_id
  account_id TEXT NOT NULL,
  caption TEXT, share_to_feed INTEGER DEFAULT 1,
  is_ai_generated INTEGER DEFAULT 1,
  container_id TEXT,                        -- để resume poll qua restart
  ig_media_id TEXT, permalink TEXT,
  publish_at TEXT,                          -- hẹn giờ (backend tự đợi)
  status TEXT DEFAULT 'queued',             -- queued|scheduled|creating|processing|published|failed|canceled
  attempt_count INTEGER DEFAULT 0,
  affiliate_slug TEXT,                      -- slug CTA, NULL nếu không affiliate
  error_message TEXT,
  created_at REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS affiliate_products (
  id TEXT PRIMARY KEY,
  network TEXT NOT NULL,                    -- clickbank|ebay
  label TEXT,                               -- tên hiển thị ("Anker Soundcore...")
  -- clickbank: account(publisher nickname của bạn) + promoter(nickname vendor) + tid
  cb_account TEXT, cb_promoter TEXT,
  -- ebay: URL sản phẩm gốc
  ebay_item_url TEXT,
  is_active INTEGER DEFAULT 1,
  created_at REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS affiliate_links ( -- 1 link/slug mỗi lần gắn vào job
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL, item_id TEXT,
  network TEXT, deeplink TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,                -- /b/{slug}
  subid TEXT,                               -- job_{item_id} (ClickBank tid / eBay customid)
  clicks INTEGER DEFAULT 0,
  created_at REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS bio_pages (       -- trang link-in-bio cho từng tài khoản
  handle TEXT PRIMARY KEY,                   -- 'main' = trang mặc định
  title TEXT, items_json TEXT,               -- danh sách {label, slug, position}
  updated_at REAL
);
```

- Rate limit IG (100 bài/24h) **không cần ledger riêng**: API có endpoint `GET /{ig-user-id}/content_publishing_limit` trả `quota_remaining` chính chủ → kiểm tra trước khi tạo container, dự phòng lỗi 429 re-queue.

### 2.2 Config & bảo mật ([config.py](../video-system/backend/app/config.py))

| Env | Bắt buộc | Ý nghĩa |
|---|---|---|
| `INSTAGRAM_APP_ID` | để bật tính năng | App ID của Meta app (kiểu Instagram Login) |
| `INSTAGRAM_APP_SECRET` | để bật tính năng | App secret |
| `IG_TOKEN_ENCRYPTION_KEY` | **fail-fast khi publish/connect nếu thiếu** | Fernet key riêng (sinh giống YouTube) |
| `CLICKBANK_NICKNAME` | không | Bật ClickBank khi có (`{nick}.{promoter}.hop.clickbank.net/?tid=`) |
| `CLICKBANK_API_KEY` | không | Sync doanh thu ClickBank (Reporting/Analytics API) |
| `EBAY_CAMPAIGN_ID` | không | Bật EPN khi có (10 chữ số, gắn `campid` + `customid` + `mkevt=1`) |
| `EBAY_DEV_KEY` | không | Sync doanh thu EPN qua TDR API (cần enable API access + eBay Dev Program) |
| `IMPACT_API_TOKEN` / `AWIN_API_TOKEN` | không | (Phase 2) deeplink + reporting |
| `IG_PUBLISH_CHUNK_WAIT_MIN` | không, default 8 | Phút tối đa poll container trước khi mark `failed` |

- Scopes chốt: `instagram_business_basic,instagram_business_content_publish`.
- OAuth `state`: HMAC bằng `session_secret()` hiện có — **copy nguyên hàm của YouTube** (`nonce.exp.sig`, 10 phút, one-time).
- Callback: `https://vidfac.gacdero.vn/api/v1/instagram/auth/callback` (suy từ `public_base_url()`).

### 2.3 Backend — [service_instagram.py](../video-system/backend/app/service_instagram.py) (file mới)

Dùng `httpx.AsyncClient` gọi `graph.instagram.com`. Copy pattern: `YouTubeError` → `InstagramError`, `TokenManager` → `IgTokenManager`, idempotency guard, startup sweep.

**a) TokenManager:**
- Callback đổi `code` → **short-lived token (1h)** `POST api.instagram.com/oauth/access_token` → **long-lived (60 ngày)** `GET graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=...`.
- Mọi call kiểm tra `token_expires_at < 7 ngày` → `GET /refresh_access_token?grant_type=ig_refresh_token` (reset về 60 ngày, KHÔNG cần user đăng nhập lại).
- Lỗi `invalid_grant`/`OAuthException` → mark disconnected, báo UI kết nối lại.
- **Startup sweep token**: account sắp hết hạn → refresh lúc khởi động (khác YouTube: refresh được vô hạn, không cần user).

**b) Đăng pipeline — state machine:**

```
queued → scheduled → creating → processing → published
   ↓         ↓           ↓          ↓
canceled   failed      failed     failed
```

1. **Validate (sync, trước khi queued):**
   - File video: `exports/*.mp4` của job; kiểm spec bằng **ffprobe sẵn có của pipeline** (MP4, ≤100MB an toàn — Meta docs ghi 300MB nhưng chênh lệch giữa các trang, test thật sẽ chốt; khuyến nghị 9:16 ≤1080×1920, 5–90s để vào Reels feed; 3s–15min là biên API).
   - Caption ≤ 2.200 ký tự (đếm rune) + tự chèn `#ad` nếu có affiliate và chưa có.
   - Idempotency: có record `processing/creating/queued` cùng `item_id` → `409`; đã `published` → `200 already_published`.
   - `publish_at` quá khứ → `422`; có `publish_at` → status `scheduled`, worker quét mỗi phút đến giờ mới chạy.
   - Check `content_publishing_limit` → `quota_remaining == 0` → `429` + thời điểm reset.
2. **Tạo signed URL công khai cho video** (IG phải tải được): `GET /api/v1/instagram/media/{item_id}?token={hmac}` — HMAC-SHA256 từ `session_secret()`, expiry 2 giờ, chỉ phục vụ file export của job (không liệt kê thư mục). Container tạo xong có thể revoke URL (IG chỉ đọc lúc tạo container/publish).
3. **Tạo container**: `POST /{ig_user_id}/media` với `media_type=REELS`, `video_url={signed_url}`, `caption`, `share_to_feed=true`, `is_ai_generated=true` (flag mặc định ON cho nội dung xưởng; cấu hình được per-job). Lưu `container_id` vào record **ngay khi có** (resume được qua restart).
4. **Poll processing**: mỗi 30s `GET /{container_id}?fields=status_code` → `FINISHED` → bước 5; `ERROR` → `failed` kèm message; `IN_PROGRESS` quá `IG_PUBLISH_CHUNK_WAIT_MIN` → retry tạo container lại 1 lần rồi `failed`.
5. **Publish**: `POST /{ig_user_id}/media_publish?creation_id={container_id}` → `ig_media_id` → `GET /{ig_media_id}?fields=permalink` lưu `permalink`. Container `EXPIRED` (không publish trong ~24h) → tự tạo lại container.
6. **Kết thúc**: bắn event `instagram.publish.completed` / `instagram.publish.failed` vào registry `EVENTS` của [webhooks.py](../video-system/backend/app/webhooks.py).

**c) Caption builder (hàm dùng chung, có unit test riêng):**
```
{hook}\n{body}\n\n{CTA}\n#ad (nếu affiliate) #{hashtag chính}
CTA khi có slug: "🔗 Link in bio"  ·  không slug: không CTA
```
- Luôn cắt ≤ 2.200 rune; `#ad` luôn nằm ở **vị trí trước mốc 125 ký tự đầu** (trước nút "more" — đúng FTC "clear & conspicuous").

### 2.4 Backend — [service_affiliate.py](../video-system/backend/app/service_affiliate.py) (file mới)

**a) Sinh deeplink (không cần API ngoài, chỉ tự dựng URL):**
- ClickBank: `https://{cb_account}.{cb_promoter}.hop.clickbank.net/?tid={subid}` — `subid = job_{item_id}` cắt ≤ 24 ký tự alphanumeric.
- eBay: `{ebay_item_url}` + `?campid={EBAY_CAMPAIGN_ID}&customid={subid}&mkevt=1` (nếu URL đã có query → gắn bằng `&`).
- Đính kèm record `affiliate_links` với slug phát sinh: `nanoid` 8 ký tự (kiểm tra unique).

**b) Redirect + đo click (route trong service_biopage):**
- `GET /b/{slug}` → `301` tới deeplink + `UPDATE affiliate_links SET clicks=clicks+1` + bắn event `affiliate.link.clicked` (payload: slug, item_id, network, referer, UA).
- `GET /bio/{handle}` → render trang HTML tĩnh gọn (danh sách link từ `bio_pages`, style tối giản, không JS nặng) → gắn làm bio TikTok/IG/YT.
- Route nằm **ngoài** lớp auth PIN/Bearer (trình duyệt user phải vào được), giống callback OAuth.

**c) Gắn link vào job:** endpoint chọn `product_id` cho job → tạo `affiliate_links` + trả `slug` → `service_instagram` chèn vào caption; cùng slug dùng được cho mọi nền tảng đăng từ job đó (sau này thêm YouTube description/TikTok bio).

**d) Đồng bộ doanh thu từ Reporting API của từng mạng (cron 1 lần/ngày):**

| Mạng | Reporting API | Credentials | Được sync gì |
|---|---|---|---|
| ClickBank | Reporting API + Analytics API | API key từ Account Settings → API Management (`CLICKBANK_API_KEY`) | Sales, commissions, hops/conversions theo `tid` (= subid) |
| eBay EPN | [Transaction Detail Report (TDR) API](https://partnerhelp.ebay.com/helpcenter/s/article/EPN-Transaction-Detail-Report-TDR-API-Documentation) + Performance by Day/Tool | Enable API access trong portal EPN + đăng ký eBay Developer Program (`EBAY_DEV_KEY`) | Transactions, earnings theo `customid` (= subid), clicks, EPC |
| Impact.com (P2) | Partner REST API (reporting + link gen) | API token (`IMPACT_API_TOKEN`) | Actions/payout theo subId1..5 |
| Awin (P2) | Publisher API (`getTransactionList`) | OAuth token từ profile (`AWIN_API_TOKEN`) | Transactions, commission theo clickReference |
| Amazon | ❌ **Không có API doanh thu** — chỉ tải CSV/XLSX tay từ dashboard (Creators API chỉ lấy data sản phẩm: giá/ảnh/link) | — | Import CSV tay qua UI (P2) |

- Kết quả ghi vào bảng mới `affiliate_conversions` (id, network, network_txn_id UNIQUE, subid, item_id, amount, currency, status, event_date, synced_at) — map `subid → affiliate_links.item_id` để biết **video nào ra đơn**.
- Subid chốt định dạng `job_{item_id}` (ClickBank tid ≤24 ký tự alphanumeric — cắt khi vượt) → mọi network đo attribution chung một chìa khoá.

### 2.5 Endpoints ([main.py](../video-system/backend/app/main.py))

| Method | Path | Scope | Chú thích |
|---|---|---|---|
| GET | `/api/v1/instagram/status` | read | `{configured, connected, account?, quota_remaining}` |
| GET | `/api/v1/instagram/auth/start` | read | `{auth_url}` (HMAC state 10 phút) |
| GET | `/api/v1/instagram/auth/callback` | — | Đổi code → long-lived token → `GET /me` → lưu DB → redirect `/#/publish?ig=connected` |
| DELETE | `/api/v1/instagram/account` | admin | Disconnect (xoá record; huỷ publish đang chạy?) |
| GET | `/api/v1/instagram/media/{item_id}` | public + HMAC token | Serve MP4 cho IG pull (`token` expiry 2h) |
| POST | `/api/v1/instagram/publish` | admin | Body: `{item_id, caption?, affiliate_product_id?, publish_at?, share_to_feed?, is_ai_generated?}` → validate + tạo record + chạy background |
| POST | `/api/v1/instagram/publishes/{id}/retry` | admin | Tạo lại container/publish |
| POST | `/api/v1/instagram/publishes/{id}/cancel` | admin | Hủy (chỉ khi chưa `published`) |
| GET | `/api/v1/instagram/publishes/{id}` | read | Poll trạng thái |
| GET | `/api/v1/instagram/publishes` | read | Danh sách + lịch sử |
| GET/POST/PATCH | `/api/v1/affiliate/products` | read/admin | CRUD sản phẩm (network, label, cb_promoter/ebay_url) |
| GET/POST | `/api/v1/affiliate/links` | read/admin | Danh sách link + click count; POST gắn product vào job |
| GET | `/b/{slug}` | public | Redirect 301 + log click |
| GET | `/bio/{handle}` | public | Trang link-in-bio |
| GET/PUT | `/api/v1/bio/pages/{handle}` | read/admin | Quản trị trang bio |

### 2.6 Frontend ([index.html](../video-system/frontend/index.html))

1. Top nav: nút **`📲 Publish`** cạnh `📺 YouTube` → `#/publish` (trang gộp, để sau này thêm TikTok không phải đổi nav).
2. Panel **Instagram**: trạng thái kết nối + username + `quota_remaining` + nút 🔗 Kết nối / 🔌 Ngắt.
3. Bảng **Lịch sử đăng IG**: job → status (processing/published/failed), progress poll 2s, permalink, Thử lại/Hủy.
4. `#/todos/{id}` — thêm nút **📲 Đăng Instagram** cạnh nút YouTube: modal (Caption prefill từ script + tự chèn #ad, chọn Product affiliate (dropdown từ `affiliate_products`, hiện slug preview), Hẹn giờ, checkbox "Khai báo AI-generated" mặc định bật) → progress → badge ✅ + link.
5. Trang con `#/affiliate`: bảng sản phẩm (CRUD) + bảng link/slug + click count theo video.

---

## 3. Meta Developer Console — checklist trước khi bấm Kết nối

1. Tạo Meta app kiểu **Business** → thêm product **Instagram API with Instagram Login** (không cần Facebook Login/Page).
2. Ghi `INSTAGRAM_APP_ID` + `INSTAGRAM_APP_SECRET` vào `.env`.
3. OAuth redirect URI: `https://vidfac.gacdero.vn/api/v1/instagram/auth/callback`.
4. **Tài khoản IG phải là Business/Creator** (Settings → Account type). Thêm chính bạn là **role** của app (Admin/Developer/Tester) để Dev Mode chạy được.
5. Dev Mode: chỉ tài khoản có role trong app connect được — **đủ cho xưởng của bạn**. App Review chỉ cần khi cho tài khoản ngoài dùng (phase sau).

> Lưu ý đã biết (hiển thị trong UI): Dev Mode vẫn đăng public bình thường cho tài khoản có role; chỉ 100 bài/24h/account; không gắn product tag qua API; không gắn Story link sticker qua API.

---

## 4. Ma trận lỗi & hành vi

| Tình huống | Hành vi hệ thống |
|---|---|
| Restart giữa lúc poll container | Startup sweep: record `creating/processing` có `container_id` → resume poll; container `EXPIRED` → tạo lại; không có container_id → `failed` |
| Token hết hạn (< 7 ngày) | Tự refresh 60 ngày; `invalid_grant` → disconnected + yêu cầu kết nối lại |
| `quota_remaining == 0` | `429` + thời điểm reset (đọc từ endpoint); publish hẹn sang slot kế |
| Container `ERROR` (spec sai, URL không tải được) | `failed` kèm message gốc của Meta; UI hiện lý do + nút Thử lại |
| Video >100MB / sai aspect | Chặn ở validate bằng ffprobe → `422` trước khi tốn quota |
| Caption > 2.200 rune | Tự cắt theo mượt (giữ #ad + CTA) + ghi chú; chỉ `422` khi hook/CTA bị cắt |
| Đăng 2 lần / 2 tab | Idempotency guard `409` |
| publish_at quá khứ | `422`; hợp lệ → `scheduled` + worker quét |
| IG trả `media_id` nhưng GET permalink lỗi | Retry 3 lần cách 10s; vẫn lỗi → vẫn `published`, permalink để trống |
| Slug trùng / product không active | `422` rõ lý do |
| Link affiliate ClickBank thiếu `CLICKBANK_NICKNAME` | Ẩn network khỏi dropdown + `422` nếu cố chọn |
| `/b/{slug}` không tồn tại | 404 trang gọn (không crash) |
| Someone đoán signed URL | HMAC expiry 2h + token gắn item_id cụ thể, không liệt kê được |

---

## 5. Kế hoạch kiểm thử (pytest, mock Meta bằng `httpx.MockTransport` — theo pattern `tests/test_youtube_publish.py`)

1. **OAuth**: state HMAC/expiry/one-time; callback đổi token 2 bước (short→long-lived), Fernet roundtrip; `invalid_grant` → disconnected.
2. **TokenManager**: <7 ngày → tự refresh; sweep startup refresh.
3. **Caption builder**: cắt ≤2.200 rune đúng; `#ad` trước 125 ký tự; CTA đúng có/không slug.
4. **Affiliate**: ClickBank/eBay URL dựng đúng (query có sẵn → gắn `&`); subid ≤24 ký tự; slug unique; click counter + event dispatch.
5. **Publish happy path**: validate ffprobe (mock) → signed URL verify HMAC → container → poll `IN_PROGRESS`→`FINISHED` → publish → permalink; assert `is_ai_generated=true` và caption gửi đúng.
6. **Lỗi**: container `ERROR`/`EXPIRED`; quota 0 → `429`; caption quá dài; thiếu file → `422`; idempotency `409`; publish_at quá khứ `422`.
7. **Scheduled**: worker quét đến giờ mới tạo container; cancel khi `scheduled` ok.
8. **Redirect**: `/b/{slug}` 301 đúng deeplink, click +1, event bắn 1 lần.
9. **Startup sweep**: container_id resume; không có → failed.
10. Lint + suite: `ruff check video-system` + `pytest video-system/tests -q`.

---

## 6. Deploy & rollout

1. Deploy code; điền `INSTAGRAM_APP_ID/SECRET`, `IG_TOKEN_ENCRYPTION_KEY` vào `/opt/video-system/.env` → restart (thiếu env → UI hiện "chưa cấu hình", không rollback gì).
2. Console checklist mục 3; chuyển IG sang Business/Creator nếu chưa.
3. Smoke test: Kết nối IG → chọn job có export → đăng ngay (không affiliate trước, sau đó gắn ClickBank demo) → kiểm permalink + click test `/b/{slug}`.
4. Đăng ký webhook test nhận `instagram.publish.completed`.

**Rollback**: self-contained (2 service file mới + routes + 4 bảng SQLite mới, không đụng bảng cũ). Bỏ env `INSTAGRAM_APP_ID` + restart = tắt hoàn toàn.

**Chi phí phase 1: $0** (không vendor, không phí mạng affiliate — ClickBank/EPN free).

---

## 7. Phase 2 backlog (sau khi phase 1 chạy ổn 1–2 tuần)

1. **Impact.com + Awin** (REST deeplink API, batch 100 link) — thêm network vào `service_affiliate.py`, chỉ là adapter mới.
2. **Amazon Associates**: apply ngay từ bây giờ (duyệt + 3 đơn/180 ngày để mở Creators API); payout gift card; thêm network `amazon` vào `service_affiliate.py`.
3. **ManyChat**: comment "LINK" → DM tự động chứa slug `/b/{slug}` — setup 1 lần trong UI ManyChat, backend chỉ cần đảm bảo slug ổn định.
4. **Scheduling thông minh**: hàng đợi giờ vàng US (17–22h ET), tối đa 3–5 Reels/ngày, tránh spam.
5. **Analytics**: click→purchase attribution qua subid từng mạng; dashboard đơn giản trong `#/affiliate`.
6. **TikTok** (khi muốn quay lại): thêm adapter Upload-Post/Ayrshare vào `service_publish` — kiến trúc không phải đổi gì.
