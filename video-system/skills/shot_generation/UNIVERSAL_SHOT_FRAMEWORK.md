# SƯỜN CHUNG GEN ẢNH / SHOT VIDEO — DÙNG CHO MỌI TÌNH HUỐNG
## (UNIVERSAL SHOT FRAMEWORK v2)

> **Tài liệu cốt lõi trong bộ Skill:** [`skills/shot_generation/`](SKILL.md) *(Tên gốc: `suon-chung-gen-anh-shot-video.md` - Bản v2)*  
> **Khuôn mẫu Crayon Capital:** Xem [`CRAYON_EXPLAINER_MOLD.md`](CRAYON_EXPLAINER_MOLD.md)  
> **Quy trình tổng thể dự án:** Xem [`skills/video_production/`](../video_production/SKILL.md)  
> 
> Đây là sườn duy nhất bạn cần mang đi. Đừng viết prompt từ đầu mỗi lần.
> Cách dùng: giữ nguyên khung, chỉ thay các ô trong ngoặc vuông `[ ]`.
> Đổi chủ đề, bối cảnh hay cảm xúc mà vẫn giữ đúng sườn thì các shot vẫn nối được với nhau.
>
> **Bản v2 — 2026-11-03. Đây là bản đồng bộ với skill `~/workspace/custom-skills/crayon_explainer_gen/SKILL.md`.**
> Nếu sườn này và skill lệch nhau ở đâu, lấy khối Style Lock ở mục “Tiêu chí đã chốt” dưới đây làm chuẩn,
> vì khối đó được copy nguyên văn từ skill v2. Đừng tự viết lại Style Lock theo trí nhớ.
>
> V2 sửa cái gì so với v1: v1 chỉ đúng với shot cảnh rộng đơn giản. V2 bổ sung đúng 4 thứ mà v1 đã bỏ sót,
> và chính 4 thứ đó làm cho video thật khác video gen thử:
> (1) nhân vật phải là người que, phân biệt rõ nhân vật chính và quần chúng;
> (2) khung hình có 3 lớp và cắt viền tiền cảnh, không phải icon ở giữa nền trống;
> (3) shot trích dẫn có chữ viết tay hoặc bong bóng suy nghĩ hiện dần, và câu trích dẫn là punchline;
> (4) shot sơ đồ là một loại shot riêng, vật vẽ lớn, số đỏ là điểm nhấn.

## BẢN ĐỒ NHÌN 1 PHÁT — LỜI THOẠI ĐI VÀO, VIDEO ĐI RA

```text
Lời thoại + timecode
  -> CHỌN LOẠI SHOT: cảnh / trích dẫn / đám đông / sơ đồ (xem mục 0.1)
  -> KHÓA: Style Lock + Character Sheet chính + Character Sheet quần chúng + tỉ lệ 16:9
     (cửa 1: chưa duyệt thì chưa gen; chưa test đủ 4 loại shot thì chưa gen hàng loạt)
  -> HÌNH DUNG: mắt nhìn đâu đầu tiên, rồi nhìn đâu thứ hai,
     khung có 3 lớp nào, trong khung KHÔNG có gì, nhân vật chính là ai, ai là quần chúng
  -> ĐIỀN 5 Ô BẮT BUỘC: chủ thể + hành động + tiêu điểm + bối cảnh 3 lớp + cấm
  -> GEN ẢNH KEYFRAME: 1 shot = 1 ý = 1 tiêu điểm
  -> GEN VIDEO: tối đa 1 chuyển động nhỏ, chậm; shot trích dẫn thì chuyển động đó là chữ/bong bóng hiện dần
  -> NỐI SHOT: cắt cứng sang ý mới, không dùng pan/zoom để giả vờ đổi ý
  -> KIỂM TRA: mặt, áo, chính/phụ, chữ, tỉ lệ có giống shot trước không (cửa 2: sai thì gen lại)
```

### 0.1. Bốn loại shot — chọn loại trước khi điền ô

Mọi shot trong khuôn này đều thuộc 1 trong 4 loại dưới đây. Chọn sai loại là prompt sẽ sai từ đầu,
dù các ô còn lại điền đẹp đến đâu.

| Loại shot | Khi nào dùng? | Tiêu điểm là gì? | Chuyển động video là gì? |
|---|---|---|---|
| **1. Cảnh rộng / cảnh kể** | Giới thiệu bối cảnh, tương phản kích thước, nhân vật làm một việc | Một vật lớn hoặc một nhân vật, ví dụ người nhỏ bên tên lửa lớn | Chớp mắt, sáng dần, vật phụ chuyển động nhẹ |
| **2. Trích dẫn / đối thoại** | Lời thoại là một câu nói trực tiếp, một câu đùa, một lời thú nhận, một suy nghĩ | Câu chữ viết tay hoặc bong bóng suy nghĩ, mặt người nói là thứ hai | Chữ hoặc bong bóng hiện dần, miệng người nói mở ra, đám đông giữ nguyên mặt |
| **3. Đám đông / phòng họp** | Nhiều người cùng xuất hiện, họp, làm việc, xem, chờ đợi | Nhân vật chính là mặt da đào duy nhất có chi tiết; đám đông là nền | Nhân vật chính đổi biểu cảm nhẹ, đám đông gần như đứng yên |
| **4. Sơ đồ / infographic** | Số liệu, thời gian, chi phí, so sánh, quy trình, nguyên nhân — kết quả | Vật lớn + số lớn, đọc từ trái sang phải hoặc từ trên xuống dưới | Đường biểu đồ đi lên, cột mọc lên, con dấu đóng xuống, số hiện lên |

Một đoạn video hay luôn xen kẽ 4 loại này: cảnh rộng để mở, đám đông để tạo bối cảnh,
trích dẫn để tạo câu đùa hoặc cao trào cảm xúc, sơ đồ để chốt số liệu. Đừng làm cả video chỉ toàn cảnh rộng.

### Dùng nhanh trong 60 giây

- Chỉ cần điền đủ 5 ô bắt buộc là đủ gen thử: chủ thể + hành động + tiêu điểm + bối cảnh 3 lớp + cấm.
- Trước 5 ô đó, phải trả lời thêm 2 câu: shot này thuộc loại nào trong 4 loại trên, và ai là nhân vật chính, ai là quần chúng.
- 9 ô còn lại trong sườn 14 ô là để tinh chỉnh, không phải để chặn lần gen đầu.
- Không bao giờ tự viết lại Style Lock hay Character Sheet theo trí nhớ. Copy nguyên văn khối đã khóa ở dưới.

### Luật tỉ lệ khung hình — không crop để cứu

- Tỉ lệ là ô KHÓA, không phải ô tùy chọn. Video ngang là 16:9, video dọc mới là 9:16.
- Nếu AI trả về sai tỉ lệ thì gen lại. Tuyệt đối không crop ảnh dọc thành ngang để cứu, vì crop sẽ cắt đầu hoặc vai nhân vật. Bài học từ shot 006 của BYD: file dọc 672x1344 bị crop về 16:9 chỉ còn khoảng 28% chiều cao gốc, mất đỉnh đầu và vai, mặt còn tràn khung khi quay đầu.

### Khuôn Crayon Capital đã xem lại ngày 2026-11-03

- Video tham chiếu: Crayon Capital, “The ENTIRE History of Elon Musk”, 19:09, https://www.youtube.com/watch?v=npoPedEq3hs. Lần xem này phát được, không cần đăng nhập, nhưng chỉ lấy mẫu khung hình theo khoảng thời gian, không kiểm tra từng frame.
- Khung hình đã xác minh từ lần xem mẫu: đầu tròn rất to, thân nhỏ dạng ống, tay chân mảnh, viền đen dày, màu phẳng, nền chỉ 1-2 vật đơn giản, gần như không có bóng đổ hay chi tiết thừa. Nhân vật thường được nhận diện bằng bối cảnh, trang phục và chữ trên hình, không phải bằng nét mặt chi tiết. Đầu đôi khi bị cắt ở mép khung để nhấn mạnh, nhưng đó là lựa chọn bố cục có chủ đích, không phải lỗi crop.
- Chuyển động đã xác minh: cắt cứng là chuyển động chính. Không xác minh được pan, zoom hay tracking trong một shot, cũng không xác minh được nhân vật đi vào hay đi ra liên tục. Ý mới được thể hiện bằng cách cắt và đổi khung: toàn cảnh -> cận cảnh -> thẻ chữ hoặc số.
- Ý trừu tượng được thể hiện bằng: chữ và số rất to (“$1 TRILLION”, “2,300% PER YEAR!”), đường zigzag xanh đi lên cho tăng trưởng, lặp lại nhiều bàn và màn hình giống nhau cho quy mô, và tương phản kích thước: người nhỏ đứng cạnh tòa nhà hoặc tên lửa khổng lồ.
- Nhịp kể: mỗi shot thường chỉ chứa 1 câu hoặc 1 trò đùa của lời thoại, rồi cắt ngay sang ý tiếp theo. Chương dài 1,5-3,5 phút, nhưng shot thì chỉ vài giây.
- Áp dụng cho khuôn này, và cũng là điểm cần nói rõ với yêu cầu animation của BYD: khuôn gốc không phải diễn xuất liên tục. Nếu dự án yêu cầu animation thì chỉ thêm tối đa 1 chuyển động nhỏ, chậm, ví dụ chớp mắt, pin sáng dần, đường biểu đồ đi lên, chữ trích dẫn hiện dần, rồi kết khung ổn định. Không biến mỗi shot thành cảnh nhân vật đi, chạy, diễn dài, và không dùng pan/zoom để giả vờ đã đổi ý.
- Chưa xác minh: mọi frame, chu trình đi bộ, vụ nổ tên lửa, chi tiết nhà máy và biểu đồ chi phí phóng, vì những khung đó không nằm trong mẫu đã xem. Đừng dùng những chi tiết đó làm bằng chứng cho khuôn.

### Bằng chứng mới ngày 2026-11-03 — 4 ảnh mẫu làm rõ khuôn

Bốn ảnh mẫu này không phải để copy hình, mà để sửa khuôn. Mỗi ảnh sửa một chỗ mà v1 đã sai:

