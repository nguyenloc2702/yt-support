# STYLE BIBLE — Crayon Capital ("The Rise of Elon Musk Explained Like You're 5")

> **CẬP NHẬT 2026-10-25:** Phong cách HÌNH đã chuyển sang **Clean Flat Vector Cartoon** (kiểu kênh Tradeoff / ảnh ASML do chủ duyệt) — xem `STYLE_PREFIX` trong `services/prompt_sanitizer.py` và judge mới trong `services/style_checker.py`. Các số đo NHỊP/THỜI LƯỢNG/WPM trong tài liệu này vẫn là chuẩn áp dụng.

> **Mục đích:** Bộ quy tắc đo đạc định lượng thực tế từ video `npoPedEq3hs` để tái tạo các video hoạt họa giải thích kinh doanh/tiểu sử sau này.
> **Trạng thái:** ✅ Đã hoàn thành đo đạc thực nghiệm (Evidence-Backed).
> **Phạm vi:** Tài liệu kỹ thuật chuẩn hóa, **không thay đổi code**.

---

## 1. NHẬN DIỆN NGUỒN (B0)

| Trường | Giá trị thực tế |
| :--- | :--- |
| URL | `https://youtu.be/npoPedEq3hs` |
| Tên kênh | **Crayon Capital** |
| Tiêu đề | **The Rise of Elon Musk Explained Like You're 5** |
| Thời lượng | **1,150 giây** (19 phút 10 giây) |
| Ngôn ngữ | Tiếng Anh (`en-orig`) |
| Phụ đề | YouTube Auto-Captions (Word-level `json3`) |
| Thể loại | Tiểu sử kinh doanh / Phân tích công nghệ / Hoạt họa ELI5 |
| Lượt xem khi phân tích | 384,069 views |

---

## 2. METRICS ĐỊNH LƯỢNG (B2) — "Nhịp Tim" Của Video

### 2.1 Nhịp Lời (Narration Pacing)
| Chỉ số | Giá trị đo được từ video gốc | Ngưỡng cho video sau |
| :--- | :--- | :--- |
| **WPM khi đang nói (Effective WPM)** | **184.8 WPM** | 170 – 195 WPM |
| **WPM tổng video (Overall WPM)** | **143.7 WPM** | 135 – 155 WPM |
| **Độ dài câu trung bình (Mean Words/Sentence)** | **7.9 từ/câu** | 6.5 – 9.0 từ/câu |
| **Độ dài câu trung vị (Median Words)** | **6.0 từ/câu** | 5.0 – 7.0 từ/câu |
| **% Câu ngắn (≤ 6 từ)** | **54.4%** (190 / 349 câu) | **≥ 50%** |
| **Khoảng lặng trung bình giữa 2 câu** | **713.0 ms** | 600 – 850 ms |
| **Số khoảng lặng kịch tính (≥ 600ms) / phút** | **11.58 lần/phút** (222 lần tổng) | 10 – 13 lần/phút |
| **Tỉ lệ thời gian nói / tổng thời lượng** | **77.8%** (894.7s / 1150s) | 75% – 80% |

### 2.2 Nhịp Hình (Visual Pacing)
| Chỉ số | Giá trị đo được từ video gốc | Ngưỡng cho video sau |
| :--- | :--- | :--- |
| **Tổng số cuts (Scene cuts)** | **230 cuts** | — |
| **Số cut / phút (Cut rate)** | **12.0 cuts/phút** | 10 – 14 cuts/phút |
| **Độ dài shot trung bình (Mean shot)** | **5.0 giây** | 4.0 – 6.0 s |
| **Độ dài shot trung vị (Median shot)** | **4.47 giây** | 3.5 – 5.5 s |
| **Độ dài shot P90 (90% shot ngắn hơn)** | **8.60 giây** | ≤ 9.0 s |
| **Visual changes nội bộ trong shot** | 1.8 – 2.5 thay đổi/shot (zoom, text pop, icon) | ≥ 1 thay đổi / 3s |

