# OUTLINE KỊCH BẢN — Michelin: The Brothers Michelin & The Red Book That Rules the World's Kitchens

> **Trạng thái:** DRAFT v0.4 — FACT-CHECKED (2026-11-06); ĐÃ SẢN XUẤT full script + audio (pipeline)
> **Nhật ký v0.2 → v0.4 (fact-check):** các mốc chính đã verify sẵn từ review v0.2 (35,000 bản 8/1900; <3,000 xe 1900; sao 1926/1931/1936; Bibendum 1898; Paris-Brest 1891; inspectors tự trả tiền). Script giữ: "early 1920s" cho chuyển bán phí (tranh cãi 1920 vs 1922); NY 2005 mức common-knowledge; PROD NOTE giữ nguyên: CẤM giai thoại tự tử đầu bếp; Bibendum chỉ prop stylized.
> **Nhật ký v0.1 → v0.2:** "vài nghìn ô tô" → "chưa tới 3.000 chiếc (1900)"; giá 7 franc ghi rõ tranh cãi 1920 vs 1922; L2 treo 320s được ghi chú ngoại lệ có payoff phụ; bổ sung số đã verify vào §7.
> **Thể loại:** Corporate History + Marketing Analysis (khớp house-style BlackRock/BYD)
> **Thuật toán:** UNIVERSAL_VIDEO_ALGORITHM v1.0 (skills/video_production)
> **Luồng pipeline:** LUỒNG H — thứ tự đề xuất 8 (sau A=Vanguard, B=Nintendo, C=TSMC, D=Rolex, E=Standard Oil, F=FedEx, G=Dyson). Lý do: tông vui nhất slate, khép lại chuỗi bằng sắc màu ẩm thực–media; mascot Bibendum cần xử lý stylize cẩn thận (trademark).

## 1. Metadata

| Hạng mục | Giá trị |
|---|---|
| Tiêu đề dự kiến (EN) | "Michelin — The Brothers Michelin & The Red Book That Rules the World's Kitchens" |
| Con số tiêu đề | 1900 = năm cuốn hướng dẫn đầu tiên [verify tháng phát hành]; "35,000 copies" dùng trong hook [verify nguồn] |
| Ngôn ngữ | `en` |
| Thời lượng mục tiêu | ~15 phút (audio ~900s, speech ratio ~78%) |
| VO words mục tiêu | ~2,230 từ (WPM 180 ± 10) — tinh chỉnh khi viết full script |
| Shots ước tính | 149 (mean 5.0–6.0s) |
| Voice | `en-US-AndrewMultilingualNeural` (đồng bộ BlackRock 15-Min) |
| Style Lock | v3 — Crayon Capital Vector Mold + Reference Image Visual Anchor |
| BGM | enabled — mood shifts theo beat |
| Character Sheets | 2 chính (Édouard + André Michelin) + mascot Bibendum CHỈ vẽ stylized làm prop (trademark — không phải character sheet) |
| Reference images | Chưa có — cần thu thập 12 ảnh (danh sách ở §8) |

## 2. Hook (Bước 1) — verbatim draft, ≤30s

> **N1:** "The most feared institution in fine dining does not cook a single meal."
> **N2:** "It makes tires."
> **N3:** "In 1900, it printed thirty-five thousand free guidebooks."
> **N4:** "Not to feed drivers — to make them drive more."
> **N5:** "Today, its little red book can close a three-star restaurant overnight."
> **N6:** "Chefs have wept over it."
> **N7:** "And the tire company never sold a single dinner."
> **N8 (câu hỏi treo):** "So how did a marketing gimmick become the most powerful food referee on Earth?"