| Ảnh mẫu | Nhìn thấy gì? | Sửa khuôn ở chỗ nào? |
|---|---|---|
| 4 kỹ sư SpaceX, chữ trên đầu “kind of exactly how rockets work.” | 4 đầu tròn to, mặt trắng, thân oval xanh navy có logo nhỏ, phía sau là tủ server với đèn đỏ vàng xanh và tủ dây. Một màn hình và một cái đầu bị cắt ở mép dưới và mép phải. Chữ viết tay trắng viền đen ở trên đầu, không che mặt người nói. | Đám đông mặt trắng, thân lặp lại; phòng làm việc là dãy vật lặp lại, không phải nền trống; chữ trích dẫn là một câu đầy đủ, không phải 1-3 từ. |
| Phòng họp, bong bóng “It exploded, but please don't fire us.” | Nhân vật chính ở giữa phía sau: mặt da đào, tóc nâu có hình, vest đen, tay que chụm lại, mắt nhắm, miệng mím. Tiền cảnh trái và phải là 2 nhân vật lớn bị cắt viền, mặt trắng, một người có giọt mồ hôi. Bong bóng mây trắng viền đen chiếm giữa khung, chữ viết tay đen bên trong. | Nhân vật chính nổi bật vì là mặt da đào duy nhất có chi tiết; tiền cảnh cắt viền tạo chiều sâu; bong bóng suy nghĩ là một dạng trích dẫn riêng, chữ bên trong là câu đầy đủ. |
| Văn phòng, chữ “and send it to Mars.” | Nhân vật chính ở giữa phải: mặt da đào, tóc nâu, vest đen, cười nhẹ, một tay que giơ ra. Bên trái là dãy nhân viên mặt trắng, bóng xám một bên, màn hình đen lặp lại. Bên phải tiền cảnh là một phụ nữ lớn bị cắt viền, mặt trắng xám, tóc búi đen, cầm bảng. | Nhân vật chính không cần ở giữa khung, chỉ cần là mặt da đào duy nhất; quần chúng mặt trắng + bóng xám; tiền cảnh lớn cắt viền làm khung có chiều sâu. |
| Sơ đồ 2 dải: xe, lịch, dấu, túi tiền, cột, số tiền | Dải trên nền xanh nhạt: xe thể thao đỏ vẽ nghiêng, lịch màu be có lò xo, chữ “2 YEARS” viết tay, con dấu đỏ nghiêng “OVERDUE”. Dải dưới nền be: túi tiền có dấu $, cột đỏ tăng dần, đường zigzag đỏ, chữ đỏ lớn “$50 MILLION”. Hai dải ngăn bằng một đường đen dày. | Sơ đồ là loại shot riêng: vật vẽ lớn, chỉ 3-4 chi tiết trong, một màu nhấn đỏ, số là chữ lớn nhất, đọc từ trái sang phải, từ trên xuống dưới. |

### Vì sao bản v1 chưa giống — nói thẳng 5 điểm

1. **V1 cho mọi nhân vật cùng một độ chi tiết.** Thật ra nhân vật chính và quần chúng khác nhau hoàn toàn: chính thì da đào, tóc có hình, vest tối; phụ thì mặt trắng, bóng xám, thân lặp lại. Cho mọi người cùng da, cùng chi tiết thì không còn ai nổi bật.
2. **V1 để nhân vật ở giữa nền trống.** Thật ra shot phòng họp, văn phòng là một căn phòng có chiều sâu: tiền cảnh cắt viền, nhân vật chính ở giữa hoặc lệch, hậu cảnh là dãy màn hình, tủ server, bàn lặp lại, mờ nhạt và phẳng. Nền trống làm shot thành icon, không thành cảnh.
3. **V1 cấm chữ dài.** Thật ra trích dẫn là punchline, phải là một câu đầy đủ, viết tay, hiện dần. Cấm chữ dài là tự bỏ đi câu đùa hay nhất của shot.
4. **V1 vẽ vật thành icon nhỏ.** Thật ra vật trong clip vẽ rất lớn, silhouette nhận ra ngay, chỉ 3-4 chi tiết trong, một màu nhấn, viền đen dày. Vật nhỏ cầm tay không bao giờ cuốn bằng vật lớn chiếm một phần ba khung hình.
5. **V1 chỉ có nét sạch và màu phẳng.** Thật ra nét cuốn còn nhờ đúng một mảng bóng xám phẳng ở bên tối của mặt và thân, không gradient, không vân bút. Bỏ mảng bóng đó thì hình phẳng lì, sạch nhưng vô hồn.

## TIÊU CHÍ ĐÃ CHỐT — ÁP DỤNG CHO MỌI VIDEO CỦA EMILY

> Cập nhật v2 ngày 2026-11-03, sau khi duyệt bản test 4 ảnh đầu của dự án BYD và sau 4 ảnh mẫu mới.
> Đây là tiêu chuẩn chung, không phải style riêng cho 1 video.
> Khi bắt đầu video mới, chỉ đổi chủ đề / nhân vật / bối cảnh — không tự đổi các tiêu chí dưới đây
> nếu chưa được duyệt lại.

### 1. Tham chiếu phong cách

- Phong cách gốc: Crayon Capital, video tham chiếu https://www.youtube.com/watch?v=npoPedEq3hs
- Học ngôn ngữ vẽ và cách kể của video đó, không copy nhân vật, không copy lời thoại, không copy hình gốc.
- Học cả 4 loại shot, không chỉ học shot cảnh rộng.

### 2. Nhân vật — người que, chính và phụ khác nhau rõ ràng

- Tất cả nhân vật đều là người que: đầu tròn rất to so với thân, mắt chỉ là chấm nhỏ, lông mày cong đơn giản.
- Thân chỉ là oval hoặc ống nhỏ, tay chân là đường que mảnh, bàn tay chỉ là blob nhỏ, không ngón, không khớp tay chi tiết.
- Miệng biểu cảm rõ: cười rộng lộ răng khi vui / thắng, há miệng hình O khi sốc, miệng thẳng khi lo lắng.
- **Nhân vật chính:** mặt màu da đào, tóc có hình rõ, trang phục một màu tối hoặc một màu nổi, là mặt duy nhất có chi tiết trong khung. Phải có Character Sheet riêng và ảnh tham chiếu duy nhất, giữ nguyên tóc, áo, tỉ lệ đầu – thân suốt video.
- **Quần chúng:** mặt trắng trơn, không màu da, một mảng bóng xám phẳng ở một bên mặt, tóc phẳng một màu, thân oval lặp lại y hệt nhau, độ tương phản thấp hơn nhân vật chính. Quần chúng là nền, không phải nhân vật thứ hai.
- Cử chỉ phóng đại, dễ đọc ngay trong 2 giây. Tay và áo là chỗ dễ lộ nhất: tay thật, tay áo có nếp, áo có khuy và nếp gấp là đã ra khỏi khuôn, phải gen lại.

### 3. Nét vẽ — sắc, sạch, chuyên nghiệp, không trẻ con, có một mảng bóng xám

- Nét: crisp, smooth, bold black outlines, cạnh sạch kiểu vector chuyên nghiệp, viền đen dày và đều.
- Bóng: đúng một mảng xám phẳng ở bên tối của mặt và thân, không gradient, không đổ bóng chi tiết, không vệt sáng.
- Cấm: vân bút màu, grain, texture giấy, nét nguệch ngoạc, nét run / wobbly / sketchy, kiểu vẽ trẻ con.
- Không vì muốn “giống bút màu” mà làm nét bẩn. Đơn giản về hình, nhưng nét phải sạch.

### 4. Màu sắc — bớt tươi một chút, vẫn sáng và rõ, một màu nhấn đỏ mỗi shot

- Palette: muted-but-clear, hơi trầm xuống, hơi desaturated một chút so với màu tươi rực.
- Gợi ý: dusty blue, muted navy, muted red, off-white, slate grey, warm earth tones.
- **Một màu nhấn đỏ mỗi shot:** chỉ dành cho vật tiêu điểm, con số, hoặc con dấu. Mọi thứ khác giữ muted để màu nhấn đấm vào mắt. Nếu mọi thứ đều đỏ, đều tươi thì không còn điểm nhấn.
- Cấm: màu kẹo quá tươi, quá bão hòa; và cấm ngược lại: tối, xám xịt, khó đọc trên điện thoại.
- Nguyên tắc: nhìn vẫn sáng, rõ, tương phản tốt, chỉ là không còn “tươi tươi” nữa.

### 5. Nguyên tắc gen ảnh / shot video

1. **1 shot = 1 ý = 1 tiêu điểm.** Hết 1 ý là đổi hình, không để 1 hình đứng yên kể dài.
2. **Chọn loại shot trước.** Cảnh, trích dẫn, đám đông hay sơ đồ — mỗi loại có tiêu điểm và chuyển động riêng.
3. **Nguồn hình là lời thoại + timecode.** Nếu visual prompt cũ lệch lời thoại thì bỏ visual prompt cũ, rút hình từ lời thoại.
4. **Khóa trước, gen sau.** Chưa duyệt Style Sheet + Character Sheet chính + Character Sheet quần chúng thì chưa gen hàng loạt.
5. **Test trước 4 loại shot, không chỉ test 4 shot đầu.** Một shot cảnh rộng, một shot trích dẫn, một shot đám đông, một shot sơ đồ. Loại nào rớt thì sửa sheet, không gen lại lung tung. Lệch thì sửa sheet, không gen thêm bản ngẫu nhiên.
6. **Gen theo cụm 20–30 shot**, duyệt từng cụm rồi mới sang cụm sau.
7. **1 scene = 1 ảnh keyframe** cho dạng explainer có sẵn audio. Dựng theo đúng timecode, nối bằng cắt cứng.
8. **Chuyển động là cắt và đổi khung, không phải pan/zoom.** Nếu cần animation thì mỗi shot tối đa 1 chuyển động nhỏ, chậm, kết khung ổn định. Shot trích dẫn thì chuyển động đó là chữ hoặc bong bóng hiện dần. Không dùng pan/zoom để giả vờ đã đổi ý, và không biến shot thành diễn xuất liên tục chỉ để cho có chuyển động.
9. **Chữ trên hình chia 3 loại, không gộp chung:**
   - Thẻ tiêu đề và con số: tối đa 1–3 từ, in hoa, rất to.
   - Trích dẫn và suy nghĩ: một câu ngắn đầy đủ, chữ viết tay, ở trên đầu hoặc trong bong bóng mây trắng.
   - Nhãn sơ đồ: chữ viết tay, nhãn trung tính màu đen, nhãn punch màu đỏ, số là chữ lớn nhất.
   - Chữ sai chính tả, chữ nhỏ li ti, chữ bị cắt là shot hỏng, không phải lỗi nhỏ.
