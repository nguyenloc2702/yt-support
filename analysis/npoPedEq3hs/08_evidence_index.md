# Evidence Index — Video `npoPedEq3hs`

**Tiêu đề:** The Rise of Elon Musk Explained Like You're 5  
**Kênh:** Crayon Capital  
**Thời lượng:** 1,150 giây (19 phút 10 giây)  
**Ngày phân tích:** 2026-10-22  
**Nguồn xác thực:** YouTube Video ID `npoPedEq3hs`  

---

## 1. Danh mục các tệp bằng chứng (Evidence Artifacts)

| Tệp | Định dạng | Mô tả nội dung | Phương pháp thu thập |
| :--- | :--- | :--- | :--- |
| `00_metadata.json` | JSON | Metadata gốc từ YouTube API / yt-dlp (views, tags, channel ID) | `yt-dlp --dump-json` |
| `00_source_manifest.json` | JSON | Bản tóm tắt nhận diện video nguồn (URL, ngày đăng, thời lượng) | Trích từ metadata |
| `01_transcript.json` | JSON | 2,755 từ, 349 câu phân đoạn chính xác theo từng mili-giây | Trích từ `sub.en-orig.json3` |
| `02_segments.json` | JSON | 8 phân đoạn cốt truyện cấu trúc từ Hook đến Callback | Phân tích cấu trúc theo nội dung |
| `03_shots.json` | JSON | Thống kê số lượng cắt cảnh, thời lượng shot trung bình/median/P90 | Đo đạc qua ffmpeg scene detection |
| `04_keyframes/*.jpg` | Ảnh JPEG | 40 keyframe tiêu biểu đại diện cho 8 phân đoạn câu chuyện | Trích xuất tại các mốc thời gian cốt lõi |
| `05_audio_metrics.json` | JSON | Đo đạc định lượng âm thanh (LUFS, LRA, Peak, WPM, Gaps, Pauses) | ffmpeg ebur128 + thống kê timestamp |
| `06_contact_sheet.jpg` | Ảnh JPEG | Grid 5x5 (25 khung hình) bao quát toàn bộ tiến trình hình ảnh | Ghép ma trận keyframes qua OpenCV |
| `09_visual_analysis.json` | JSON | Phân tích Visual DNA (nét vẽ, màu sắc, nhân vật, background) | Khảo sát keyframe định lượng |
| `10_story_analysis.json` | JSON | Phân tích Story DNA (Hook, Open Loops, Metaphors, Humor, Pacing) | Khảo sát cấu trúc kịch bản |
| `11_audio_analysis.json` | JSON | Phân tích Audio DNA (giọng kể, BGM, SFX mapping, audio ducking) | Khảo sát âm thanh định lượng |
| `12_independent_critique.json` | JSON | Đánh giá phản biện độc lập (chống thiên kiến, tách biệt nguyên nhân) | Đánh giá đa chiều |
| `13_triangulation.md` | Markdown | Bảng đối chiếu chéo Observed / Measured / Inferred và độ tin cậy | Tổng hợp bằng chứng |

---

## 2. Bảng bằng chứng theo mốc thời gian (Timestamped Evidence Ledger)

| ID | Timestamp | Phân loại | Bằng chứng quan sát / đo đạc | Ý nghĩa đối với độ giữ chân (Retention) | Độ tin cậy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **E001** | `00:00 - 00:06` | Hook | *"June 2026, SpaceX goes public and Elon Musk becomes the first trillionaire in human history."* | Mở đầu bằng một mốc giả định tương lai gây sốc, đặt cọc sự tò mò ngay giây 0. | **High (Observed)** |
| **E002** | `00:06 - 00:18` | Hook Paradox | *"If you have $1 in your pocket, you are closer to him than he is to Elon."* | So sánh phi lý nhưng đúng về mặt toán học ($300B vs $1T), neo tâm trí người xem vào khoảng cách tài sản không tưởng. | **High (Observed)** |
| **E003** | `00:18 - 00:32` | Open Loop #1 | *"How the freak does one man get that far ahead? Fired from both companies he built, bet everything on rockets that kept exploding, and somehow ended up here."* | Mở vòng tò mò cốt lõi (câu hỏi trung tâm) bao trùm toàn bộ 19 phút: Từ kẻ bị sa thải đến người giàu nhất. | **High (Observed)** |
| **E004** | `00:32 - 02:25` | Early Setup | Pretoria, South Africa (1971), game Blastar bán $500 năm 12 tuổi, rời bỏ quê hương sang Canada với $2,000. | Thiết lập xuất phát điểm bất lợi và phẩm chất tự học (self-taught). | **High (Observed)** |
| **E005** | `02:25 - 04:45` | First Venture | Zip2: Bỏ Stanford sau 2 ngày, ngủ ở văn phòng, tắm ở YMCA, bị HĐQT tước quyền CEO, bán cho Compaq lấy $22M. | Vòng lặp leo thang đầu tiên: Làm việc điên cuồng $\to$ Bị tước quyền $\to$ Thắng lớn $\to$ Mua McLaren F1. | **High (Observed)** |
| **E006** | `04:45 - 07:30` | Escalation | X.com sáp nhập PayPal: Đảo chính lật đổ khi Elon đang trên máy bay hưởng tuần trăng mật; bán cho eBay lấy $180M. | Khủng hoảng liên tiếp: Thành công tài chính nhưng luôn bị hất cẳng khỏi chính đứa con của mình. | **High (Observed)** |
| **E007** | `07:30 - 10:40` | All In Crisis | Bỏ $100M vào SpaceX, $70M vào Tesla. Sang Nga mua tên lửa bị nhổ nước bọt. Falcon 1 nổ lần 1, lần 2, lần 3 liên tiếp. | Đỉnh điểm của rủi ro (All is lost moment): Tiền cạn, danh tiếng tan tành, bị báo chí gọi là kẻ lừa đảo. | **High (Observed)** |
| **E008** | `10:40 - 14:00` | Climax 2008 | Tháng 9/2008: Falcon 1 lần 4 bay thành công. Nhưng Giáng sinh 2008: Tesla chỉ còn tiền sống 72 giờ. Hợp đồng NASA $1.6B & vòng gọi vốn hoàn tất vào đêm 24/12. | Giải tỏa căng thẳng tột độ (The Miracle breakthrough). | **High (Observed)** |
| **E009** | `14:00 - 17:20` | Scaling | Falcon 9 tái sử dụng tên lửa, giảm 90% chi phí. Model 3 Production Hell ngủ trên sàn nhà máy. Starlink thống trị quỹ đạo. | Chứng minh quy mô: Không chỉ là may mắn một lần mà là hệ thống vận hành. | **High (Observed)** |
| **E010** | `17:20 - 19:10` | Callback & Moral | Quay lại mốc 2026: Callback trực tiếp về câu nói mở đầu $1. Đúc kết: Ranh giới giữa kẻ điên và thiên tài chỉ là một lần phóng không phát nổ. | Đóng toàn bộ vòng tò mò, mang lại cảm giác trọn vẹn (Catharsis). | **High (Observed)** |
| **E011** | Toàn video | Audio Metric | Loudness: `-18.3 LUFS`, WPM lời nói: `184.8 WPM`, 54.4% câu $\le$ 6 từ, 11.58 khoảng lặng kịch tính/phút. | Nhịp điệu dồn dập nhưng có kiểm soát, tạo sự lôi cuốn tự nhiên như một câu chuyện phiếm kịch tính. | **High (Measured)** |