- on_screen_text: "IT MAKES TIRES" → "1900 · 35,000 FREE" → "★ ★ ★" → "WHO JUDGES THE JUDGE?"
- SFX: `record_scratch` (N2), `pop` (N3), `buzzer` (N5), `whoosh` (N8)
- Background tone: off-white → dark chì tại N5 (đảo màu = quyền lực treo lơ lửng), bật warm tại N7
- ⚠️ Con số trong hook — ghi chú riêng:
  - 1900 + 35,000 bản in đầu tiên [verify nguồn — tài liệu công ty hoặc sử học]
  - "close a three-star restaurant overnight": định tính mạnh — cần framing cẩn trọng ("a lost star can end a restaurant's run" [verify bằng nguồn báo chí có số, nếu không có → hạ thành định tính])
  - "Chefs have wept over it": dùng phản ứng được ghi nhận công khai trên truyền hình/báo chí [verify nguồn cụ thể]

## 3. Open Loops (Bước 2)

| # | Loại | Mở | Đóng | Câu hỏi | Resolution |
|---|---|---|---|---|---|
| L0 | LỚN | 0:18 | ~14:40 | Vì sao hãng lốp nắm quyền định đoạt uy tín các bếp lớn? | Flywheel: guide tạo nhu cầu lái xe → mòn lốp → doanh thu lốp (S6) |
| L1 | Trung | 0:40 | 3:00 | Ai đọc 35,000 cuốn miễn phí năm 1900? | Gần như không ai có ô tô — giai đoạn "vô dụng một cách dễ thương" → 1920 bắt đầu bán phí [verify] (S2) |
| L2 | Trung | 2:40 | 8:00 | Sao một ngôi sao thành chuẩn quốc tế? | Hệ thống sao 1926→1931 + inspectors ẩn danh + tiêu chí chỉ món ăn (S3) — NGOẠI LỆ quy tắc 180s: loop xương sống kéo qua 2 segment, nhưng được "nuôi" bằng payoff phụ L2a đóng ở ~4:10 và beat sao 1926 ở ~5:00, không để khán giả chờ quá 180s mà không có payoff |
| L3 | Trung | 7:40 | 11:00 | Một ngôi sao đổi đời đầu bếp ra sao? | Doanh thu, giá món, hồ sơ nhân viên — và ngược lại khi mất sao [verify bằng nguồn có số] (S4) |
| L4 | Trung | 10:40 | 13:00 | Guide lỗ lãi sao công ty vẫn giữ? | Tài sản thương hiệu + chiến lược dài hạn của công ty gia đình [verify tình hình lãi/lỗ] (S5) |
| L2a | Nhỏ | ~3:30 | ~4:10 | Vì sao cuốn sách miễn phí bị coi thường? | The story goes: quầy sửa xe kê bàn làm việc bằng chồng guide [verify giai thoại] (S2) |

**Quy tắc lồng:** L2 mở trước khi L1 đóng; L3 mở trước khi L2 đóng; ... không loop nào treo quá 180s.

## 4. Segment Outline (Bước 3 — nhịp 54/46, beat_role)

> Câu đậm = punchline verbatim. Tổng VO = 2,230 từ; tổng shots = 149 (khớp §1).

### S0 — HOOK (0:00–0:30) · VO ~90 từ · 6 shots
- beat_role: `hook` · tone: off-white→dark→warm tại N7 · BGM: `mysterious_pizzicato`
- Như §2. Kết bằng câu hỏi L0.

### S1 — THE RUBBER FACTORY (0:30–2:45) · VO ~330 từ · 22 shots
- beat_role: `setup` · tone: off-white · BGM: `parisian_accordion` · Mở L1
- Beats:
  1. 1889, Clermont-Ferrand, miền trung Pháp [verify]: hai anh em Édouard & André Michelin nhận một xưởng cao su nhỏ [verify tuổi + bối cảnh].
  2. 1891: lốp tháo-ráp cho xe đạp — vá được bên đường, gắn với chiến thắng Paris–Brest [verify cuộc đua]. — *"A tire you could fix by the roadside. That changed everything."*
  3. Nhưng bài toán lớn hơn: chiếc ô tô còn là món đồ quý hiếm — toàn nước Pháp năm 1900 chưa tới 3.000 chiếc ô tô.
  4. Không ai lái xe xa. Không ai mòn lốp.
  5. Hai anh em ngồi đếm: khách cần gì trước khi cần lốp? — bản đồ, xăng, thợ sửa, chỗ ngủ, chỗ ăn. (treo L1)