10. **Khung hình có 3 lớp với shot cảnh và shot đám đông:** tiền cảnh cắt viền, nhân vật chính ở giữa, hậu cảnh là dãy vật lặp lại mờ nhạt. Shot nào mọi nhân vật đều nằm gọn trong khung, nền trống, là shot chưa đạt.
11. **Vật phải vẽ lớn.** Vật là ý thì vật chiếm ít nhất một phần ba khung hình, hoặc có một vùng riêng trong shot sơ đồ. Vật nhỏ cầm tay không gánh được shot.
12. **Nhân vật chính và quần chúng không đổi vai giữa chừng.** Một người đang là quần chúng mặt trắng thì không tự nhiên thành nhân vật chính da đào ở shot sau, trừ khi đó là chuyển vai có chủ đích và đã ghi rõ trong shot list.

### 6. Các bản đã bị loại — đừng quay lại

| Bản đã thử | Vì sao loại |
|---|---|
| Vector bóng bẩy, gradient mềm, chi tiết nhiều | Không phải phong cách Crayon Capital đã chọn |
| Vân bút màu, nét nguệch ngoạc, grain | Trông trẻ con, nét không sạch |
| Màu quá tươi, bão hòa cao | Trông “tươi tươi” quá, không đúng gu đã duyệt |
| Mọi nhân vật cùng da, cùng chi tiết, không ai nổi bật | Mất nhân vật chính, shot thành đám đông phẳng |
| Nhân vật ở giữa nền trống, không tiền cảnh, không hậu cảnh | Nhìn như icon, không giống cảnh trong clip thật |
| Chữ trích dẫn bị rút còn 1-3 từ hoặc bỏ luôn | Mất punchline, mất câu đùa và cao trào cảm xúc |
| Vật vẽ nhỏ, icon, nhiều chi tiết kỹ thuật | Không cuốn, không đọc được trong 2 giây |
| Chữ sai chính tả, chữ nhỏ, số bị chìm | Shot sơ đồ và shot trích dẫn hỏng ngay từ chữ |

### 7. Khối Style Lock dùng cho mọi prompt — dán nguyên văn, không viết lại

```text
STYLE LOCKED:
Simple flat 2D cartoon explainer, clean sharp professional line art.
Crisp, smooth, bold black outlines, clean vector edges.
Flat colours, minimal shading: exactly one flat grey side shadow
on the shaded side of a head or body, no gradient, no detailed shading.
Characters are stick figures: oversized round heads, very small dot eyes,
simple curved eyebrows, wide toothy grins OR shocked open mouths,
a small oval or tube body, thin single-line stick arms and legs,
hands as small plain blobs, no fingers, no detailed clothing folds.
Main character: peach skin face, detailed hair, dark or coloured outfit,
sharp and high contrast. Crowd characters: plain white faces,
no skin tone, one flat grey side shadow, simple flat hair,
repeated identical oval bodies, lower contrast than the main character.
Muted-but-clear palette, slightly desaturated: dusty blue, muted navy,
muted red, off-white, slate grey, warm earth tones.
One accent colour per shot, muted red, reserved for the focal object,
number, or stamp. Everything else stays muted so the accent punches.
Bright, simple backgrounds, one clear scene, no clutter, still bright and readable.
Aspect ratio: 16:9, unless the format requires 9:16.
Negative: NO crayon texture, NO grain, NO paper texture,
NO rough sketchy wobbly lines, NO childish doodle texture,
NO overly vivid candy colours, NO dark grey muddy look,
no photorealism, no 3D render, no detailed shading, no gradient,
no blurry, no extra limbs, no distorted hands,
no crowded background, no tiny unreadable text, no garbled text,
no realistic torso, shirt, or jacket with folds and buttons,
no crowd character with the same skin tone and detail as the main character.
```

> Lưu ý khi dán vào prompt tiếng Việt hoặc công cụ chỉ hiểu prompt ngắn: vẫn dán nguyên khối tiếng Anh này trước,
> rồi mới viết phần mô tả shot bằng tiếng Anh ngắn gọn ở sau. Đừng dịch khối này, đừng tóm tắt khối này,
> vì dịch và tóm tắt là hai cách nhanh nhất làm mất quy tắc chính/phụ và quy tắc bóng xám.

---

## 0. NGUYÊN TẮC CỐT LÕI: TÁCH PHẦN KHÓA VÀ PHẦN BIẾN

Một shot đẹp không phải do prompt dài, mà do **phần khóa không đổi, phần biến đổi có chủ đích**.

| Nhóm | Vai trò | Khi nào được đổi? |
|---|---|---|
| **KHÓA — LOCKED** | Phong cách vẽ, bảng màu, tỉ lệ khung hình, cách tả nhân vật chính, cách tả quần chúng, chất lượng hình, quy tắc bóng xám, quy tắc màu nhấn đỏ | Khóa ngay từ shot đầu tiên, giữ suốt video. Chỉ đổi khi làm video mới hoàn toàn. |
| **BIẾN — VARIABLE** | Loại shot, chủ thể phụ, hành động, bối cảnh, cảm xúc, góc máy, chuyển động, chữ trên hình, vật tiêu điểm | Đổi theo từng shot, theo đúng ý của câu thoại / ý cần kể. |
| **CẤM ĐỔI LUNG TUNG** | Kiểu tóc, màu áo, tỉ lệ đầu – thân, font chữ, tông màu chủ đạo của nhân vật chính, và vai trò chính/phụ của một nhân vật | Đổi những thứ này là nhân vật “đổi mặt”, video vỡ ngay. |

Quy tắc vàng:

1. **1 shot = 1 ý = 1 tiêu điểm.** Người xem phải biết nhìn vào đâu trong 2 giây đầu.
2. **Chọn loại shot trước, điền ô sau.** Shot trích dẫn mà điền như shot cảnh rộng thì sẽ không có chỗ cho chữ. Shot sơ đồ mà điền như shot cảnh thì vật sẽ nhỏ và số sẽ chìm.
3. **Viết những gì máy sẽ thấy, không viết những gì bạn đang nghĩ.** Thay vì “cảm giác thành công”, hãy viết “nhân vật dang tay que, cười rộng, phía sau là tên lửa đang bay lên”.
4. **Ảnh là 1 khoảnh khắc, video là 1 chuyển động.** Prompt ảnh mô tả trạng thái đứng yên. Prompt video phải có: bắt đầu thế nào → chuyển động gì → kết thúc ra sao.
5. **Mỗi lần chỉ đổi 1 nhóm ô.** Đổi cùng lúc chủ thể, bối cảnh, cảm xúc, góc máy và phong cách thì đó không còn là biến hóa nữa, mà là làm video mới.

---

## 1. CÔNG THỨC GHÉP PROMPT — 1 DÒNG DUY NHẤT

Dùng đúng thứ tự này, đừng đảo lộn, vì AI đọc theo thứ tự ưu tiên từ trái sang phải:

```text
[PHONG CÁCH] + [LOẠI SHOT] + [CHỦ THỂ CHÍNH + QUẦN CHÚNG]
+ [HÀNH ĐỘNG / TRẠNG THÁI] + [BỐI CẢNH 3 LỚP]
+ [GÓC MÁY + CỠ CẢNH] + [CHUYỂN ĐỘNG, chỉ dùng cho video]
+ [ÁNH SÁNG] + [MÀU SẮC / CẢM XÚC] + [VẬT TIÊU ĐIỂM + CHỮ TRÊN HÌNH]
+ [KỸ THUẬT] + [KHÔNG ĐƯỢC CÓ]
```

Ví dụ đọc công thức này thành câu:

> Phong cách gì, loại shot nào, ai là chính và ai là phụ, ai đang làm gì, khung có 3 lớp nào,
> máy quay đặt ở đâu và quay kiểu gì, sáng ra sao, tâm trạng gì, vật tiêu điểm và chữ gì,
> cuối cùng là cấm cái gì.

---

## 2. SƯỜN 14 Ô — ĐIỀN ĐỦ 14 Ô LÀ ĐỦ GEN

Copy bảng này cho mỗi shot. Ô nào không cần thì ghi `—`, đừng bỏ trống mơ hồ.

| # | Ô trong sườn | Hỏi chính mình | Điền ví dụ |
|---|---|---|---|
| 1 | **MỤC ĐÍCH SHOT** | Shot này để làm gì? Giới thiệu, giải thích, gây sốc, chuyển cảnh, kết thúc? | Giới thiệu nhân vật lần đầu |
| 2 | **LOẠI SHOT** | Thuộc 1 trong 4 loại: cảnh / trích dẫn / đám đông / sơ đồ? | Trích dẫn |
| 3 | **CHỦ THỂ CHÍNH** | Ai / cái gì là trung tâm? Tả đủ để nhận ra ngay, kèm mặt da đào hay mặt trắng. | Một chàng trai trẻ, đầu tròn to, mặt da đào, tóc nâu chải lệch, vest đen |
| 4 | **QUẦN CHÚNG / NHÂN VẬT PHỤ** | Ai là nền? Họ mặt trắng hay mặt da đào, đứng ở lớp nào? Nếu không có thì ghi `Không có quần chúng`. | 3 nhân viên mặt trắng, bóng xám một bên, thân oval xanh navy lặp lại, đứng phía sau |
| 5 | **HÀNH ĐỘNG / TRẠNG THÁI** | Chủ thể đang làm gì, ở tư thế nào? Dùng động từ cụ thể, chỉ 1 hành động chính. | Đang giơ một tay que ra, cười nhẹ, nhìn sang người đối diện |
| 6 | **BIỂU CẢM / CẢM XÚC** | Mặt ra sao? Vui, sốc, mệt, quyết tâm? Người nói và đám đông khác nhau ra sao? | Chính: cười nhẹ tự tin. Đám đông: mặt thẳng, lo lắng, không đổi |
| 7 | **BỐI CẢNH 3 LỚP** | Tiền cảnh là gì và bị cắt viền ra sao, nhân vật chính ở đâu, hậu cảnh là dãy vật gì lặp lại? | Tiền cảnh: một đầu người lớn bị cắt ở mép phải. Hậu cảnh: dãy màn hình đen lặp lại |
| 8 | **THỜI GIAN / KHÔNG KHÍ** | Ngày hay đêm, trong nhà hay ngoài trời, yên tĩnh hay náo nhiệt? | Ban ngày, trong văn phòng, yên tĩnh nhưng căng thẳng |
| 9 | **CỠ CẢNH + GÓC MÁY** | Lấy toàn thân, nửa người hay cận mặt? Máy ngang mắt, từ dưới lên, từ trên xuống, hay chéo? | Medium shot, ngang tầm mắt, hơi chéo 3/4 |
| 10 | **BỐ CỤC + MẮT NHÌN** | Mắt nhìn vào đâu đầu tiên, rồi nhìn đâu thứ hai? Chủ thể ở đâu trong khung? | Nhìn vào mặt nhân vật chính trước, rồi nhìn sang chữ trích dẫn ở trên |
| 11 | **ÁNH SÁNG + MÀU SẮC / PHONG CÁCH** | Đây là ô KHÓA. Ghi đúng phong cách đã chốt từ đầu, kèm màu nhấn đỏ của shot này là gì. | Dán nguyên khối Style Lock. Màu nhấn đỏ của shot này: không có vật đỏ, chỉ có chữ đen |
| 12 | **CHỮ TRÊN HÌNH** | Thuộc loại chữ nào: tiêu đề 1-3 từ, trích dẫn một câu, hay nhãn sơ đồ? Viết đúng chính tả trong ngoặc kép. Nếu không có thì ghi `Không có chữ`. | Trích dẫn, chữ viết tay trắng viền đen ở trên đầu: “and send it to Mars.” |
| 13 | **CHUYỂN ĐỘNG + ÂM THANH (chỉ cho video)** | Nhân vật cử động gì, chữ hiện ra sao, máy có đứng yên không? | Xem mục 5 |
| 14 | **KỸ THUẬT + CẤM** | Tỉ lệ, độ dài, độ phân giải, và những thứ cấm xuất hiện. | 16:9, 10 giây, không tay thật, không áo có nếp gấp, không chữ sai chính tả, không nền rối, không quần chúng cùng da với nhân vật chính |