### 2.3 Nhịp Truyện (Story Pacing)
| Chỉ số | Giá trị thực tế | Ngưỡng |
| :--- | :--- | :--- |
| **Độ dài Hook mở đầu** | **32.4 giây** (kết thúc ở câu: *"ended up here."*) | 20 – 35 giây |
| **Khoảng cách mở vòng tò mò (Open Loop cycle)** | Cứ mỗi **60 – 120 giây** mở 1 nút thắt mới | ≤ 120 giây |
| **Số câu hỏi tu từ / phút** | **1.8 câu/phút** | 1 – 3 câu/phút |
| **Số ẩn dụ đời thường / phút** | **2.2 ẩn dụ/phút** | ≥ 2 ẩn dụ/phút |
| **Tần suất khủng hoảng lớn (High-tension beats)** | 4 mốc khủng hoảng lớn (Zip2 ouster, PayPal coup, Falcon 1 crashes, 2008 Christmas) | ≥ 3 lần / video |

### 2.4 Nhịp Âm Thanh (Audio Pacing)
| Chỉ số | Giá trị đo được | Quy chuẩn áp dụng |
| :--- | :--- | :--- |
| **Integrated Loudness** | **-18.3 LUFS** | -18.0 đến -19.0 LUFS |
| **Loudness Range (LRA)** | **3.8 LU** | 3.0 – 5.0 LU |
| **True Peak** | **0.0 dBFS** | ≤ -0.5 dBFS |
| **Mức BGM dưới Voice (Audio Ducking)** | **-18 dB đến -22 dB** | `volume=0.10` đến `0.12` |
| **Mật độ SFX xuất hiện** | **~8.5 sự kiện / phút** | 6 – 10 SFX / phút |

---

## 3. VISUAL DNA (B3)

### 3.1 Bảng Màu & Kết Cấu Đo Được (Empirical Color Palette)
* **Nền động (Dynamic Backgrounds):**
  * Tông tiêu chuẩn: Nền giấy vẽ sketch sần, ngà vàng (`#edf1ee` đến `#d9d5c5`).
  * Tông kịch tính / Khủng hoảng: Chuyển sang bảng đen chì than (`#252525` đến `#303638`).
  * Tông lịch sử / Hồi ức: Nền đất nung sepia hoặc olive xám (`#7b4f49`, `#565c58`).
* **Màu nét vẽ (Line):** Chì than thô, sáp đen, độ rung nhẹ (line boil tự nhiên), không dùng nét vector hoàn hảo.
* **Màu nhấn (Primary Crayon Accents):**
  * Vàng sáp (`#e8cf5c`): tiền bạc, ý tưởng, biểu tượng.
  * Đỏ cảnh báo (`#c94a4a`): tên lửa nổ, phá sản, đồ thị cắm đầu.
  * Xanh dương sáp (`#4a7ac9`): đồ vật trung tính, xe hơi, công nghệ.

### 3.2 Master Style Prompt
Dùng để sinh keyframes minh họa đồng nhất cho các công cụ AI Image (Flux / Midjourney / DALL-E):
```text
A whimsical hand-drawn editorial cartoon illustration of [NHÂN VẬT & ẨN DỤ HÀNH ĐỘNG],
rough wax crayon and colored pencil sketch strokes, grainy paper texture,
flat 2D minimalist composition, generous negative space around subject,
sketchy imperfect contours, limited muted color palette with vibrant yellow and red crayon accents,
[NỀN: off-white textured sketchbook paper OR dark blackboard depending on scene mood],
no gradients, no 3D shading, no photorealism --ar 16:9
```
* **Negative Prompt cố định:** `3d render, glossy, CGI, gradient lighting, smooth vector, photo, photorealistic, complex background, crowded clutter`.

### 3.3 Character Sheet Chuẩn
| Nhân vật | Fixed Descriptor (Prompt Template) |
| :--- | :--- |
| **Elon Musk** | `Cute simple caricature of Elon Musk, messy brown hair, round cartoon eyes, wearing a simple dark t-shirt or business suit drawn with sketchy wax crayon lines, expressive comedic facial emotions` |

---

## 4. STORY DNA (B4) — Công Thức Kịch Bản ELI5

### 4.1 Thuật toán Kịch Bản 5 Giai Đoạn (The 5-Stage Story Algorithm)
1. **The Math Paradox Hook (0% - 3% thời lượng):**
   * Đặt 1 thành tựu không tưởng ở thì tương lai.
   * Tạo 1 phép so sánh toán học gây sốc với trải nghiệm đời thường của người xem ($1).
   * Khép lại hook bằng câu hỏi: *"Làm thế nào một kẻ thất bại lại có thể đến được đây?"*
