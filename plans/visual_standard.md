# Chuẩn hình ảnh & dựng hình chung ("Crayon standard")

Một chuẩn duy nhất áp cho **mọi job** của pipeline: ảnh sinh ra và video dựng ra đều phải đạt các mục tiêu dưới đây. Đây là **target sản xuất**, không phải gate QA mới — scorecard QA vẫn là nguồn chân lý khi chấm điểm.

Bằng chứng: 17 clip tham chiếu (14 Crayon Capital + 3 kênh khác) đo bằng `tools.reference_bench`; thẻ đen và caption đo/đếm trên toàn bộ 17 clip (`analysis/refs_sample/black_cards.json`, `caption_sheet.jpg`); bản của mình: `analysis/ours/ours_e7c43a_en/report.json` + `analysis/refs/ours_299596_en_v3/report.json`.

## 1. Chuẩn ảnh (art direction)

| # | Luật | Mục tiêu |
|---|---|---|
| A1 | Một phong cách cho cả video | vector phẳng / cartoon 2D, nét đều, 3–4 màu chủ đạo; không trộn ảnh 3D bóng với vector phẳng trong cùng video |
| A2 | Nhất quán nhân vật | mỗi nhân vật có 1 reference sheet dùng lại cho mọi cảnh (mặt, tóc, áo, tuổi) |
| A3 | Mỗi khung 1 chủ thể | 1 chủ thể chính + tối đa 2 phụ; chừa 35–45% khoảng trống cho caption |
| A4 | Cấm chữ trong ảnh gen | mọi chữ/nhãn/số do lớp typography đặt; không để AI vẽ chữ, bảng số, logo chữ |
| A5 | Dữ liệu = motion graphic | biểu đồ/bảng/số liệu dựng bằng layer đồ hoạ từ số thật, không gen bằng ảnh |
| A6 | Được phép ảnh chụp thật | screenshot/tài liệu thật được dùng full-bleed cho cảnh bằng chứng (mẫu dùng nhiều) |

## 2. Chuẩn dựng (timeline & chuyển động)

Mục tiêu lấy trung vị/p10–p90 của 14 clip Crayon:

| # | Luật | Mục tiêu | Crayon-14: median (p10–p90) |
|---|---|---|---|
| B1 | FPS | **30 fps**, 1080p | 30,0 (17/17 clip đều 30) |
| B2 | Nhịp cắt | **~12,5 cảnh/phút** | 12,48 (9,05–16,20) |
| B3 | Độ dài cảnh | median **4,1 s**, p90 ≤ 10 s | 4,13 (3,10–5,49) |
| B4 | Pan | median **~10 px/s**, trần sản xuất **14,2 px/s** | 10,11 (0,23–18,24) — trần 14,2 là luật mình đặt, chặt hơn p90 đo được |
| B5 | Zoom | median **~0,9 %/s**, ease in/out | 0,92 (0,01–3,20); share cảnh có zoom 0,53 (0,12–0,71) |
| B6 | Hỗn hợp chuyển động mỗi tập | ~14% cảnh đứng yên, ≥10% chuyển động nội khung mạnh, ≤60% chỉ camera | static 0,14 (0,04–0,58); strong-local 0,13 (0,05–0,22); rigid 0,38 (0,28–0,78) — mốc 0,26/0,60 chặt hơn p90 vì ảnh chụp full-bley đẩy static/rigid lên cao |
| B7 | Thẻ đen chốt ý | **~1,3 thẻ/phút**, mỗi thẻ ≥ 0,4 s, tổng ~3% thời lượng | 21,5 thẻ/clip (7–45), 1,28/phút (0,51–2,20), 2,9% (p90 11,1%) |
| B8 | Caption | cháy sẵn trên **mọi cảnh minh hoạ**, đồng bộ từng từ, highlight từ khoá; cảnh screenshot/thẻ đen được miễn | có ở 17/17 clip, phủ gần hết thời lượng |

## 3. Chuẩn âm thanh

| # | Luật | Mục tiêu | Crayon-14: median (p10–p90) |
|---|---|---|---|
| C1 | Loudness | **−18 LUFS** integrated, LRA 3,45–4,95, true peak ≤ −1 dBFS | −18,05 (−21,20…−17,05); LRA 3,80 (3,45–4,95); TP −0,0 (−4,05…1,10) — trần −1 dBFS là luật của mình, mẫu gốc gần như đều chạm 0 |
| C2 | Nhạc nền | bed **stereo liên tục** suốt video, thấp hơn giọng ~18 dB (−38 LUFS đã level sẵn), side−mid −4…−6 dB khi giọng nghỉ | 14/14 clip Crayon đạt, median −5,15 dB (−7,70…−3,80); trong 3 kênh khác chỉ 1 có bed mờ (−14,7), 2 kênh gần như không có side (−91…−168 dB) |
| C3 | Nghỉ cuối | 10 s im lặng cuối video, không pump quanh chỗ nhạc vào/ra | — |

