# Kế hoạch v1.0: Đăng video sang TikTok Mỹ + Instagram Reels Mỹ & Kiếm tiền Affiliate (xưởng video)

> Ngày lập: 2026-11-18 · Dựa trên khảo sát API mới nhất (tài liệu chính thức TikTok/Meta/Amazon + nhà cung cấp trung gian, cross-check 11/2026).
> Mục tiêu: mở rộng hệ thống publish hiện có (`service_youtube.py` + trang `#/youtube`) sang **TikTok US** và **Instagram Reels US**, kèm **tự động hóa link affiliate** tối đa, vận hành từ Việt Nam.

---

## 0. Tóm tắt điều hành (TL;DR)

| Câu hỏi | Kết luận |
|---|---|
| Đăng TikTok US tự động được không? | **Được.** Content Posting API hỗ trợ direct post MP4 (file hoặc PULL_FROM_URL từ domain đã verify). Nhưng **app của bạn phải qua audit TikTok**; chưa audit → chỉ đăng được chế độ riêng tư (tối đa 5 user/24h). ⚠️ Có bằng chứng TikTok **từ chối audit tool chỉ dùng nội bộ** (đăng cho tài khoản của chính mình) → đường an toàn nhất: dùng **API trung gian đã audit** (Upload-Post / Blotato / Ayrshare). |
| Đăng Instagram Reels tự động được không? | **Được và dễ hơn TikTok.** Graph API `media_type=REELS`, giới hạn **100 bài/24h** (mới nâng), Reels + Stories + Carousel đều hỗ trợ. Tài khoản chính của bạn chạy được ngay ở Development Mode; chỉ cần **Meta App Review + Business Verification** (doanh nghiệp VN được chấp nhận) khi mở cho tài khoản ngoài. |
| Gắn link affiliate vào đâu? | Caption TikTok/IG **không có link clickable** → link thật đặt ở **bio** (đã có Linktree Admin API; tốt hơn: tự host trang bio + redirect trên domain của bạn) và dùng DM automation (ManyChat) cho IG. TikTok Shop gắn product card **không làm được qua Content Posting API**. |
| Affiliate Mỹ nào tự động hóa tốt nhất? | Shortlist: **Amazon Associates (Creators API)**, **Impact.com** (REST deeplink API, có Walmart), **Awin** (Link Builder API batch 100 link, Etsy...), **ClickBank** (HopLink tự dựng URL, hoa hồng 30–75%), **eBay Partner Network** (tự dựng URL `campid`). TikTok Shop US hoa hồng cao nhất (~5–20%) nhưng cần US tax ID + ngân hàng Mỹ hoặc vào qua agency CAP. |
| Từ Việt Nam có bị chặn địa lý không? | **Không bị chặn hoàn toàn.** VN **không** nằm trong danh sách "Countries of Concern" của TikTok (chỉ TQ/Nga/Iran/Triều Tiên/Cuba/Venezuela, và quy trình đó chỉ áp dụng cho TikTok Minis). Amazon cho VN join (W-8BEN, tỷ lệ thuế VN–Mỹ). Impact/Awin/CJ/Rakuten nhận publisher quốc tế. Meta Business Verification nhận giấy ĐKKD VN. |
| Audience "Mỹ" trên TikTok cần gì? | Region TikTok do **SIM (mạnh nhất) → IP → ngôn ngữ/múi giờ thiết bị → App Store** quyết định. Muốn FYP Mỹ: US SIM (hoặc bỏ SIM) + IP residential Mỹ sạch + device US + warm-up. **API chỉ là đường ống — region nằm ở account/thiết bị, không phải ở lệnh API.** Instagram thì không cần IP Mỹ, audience do nội dung quyết định. |
| Chi phí khởi điểm | ~$25–60/tháng: Upload-Post Basic $24 (hoặc Blotato $29) + Awin $1 (refundable) + domain. So với xây direct: 3–6 tuần dev + 2–4 tuần chờ audit + rủi ro bị từ chối. |

**Khuyến nghị kiến trúc (ngắn):** giữ nguyên YouTube direct như hiện tại; thêm 1 lớp adapter `service_publish` gọi **Upload-Post hoặc Blotato** cho TikTok + Instagram; thêm `service_affiliate` sinh deeplink (Impact/Awin/ClickBank/eBay/Amazon) + **trang bio/redirect tự host** trên `vidfac.gacdero.vn`; thêm compliance checker tự chèn `#ad` + bật `is_aigc`/`is_paid_partnership`. Chi tiết mục 6–7.

---

## 1. Hiện trạng hệ thống (mỏ neo triển khai)

Những gì đã có sẵn và sẽ **tái sử dụng nguyên pattern**:

- [service_youtube.py](../video-system/backend/app/service_youtube.py): OAuth + `TokenManager` (Fernet, auto-refresh), `QuotaLedger` chặn cạn quota, idempotency guard 409, state machine `queued→uploading→uploaded/failed`, startup sweep resume qua restart, event webhook `youtube.*` vào registry `EVENTS` sẵn có.
- [main.py](../video-system/backend/app/main.py): endpoint nằm sau lớp auth PIN/Bearer, scope `admin` cho thao tác ghi.
- Frontend `#/youtube` SPA — sẽ nhân bản thành tab `#/publish` gộp đa nền tảng.
- Ảnh/video thành phẩm: `shorts/{short_id}/exports/short_{short_id}.mp4` (shorts) và `items/{id}/exports/final_en.mp4` (long-form), đã có `has_thumbnail`.

**Nguyên tắc thiết kế cho kế hoạch này:** mỗi nền tảng = 1 adapter sau cùng interface `publish()`, đúng pattern state machine + ledger + webhook của YouTube, để sau này đổi nhà cung cấp trung gian (hoặc chuyển sang direct API) không phải sửa pipeline.

---

## 2. TikTok US — đăng bài tự động

### 2.1 Cơ chế Content Posting API (Direct Post)

Nguồn: [Content Posting API Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started), [Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post), [Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines)

| Hạng mục | Giá trị đã xác minh (11/2026) |
|---|---|
| Endpoint | `POST /v2/post/publish/video/init/` → trả `upload_url` + `publish_id` (FILE_UPLOAD) hoặc `publish_id` (PULL_FROM_URL); upload chunk qua `PUT open-upload.tiktokapis.com`; check trạng thái `POST /v2/post/publish/status/fetch/`; query creator `POST /v2/post/publish/creator_info/query/` |
| Scope | `video.publish` (app được duyệt + user authorize qua Login Kit) |
| Nguồn video | FILE_UPLOAD (MP4/H.264, ≤ 1GB, chunk) hoặc PULL_FROM_URL (**bắt buộc domain đã verify**) |
| Caption | ≤ **2.200 ký tự** (UTF-16, gồm hashtag); photo post 4.000, title photo 90 |
| Privacy | `PUBLIC_TO_EVERYONE` / `MUTUAL_FOLLOW_FRIENDS` / `SELF_ONLY` |
| Nhãn nội dung | `is_aigc` (khai báo AI-generated, default false); branded content toggle ("Branded content"/"Your brand" → nhãn *Paid partnership*). **Branded content không được đặt private** |
| Rate limit | ~**15 bài/ngày/creator** (chia sẻ giữa mọi app, lỗi `spam_risk_too_many_posts`); 6 init/phút; 20 creator_info/phút; 30 status/phút; tối đa 5 bài đang chờ |
| App chưa audit | Chỉ đăng được **riêng tư** (`unaudited_client_can_only_post_to_private_accounts`), tối đa **5 user/24h**, account phải private |

### 2.2 Rào cản thật: audit app + rủi ro "tool nội bộ"