### Mẹo điền nhanh

- **Không biết điền ô nào → điền ô đó bằng câu đơn giản nhất.** “Trong phòng, có dãy màn hình phía sau” vẫn tốt hơn bỏ trống.
- **Càng ít vật phụ càng tốt, nhưng phòng thì cần dãy vật lặp lại.** Dãy màn hình lặp lại là một vật lặp, không phải 10 vật khác nhau.
- **Chữ trên hình phải ghi đúng loại.** Đừng ghi chung chung là “có chữ”. Ghi rõ: tiêu đề, trích dẫn, hay nhãn sơ đồ, và viết đúng từng chữ trong ngoặc kép.
- **Ô quần chúng không được bỏ trống khi shot có nhiều người.** Nếu để trống, AI sẽ tự cho mọi người cùng da, cùng chi tiết, và nhân vật chính sẽ chìm.

---

## 3. Ô KHÓA: CHARACTER SHEET + STYLE SHEET — VIẾT 1 LẦN, DÁN VÀO MỌI PROMPT

Đây là phần quan trọng nhất để các shot không bị “mỗi shot một mặt”.

### 3.1. Character Sheet — nhân vật chính

```text
CHARACTER: [Tên gọi nội bộ, ví dụ: NHÂN VẬT A]
- Dạng: flat 2D cartoon, người que, đầu tròn rất to so với thân, thân chỉ là oval hoặc ống nhỏ
- Mặt: mặt màu da đào, là mặt duy nhất có chi tiết trong khung
- Tóc: [màu + kiểu + hướng chải, ví dụ: tóc nâu, chải lệch sang phải, có hình rõ]
- Mắt: [ví dụ: 2 chấm đen nhỏ, lông mày cong đơn giản]
- Miệng: [ví dụ: cười rộng lộ răng khi vui, há miệng hình O khi sốc, miệng thẳng khi lo lắng]
- Tay chân: tay chân là đường que mảnh, bàn tay là blob nhỏ, không ngón, không tay áo có nếp
- Trang phục cố định: [ví dụ: vest đen một màu, chỉ có cổ áo chữ V trắng, không khuy, không nếp gấp]
- Dấu hiệu nhận diện duy nhất: [1 thứ không bao giờ đổi, ví dụ: kiểu tóc lệch phải]
- Chiều cao / tỉ lệ: [ví dụ: đầu chiếm khoảng 1/3 chiều cao cơ thể, thân nhỏ hơn đầu rất nhiều]
- Bóng: một mảng xám phẳng ở một bên mặt và thân, không gradient
```

Dán nguyên đoạn này vào đầu mọi prompt có nhân vật chính. Đừng viết tắt, đừng viết lại theo trí nhớ mỗi lần.

### 3.2. Character Sheet — quần chúng, dùng chung cho mọi shot đông người

```text
CROWD: [Nhóm quần chúng, ví dụ: NHÂN VIÊN VĂN PHÒNG]
- Dạng: flat 2D cartoon, người que, đầu tròn rất to so với thân, thân chỉ là oval nhỏ lặp lại
- Mặt: mặt trắng trơn, KHÔNG màu da, một mảng bóng xám phẳng ở một bên mặt
- Tóc: tóc phẳng một màu, đơn giản, không có hình chi tiết
- Mắt: 2 chấm đen nhỏ, lông mày thẳng hoặc cong nhẹ
- Miệng: miệng thẳng hoặc hơi cong xuống, lo lắng hoặc bình thản, không đổi theo nhân vật chính
- Tay chân: đường que mảnh, bàn tay blob nhỏ, không ngón
- Trang phục: một màu phẳng lặp lại cho cả nhóm, ví dụ áo xanh navy, chỉ có một dấu nhỏ nếu cần
- Vai trò: là nền, độ tương phản thấp hơn nhân vật chính, không có đặc điểm riêng nổi bật
```

Quy tắc vàng cho quần chúng: nếu một nhân vật phụ trong shot trông giống nhân vật chính về da, tóc và chi tiết,
thì shot đó đã sai, dù nhìn riêng nó rất đẹp. Hạ chi tiết của quần chúng xuống, không nâng chi tiết của nhân vật chính lên thêm.

### 3.3. Style Sheet — tả phong cách chung

Mặc định dùng khối Style Lock ở mục “Tiêu chí đã chốt” phía trên, copy nguyên văn. Chỉ khi làm video khác hẳn mới viết lại, và phải duyệt lại trước khi gen.

Tóm tắt bằng tiếng Việt để kiểm tra nhanh, không thay cho khối tiếng Anh:

```text
STYLE LOCKED — tóm tắt kiểm tra nhanh:
- Phong cách: Simple flat 2D cartoon explainer, clean sharp professional line art,
  crisp smooth bold black outlines, clean vector edges, flat colours
- Bóng: đúng một mảng xám phẳng ở bên tối, không gradient, không đổ bóng chi tiết
- Người: người que, đầu tròn to, mắt chấm, thân oval nhỏ, tay chân que, tay blob không ngón
- Chính: mặt da đào, tóc có hình, áo tối hoặc áo nổi, tương phản cao
- Phụ: mặt trắng, bóng xám, thân oval lặp lại, tương phản thấp
- Bảng màu: Muted-but-clear, hơi desaturated: dusty blue, muted navy,
  muted red, off-white, slate grey, warm earth tones — vẫn sáng và rõ, không tối, không xám xịt
- Màu nhấn: một màu đỏ muted mỗi shot, chỉ cho vật tiêu điểm, số, hoặc con dấu
- Nền: sáng, đơn giản, tương phản tốt, shot phòng thì có dãy vật lặp lại mờ nhạt, không rối
- Chữ: tiêu đề in hoa to; trích dẫn chữ viết tay; nhãn sơ đồ viết tay, số punch màu đỏ
- Tỉ lệ khung hình: 16:9 cho YouTube, 9:16 cho Shorts / TikTok
- Chất lượng: sharp, clean lines, high detail on face của nhân vật chính, simple background
- Cấm: crayon texture, grain, paper texture, nét nguệch ngoạc / wobbly / sketchy,
  màu kẹo quá tươi, photorealism, 3D render, đổ bóng chi tiết, gradient,
  tay thật, áo có nếp gấp và khuy, quần chúng cùng da với nhân vật chính, chữ sai chính tả
```

### 3.4. Nếu có ảnh tham chiếu

Khi công cụ cho phép đính kèm ảnh tham chiếu, hãy dùng đúng 1 ảnh nhân vật chuẩn cho mọi shot và ghi thêm:

```text
Use the attached reference image for the main character.
Keep the same face, hairstyle, outfit and art style. Do not redesign the character.
Crowd characters in this shot are white-faced and less detailed than the main character.
Do not give the crowd the main character's skin tone or detail.
```

Đừng đính mỗi shot một ảnh khác nhau. Một ảnh chuẩn duy nhất mới giữ được sự nhất quán.
Với shot sơ đồ, không cần ảnh tham chiếu nhân vật, vì sơ đồ không có nhân vật chính.

---

## 4. PROMPT GEN ẢNH — MẪU ĐẦY ĐỦ, CHỈ VIỆC THAY Ô VUÔNG

### 4.1. Mẫu tiếng Anh cho shot cảnh — dùng trực tiếp cho hầu hết công cụ gen ảnh

```text
[STYLE LOCKED],
[CHARACTER SHEET chính rút gọn: đầu tròn to, mặt da đào, tóc nâu chải lệch, vest đen,
 thân oval nhỏ, tay chân que, tay blob],
[CROWD nếu có: mặt trắng, bóng xám, thân oval lặp lại],
[CHỦ THỂ] is [HÀNH ĐỘNG, 1 động từ chính], [BIỂU CẢM],
[TIỀN CẢNH: vật hoặc người bị cắt viền],
in [BỐI CẢNH, dãy vật lặp lại phía sau], [THỜI GIAN / KHÔNG KHÍ],
[CỠ CẢNH], [GÓC MÁY], [BỐ CỤC: mắt nhìn vào đâu trước, rồi nhìn đâu sau],
[ÁNH SÁNG], [MÀU SẮC / CẢM XÚC, màu nhấn đỏ là gì],
[CHI TIẾT PHỤ, tối đa 2–3 vật, hoặc một dãy vật lặp lại],
[CHỮ TRÊN HÌNH, nếu có, viết đúng chính tả trong ngoặc kép: "TEXT"].
Sharp, clean lines, one clear focal point, no clutter.
Negative: photorealistic, 3D render, blurry, extra limbs, extra fingers,
realistic hands, detailed shirt with folds and buttons,
crowd with the same skin tone as the main character,
changed hairstyle, changed outfit, tiny unreadable text, garbled text, crowded background.
```

### 4.2. Ví dụ đã điền — shot giới thiệu nhân vật đang làm việc ban đêm