## 4. Mình đang ở đâu

| Hạng mục | Bản EN 4:32 (e7c43a) | Bản EN 40 s (299596 v3) | Chuẩn |
|---|---|---|---|
| FPS | 24 | 24 | 30 |
| Cắt/phút | 8.37 | 7.42 | ~12,5 |
| Shot median | 6.88 s | 6.96 s | ~4,1 s |
| Pan px/s | 17.2 | 19.0 | ~10, trần 14,2 |
| Zoom %/s | 0.01 | 0.01 | ~0,9 |
| Static hold | 0% | 0% | ~14% |
| Strong local | 0% | 0% | ≥10% |
| Rigid | 69% | 17% | ~38%, trần 60% |
| Thẻ đen | 0 | 0 | ~1,3/phút |
| Caption | 0 | 0 | mọi cảnh minh hoạ |
| LUFS | −20.2 | −19.8 | −18 |
| LRA | 2.3 | 2.8 | 3,45–4,95 |
| Bed khi giọng nghỉ | −145 dB | −118 dB | −4…−6 dB |
| Stereo | mono | mono | bed stereo |

## 5. Thứ tự lấp (theo tác động/tuần)

1. **Caption cháy sẵn** — code có, assembler chưa truyền; lấp khoảng cách lớn nhất.
2. **Nhịp cắt 8.4 → 12,5/phút**, cắt đúng chỗ lời nghỉ; shot median 6.9 → 4,1 s.
3. **Thẻ đen ~1,3/phút** tại chỗ chốt ý/số lớn.
4. **30 fps** + trần pan 14,2 px/s + zoom có thật (0,9 %/s) thay cho 0.01.
5. **Chuyển động nội khung** (prop/nhân vật động) để ≥10% cảnh có residual mạnh và ~14% cảnh đứng yên — cần layer hoạt hoạ trên asset vector.
6. **Nhất quán phong cách + cấm chữ trong ảnh** ở prompt/art director.

Việc 1–4 là dựng hình, không cần model mới. Việc 5–6 là art direction.

## 6. Machine targets

`core/smoothness/standard.py` đọc đúng danh sách này để chấm một bản render
(`python -m tools.standard_report final.mp4`). Số nào đi kèm `measured` là
p10–p90 đo trên 14 clip Crayon; số nào đi kèm `cap` là trần/sàn sản xuất mình
đặt, chặt hơn dải đo được, kèm lý do. Bảng này và code phải khớp nhau —
`tests/test_standard_report.py::test_machine_targets_match_the_written_spec`
sẽ fail khi một bên sửa.

```targets
fps                   >=30        measured  17/17 clip tham chiếu đều 30 fps
cuts_per_min          9.05..16.2  measured  p10–p90 Crayon, median 12,48
shot_median           3.1..5.49   measured  p10–p90, median 4,13 s
pan_px_s              <=14.2      cap       median đo 10,1 và p90 tới 18,2; hạ trần vì ảnh mình pan nhanh hơn hẳn mẫu
zoom_pct_s            0.01..3.2   measured  p10–p90, median 0,92
zoom_share            >=0.12      measured  share cảnh có zoom, p10 0,12 median 0,53
static_share          0.06..0.26  cap       p90 đo 0,58 nhờ clip nhiều screenshot; 0,26 là trần cho video minh hoạ
local_share           >=0.05      measured  share cảnh residual ≥6, p10 0,05 median 0,13
rigid_share           <=0.6       cap       median đo 0,38 p90 0,78; 0,6 để không chỉ có camera chạy
black_per_min         0.51..2.2   measured  p10–p90, median 1,28
caption_share         >=0.85      cap       17/17 clip caption phủ gần hết; 0,85 trừ cảnh screenshot/thẻ đen
integrated_lufs       -21.2..-17.05 measured p10–p90, median −18,05
lra_lu                3.45..4.95  measured  p10–p90, median 3,80
bed_side_quiet_db     >=-20       cap       median mẫu −5,15 dB; −20 chỉ là sàn "có bed stereo thật"
```

Năm bound là `cap` (pan, static, rigid, caption, bed); chín bound còn lại lấy
thẳng từ phép đo trên `analysis/refs*/` + `analysis/refs_sample/black_cards.json`,
không chỉnh theo ý muốn.