- Audit Direct Post mất **2–4 tuần, nhiều vòng**, yêu cầu UX cụ thể (dropdown privacy không có default, toggle disclosure, preview...).
- ⚠️ Phát hiện quan trọng nhất của toàn bộ khảo sát: TikTok support đã thông báo cho applicant rằng tool **chỉ đăng cho tài khoản của chính mình/org là KHÔNG đủ điều kiện audit** ("internal use") — ghi nhận tại [Upload-Post TikTok API docs](https://www.upload-post.com/tiktok-api) và [Postiz issue #1563 (audit bị từ chối, còn mở 11/2026)](https://github.com/gitroomhq/postiz-app/issues/1563). Xưởng video của bạn đăng cho chính tài khoản của mình rơi đúng vào ca rủi ro này.

**→ Kết luận TikTok:** đi qua **nhà cung cấp trung gian đã audit** (app của họ đã qua audit, bạn chỉ OAuth tài khoản TikTok của mình qua họ và post public bình thường). Đây chính là giá trị cốt lõi của Ayrshare/Blotato/Upload-Post — đã xác minh từ cả 3 vendor. Nếu sau này volume lớn (>500–1.000 bài/ngày) hoặc cần quyền riêng tư mà vendor không hỗ trợ, mới quay lại xây direct + audit (kịch bản "sản phẩm cho nhiều user", không phải "tool nội bộ").

### 2.3 Link affiliate trên TikTok — chỗ nào click được?

| Vị trí | Clickable? | Tự động hóa được? |
|---|---|---|
| Caption (URL trong text) | ❌ Không clickable | — (chỉ để người xem copy tay, gần như vô dụng) |
| **Bio link** | ✅ | ✅ **Linktree Admin API** (api.linktr.ee); tốt hơn: **trang bio tự host** trên domain của bạn (mục 6.3) |
| TikTok Shop product card trên video | ✅ | ❌ **Không gắn được qua Content Posting API** — phải dùng TikTok Shop UI/túi "Showcase". Muốn tự động: dùng TikTok Shop Open API (mục 2.4) cho phần link, còn card phải thao tác trong app (hoặc dùng app/automation bên thứ 3 — rủi ro ban) |
| Comment của chính account | ⚠️ Link trong comment cũng không click được trên TikTok | — |

→ Chiến lược TikTok: caption chứa CTA *"link in bio 🛒"* + bio chứa trang redirect tự host (mỗi video một slug riêng, đo được click theo video).

### 2.4 TikTok Shop US Affiliate (hoa hồng cao nhất nhưng rào cản pháp lý)

Nguồn: [Creator Eligibility Policy](https://seller-us.tiktok.com/university/essay?knowledge_id=6939143037667118&lang=en), [TikTok Shop Partner — CAP](https://partner.tiktokshop.com/docv2/page/creator-agency-partner-cap), [Affiliate API Solution Guide](https://partner.tiktokshop.com/docv2/page/affiliate-integration)

- Điều kiện creator US: **18+, ~1.000+ followers** (một số feature marketplace cần 5k), **US-based**, verify danh tính, tài khoản tốt, giữ *Creator Health Rating ≥ 3.5* và *PPS > 3.5*.
- Verify thuế: cần **SSN / ITIN / EIN** (EIN dùng được cho account dạng entity) + **tài khoản ngân hàng Mỹ** để nhận tiền (verify bank ~48h trước khi chi trả).
- Commission: trung bình ~13%, phổ biến **5–20%** (số liệu 2026).
- **Đường cho operator VN:**
  1. **US LLC + EIN + US bank** (Mercury/Payoneer) — tự vận hành, kiểm soát toàn bộ, chi phí setup LLC ~$150–500 + phí duy trì.
  2. **Creator Agency Partner (CAP)** — TikTok chính thức cho phép agency đưa creator **non-US** vào affiliate US; agency lo compliance, chi trả gộp. Nhanh nhất, mất % hoa hồng.
- Tự động hóa: TikTok Shop có **Affiliate Open API** tách theo role — *Seller API* (hàng hóa/commission), *Partner API* (agency: matchmaking, link), *Creator API* (creator: product marketplace, commission link). Đăng ký developer qua TikTok Shop Partner Center; access **first-party** (bạn phải là seller/creator/agency thật, không có API "third-party thuần"). → Sau khi có tư cách (LLC hoặc CAP), **link hoa hồng sinh được bằng API**.

### 2.5 Xác minh địa lý (câu hỏi của bạn) — TikTok

| Vấn đề | Kết quả xác minh |
|---|---|
| Developer VN đăng ký app TikTok có bị chặn? | ❌ Không. Danh sách **Countries of Concern** = Trung Quốc (HK/Macau), Nga, Iran, Triều Tiên, Cuba, Venezuela — **VN không có tên**. Quy trình "U.S. Launch Approval" đó chỉ áp dụng cho **TikTok Minis** (mini game/drama), không áp dụng Content Posting API ([nguồn](https://developers.tiktok.com/docs/en/us-launch-approval-process)) |
| Audience Mỹ cần gì? | Region do **SIM card (mạnh nhất) → IP → ngôn ngữ/region/múi giờ thiết bị → App Store region** quyết định. Setup chuẩn từ VN: máy riêng + US SIM (hoặc không SIM) + **IP residential Mỹ sạch** (IP datacenter hay bị flag) + device US + warm-up tài khoản như user Mỹ trước khi đăng |
| Đăng bằng API có ảnh hưởng region? | Không — API chỉ đẩy video lên server TikTok. **Region nằm ở account + thiết bị.** Vì vậy làm chuẩn "US account" trước, API sau |
| ⚠️ Lưu ý ToS | Mô phỏng vị trí/vượt region có thể vi phạm ToS TikTok; chấp nhận rủi ro giới hạn (ban account nội dung), không liên quan pháp lý hình sự |

### 2.6 Trạng thái pháp lý TikTok US 2025–2026

Deal joint venture **TikTok USDS Joint Venture LLC** ký chính thức **22/01/2026** — Oracle/Silver Lake/MGX mỗi bên 15% (công ty Mỹ ~50%), ByteDance giữ 19.9%. App vận hành bình thường cho user US, không còn rủi ro "bị cấm" ([Silver Lake](https://www.silverlake.com/tiktok-usds-joint-venture-llc-established-in-compliance-with-u-s-regulatory-requirements), [PBS](https://www.pbs.org/newshour/economy/tiktok-finalizes-deal-to-form-new-u-s-unit-with-major-american-investors)). Content Posting API vẫn hoạt động cho US accounts 11/2026.

---

## 3. Instagram Reels US — đăng bài tự động

### 3.1 Content Publishing API (Graph API)

Nguồn: [Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing), [IG User Media reference](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media)

| Hạng mục | Giá trị đã xác minh (11/2026) |
|---|---|
| Flow | (1) `POST /{ig-user-id}/media` tạo container (`media_type=REELS`, `video_url`) → (2) poll `GET /{container-id}?fields=status_code` đến `FINISHED` (poll 1 lần/phút, tối đa ~5 phút) → (3) `POST /{ig-user-id}/media_publish` với `creation_id` |
| Spec video Reels | MP4/MOV, ≤ **300 MB**, **3 giây – 15 phút**, khuyến nghị **9:16**, max width **1920px**, 23–60fps; `video_url` phải public tại thời điểm publish; video lớn → resumable qua `rupload.facebook.com/ig-api-upload/{container_id}` |
| Rate limit | **100 bài/24h** (cửa sổ trượt, đếm theo account — carousels tính 1 bài); check quota: `GET /{ig-user-id}/content_publishing_limit` |
| Loại nội dung | Reels, Feed video, Carousel (≤20 items), **Stories (`media_type=STORIES`) — có hỗ trợ qua API**, **Trial Reels** (`trial_params`, chỉ hiện cho non-followers, có `graduation_strategy` — rất hợp test nội dung affiliate) |
| Nhãn nội dung | `is_ai_generated=true` (khai báo AI); **`is_paid_partnership`** + `branded_content_sponsor_ids` (≤2 sponsor) qua Facebook Login → nhãn *Paid partnership* tự động |
| Hạn chế | **Không support shopping/product tags qua API**; không support **sticker** (kể cả link sticker) khi đăng Story; ảnh chỉ JPEG |

### 3.2 Permission & setup

- Hai biến thể token: **Instagram Login** (`instagram_business_basic` + `instagram_business_content_publish` — mới, gọn hơn) hoặc Facebook Login (`instagram_basic` + `instagram_content_publish` + `pages_read_engagement`). Tài khoản IG phải là **Business/Creator (professional)**.
- **Tài khoản của chính bạn:** chạy được ngay ở Development Mode (Standard Access) với tài khoản là admin/developer/tester của app — **không cần đợi App Review để bắt đầu**.
- **Mở cho tài khoản ngoài (nếu sau này cho user khác dùng):** cần **Meta Business Verification** + **App Review** (Advanced Access, demo video đăng bài end-to-end, ~2–3 ngày/lượt, hay bị reject vì mô tả mơ hồ).
- Meta Business Verification **chấp nhận doanh nghiệp Việt Nam**: Giấy chứng nhận đăng ký doanh nghiệp / giấy đăng ký thuế, tên + địa chỉ khớp registry, website HTTPS ([Meta Business Help](https://www.facebook.com/business/help/159334372093366)).

### 3.3 Link affiliate trên Instagram — chỗ nào click được?

| Vị trí | Clickable? | Tự động hóa |
|---|---|---|
| Caption | ❌ | — |
| **Bio link** | ✅ 1 link duy nhất | ✅ Linktree Admin API hoặc trang bio tự host (mục 6.3) |
| Story link sticker | ✅ | ❌ **API chính thức không gắn sticker được** — phải gắn tay trên app (việc thủ công ~30s/story, hoặc bỏ qua) |
| **DM automation (ManyChat)** | ✅ link trong DM click được | ✅ "Comment X → gửi DM chứa link affiliate" — ManyChat là official Meta partner; kịch bản chuẩn của affiliate IG; ManyChat cũng có API để gửi tin nhắn theo chương trình |
| Product tags | ✅ trên app | ❌ không qua API |

→ Chiến lược IG: caption CTA *"Comment 'LINK' and I'll DM it to you"* + ManyChat tự trả DM chứa deeplink affiliate; bio đặt trang redirect tự host.

### 3.4 Xác minh địa lý — Instagram

- API không quan tâm IP của bạn (gọi từ server VN bình thường), không yêu cầu IP Mỹ cho account.
- Audience Mỹ = nội dung tiếng Anh + hashtag/caption US; IG phân phối theo interest, không gate theo vị trí người đăng.
- Business Verification làm được từ VN (mục 3.2). Không có rào cản địa lý nào đáng kể.

---

## 4. Affiliate Mỹ — so sánh & khả năng tự động hóa link

### 4.1 Bảng so sánh tổng (mục tiêu: link sinh được BẰNG API/tự dựng URL)

| Chương trình / Mạng | Hoa hồng | Cookie | Tự động sinh link? | API cụ thể | Rào cản cho operator VN | Ghi chú AI/faceless |
|---|---|---|---|---|---|---|
| **Amazon Associates US** | 1–10% (games 20%, fashion 4–6%, home/pets 3%, điện tử ~1–2%) | 24h | ✅ **Creators API** (REST, thay PA-API 5.0) | [affiliate-program.amazon.com/creatorsapi](https://affiliate-program.amazon.com/creatorsapi/docs) | Thấp — VN join được, tax interview **W-8BEN** (hiệp định thuế VN–Mỹ). Payout: gift card ($10) / check ($50 + $15 phí) / international direct deposit (VN có thể không được hỗ trợ; Payoneer KHÔNG chính thức) | Chấp nhận; **cấm** generic URL shortener cloaking (amzn.to của Amazon OK); **bắt buộc** dòng "As an Amazon Associate I earn from qualifying purchases" |
| **Impact.com** | Tùy merchant (Walmart 1–4%, Canva, Adidas...) | Tùy merchant | ✅ REST API tạo tracking/deeplink | [Impact partner API](https://help.impact.com/partner/what-would-you-like-to-learn-about/platform-features/tracking/tracking-links/create-and-manage-links/create-a-deep-link-for-an-ad) | Thấp — nhận publisher quốc tế; **payout min $10**, wire/Payoneer | Tùy từng merchant có policy riêng |
| **Walmart Creator** | **1–18%** (fashion 15% — 10/2026) | ~3 ngày (tùy) | ✅ qua Impact / creator.walmart.com | Impact API | Thấp | Creator program thân thiện nội dung video |
| **eBay Partner Network** | ~1–4% theo danh mục | 24h | ✅ **Tự dựng URL**: `?campid={campaign}&customid={subid}&mkevt=1` — không cần API riêng | [EPN tracking link](https://developer.ebay.com/api-docs/buy/static/ref-epn-link.html) | Thấp | Dễ nhất về kỹ thuật |
| **ClickBank** | **30–75%** (sản phẩm digital) | 60 ngày | ✅ **Tự dựng HopLink**: `https://{account}.{promoter}.hop.clickbank.net/?tid={subid}` | [developers.clickbank.com](https://developers.clickbank.com/) | Thấp — VN được nhận, payout qua check/wire/Payoneer (Payoneer phổ biến) | Rất hợp faceless (không cần đổi sản phẩm vật lý); chất lượng sản phẩm phải tuyển kỹ |
| **Awin** | Tùy merchant (Etsy ~4–8%...) | Tùy merchant | ✅ **Link Builder API** — batch 100 link/request, OAuth | [Awin Link Builder API](https://www.awin.com/us/how-to-use-awin/link-builder-api) | Thấp; **$1 refundable deposit** khi join; min payout $20, chi trả ngày 1 & 15 hàng tháng | ⚠️ **ShareASale đã ĐÓNG, dồn hết về Awin** (thay đổi 2025–26 quan trọng) |
| **CJ Affiliate** | Tùy merchant | Tùy merchant | ⚠️ Tự dựng link theo format `anrdoezrs.net/links/{pid}...` + Link Search API | [CJ deep linking](https://junction.cj.com/article/deep-linking-tools-publishers) | Trung bình — lớn, chậm duyệt, min payout $50 | Tùy merchant |
| **Rakuten Advertising** | Tùy merchant | Tùy merchant | ✅ **Deep Links API** (validate URL + batch) | [Rakuten Deep Links API](https://developers.rakutenadvertising.com/guides/deep_link) | Trung bình — khó duyệt, min $100, ~quarterly | — |
| **Skimlinks / Sovrn Commerce** | Net của ~48.500 merchant | Tùy merchant | ⚠️ JS auto-convert cho web; Merchant/Reporting API; cho caption video thì dùng trực tiếp deeplink qua mạng gốc | [Sovrn Commerce KB](https://knowledge.sovrn.com/kb/connect-your-affiliate-networks-to-commerce) | Thấp | Dùng khi cần "1 link → nhiều merchant" |
| **Geniuslink** | (mức Amazon) | 24h | ✅ **API** tạo geo-link (tự route theo nước người xem) | geniuslink.com | Phí gói ~$29+/tháng | Amazon **phê duyệt** Geniuslink → an toàn về cloaking; bonus khi audience ngoài US lọt vào |
| **TEMU Affiliate** | **20–40%** theo region | ~30 ngày? | ⚠️ Link sinh trong app/dashboard; chưa có API công khai | [temu.com/affiliate_recruit](https://www.temu.com/affiliate_recruit.html) | Thấp — 80+ nước (VN cần kiểm tra khi signup), min rút ~$20, PayPal/bank | Program mạnh cho faceless; policy đổi nhanh |
| **AliExpress (Admitad/Portals)** | ~6–9% | ~30 ngày | ⚠️ Admitad có API deeplink | admitad.com | Thấp | Hold tiền dài (~78 ngày trung bình Admitad) |
| **LTK** | Tùy brand | — | ❌ Không có API công khai (app cho creator, invite) | — | Cao | Bỏ qua cho automation |
| **TikTok Shop US** | **5–20%+** (TB ~13%) | Tùy seller | ✅ qua **TikTok Shop Open API** (Affiliate Creator/Partner API) — cần tư cách (mục 2.4) | [Affiliate API guide](https://partner.tiktokshop.com/docv2/page/affiliate-integration) | **Cao** — US tax ID + US bank, hoặc vào qua CAP | Hoa hồng cao nhất nhưng cần US LLC/CAP |

### 4.2 Shortlist khuyến nghị cho xưởng của bạn (nội dung faceless/AI, thực phẩm-nhà-bếp/gadget/digital)

1. **ClickBank** — bắt đầu ngay hôm nay: link tự dựng, hoa hồng 30–75%, không cần duyệt từng merchant phức tạp. Phù hợp digital (course, tool, ebook) khớp nội dung video.
2. **eBay Partner Network** — link tự dựng `campid`, duyệt nhanh, phù hợp gadget/máy cũ.
3. **Impact.com** — 1 API duy nhất cho hàng chục merchant lớn (Walmart, Canva...), payout $10, REST chuẩn.
4. **Awin** — Link Builder API batch (Etsy + hàng nghìn merchant), $1 deposit, chi trả 2 lần/tháng.
5. **Amazon Associates** — bắt buộc phải có về lâu dài (niềm tin người mua cao nhất): vào ngay bằng link thủ công/storefront, còn **Creators API chỉ mở sau khi có 3 đơn trong 180 ngày** (và cần ~10 đơn/30 ngày để GIỮ quyền API — phải thiết kế pipeline giữ nhiệt). Payout cho VN: chọn gift card là ổn nhất.
6. **Phase 2:** TikTok Shop US (qua US LLC hoặc CAP) — hoa hồng cao nhất, đầu tư sau khi có doanh thu chứng minh mô hình.
7. **TEMU** — thêm vào khi cần commission cao cho sản phẩm rẻ; kiểm tra VN khả dụng khi đăng ký.

### 4.3 Quy tắc compliance bắt buộc (FTC + chính sách từng chương trình)

Nguồn: [FTC Disclosures 101](https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers)

- Disclosure **trong chính caption**, đặt **trước nút "more"** (vị trí đầu caption), dùng `#ad` / `#affiliate` / "paid link". Nhãn bio dạng "#ad for all posts" **không được tính**.
- Dùng **cả 2 lớp**: nhãn nền tảng (TikTok branded content toggle → *Paid partnership*; IG `is_paid_partnership=true`) **+** `#ad` trong caption. TikTok 2025 đã siết, tự gắn nhãn quảng cáo cho nội dung vi phạm.
- Amazon: không dùng bit.ly/generic shortener che link Amazon; dùng link gốc/amzn.to/Geniuslink. Đủ dòng disclosure Amazon.
- AI content: bật `is_aigc` (TikTok) và `is_ai_generated` (IG) — vừa đúng policy vừa giảm rủi ro giảm phân phối do bị phát hiện.
- ⚠️ Nội dung faceless/AI nhân phẩm + affiliate: một số merchant (không phải mạng) cấm "spammy AI content" — đọc policy từng merchant khi apply qua Impact/Awin.

---

## 5. Ma trận "gắn link ở đâu" theo nền tảng (kết hợp mục 2.3 + 3.3)

| Kênh | Link thật nằm ở | CTA trong caption (mẫu) | Tự động hóa đầy đủ? |
|---|---|---|---|
| TikTok US | Bio → trang redirect tự host `vidfac.gacdero.vn/b/{slug}` | "🛒 Link in bio — #ad" | ✅ 100% |
| Instagram Reels | Bio + **DM qua ManyChat** (comment → DM link) | "Comment *LINK* → I'll DM it — #ad" | ✅ 100% (trừ setup ManyChat 1 lần) |
| YouTube (đã có) | Description + pinned comment | Có link trực tiếp trong description | ✅ có sẵn |
| TikTok Shop (phase 2) | Product card trên video | "🛒 ngay trên TikTok" | ⚠️ card gắn tay trong app; link hoa hồng sinh bằng Open API |

---

## 6. Kiến trúc đề xuất cho video-system

### 6.1 Nguyên tắc

Giữ nguyên mọi pattern của `service_youtube.py`: state machine, Fernet token, idempotency, ledger rate-limit, event webhook, startup sweep. Thêm 3 service mới, không đụng code cũ.

### 6.2 Sơ đồ

```
Todo/Short job (final .mp4 + thumbnail)
        │
        ▼
service_publish.py  ── 1 interface duy nhất: publish(target, caption, schedule, links)
   ├─ youtube  → service_youtube.py (hiện có, giữ nguyên)
   ├─ tiktok   → service_uploadpost.py (adapter Upload-Post REST, app đã audit)
   └─ instagram→ service_uploadpost.py (cùng adapter; về sau có thể swap sang Graph API direct)
        │
        ▼
service_affiliate.py
   ├─ product catalog (ASIN/URL/merchant) → chọn mạng (ClickBank/eBay/Impact/Awin/Amazon)
   ├─ sinh deeplink (tự dựng URL hoặc REST API) + cache trong DB (affiliate_links)
   └─ trả slug trang bio: /b/{slug} → 301 → deeplink (subid = video_id để đo theo video)
        │
        ▼
service_biopage.py (route FastAPI + 1 bảng DB)
   ├─ GET /b/{slug} → redirect + log click (theo video/nền tảng/subid)
   └─ GET /bio/{handle} → trang link-in-bio render cho bio TikTok/IG (thay Linktree)
```

### 6.3 Vì sao tự host trang bio thay vì Linktree

- Linktree Admin API có thật nhưng bị phụ thuộc vendor + giới hạn gói; trang tự host = **đo click theo từng video** (subid), đổi link tức thì, không tốn phí, khớp domain đã verify TikTok PULL_FROM_URL nếu sau này chuyển direct API.

### 6.4 Bảng DB mới (pattern `CREATE TABLE IF NOT EXISTS` hiện có)

```sql
CREATE TABLE IF NOT EXISTS publish_channels (      -- tài khoản TikTok/IG đã OAuth qua Upload-Post
  id TEXT PRIMARY KEY, platform TEXT, handle TEXT,
  provider TEXT DEFAULT 'upload_post', provider_profile_id TEXT,
  is_default INTEGER DEFAULT 0, connected_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS publish_jobs (          -- state machine như youtube_uploads
  id TEXT PRIMARY KEY, item_id TEXT, platform TEXT, channel_id TEXT,
  caption TEXT, schedule_at TEXT, post_url TEXT, provider_post_id TEXT,
  status TEXT DEFAULT 'queued',   -- queued|posting|posted|failed|canceled
  attempt_count INTEGER DEFAULT 0, error_message TEXT,
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS affiliate_links (
  id TEXT PRIMARY KEY, item_id TEXT, network TEXT,   -- clickbank|ebay|impact|awin|amazon
  source_url TEXT, deeplink TEXT, slug TEXT UNIQUE,  -- /b/{slug}
  subid TEXT, clicks INTEGER DEFAULT 0, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS publish_rate_ledger (   -- TikTok 15/ngày/creator, IG 100/ngày
  day TEXT, platform TEXT, channel_id TEXT, posts INTEGER DEFAULT 0,
  PRIMARY KEY (day, platform, channel_id)
);
```

### 6.5 Env mới

| Env | Vai trò |
|---|---|
| `UPLOAD_POST_API_KEY` | Bật tính năng TikTok/IG qua Upload-Post (thiếu → UI hiện "chưa cấu hình", pattern YouTube) |
| `MANYCHAT_API_TOKEN` | (phase 2) DM automation IG |
| `AFFILIATE_*` (clickbank_account, ebay_campid, impact_api_token, awin_api_token, amazon_access_key...) | Bật từng mạng affiliate, per-network fail-soft |

### 6.6 Caption builder + compliance checker

1. Hook (2 dòng đầu) — bắt buộc trước nút "more".
2. Body + CTA ("Link in bio 🛒" / "Comment LINK").
3. Disclosure tự chèn: `#ad` (không cho phép tắt — hard rule) + `#affiliate`.
4. Nhãn nền tảng: `is_aigc=true` (TikTok), `is_ai_generated=true`, `is_paid_partnership` khi có sponsor (IG).
5. Chống trùng: mỗi job có caption fingerprint; RateLedger chặn khi vượt 15/ngày TikTok hoặc 100/ngày IG → hẹn sang ngày mai (re-queue).

### 6.7 Sự kiện webhook mới

`publish.posted`, `publish.failed`, `publish.channel.connected`, `affiliate.link.clicked` — thêm vào registry `EVENTS` của webhooks.py, tái dùng `dispatch_event` để agent ngoài nhận thông báo.

---

## 7. Lộ trình triển khai

### Phase 1 — Đăng TikTok + IG qua Upload-Post, link ClickBank/eBay (≈ 1 tuần dev)
1. Đăng ký Upload-Post Basic $24/tháng (hoặc Blotato Starter $29 nếu muốn thêm AI credits; lưu ý: sinh API key Blotato = hết trial). Lấy Python SDK Upload-Post.
2. Xây `service_uploadpost.py` + `service_publish.py` + bảng `publish_channels`/`publish_jobs`/`publish_rate_ledger`, endpoint `/api/v1/publish/*` (pattern `#/youtube`), tab frontend `#/publish`.
3. Xây `service_biopage.py` (trang bio + redirect đo click) + `service_affiliate.py` cho ClickBank + EPN (tự dựng URL — không cần duyệt API gì).
4. Compliance checker (mục 6.6). Smoke test: đăng private 1 video TikTok US qua OAuth Upload-Post; đăng 1 Reels.
5. Chuẩn bị "US account TikTok": máy riêng + US SIM/IP residential + warm-up 1–2 tuần trước khi đăng chính thức.

### Phase 2 — Amazon + Impact + Awin, scheduling, analytics (tuần 2–4)
1. Apply Amazon Associates (chọn payout gift card), Impact, Awin ($1 deposit); thêm vào `service_affiliate.py` qua REST.
2. Lên lịch đăng: hàng đợi phân bổ 3–5 slot TikTok/ngày/creator + Reels; data-driven chọn giờ US prime time (17–22h ET).
3. Đo hiệu quả: clicks theo video (`affiliate_links.clicks`), lọc video chuẩn hoá → nhân bản format.
4. ManyChat IG comment→DM (tạo flow 1 lần trong UI ManyChat; backend chỉ cần cập nhật link/slug).

### Phase 3 — TikTok Shop US + mở rộng (tháng 2–3)
1. Chọn đường: **US LLC + EIN + US bank (Mercury/Payoneer)** hoặc **CAP agency**; đăng ký TikTok Shop affiliate + TikTok Shop Partner Center (Affiliate Creator API để tự sinh link hoa hồng).
2. Xem xét chuyển direct API (TikTok Content Posting + Meta Graph) khi volume > 500 bài/ngày hoặc cần tự chủ — dựng UI theo [Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) để qua audit (kịch bản "cho nhiều user", tránh ca "internal use").
3. YouTube Shopping affiliate (tiện đường: kênh YouTube đã có).

### Chi phí chạy dự kiến
| Hạng mục | Tháng đầu | Sau đó |
|---|---|---|
| Upload-Post Basic | $24 | $24 (nâng Pro $50 khi >5 tài khoản) |
| Awin deposit | $1 (refundable) | 0 |
| Geniuslink (nếu dùng geo-link Amazon) | ~$29 | tùy chọn |
| Domain/trang bio | đã có `vidfac.gacdero.vn` | 0 |
| US LLC (phase 3, tùy chọn) | $150–500 setup + $50–300/năm | 1 lần |

---

## 8. Rủi ro & lỗ hổng còn mở (chưa xác minh được, theo dõi khi triển khai)

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| TikTok audit từ chối tool nội bộ (nếu đi direct) | Cao | Đi qua vendor đã audit (Upload-Post/Blotato/Ayrshare) — thiết kế này đã né rủi ro |
| Vendor nhỏ (Upload-Post/Blotato) biến mất | Trung bình | Adapter sau interface `publish()`; Ayrshare là migration target đã sẵn |
| Amazon thu hồi Creators API nếu <10 đơn/30 ngày | Trung bình | Sau khi có API, thiết kế cron kiểm tra tổng đơn; dưới ngưỡng → fallback link thủ công/storefront |
| Geo-spoofing TikTok (US SIM/IP) vi phạm ToS | Trung bình (rủi ro account, không pháp lý) | 1 domain công khai cho job chính; dùng IP residential sạch; warm-up; chuẩn bị account dự phòng |
| Số follower TikTok Shop affiliate dao động (1k vs 5k) theo thời gian | Thấp | Xác minh lại khi vào program thật |
| PostWire/API.SH (nhà cung cấp mới 2025–26) chưa kiểm chứng | Thấp | Chỉ dùng khi tự test ổn định 2 tuần |
| Nội dung AI bị giảm phân phối khi khai `is_aigc` | Chưa rõ | Khai đúng (bắt buộc theo policy); A/B so sánh khai vs không khai trên Trial Reels của IG trước khi nhân bản |
| Nhiều bài/ngày từ 1 IP/server (Upload-Post) bị flag spam | Trung bình | Giới hạn 3–5 bài/ngày/creator, tăng dần, đa dạng caption; follow rate ledger |

---

## 9. Nguồn chính (đã trích xuất 11/2026)

**TikTok:** [Content Posting API Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started) · [Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) · [Photo Post (caption 4000)](https://developers.tiktok.com/doc/content-posting-api-reference-photo-post) · [Content Sharing Guidelines (audit UX)](https://developers.tiktok.com/doc/content-sharing-guidelines) · [US Launch Approval / Countries of Concern](https://developers.tiktok.com/docs/en/us-launch-approval-process) · [TikTok Shop Creator Eligibility](https://seller-us.tiktok.com/university/essay?knowledge_id=6939143037667118&lang=en) · [CAP agency](https://partner.tiktokshop.com/docv2/page/creator-agency-partner-cap) · [Affiliate API](https://partner.tiktokshop.com/docv2/page/affiliate-integration) · [Silver Lake — USDS JV](https://www.silverlake.com/tiktok-usds-joint-venture-llc-established-in-compliance-with-u-s-regulatory-requirements)

**Instagram/Meta:** [Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing) · [IG User Media (Reels specs)](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media) · [App Review](https://developers.facebook.com/docs/resp-plat-understand/app-review) · [Business Verification](https://www.facebook.com/business/help/159334372093366)

**Affiliate:** [Amazon Creators API](https://affiliate-program.amazon.com/creatorsapi/docs) · [Amazon payment methods](https://affiliate-program.amazon.com/help/node/topic/GKDG94FQSRXSJCGK) · [EPN tracking link](https://developer.ebay.com/api-docs/buy/static/ref-epn-link.html) · [ClickBank APIs](https://developers.clickbank.com/) · [Awin Link Builder API](https://www.awin.com/us/how-to-use-awin/link-builder-api) · [Impact deeplink](https://help.impact.com/partner/what-would-you-like-to-learn-about/platform-features/tracking/tracking-links/create-and-manage-links/create-a-deep-link-for-an-ad) · [Rakuten Deep Links API](https://developers.rakutenadvertising.com/guides/deep_link) · [CJ deep linking](https://junction.cj.com/article/deep-linking-tools-publishers) · [Sovrn Commerce](https://knowledge.sovrn.com/kb/connect-your-affiliate-networks-to-commerce) · [TEMU affiliate](https://www.temu.com/affiliate_recruit.html) · [FTC Disclosures 101](https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers)

**Trung gian đăng bài:** [Upload-Post](https://upload-post.com/pricing) · [Blotato](https://www.blotato.com/pricing) · [Ayrshare](https://www.ayrshare.com/pricing) · [Postiz issue #1563 (audit TikTok bị từ chối)](https://github.com/gitroomhq/postiz-app/issues/1563) · [Mixpost](https://mixpost.app/pricing) · [Post Bridge](https://post-bridge.com/pricing) · [Upload-Post TikTok mechanics](https://www.upload-post.com/tiktok-api)

---

*Hết báo cáo. Bước tiếp theo đề xuất: duyệt kiến trúc mục 6 → lập kế hoạch triển khai chi tiết v1 (bám mẫu `ke_hoach_youtube_publish_v1_1.md`) cho Phase 1.*