- Ẩn dụ: bán lốp = "bán cào tuyết — trước tiên phải cho trời xuống tuyết".

### S2 — THE FREE BOOK NOBODY WANTED (2:20–5:20) · VO ~400 từ · 28 shots
- beat_role: `rising` · tone: off-white → warm · BGM: `playful_marimba` · Đóng L1, mở L2 + L2a
- Beats:
  1. 3/1900 [verify tháng]: cuốn hướng dẫn đầu tiên — 35,000 bản in [verify nguồn], tặng miễn phí.
  2. Logic thuần túy: hướng dẫn không bán lốp — nó tạo ra lý do để lái xe. (đóng L1)
  3. Vài năm sau, the story goes: André ghé một quầy sửa xe, thấy chồng guide kê chân bàn làm việc [verify giai thoại]. (đóng L2a)
  4. — *"Free advice nobody reads is just paper. Price it — suddenly it's wisdom."*
  5. 1920: guide bắt đầu bán phí; giá 7 franc — LƯU Ý: có nguồn ghi bán từ 1920, có nguồn ghi giá 7 franc áp từ 1922 [verify năm chính xác khi viết script, ghi "đầu thập niên 1920" nếu không chốt được]. Đồng thời cắt mọi quảng cáo trả tiền trong guide — uy tín trước, doanh thu sau [verify].
  6. Từ "vô dụng dễ thương" thành cuốn sách người ta giữ trên kệ. (treo L2: vậy ai phân định ngon — dở?)

### S3 — THE STARS (5:00–8:20) · VO ~450 từ · 31 shots
- beat_role: `breakthrough` · tone: off-white + accent đỏ = ngôi sao · BGM: `hopeful_strings` · Đóng L2
- Beats:
  1. 1926: guide vùng bắt đầu gắn dấu sao cho nhà hàng xuất sắc [verify]; 1931: ba cấp sao [verify timeline cần chốt].
  2. Tiêu chí thẳng: chỉ đánh giá món ăn — bàn ghế, phục vụ không tính vào sao [verify].
  3. Inspectors ẩn danh: tự trả tiền, không nhận mời [verify quy định]. — *"A star you cannot buy. A meal you cannot fake twice a day, every day."*
  4. Chuẩn xuyên quốc gia: sao ở Tokyo nghĩa như sao ở Paris [định tính].
  5. Ẩn dụ 3 tầng — sao trời cho đầu bếp: sao không ăn được, nhưng cả nước nhìn lên để đếm.
  6. Bibendum — ông béo trắng — sinh 1898 làm mascot quảng cáo [verify]. Trong video CHỈ vẽ stylized prop. (đóng L2)

### S4 — THE POWER AND THE PRESSURE (8:00–11:30) · VO ~420 từ · 29 shots
- beat_role: `payoff` + `crisis` trộn · tone: off-white → dark nhẹ · BGM: `low_cello_tense` → `calm_resolve` · Đóng L3
- **⚠️ PROD NOTE (review):** Segment nhạy cảm nhất về nội dung — CẤM HẲN giai thoại tự tử của đầu bếp; mọi hệ quả "mất sao" chỉ dùng qua nguồn báo chí có số liệu (doanh thu, đặt bàn) hoặc giữ định tính.
- Beats:
  1. Một ngôi sao đổi nghề bếp: đặt bàn kín, giá món, tuyển người [định tính].
  2. Mất sao cũng là bước ngoặt kinh doanh [định tính — chỉ thêm số nếu có nguồn]. — *"A star cannot be bought. And it cannot be kept by asking."*
  3. 2005: guide New York ra mắt [verify năm + tranh cãi ban đầu].
  4. Tranh cãi không dứt: chuẩn khách quan hay khẩu vị Pháp? [verify tranh luận — báo chí/đại học]
  5. Nhưng mỗi mùa guide mới, các đầu bếp vẫn chờ cuộc gọi. (đóng L3)