```text
STYLE LOCKED: clean sharp flat 2D cartoon, crisp smooth bold black outlines,
flat colours, exactly one flat grey side shadow, muted-but-clear palette, no crayon texture,
a young man with an oversized round head, peach skin face, very small dot eyes,
brown side-swept hair, black oval body, thin stick arms, blob hands, black suit,
is typing on an old computer, leaning forward, focused and excited,
foreground: a monitor edge cropped at the bottom of the frame,
in a small room with a repeated row of dark monitors behind him, at night, quiet atmosphere,
medium shot, eye-level, three-quarter angle,
character on the right, computer screen on the left, empty space on top,
warm muted desk lamp light from the left, simple muted background,
simple props only: computer, notebook, mug.
Sharp, clean lines, one clear focal point, no clutter.
Negative: NO crayon texture, NO grain, NO sketchy wobbly lines,
photorealistic, 3D render, blurry, extra limbs, extra fingers, realistic hands,
detailed shirt with folds, tiny unreadable text, crowded background.
```

### 4.3. Mẫu và ví dụ cho shot trích dẫn — chữ là tiêu điểm

Mẫu:

```text
[STYLE LOCKED],
[CHARACTER SHEET chính], [CROWD nếu có],
[NGƯỜI NÓI] is [HÀNH ĐỘNG], [BIỂU CẢM KHỚP VỚI CÂU NÓI],
[TIỀN CẢNH cắt viền nếu có], in [BỐI CẢNH, dãy vật lặp lại],
[CỠ CẢNH], [GÓC MÁY],
[CHỮ TRÍCH DẪN: loại chữ, vị trí, màu, viết đúng chính tả trong ngoặc kép: "QUOTE"].
The quote is the focal point. The speaker's face is the second focal point.
Crowd keeps a flat worried expression and does not change.
Sharp, clean lines, no clutter.
Negative: garbled text, misspelled text, small text, text covering the speaker's face,
crowd with the same skin tone as the main character, realistic hands, crowded background.
```

Ví dụ đã điền, dạng chữ trên đầu:

```text
STYLE LOCKED, a young man with an oversized round head, peach skin face,
brown hair, black suit, thin stick arms, blob hands,
is smiling slightly and raising one stick arm, confident,
in an office with a repeated row of dark monitors and white-faced staff behind him,
medium shot, eye-level, character on the right side of the frame,
handwritten white caption with black outline at the top of the frame, large and readable:
"and send it to Mars."
The caption is the focal point, the man's face is the second focal point.
Crowd: white faces, one flat grey side shadow, flat worried expressions, unchanged.
Sharp, clean lines, no clutter.
Negative: garbled text, small text, text covering the face, realistic hands, crowded background.
```

Ví dụ đã điền, dạng bong bóng suy nghĩ:

```text
STYLE LOCKED, a young man with an oversized round head, peach skin face,
brown hair, black suit, thin stick arms, sitting behind a wooden table,
eyes closed, mouth in a flat worried line, hands steepled as two thin lines,
foreground left and right: two large white-faced characters cropped by the frame edge,
one with a sweat drop, in a plain meeting room, grey wall, wooden table,
medium shot, eye-level,
large white cloud thought bubble with thick black outline in the centre foreground,
black handwritten text inside the bubble, large and readable:
"It exploded, but please don't fire us."
The bubble is the focal point, the man's face behind it is the second focal point.
Sharp, clean lines, no clutter.
Negative: garbled text, small text, realistic hands, crowded background.
```

Quy tắc chữ trích dẫn, nhắc lại vì đây là chỗ hay sai nhất:

- Chỉ một câu ngắn. Câu dài thì cắt thành 2 shot, đừng thu nhỏ chữ.
- Chữ phải đúng chính tả từng từ, kể cả dấu câu. AI gen chữ hay sai, nên kiểm tra chữ trước khi kiểm tra mặt.
- Nếu công cụ gen chữ hay sai, gen khung có bong bóng trống hoặc khoảng trống ở trên đầu, rồi chèn chữ đúng lúc dựng. Đừng giao shot có chữ sai.
- Vị trí chữ không được che mặt người nói. Chữ trên đầu thì để ở trên, bong bóng thì để ở giữa nhưng người nói vẫn lộ ở sau hoặc ở mép.

### 4.4. Mẫu và ví dụ cho shot sơ đồ — vật và số là tiêu điểm

Mẫu:

```text
[STYLE LOCKED],
[DIAGRAM LAYOUT: số dải, màu nền mỗi dải, đường ngăn, thứ tự đọc],
[ZONE 1: vật, màu, 3-4 chi tiết trong, nhãn],
[ZONE 2: vật, màu, 3-4 chi tiết trong, nhãn],
[ZONE 3 nếu có: vật hoặc con dấu, màu nhấn đỏ],
[CON SỐ PUNCH: chữ gì, màu gì, lớn cỡ nào, ở đâu].
No main character, no room, no crowd, unless a character is the scale reference.
Sharp, clean lines, one idea, one reading order.
Negative: small objects, small numbers, technical detail, gradient,
garbled text, crowded zones, photorealistic, 3D render.
```

Ví dụ đã điền, sơ đồ 2 dải:

```text
STYLE LOCKED, two flat bands split by a thick black horizontal line,
top band pale blue, bottom band beige, reading order left to right, top to bottom.
Top band, left: a large red sports car in side profile, black roof, grey windows,
dark wheels, one red side mirror, black handwritten label above it: "ROADSTER".
Top band, middle: a large beige calendar page with a spiral top,
black handwritten text inside: "2 YEARS".
Top band, right: a tilted red rectangular stamp with bold red capitals inside: "OVERDUE".
Bottom band, left: a large tan money bag with a single green dollar sign.
Bottom band, middle: four red bars rising left to right, with a red zigzag line above them.
Bottom band, right: large red handwritten number: "$50 MILLION".
No character, no room, no crowd. Sharp, clean lines, one idea.
Negative: small objects, small numbers, technical detail, garbled text, gradient.
```

### 4.5. Cách vẽ vật sao cho cuốn — silhouette, 3-4 chi tiết, vẽ lớn

- **Silhouette trước.** Vật phải nhận ra từ đường viền ngoài, trước khi đọc chi tiết trong.
- **Chỉ 3-4 chi tiết trong.** Xe: cửa kính, bánh xe, gương, nóc đen. Tên lửa: vây, dải ngang, mũi. Tủ server: hàng đèn đỏ vàng xanh, hàng lỗ nhỏ. Thêm chi tiết thứ năm là bắt đầu giống bản vẽ kỹ thuật, không còn giống tranh kể chuyện.
- **Một màu phẳng chủ đạo + viền đen dày + xám hoặc trắng cho chi tiết phụ.** Không gradient, không vệt sáng, không texture.
- **Vẽ lớn.** Vật là ý thì vật chiếm ít nhất một phần ba khung hình, hoặc có một vùng riêng trong shot sơ đồ. Vật nhỏ cầm tay không gánh được shot.
- **Lặp lại y hệt.** Cùng một vật xuất hiện nhiều lần thì phải vẽ giống nhau mọi lần: cùng màu, cùng số chi tiết, cùng góc nhìn. Đổi góc nhìn của vật giữa chừng cũng giống như đổi mặt nhân vật.

### 4.6. Cách biến hóa từ ví dụ mà không phá sườn

| Muốn đổi gì? | Chỉ đổi ô này | Giữ nguyên |
|---|---|---|
| Đổi sang ban ngày, ngoài trời | Bối cảnh + Thời gian + Ánh sáng | Nhân vật, phong cách, cỡ cảnh, chính/phụ |
| Đổi sang cảm xúc thất bại | Biểu cảm + Hành động + Màu nền tối hơn một chút | Nhân vật, trang phục, kiểu tóc |
| Đổi sang cận mặt để nhấn mạnh | Cỡ cảnh thành close-up + Bố cục | Nhân vật, ánh sáng, phong cách |
| Đổi nhân vật thành nhân vật khác | Character Sheet chính | Phong cách vẽ, bảng màu, tỉ lệ khung, quy tắc quần chúng |
| Đổi từ shot cảnh sang shot trích dẫn | Loại shot + Ô chữ + Chuyển động video | Nhân vật, bối cảnh, phong cách |
| Đổi từ shot cảnh sang shot sơ đồ | Loại shot + Bố cục thành các dải + Vật và số | Phong cách vẽ, bảng màu, màu nhấn đỏ |

---

## 5. PROMPT GEN SHOT VIDEO — MẪU ĐẦY ĐỦ, CÓ TIMELINE CHUYỂN ĐỘNG

Ảnh chỉ cần tả trạng thái. Video bắt buộc phải tả **chuyển động theo thời gian**.

### 5.1. Sườn timeline cho 1 shot video — luôn chia 3 đoạn theo tỉ lệ, không đóng khung 5 giây

```text
Shot: [SHOT 01 — mục đích, loại shot]
Thời lượng: [5 giây / 10 giây / 30 giây]
Ảnh keyframe: [dùng ảnh đã gen ở mục 4, đính kèm nếu công cụ cho phép]

MỞ, khoảng 30% thời lượng — ví dụ 0–3 giây nếu shot dài 10 giây:
  Nhân vật ở trạng thái: [ví dụ: đứng yên, nhìn vào máy quay]
  Chuyển động: [ví dụ: chưa di chuyển, chỉ chớp mắt nhẹ]

DIỄN, khoảng 40% thời lượng — ví dụ 3–7 giây nếu shot dài 10 giây:
  Hành động chính, duy nhất: [ví dụ: dang tay que rộng]
  Máy quay: [ví dụ: giữ máy yên, chỉ nhân vật cử động]
  Vật phụ chuyển động: [ví dụ: đường biểu đồ đi lên chậm]

KẾT, khoảng 30% thời lượng — ví dụ 7–10 giây nếu shot dài 10 giây:
  Trạng thái cuối: [ví dụ: nhân vật giữ tư thế dang tay, cười rộng]
  Máy quay: [ví dụ: dừng lại, giữ khung hình ổn định để cắt cứng sang shot sau]
```

Quy tắc chuyển động, áp dụng cho mọi loại shot:

- **1 shot chỉ có 1 chuyển động chính.** Chuyển động chính đó có thể là của nhân vật, của vật, hoặc của chữ. Không cộng dồn 2-3 chuyển động cùng lúc.
- **Chuyển động phải chậm và rõ.** Viết “slow”, “gently”, “gradually”. Đừng viết “fast”, “dramatically”, “suddenly”.
- **Kết shot phải ổn định.** Đừng kết ở giữa chuyển động, vì shot sau cắt vào sẽ bị giật.
- **Máy quay mặc định là đứng yên.** Trong khuôn này, cắt và đổi khung mới là cách đổi ý, không phải lia máy. Xem mục 5.4 để biết khi nào được phép lia máy, và khi nào tuyệt đối không.
- **Không biến shot thành diễn xuất liên tục.** Không đi, chạy, diễn dài, không chu trình đi bộ, trừ khi shot đó cố tình là shot đi bộ và đã ghi rõ trong shot list. Khuôn gốc không xác minh được chu trình đi bộ, nên đừng tự thêm.

### 5.2. Timeline riêng cho từng loại shot

| Loại shot | Mở 30% | Diễn 40% — chuyển động duy nhất | Kết 30% |
|---|---|---|---|
| Cảnh rộng | Nhân vật và vật ở trạng thái mở, máy yên | Chớp mắt, sáng dần, vật phụ chuyển động nhẹ, hoặc tương phản kích thước được giữ nguyên | Giữ khung ổn định, nhân vật nhìn về hướng đã định |
| Trích dẫn | Mặt người nói ở trạng thái phản ứng: sốc, lo, cười nhẹ. Đám đông giữ mặt phẳng. Chữ chưa hiện, hoặc chỉ hiện một phần rất nhỏ | **Chữ hoặc bong bóng hiện dần**, miệng người nói mở ra theo câu nói, đám đông không đổi | Giữ nguyên chữ đã hiện đầy đủ để người xem đọc kịp, máy yên, mặt người nói giữ nguyên |
| Đám đông | Cả phòng ở trạng thái mở, nhân vật chính ở vị trí đã định | Nhân vật chính đổi biểu cảm nhẹ một lần, ví dụ từ nghiêm sang cười nhẹ, đám đông gần như đứng yên | Giữ khung ổn định, nhân vật chính vẫn là mặt da đào duy nhất có chi tiết |
| Sơ đồ | Các vật và nhãn ở trạng thái mở, số punch chưa hiện hoặc hiện mờ | **Đường biểu đồ đi lên, cột mọc lên, con dấu đóng xuống, hoặc số hiện lên**, chỉ một trong các chuyển động đó | Giữ nguyên sơ đồ đã hoàn thành để người xem đọc số kịp, máy yên |

Với shot dài 30 giây ghép từ nhiều đoạn 10 giây, mỗi đoạn 10 giây vẫn chia 30/40/30 trong đoạn đó,
và đoạn cuối phải kết khung ổn định. Đừng để đoạn giữa kết ở giữa chuyển động rồi đoạn sau nhảy về trạng thái đầu.

### 5.3. Mẫu prompt video đầy đủ — dùng trực tiếp

```text
[STYLE LOCKED],
[CHARACTER SHEET chính rút gọn], [CROWD nếu có],
[CHỦ THỂ] [HÀNH ĐỘNG MỞ ĐẦU],
then [HÀNH ĐỘNG CHÍNH, mô tả rõ bằng động từ, chỉ 1 hành động],
finally [TRẠNG THÁI KẾT THÚC, giữ yên].
Setting: [BỐI CẢNH 3 LỚP], [THỜI GIAN / KHÔNG KHÍ].
Camera: [CỠ CẢNH], [GÓC MÁY], [MÁY ĐỨNG YÊN, chỉ nhân vật hoặc chữ cử động].
Lighting: [ÁNH SÁNG]. Mood: [CẢM XÚC].
[CHỮ TRÊN HÌNH, nếu có: loại chữ + vị trí + "TEXT" đúng chính tả].
Duration: [5s / 10s]. Aspect ratio: [16:9].
Smooth slow motion, stable final frame, one clear focal point.
Negative: fast shaky camera, morphing face, changing outfit, changing crowd skin tone,
extra limbs, distorted hands, realistic hands, flickering background,
unreadable text, garbled text.
```

### 5.4. Ví dụ đã điền — shot nhân vật từ làm việc chuyển sang ăn mừng

```text
STYLE LOCKED: clean sharp flat 2D cartoon, crisp smooth bold black outlines,
flat colours, exactly one flat grey side shadow, muted-but-clear palette, no crayon texture,
a young man with an oversized round head, peach skin face, very small dot eyes,
brown side-swept hair, black oval body, thin stick arms, blob hands, black suit,
starts typing on a computer, then stands up and raises both stick arms in celebration,
finally holds the celebrating pose and smiles at the camera.
Setting: a small room with a wooden desk and a window, a repeated row of monitors behind,
at night.
Camera: medium shot, eye-level, static camera, character moves only.
Lighting: warm muted desk lamp from the left.
Mood: excited, triumphant.
Duration: 10s. Aspect ratio: 16:9.
Smooth slow motion, stable final frame, one clear focal point.
Negative: NO crayon texture, fast shaky camera, morphing face, changing outfit,
extra limbs, distorted hands, realistic hands, flickering background, unreadable text.
```

### 5.5. Ví dụ đã điền — shot trích dẫn, chữ hiện dần

```text
STYLE LOCKED, a young man with an oversized round head, peach skin face,
brown hair, black suit, thin stick arms, blob hands,
starts with a shocked open O mouth, looking at the camera,
then a large white cloud thought bubble with thick black outline pops in slowly
in the centre foreground, with black handwritten text inside:
"It exploded, but please don't fire us.",
finally holds the finished bubble and the shocked expression, camera static.
Setting: a plain meeting room, wooden table, foreground characters cropped at the edges.
Crowd: white faces, flat grey side shadow, flat worried expressions, unchanged.
Duration: 10s. Aspect ratio: 16:9.
Smooth slow motion, stable final frame, quote readable for at least 3 seconds at the end.
Negative: garbled text, small text, text covering the speaker's face,
fast camera, morphing face, realistic hands, crowded background.
```

### 5.6. Từ vựng chuyển động máy — mặc định là đứng yên, chỉ dùng lia máy khi ý không đổi

| Muốn hiệu ứng gì? | Viết đúng cụm này | Khi nào được dùng? |
|---|---|---|
| Giữ máy yên, chỉ nhân vật hoặc chữ cử động | static camera, character moves only | Mặc định cho mọi shot. Dùng trước, luôn đúng với khuôn. |
| Tiến lại gần nhân vật, ý vẫn là ý cũ | slow dolly-in, same idea, no new subject | Chỉ khi shot dài và cần nhấn mạnh dần, không dùng để giới thiệu ý mới. |
| Lùi ra để lộ bối cảnh, ý vẫn là ý cũ | slow dolly-out, same idea, no new subject | Chỉ khi shot dài và cần mở khung, không dùng để chuyển đoạn. |
| Quét ngang để giới thiệu bối cảnh | slow pan left / slow pan right | Chỉ dùng cho shot cảnh rộng mở đầu, và phải ghi rõ pan xong thì khung kết ổn định. Không dùng pan để thay cho một shot mới. |
| Quét dọc từ dưới lên trên | slow tilt up | Chỉ dùng cho vật rất lớn như tên lửa, tòa nhà, và phải ghi rõ ý vẫn là một ý. |
| Xoay quanh nhân vật | Không dùng trong khuôn này | Xoay quanh làm mặt biến dạng và phá quy tắc 1 chuyển động. Muốn đổi góc thì cắt cứng sang shot mới. |

Quy tắc nhắc lại, vì đây là chỗ hay bị hiểu sai: **pan, zoom, dolly không phải là shot mới.**
Nếu ý đã đổi, cắt cứng sang một khung mới. Nếu lia máy mà ý vẫn cũ, lia thật chậm và kết khung ổn định.
Nếu không chắc, chọn static camera.

### 5.7. Từ vựng cỡ cảnh — chọn theo mục đích

| Cỡ cảnh | Khi nào dùng? |
|---|---|
| Extreme wide shot | Giới thiệu bối cảnh lớn: thành phố, sân phóng, nhà máy, tương phản kích thước |
| Wide shot | Giới thiệu toàn cảnh + vị trí nhân vật trong bối cảnh |
| Medium shot | Cảnh kể chuyện phổ biến nhất: thấy cả người và hành động, dùng cho trích dẫn và đám đông |
| Close-up | Nhấn mạnh cảm xúc, khuôn mặt, hoặc 1 vật quan trọng |
| Extreme close-up | Nhấn mạnh 1 chi tiết duy nhất: con số, bàn tay blob, ánh mắt, con dấu |

---

## 6. BẢNG SHOT LIST — GHÉP NHIỀU SHOT THÀNH 1 VIDEO

Đừng gen lẻ từng shot rồi mới nghĩ cách nối. Viết shot list trước, gen sau. Shot list v2 bắt buộc có thêm 2 cột:
loại shot và ai là chính/phụ, vì thiếu 2 cột đó là thiếu khuôn.

| Shot | Loại shot | Mục đích | Nhân vật chính / quần chúng | Thời lượng | Cỡ cảnh / Góc máy | Chuyển động duy nhất | Chữ trên hình | Ghi chú nối shot |
|---|---|---|---|---|---|---|---|---|
| 01 | Cảnh rộng | Mở / hook | Chính: nhân vật A. Không có quần chúng | 10s | Wide, ngang mắt | Nhân vật chớp mắt, vật phụ sáng dần | 1 từ khóa to, nếu cần | Bắt đầu từ bối cảnh lớn |
| 02 | Đám đông | Giới thiệu bối cảnh làm việc | Chính: nhân vật A da đào. Phụ: 3 người mặt trắng phía sau | 10s | Medium, 3/4 | Chính đổi biểu cảm nhẹ, phụ đứng yên | Không có | Giữ cùng ánh sáng với shot 01 |
| 03 | Trích dẫn | Câu nói punchline | Chính: người nói. Phụ: đám đông giữ mặt lo | 10s | Medium / Close-up | Chữ hoặc bong bóng hiện dần | Một câu đầy đủ, viết tay | Kết khung ổn định để đọc kịp chữ |
| 04 | Sơ đồ | Chốt số liệu | Không có nhân vật, trừ khi cần người làm thước đo | 10s | Wide, thẳng | Đường biểu đồ đi lên hoặc con dấu đóng xuống | Số punch màu đỏ, lớn nhất khung | Để khoảng trống cuối để cắt |
| 05 | Cảnh rộng / kết | Cao trào / kết | Chính: nhân vật A | 10s | Wide hoặc Close-up | Giữ tư thế cuối, máy yên | 1 câu kết ngắn, nếu cần | Kết khung ổn định, không kết giữa chuyển động |

