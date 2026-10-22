# Pilot Comparison & Blind Test Protocol — Crayon Capital Style

> **Mục đích:** Thiết kế kịch bản thử nghiệm mù (Blind Pilot Test) thời lượng 60–90 giây trên một chủ đề hoàn toàn mới để kiểm chứng tính hiệu quả của Style Bible trước khi sản xuất hàng loạt.
> **Chủ đề thí điểm được chọn:** *"Cách Jensen Huang biến một quán cà phê Denny's thành đế chế chip AI 3.000 tỷ USD (Nvidia)"*.

---

## 1. Thiết Kế 3 Phiên Bản Thử Nghiệm (A / B / C Test)

Cả 3 phiên bản đều dùng chung:
* Độ dài: **75 giây** (~220 từ).
* Giọng đọc: `vi-VN-NamMinhNeural` hoặc `en-US-GuyNeural` (TTS có sẵn).
* Mức loudness: `-18.5 LUFS`.

| Tiêu chí | Version A (Chỉ Story DNA) | Version B (Chỉ Visual & Audio Style) | Version C (Full Style Bible: Story + Visual + Audio) |
| :--- | :--- | :--- | :--- |
| **Kịch bản** | Áp dụng công thức Hook nghịch lý, câu ngắn > 50%, ẩn dụ ELI5, callback | Kịch bản phong cách bài báo thông thường ("Hôm nay chúng ta tìm hiểu lịch sử Nvidia...") | Áp dụng đầy đủ thuật ngữ ẩn dụ 3 tầng, hook nghịch lý, câu chốt hạ ngắn |
| **Hình ảnh** | Slide ảnh thông thường hoặc video stock | Nét vẽ sáp màu Crayon Capital, nhân vật biếm họa | Nét vẽ sáp màu Crayon Capital + nền giấy sần động đổi màu theo mood |
| **Âm thanh** | BGM thông thường, không ngắt | Nhạc pizzicato, có tiếng sột soạt sáp màu | Nhạc pizzicato ngắt nhịp ở câu chốt + SFX ka-ching và bubble pop |

---

## 2. Kịch Bản Mẫu Cho Version C (Chuẩn Full Style Bible - 75 Giây)

```text
[00:00 - 00:12] (Hook Nghịch Lý - Nền bảng đen)
Năm 1993, ba kỹ sư ngồi trong một quán ăn ven đường, gọi món xúc xích rẻ tiền nhất và quyết định lập một công ty. 
Hôm nay, công ty đó đáng giá hơn toàn bộ nền kinh tế của nước Đức cộng lại.
Và đây mới là điều điên rồ.

[00:12 - 00:25] (Setup & Ẩn Dụ - Nền giấy sần)
Một trong ba người đó là Jensen Huang. 
Thời trẻ, ông từng làm phục vụ bàn, cọ rửa nhà vệ sinh và liên tục bị dọa đuổi việc.
Ý tưởng của họ lúc đó nghe như trò đùa: Bán card đồ họa để game thủ chơi trò bắn quái vật mượt hơn.

[00:25 - 00:45] (Leo Thang & Khủng Hoảng - Nền xám chì)
Nhưng trò đùa đó suýt biến thành thảm họa.
Năm 1995: Con chip đầu tiên thất bại.
Năm 1996: Tiền túi cạn sạch, chỉ còn sống được 30 ngày.
Jensen phải sa thải hơn một nửa nhân viên.
Ông đặt cược số tiền cuối cùng vào một con chip mới mang tên RIVA 128.
Nếu nó nổ? Phá sản. Hết phim.

[00:45 - 00:60] (Bước Ngoặt Thần Kỳ - Nền sáng dần)
Nhưng con chip đó bán được một triệu chiếc trong bốn tháng.
Và 20 năm sau, khi làn sóng trí tuệ nhân tạo ập đến, người ta nhận ra:
Những con chip dùng để bắn quái vật... lại là thứ duy nhất trên đời đủ thông minh để huấn luyện AI.

[00:60 - 00:75] (Callback & Đúc Kết)
Từ quán ăn Denny's đến tập đoàn 3 nghìn tỷ đô-la.
Bài học rút ra là gì?
Đôi khi, người giàu nhất trong cơn sốt vàng không phải là người đi đào vàng... mà là người kiên trì bán những chiếc xẻng xịn nhất thế giới.
```

---

## 3. Bảng Đo Lường & So Sánh Chỉ Số (Pilot Metrics vs Gốc)

| Chỉ số định lượng | Video Gốc (`npoPedEq3hs`) | Mục tiêu Pilot 75s | Ngưỡng chấp nhận |
| :--- | :--- | :--- | :--- |
| **WPM khi nói (Effective WPM)** | **184.8 WPM** | 180 WPM | 170 – 195 WPM |
| **% Câu ngắn ($\le$ 6 từ)** | **54.4%** | 52.0% (13/25 câu) | $\ge 50\%$ |
| **Độ dài shot trung bình** | **5.0 giây** | 4.8 giây (15 shots / 75s) | 4.0 – 6.0 giây |
| **Số khoảng lặng kịch tính** | **11.58 lần/phút** | 12.0 lần/phút (~15 lần / 75s) | 10 – 14 lần/phút |
| **Integrated Loudness** | **-18.3 LUFS** | -18.5 LUFS | -18.0 đến -19.0 LUFS |
| **Tỉ lệ nói / tổng thời lượng** | **77.8%** | 78.0% | 75% – 80% |

---

## 4. Tiêu Chuẩn Đánh Giá Mù (Blind Evaluation Matrix)

Cho 10 người xem ngẫu nhiên xem 3 video A, B, C mà không tiết lộ thông tin kỹ thuật:

| Tiêu chí khảo sát (Thang điểm 1 - 10) | Giả thuyết kỳ vọng | Kết luận kiểm chứng |
| :--- | :--- | :--- |
| **1. Tỉ lệ giữ chân qua 15 giây đầu (Hook Retention)** | Version C & A vượt trội B | Khẳng định Hook nghịch lý là yếu tố quyết định người xem ở lại |
| **2. Mức độ dễ hiểu khái niệm công nghệ (Comprehension)** | Version C & A vượt trội B | Khẳng định ẩn dụ ELI5 hiệu quả hơn việc giải thích thuật ngữ |
| **3. Cảm giác thú vị, không buồn ngủ (Engagement)** | Version C cao nhất, kế đến B | Khẳng định nhịp cắt 5s + nét vẽ sáp hỗ trợ cảm xúc tốt khi kịch bản đã chuẩn |
| **4. Khả năng ghi nhớ thông điệp sau 24 giờ (Recall)** | Version C cao nhất | Nhờ có câu chốt hạ Callback ở phần kết |

> **Quy tắc chốt:** Nếu Version C đạt điểm trung bình $\ge 8.5/10$ và các chỉ số đo đạc lệch không quá $\pm 10\%$ so với video gốc, bộ Style Bible chính thức được khóa thành **Thuật Toán Sản Xuất Cố Định** cho toàn bộ xưởng nội dung.