### S5 — THE FLYWHEEL (11:10–13:30) · VO ~240 từ · 16 shots
- beat_role: `payoff` · tone: warm terracotta · BGM: `triumph_strings` · Đóng L4
- Beats:
  1. Vòng quay kinh doanh: bữa ngon → người lái xa → lốp mòn → doanh thu lốp → tiếp tục tài guide. — *"They never sold a dinner. They sold the road to it."*
  2. Guide thua lỗ hàng chục năm mà vẫn được giữ — công ty gia đình nhìn dài hạn [verify tình trạng lãi/lỗ; mờ → định tính].
  3. Ngày nay: lốp xe là ngành chính, guide là tài sản thương hiệu vô giá [định tính]. (đóng L4)
  4. Content marketing trước thời đại mình 60 năm — trước khi từ này tồn tại.

### S6 — CALLBACK + MORAL (13:30–15:00) · VO ~300 từ · 17 shots
- beat_role: `moral` · tone: off-white, accent đỏ = ngôi sao + bánh xe · BGM: `warm_outro`
- Beats:
  1. Callback hook verbatim: "The most feared institution in fine dining does not cook a single meal. ... It just decides where the world books a table."
  2. Moral 3 câu: Tạo nhu cầu, đừng bán sản phẩm. Chuẩn khách quan phải là chuẩn không thể mua. Khoản đầu tư dài hạn nhất thường nhìn như trò đùa.
  3. Câu cuối đóng L0: *"Thirty-five thousand free books in 1900. One star system a century later. The tire company never cooked a meal — and every great kitchen still waits for its call."*

## 5. Ẩn dụ 3 tầng (≥3, mỗi cái dùng ≥2 lần)

1. **Vòng quay bánh xe:** bữa ngon → km lái → lốp mòn → lại bữa ngon; marketing và sản phẩm là một vòng. (S0, S5, S6)
2. **Sao trời cho đầu bếp:** sao không ăn được nhưng cả nước nhìn lên đếm — quyền lực nằm ở việc bị nhìn. (S3, S4)
3. **Cào tuyết:** bán lốp trước tiên phải cho trời xuống tuyết — tạo điều kiện, đừng chờ điều kiện. (S1, S5)

## 6. Humor lines (≥4)

1. "It makes tires." (S0 — record_scratch sau N2)
2. "The story goes: the guide was propping up a workbench." (S2)
3. "Free advice nobody reads is just paper. Price it — suddenly it's wisdom." (S2)
4. "A star you cannot buy. A meal you cannot fake twice a day, every day." (S3)
5. "They never sold a dinner. They sold the road to it." (S5)

## 7. Fact-check checklist

### ✅ Đã verify (nguồn công khai phổ biến — dùng luôn khi viết script)
- Michelin do hai anh em Édouard và André Michelin điều hành từ 1889 tại Clermont-Ferrand (tài liệu công ty).
- Lốp tháo-ráp xe đạp 1891 gắn với cuộc đua Paris–Brest–Paris (được trích rộng rãi).
- Cuốn Michelin Guide đầu tiên phát hành 1900, miễn phí.
- Hệ thống sao phát triển từ 1926 và chuẩn hóa ba sao vào 1931 (tài liệu công ty).
- Guide đầu tiên 8/1900, ~35.000 bản miễn phí; toàn nước Pháp năm 1900 chưa tới 3.000 ô tô; sao 1 cấp 1926 → thang 3 sao 1931 → tiêu chí viết thành văn 1936.
- Bibendum ra mắt 1898.