2. **Underdog Origin (3% - 15% thời lượng):**
   * Xuất phát điểm bất lợi, cô độc, bị bắt nạt.
   * Chi tiết tự học & chiến thắng tí hon đầu tiên (bán game Blastar $500 năm 12 tuổi).
3. **The Rollercoaster Loop (15% - 75% thời lượng - Lặp lại 3–4 lần):**
   * *Nỗ lực điên cuồng:* Ngủ sàn văn phòng, làm việc 100 giờ/tuần.
   * *Thành công bước đầu:* Bán công ty triệu đô.
   * *Bi kịch / Bị đâm sau lưng:* Bị HĐQT lật đổ, sa thải ngay trên máy bay.
   * *Gamble All In:* Đặt cược toàn bộ tài sản vào lĩnh vực mới điên rồ hơn.
   * *Near-Death Crisis:* Điểm rơi sâu nhất (The Christmas 2008 moment - chỉ còn 72 giờ trước khi chết hẳn).
4. **The Breakthrough & System Scaling (75% - 90% thời lượng):**
   * Khoảnh khắc tháo nút thắt: Lần thử thứ 4 thành công, hợp đồng cứu mạng.
   * Chuyển hóa từ may mắn sang hệ thống quy mô lớn (hạ cánh tên lửa tái sử dụng).
5. **The Callback & Moral (90% - 100% thời lượng):**
   * Trả lời câu hỏi ở Hook: Kết nối lại với mốc tài sản nghìn tỷ và tờ 1 đô-la.
   * Đúc kết triết lý dí dỏm: *"Ranh giới giữa một con bạc điên rồ và một thiên tài có tầm nhìn chỉ là quả tên lửa có nổ ở lần phóng thứ tư hay không."*

### 4.2 Kỹ thuật viết câu (Sentence Engineering Rules)
* **Nguyên tắc ngắt nhịp 50/50:** Cứ 1 câu dài giải thích phải đi kèm 1–2 câu cực ngắn ($\le$ 6 từ) để chốt hạ.
  * *Ví dụ:* "Launch one: exploded. Launch two: failed. Money was gone."
* **Cụm từ chuyển tiếp định hình Persona:**
  * *"And here's the thing."* (Và đây mới là mấu chốt.)
  * *"Look at this."* (Nhìn vào điều này xem.)
  * *"On paper, fine. In reality, complete chaos."* (Trên giấy tờ thì ổn. Thực tế thì toang.)

---

## 5. VOICE SPEC (Áp Dụng Cho Pipeline Repo)

| Thuộc tính | Đo đạc video gốc | Cấu hình cho pipeline hiện tại (xKiro / Edge-TTS) |
| :--- | :--- | :--- |
| **Giọng đọc** | Nam, 30s, đàm thoại, tự nhiên, nhịp nhanh | `en-US-GuyNeural` (Tiếng Anh) hoặc `vi-VN-NamMinhNeural` (Tiếng Việt) |
| **Tốc độ (Rate)** | 184.8 WPM | Điều chỉnh `rate="+10%"` đến `"+15%"` để khớp 180 WPM |
| **Khoảng lặng (Pause)** | 713 ms trung bình | Tách các câu punchline thành **cue độc lập**, chèn silence 500–700ms |
| **Câu hỏi tu từ** | Lên giọng cuối câu | Kết thúc câu bằng dấu chấm hỏi `?` để TTS tự động bẻ tone |

---

## 6. AUDIO SPEC (BGM & SFX)

### 6.1 Nhạc nền (BGM)
* **Nhạc cụ chủ đạo:** Pizzicato strings (đàn dây gảy), acoustic guitar, bộ gõ nhẹ lo-fi.
* **Volume chuẩn:** Đặt mức âm lượng BGM ở mức `-18.5 dB` so với voice (`volume=0.10 - 0.12`).
* **Quy tắc ngắt nhạc:** Ngắt BGM hoàn toàn (0.5s silence) trước các câu chốt hạ hoặc sự cố nổ tên lửa/phá sản.