Quy tắc nối shot:

1. **Ánh sáng và màu nền không đổi đột ngột** giữa 2 shot liên tiếp, trừ khi cố tình chuyển ngày → đêm.
2. **Hướng nhìn của nhân vật phải nhất quán.** Shot trước nhìn sang phải thì shot sau đừng đột ngột nhìn sang trái, trừ khi có lý do.
3. **Vai trò chính/phụ không đổi đột ngột.** Shot trước một người là quần chúng mặt trắng thì shot sau đừng tự nhiên cho người đó mặt da đào và chi tiết đầy đủ.
4. **Vị trí nhân vật trong khung nên gần nhau** ở 2 shot liên tiếp, để mắt người xem không phải tìm lại nhân vật.
5. **Shot cuối của đoạn này và shot đầu của đoạn sau nên khác cỡ cảnh** để tạo cảm giác chuyển đoạn rõ ràng, ví dụ: close-up → wide shot.
6. **Chữ trích dẫn phải đọc kịp.** Shot trích dẫn phải giữ chữ đầy đủ ít nhất 30% thời lượng cuối, không cắt ngay khi chữ vừa hiện xong.

---

## 7. QUY TRÌNH 6 BƯỚC — TỪ Ý TƯỞNG ĐẾN SHOT HOÀN CHỈNH

```text
Bước 0 — Test 4 loại: Gen thử 1 shot cảnh rộng, 1 shot trích dẫn, 1 shot đám đông, 1 shot sơ đồ.
  Loại nào rớt thì sửa Style Sheet hoặc Character Sheet, rồi test lại loại đó.
  Chưa test đủ 4 loại thì chưa gen hàng loạt.
Bước 1 — Chốt ý: Viết 1 câu: “Shot này muốn người xem hiểu / cảm thấy điều gì?”
  Viết thêm 1 câu nữa: “Shot này thuộc loại nào, ai là chính, ai là phụ?”
Bước 2 — Điền sườn: Điền đủ 14 ô ở mục 2. Ô nào chưa rõ thì viết câu đơn giản nhất.
  Ô quần chúng và ô chữ không được bỏ trống một cách mơ hồ.
Bước 3 — Gen ảnh keyframe: Dùng prompt mục 4. Gen 2–3 bản, chọn bản nào nhân vật đúng nhất,
  chữ đúng chính tả nhất, chính/phụ rõ nhất — không chọn bản đẹp nhưng sai mặt hoặc sai chữ.
Bước 4 — Gen video từ keyframe: Dùng prompt mục 5, đính kèm ảnh keyframe nếu được.
  Chỉ gen 1 chuyển động chính. Shot trích dẫn thì chuyển động chính là chữ hiện dần.
Bước 5 — Kiểm tra nối shot: So shot mới với shot trước bằng checklist mục 8.
  Sai thì gen lại, đừng cố chữa bằng hiệu ứng dựng, đừng crop để cứu tỉ lệ.
```

---

## 8. CHECKLIST KIỂM TRA MỖI SHOT TRƯỚC KHI DÙNG

- [ ] Nhìn 2 giây đầu đã biết nhân vật chính là ai và đang làm gì chưa?
- [ ] Nhân vật có giữ đúng tóc, áo, tỉ lệ đầu – thân, thân oval nhỏ, tay que, tay blob như Character Sheet không?
- [ ] Nhân vật chính có phải là mặt da đào duy nhất có chi tiết không? Quần chúng có phải mặt trắng, bóng xám, thân lặp lại không?
- [ ] Khung có 3 lớp không, hay chỉ là nhân vật ở giữa nền trống? Tiền cảnh có bị cắt viền một cách có chủ đích không?
- [ ] Có đúng 1 tiêu điểm, nền không rối, không có vật lạ chen vào không?
- [ ] Tay, chân, mặt có bị biến dạng, thừa ngón, méo miệng, tay thật, áo có nếp gấp không?
- [ ] Chữ trên hình (nếu có) có đúng loại không: tiêu đề 1-3 từ, trích dẫn một câu, nhãn sơ đồ? Có đúng chính tả, đủ to, đọc được trên điện thoại không?
- [ ] Với shot sơ đồ: vật có đủ lớn không, số punch có phải chữ lớn nhất và màu đỏ không, thứ tự đọc có rõ không?
- [ ] Với video: chuyển động có chậm, mượt, chỉ có 1 chuyển động chính, kết khung ổn định không?
- [ ] Với video trích dẫn: chữ có hiện đầy đủ và giữ đủ lâu để đọc không, đám đông có giữ nguyên mặt không?
- [ ] Với video: mặt nhân vật có bị biến dạng dần về cuối shot không? Quần chúng có tự nhiên đổi da, đổi chi tiết không?
- [ ] Shot này nối với shot trước có bị lệch ánh sáng, lệch hướng nhìn, lệch vai trò chính/phụ không?
- [ ] Tỉ lệ khung hình có đúng 16:9 không? Nếu sai tỉ lệ thì gen lại, không crop.
- [ ] Với shot có ảnh tham khảo (`reference_image_url` hoặc 10-20 ảnh tư liệu của script): Đã khai thác chi tiết đặc thù từ ảnh thực tế (kiểu dáng xe, cấu trúc pin, trụ sở, thiết bị, nhân vật) làm ý tưởng trung tâm và CHUYỂN THỂ mượt mà sang chuẩn vector Crayon Capital (người que, nét mực đen đậm, 1 màu đỏ nhấn, tuyệt đối không dán ảnh thật) chưa?

Nếu rớt 1 mục trong checklist, sửa đúng ô đó trong sườn rồi gen lại. Đừng sửa bằng cách viết prompt dài thêm một cách mơ hồ.
Đặc biệt: rớt mục chữ hoặc mục chính/phụ là rớt nặng, phải gen lại, không được giữ lại vì “nhìn cũng đẹp”.

---

## 9. LỖI HAY GẶP VÀ CÁCH SỬA ĐÚNG Ô

| Lỗi | Nguyên nhân thường gặp | Sửa ở ô nào? |
|---|---|---|
| Mỗi shot nhân vật một mặt | Không dùng Character Sheet, hoặc mỗi lần viết lại khác nhau | Dán lại Character Sheet chính nguyên văn vào mọi prompt |
| Quần chúng giống hệt nhân vật chính, không ai nổi bật | Không điền ô quần chúng, hoặc không ghi mặt trắng và bóng xám | Điền ô 4: mặt trắng, bóng xám, thân oval lặp lại, tương phản thấp |
| Tay thật, áo có nếp gấp, khuy, tay áo chi tiết | Prompt tả trang phục kiểu thật, hoặc công cụ tự thêm chi tiết | Thêm vào ô trang phục và ô cấm: thân oval nhỏ, tay que, tay blob, không nếp gấp, không khuy |
| Tay thừa, mặt méo | Prompt quá dài, nhiều hành động cùng lúc | Giảm xuống 1 hành động chính, thêm vào Negative: extra limbs, distorted hands, realistic hands |
| Chữ sai chính tả, chữ nhỏ li ti | Để AI tự vẽ chữ dài, hoặc ghi chữ chung chung | Rút trích dẫn còn 1 câu ngắn, viết trong ngoặc kép, hoặc gen bong bóng trống rồi chèn chữ lúc dựng |
| Chữ trích dẫn bị rút còn 1-3 từ, mất punchline | Áp nhầm quy tắc chữ tiêu đề cho shot trích dẫn | Đổi ô chữ thành loại trích dẫn: một câu đầy đủ, chữ viết tay |
| Video rung, mờ, mặt biến dạng | Nhiều chuyển động máy + nhân vật cùng lúc, chuyển động quá nhanh | Chỉ giữ 1 chuyển động chậm, máy đứng yên, kết khung ổn định |
| Shot trích dẫn cắt ngay khi chữ vừa hiện | Không giữ chữ đủ lâu ở đoạn kết | Timeline đoạn kết 30% phải giữ nguyên chữ đầy đủ |
| Nền rối, không biết nhìn vào đâu | Nhồi quá nhiều đạo cụ khác nhau | Giữ tối đa 2–3 vật phụ, hoặc một dãy vật lặp lại duy nhất, xóa vật không phục vụ ý chính |
| Shot phòng họp nhìn như icon, nền trống | Không có tiền cảnh cắt viền, không có hậu cảnh lặp lại | Điền ô bối cảnh 3 lớp: tiền cảnh cắt viền, hậu cảnh dãy màn hình hoặc tủ |
| Sơ đồ không cuốn, số bị chìm | Vật nhỏ, số nhỏ, nhiều chi tiết kỹ thuật | Vẽ vật lớn, chỉ 3-4 chi tiết trong, số punch màu đỏ và lớn nhất khung |
| Các shot rời rạc, không giống 1 video | Mỗi shot đổi phong cách, bảng màu, tỉ lệ, vai trò chính/phụ | Khóa Style Sheet và Character Sheet từ shot đầu, chỉ đổi ô Biến |
| Shot đẹp nhưng không kể được chuyện | Gen hình trước, nghĩ lời thoại sau | Quay lại mục 2: chốt Mục đích shot và Loại shot trước, rồi mới điền các ô còn lại |

---

## 10. CÁCH BIẾN HÓA SƯỜN NÀY CHO MỌI TÌNH HUỐNG — CHỈ 3 THAO TÁC

### Thao tác 1: Đổi chủ đề — thay Chủ thể + Bối cảnh + Đạo cụ

Giữ nguyên: phong cách, cỡ cảnh, ánh sáng, nhịp chuyển động, quy tắc chính/phụ.
Ví dụ: từ “chàng trai gõ máy tính trong phòng” → “cô gái vẽ bản thiết kế trong xưởng” — chỉ cần thay 3 ô đó, sườn còn lại giữ nguyên.

### Thao tác 2: Đổi cảm xúc — thay Biểu cảm + Hành động + Màu nền

Giữ nguyên: nhân vật, bối cảnh, góc máy, loại shot.
Ví dụ: từ hào hứng → thất vọng: đổi cười rộng thành cúi đầu, dang tay thành ôm đầu, nền sáng thành nền tối hơn một chút. Đám đông vẫn giữ mặt phẳng, chỉ nhân vật chính đổi biểu cảm.

### Thao tác 3: Đổi định dạng — thay Tỉ lệ + Cỡ cảnh + Thời lượng