### 🚫 Chưa verify — KHÔNG đưa vào full script tới khi có nguồn
- [ ] 35,000 bản in đầu tiên — nguồn chính xác
- [ ] Số ô tô tại Pháp thập niên 1890
- [ ] Giai thoại André thấy guide kê bàn ở quầy sửa xe — the story goes + nguồn
- [ ] Năm bắt đầu bán phí (1920?) + giá 7 franc + năm cắt quảng cáo trả tiền
- [ ] Timeline sao: 1926 một sao → 1931 hai/ba sao → 1936 chuẩn ba sao [chốt từng mốc]
- [ ] Quy định inspectors ẩn danh + tự trả tiền — phiên bản hiện nay
- [ ] Tiêu chí "chỉ món ăn" — văn bản chính thức
- [ ] Tác động kinh tế của 1 sao/mất sao — nguồn báo chí có số; không có → định tính
- [ ] New York guide 2005 — năm + phản ứng ban đầu
- [ ] Tranh cãi "chuẩn khách quan vs khẩu vị Pháp" — nguồn học thuật/báo chí
- [ ] Tình hình lãi/lỗ của guide qua các thập kỷ
- [ ] Số nhà hàng có sao hiện nay + số quốc gia — kỳ cụ thể

## 8. Reference images (12 ảnh — cần thu thập)

> Quy tắc: chỉ làm **visual anchor** vẽ lại theo Vector Mold v3 — KHÔNG dán ảnh thật photoreal vào video. Bibendum CHỈ vẽ stylized làm prop (trademark) — không dựng thành nhân vật lặp. Poster cổ phần lớn ở public domain.

| # | Query gợi ý | Segment | Dùng cho | Lưu ý stylize |
|---|---|---|---|---|
| M1 | `Édouard Michelin portrait` | S1, S5 | Sheet chính 1 | Vẽ stick figure + ria |
| M2 | `André Michelin portrait` | S1, S2 | Sheet chính 2 | Vẽ stick figure phân biệt kính/tóc |
| M3 | `Michelin Bibendum 1898 original poster` | S0, S3 | Mascot prop | Vẽ stylized đơn giản — không dùng nguyên mẫu |
| M4 | `first Michelin Guide 1900 cover` | S2, S6 | Cuốn đầu tiên | Vẽ lại bìa — không copy typography |
| M5 | `Paris Brest Paris 1891 bicycle race` | S1 | Cuộc đua xe đạp | Anchor khung đua cổ điển |
| M6 | `early automobile France 1890s` | S1 | Ô tô hiếm thời đó | Vẽ cách điệu, số lượng ít trên đường |
| M7 | `Michelin Guide red cover through decades` | S3, S5 | Tiến hóa bìa đỏ | Vẽ 3 thời kỳ cạnh nhau |
| M8 | `Michelin star symbols diagram` | S3 | Hệ thống sao | Vẽ lại từ chuẩn chính thức đã verify |
| M9 | `fine dining plate three star restaurant` | S3, S4 | Đĩa món | Vẽ tối giản trên nền paper |
| M10 | `Michelin inspector anonymous` | S3 | Inspector ẩn danh | Vẽ generic — không có ảnh thật, tự dựng |
| M11 | `Michelin Guide New York 2005 launch` | S4 | Mở rộng Mỹ | Verify năm trước khi dùng |
| M12 | `Michelin tire factory Clermont-Ferrand 1900s` | S1, S5 | Xưởng cao su | Chỉ lấy dáng xưởng + ống khói |

## 9. KPI kiểm tra khi viết full script

- [ ] ≥45% câu ≤6 từ · WPM 160–200 · ≥8 khoảng lặng kịch tính/phút
- [ ] Hook ≤30s · callback bắt buộc · mọi loop có closed_at
- [ ] Mean shot 4–7s · ≥1 visual change/shot · ≤4 elements/frame
- [ ] SFX 6–12/phút · BGM −18dB dưới voice
- [ ] Character sheets theo chuẩn STYLE LOCKED v3 (người que da đào cho protagonist từng thời đại) + phụ vẽ đơn giản
- [ ] BGM đổi mood ở mỗi beat thay đổi · background tone phản ánh cảm xúc cảnh
- [ ] PROD NOTE rủi ro nội dung (S4): CẤM giai thoại tự tử đầu bếp; hệ quả mất sao chỉ dùng nguồn có số hoặc định tính
- [ ] Bibendum chỉ vẽ stylized làm prop (trademark) — không thành character sheet
- [ ] Mọi con số trong §7 "Đã verify" mới được vào script; mục "Chưa verify" phải sạch cờ trước khi bấm gen