### 6.2 Bảng khớp sự kiện SFX (SFX Event Mapping)
| Sự kiện trong hình ảnh / kịch bản | Âm thanh SFX tương ứng |
| :--- | :--- |
| Vẽ chữ hoặc biểu đồ lên màn hình | `crayon_scribble.mp3` |
| Nhân vật hoặc icon bật lên | `bubble_pop.mp3` / `plop.mp3` |
| Chuyển cảnh trượt nhanh | `paper_whoosh.mp3` |
| Nhắc đến tiền triệu đô / nghìn tỷ đô | `cash_register_kaching.mp3` |
| Tên lửa nổ / thất bại thảm hại | `muffled_explosion.mp3` / `record_scratch.mp3` |

---

## 7. MOTION SPEC (2D Animation Rules)

| Kỹ thuật | Thông số kỹ thuật | Ghi chú thực thi |
| :--- | :--- | :--- |
| **Ken Burns** | Zoom nhẹ `1.00` $\to$ `1.08` trong suốt 5 giây của shot | Đã hỗ trợ bởi `image_kenburns_provider.py` |
| **Line Boil (Rung nét sáp)** | Dao động nét vẽ ở tần số **8 – 12 fps** | Giả lập vẽ tay stop-motion, không để hình bị chết |
| **Pop-in Elements** | Scale icon từ `0%` $\to$ `115%` $\to$ `100%` trong 200ms | Tạo nhịp điệu thị giác mỗi 2–3 giây trong 1 shot dài |
| **Thời lượng shot** | **4.5s – 6.0s** | Phù hợp với chỉ số median 4.47s đo được |

---

## 8. GAP ANALYSIS VỚI PIPELINE HIỆN TẠI (Không Sửa Code)

| Yêu cầu Style Bible | Hiện trạng codebase của repo | Khoảng cách (Gap) | Mức độ |
| :--- | :--- | :--- | :--- |
| **Shot ngắn 4.5 – 5.0s** | `script_auditor.py` đang cố định shot 10s, 6–28 từ | Cần cơ chế chia sub-shot hoặc cho phép shot 4–6s | 🔴 P0 |
| **Tỉ lệ câu ngắn > 50%** | Auditor cho phép câu dài đến 28 từ | Cần cập nhật prompt LLM viết kịch bản ưu tiên câu ngắn | 🔴 P0 |
| **Master Style Suffix** | Veo/Imagen prompt chưa có bộ suffix sáp màu cố định | Cần inject Master Prompt và Negative Prompt vào prompt gen ảnh | 🔴 P0 |
| **Lớp âm thanh SFX** | Pipeline hiện mới có Voice + BGM | Chưa có module tự động chèn SFX theo sự kiện | 🟡 P1 |
| **BGM đổi mood theo Beat** | BGM chạy 1 bài duy nhất xuyên suốt | Cần hỗ trợ chuyển bài hoặc ngắt nhạc ở beat khủng hoảng | 🟡 P1 |
| **Voice cảm xúc qua cue** | Đã có TTS xKiro / Edge-TTS | Tận dụng dấu câu và tách cue thay vì phụ thuộc slider của ElevenLabs | 🟢 Đã đáp ứng |

---

## 9. CHECKLIST QA CHO MỌI VIDEO SẢN XUẤT SAU NÀY

- [ ] **Hook Test:** Giây 0–15 có chứa nghịch lý hoặc câu đố toán học đời thường không?
- [ ] **Short Sentence Check:** Tỉ lệ câu ngắn ($\le$ 6 từ) có đạt $\ge$ 50% không?
- [ ] **Pacing Check:** WPM nằm trong khoảng 170–190 WPM, cứ mỗi shot có ít nhất 1 thay đổi thị giác sau 3 giây không?
- [ ] **Crisis Test:** Cốt truyện có chứa ít nhất 2 lần nhân vật rơi vào trạng thái "suýt mất hết sạch tiền" không?
- [ ] **Callback Test:** Câu kết có quay lại trả lời hoặc nhắc lại câu hỏi ở phần mở đầu không?
- [ ] **Audio Check:** Loudness tổng nằm ở `-18.0` đến `-19.0 LUFS`, BGM không đè át voice?
- [ ] **Visual Check:** 100% hình ảnh tuân thủ đúng bảng màu sáp và nền giấy sần, không có bóng bẩy 3D?