Giữ nguyên: nhân vật, phong cách, ý chính.
Ví dụ: từ video dài 16:9 → video ngắn 9:16: đổi tỉ lệ, chuyển wide shot thành medium / close-up để nhân vật không bị nhỏ, rút thời lượng mỗi shot còn 3–5 giây. Lưu ý: đổi tỉ lệ là đổi ô khóa cho định dạng mới, phải test lại 4 loại shot ở tỉ lệ mới, không tự crop shot cũ.

> Tóm lại: **muốn biến hóa mà không vỡ video, mỗi lần chỉ đổi 1 nhóm ô.**
> Đổi cùng lúc chủ thể, bối cảnh, cảm xúc, góc máy và phong cách thì đó không còn là biến hóa nữa, mà là làm video mới.
> Và có 2 thứ không bao giờ được đổi lung tung trong một video: mặt và áo của nhân vật chính, và vai trò chính/phụ của các nhân vật.

---

## 11. MẪU COPY NHANH — DÁN VÀO CÔNG CỤ GEN

### Mẫu ảnh cảnh — copy và thay ô vuông

```text
[STYLE LOCKED], [CHARACTER chính], [CROWD nếu có],
[CHỦ THỂ] is [HÀNH ĐỘNG], [BIỂU CẢM],
[TIỀN CẢNH cắt viền], in [BỐI CẢNH, dãy vật lặp lại], [THỜI GIAN],
[CỠ CẢNH], [GÓC MÁY], [BỐ CỤC: mắt nhìn đâu trước, rồi nhìn đâu sau],
[ÁNH SÁNG], [MÀU SẮC / CẢM XÚC, màu nhấn đỏ là gì],
[2–3 CHI TIẾT PHỤ], [CHỮ: "TEXT" nếu có].
Sharp, clean lines, one clear focal point.
Negative: photorealistic, 3D, blurry, extra limbs, distorted hands, realistic hands,
detailed shirt with folds, crowd with the same skin tone as the main character,
changed face, changed outfit, unreadable text, garbled text, crowded background.
```

### Mẫu ảnh trích dẫn — copy và thay ô vuông

```text
[STYLE LOCKED], [CHARACTER chính], [CROWD nếu có],
[NGƯỜI NÓI] is [HÀNH ĐỘNG], [BIỂU CẢM KHỚP CÂU NÓI],
[TIỀN CẢNH cắt viền nếu có], in [BỐI CẢNH], [THỜI GIAN],
[CỠ CẢNH], [GÓC MÁY],
[LOẠI CHỮ: caption trên đầu hoặc bong bóng mây trắng, vị trí, "QUOTE" đúng chính tả].
Quote is the focal point, speaker's face is the second focal point.
Crowd keeps a flat worried expression, unchanged.
Sharp, clean lines, no clutter.
Negative: garbled text, misspelled text, small text, text covering the face,
realistic hands, crowded background.
```

### Mẫu ảnh sơ đồ — copy và thay ô vuông

```text
[STYLE LOCKED],
[LAYOUT: số dải, màu nền mỗi dải, đường ngăn, thứ tự đọc],
[ZONE 1: vật lớn, 3-4 chi tiết, nhãn], [ZONE 2: vật lớn, 3-4 chi tiết, nhãn],
[ZONE 3: con dấu hoặc số punch màu đỏ, lớn nhất khung].
No character, no room, no crowd, unless a character is the scale reference.
Sharp, clean lines, one idea, one reading order.
Negative: small objects, small numbers, technical detail, gradient, garbled text, crowded zones.
```

### Mẫu video — copy và thay ô vuông

```text
[STYLE LOCKED], [CHARACTER chính], [CROWD nếu có],
[CHỦ THỂ] [HÀNH ĐỘNG MỞ ĐẦU],
then [HÀNH ĐỘNG CHÍNH, chỉ 1 hành động], finally [TRẠNG THÁI KẾT, giữ yên].
Setting: [BỐI CẢNH 3 LỚP], [THỜI GIAN].
Camera: [CỠ CẢNH], [GÓC MÁY], [MÁY ĐỨNG YÊN, trừ khi ghi rõ lia máy cùng ý].
Lighting: [ÁNH SÁNG]. Mood: [CẢM XÚC].
[CHỮ: loại chữ + vị trí + "TEXT" đúng chính tả, nếu có].
Duration: [10s]. Aspect ratio: [16:9].
Smooth slow motion, stable final frame, one clear focal point.
Negative: shaky camera, morphing face, changing outfit, changing crowd skin tone,
extra limbs, distorted hands, realistic hands, flickering background,
unreadable text, garbled text.
```

---

## 12. CHUỖI 3 SHOT MẪU — CÙNG MỘT NHÂN VẬT, KHÔNG VỠ

Dùng cùng Style Lock, cùng Character Sheet chính và cùng Character Sheet quần chúng cho cả 3 shot.
Chỉ đổi cỡ cảnh và ý, không đổi mặt, áo, bảng màu, tỉ lệ, hay vai trò chính/phụ.

- Shot 1, wide shot, loại cảnh rộng: nhân vật đứng nhỏ bên cạnh một tòa nhà rất lớn, tiêu điểm là tương phản kích thước. Mắt nhìn vào tòa nhà trước, rồi mới nhìn xuống nhân vật. Tiền cảnh: mép dưới của khung là mặt đất phẳng, không có nhân vật phụ.
- Shot 2, medium shot, loại đám đông: cùng nhân vật, cùng áo, cùng tóc, đang làm việc ở bàn, phía sau là dãy màn hình lặp lại và 2 nhân viên mặt trắng. Tiêu điểm là hành động của nhân vật chính. Ánh sáng và bảng màu giữ nguyên shot 1.
- Shot 3, close-up, loại trích dẫn: cùng nhân vật, cùng áo, cùng tóc, cận mặt với biểu cảm rõ, trên đầu là chữ viết tay trắng viền đen, một câu ngắn. Tiêu điểm là chữ, thứ hai là mặt. Không có vật phụ mới chen vào.

Kiểm tra chuỗi bằng 5 câu: mặt có phải cùng một người không, áo và tóc có giống không, chính/phụ có giữ nguyên không,
chữ có đúng chính tả không, ánh sáng có lệch đột ngột không. Rớt 1 câu thì gen lại shot đó, không chữa bằng hiệu ứng dựng.

---

## 13. CHUỖI 4 SHOT MẪU — ĐỦ 4 LOẠI, ĐÚNG NHỊP CỦA CLIP THẬT

Chuỗi này là nhịp mẫu để test khuôn v2. Làm được chuỗi này thì mới gọi là đã khóa khuôn.

- Shot 1, cảnh rộng, 10 giây: người nhỏ bên tên lửa lớn, máy yên, nhân vật chớp mắt và ngước nhìn, khói nhẹ ở chân tên lửa. Ý: quy mô lớn đến mức nào.
- Shot 2, đám đông, 10 giây: phòng làm việc, nhân vật chính da đào ở giữa phải, bên trái là dãy nhân viên mặt trắng và màn hình lặp lại, bên phải tiền cảnh là một người lớn bị cắt viền. Nhân vật chính cười nhẹ, đám đông giữ mặt phẳng. Ý: ai là người dẫn dắt, ai là nền.
- Shot 3, trích dẫn, 10 giây: phòng họp, nhân vật chính phía sau, tiền cảnh là 2 nhân vật lớn bị cắt viền, bong bóng mây trắng hiện dần ở giữa với một câu ngắn đầy đủ. Đám đông giữ mặt lo, chữ giữ đủ lâu để đọc. Ý: câu nói punchline.
- Shot 4, sơ đồ, 10 giây: 2 dải, dải trên là vật + thời gian + con dấu, dải dưới là túi tiền + cột tăng + số đỏ lớn nhất khung. Đường biểu đồ đi lên chậm, rồi giữ nguyên sơ đồ để đọc số. Ý: số liệu chốt lại câu chuyện.

Nhịp nối: cắt cứng từ shot 1 sang shot 2, từ shot 2 sang shot 3, từ shot 3 sang shot 4. Không lia máy để chuyển ý.
Mỗi shot kết khung ổn định, shot trích dẫn và shot sơ đồ giữ chữ và số đủ lâu để đọc trên điện thoại.

---

## 14. BẰNG CHỨNG VÀ GIỚI HẠN — ĐỪNG DÙNG SƯỜN NÀY ĐỂ KHẲNG ĐỊNH THỨ CHƯA XÁC MINH

- Tham chiếu chính: Crayon Capital, “The ENTIRE History of Elon Musk”, 19:09, https://www.youtube.com/watch?v=npoPedEq3hs.
- Lần xem mẫu ngày 2026-11-03: phát được, không cần đăng nhập, lấy mẫu khung hình theo khoảng thời gian, không kiểm tra từng frame.
- Bằng chứng bổ sung ngày 2026-11-03: 4 ảnh mẫu do người dùng gửi, gồm 4 kỹ sư SpaceX, phòng họp có bong bóng suy nghĩ, văn phòng có chữ “and send it to Mars.”, và sơ đồ 2 dải Roadster / 2 YEARS / OVERDUE / $50 MILLION. Các quy tắc chính/phụ, trích dẫn, sơ đồ trong v2 lấy từ 4 ảnh này.
- Chưa xác minh, đừng khẳng định: pan, zoom, tracking trong một shot, chu trình đi bộ, vụ nổ tên lửa, chi tiết nhà máy, biểu đồ chi phí phóng, và mọi frame ngoài mẫu đã xem.
- Sponsor trong clip tham chiếu không thuộc khuôn này, không copy sponsor vào video của mình.
- Khi một AI khác đọc sườn này, AI đó vẫn phải tuân theo quy tắc đồng bộ ở mục “Vì sao bản v1 chưa giống” và mục 8: skill và sườn không đảm bảo 100% mặt giống hệt nhau qua mọi công cụ. Đồng bộ mạnh nhất vẫn cần 1 ảnh tham chiếu nhân vật duy nhất, dùng image-to-image hoặc image-to-video cho mọi shot sau, dùng cùng một công cụ suốt dự án, và chia vai: một bên gen keyframe, một bên chỉ animate từ keyframe đó.

---

> Tóm lại một câu để mang đi: **người que, chính da đào phụ mặt trắng, khung 3 lớp cắt viền,
> trích dẫn hiện dần, sơ đồ vật lớn số đỏ, cắt cứng, máy yên, chữ đúng chính tả, tỉ lệ 16:9 không crop.**
> Shot nào vi phạm một trong các cụm đó thì gen lại, dù nhìn riêng nó có đẹp đến đâu.
