# Kiến trúc "Smoothness Compiler" — đo chuẩn tham chiếu, chẩn đoán thiếu gì, sinh chuyển động cho mọi job

Trạng thái: **đề xuất, chờ duyệt**
Ngày: 2026-10-31
Bằng chứng đo: `analysis/byd/report.json` (video tham chiếu), `analysis/byd/report_ours_en.json` (bản render final của mình)

---

## 1. Nguyên tắc

1. **Spec tách khỏi implementation.** "Smooth" là tập số đo được, lưu trong một `reference_profile.json`. Pipeline chỉ đọc profile, không hard-code style của một kênh.
2. **Đo trước, sửa sau.** Mỗi thay đổi sản xuất phải chứng minh bằng metric trên bản render thật, so với profile.
3. **Không train model.** "Huấn luyện cỗ máy" ở đây = vòng hiệu chỉnh tham số + chọn lớp chuyển động dựa trên số đo và retention (khi có). Không cần GPU, không fine-tune video model.
4. **Không copy tài sản.** Chỉ lưu metric/đặc trưng của video tham chiếu. Không lưu hình/âm thanh gốc, không dùng lại nhạc của họ.
5. **Không phá luật hiện có.** QA smoothness là **cảnh báo + auto-fix**, không được chặt hơn QA scorecard hiện tại. Hard-fail chỉ khi hỏng kỹ thuật thật (fps sai, file lỗi, thiếu audio).

---

## 2. Bằng chứng: ta đang thiếu gì (đo cùng một bộ metric)

| Chỉ số | Tham chiếu (BYD, 16:40) | Của mình (272 s EN, 30/10) | Đọc ra |
|---|---|---|---|
| Nhịp cắt | 200 cảnh, TB **5,0 s**, median 4,45 | 38 cắt / 39 cảnh, TB **6,98 s**, p90 8,0 | Mình cắt thưa hơn ~40% |
| Camera dịch chuyển | median 9,9 px/s, phủ **72%** cảnh | median 16,1 px/s, phủ **100%** | Mình pan nhanh hơn nhưng *đơn điệu* (mọi cảnh đều dịch) |
| Zoom | median **1,43 %/s**, phủ 58% | median **0,01 %/s**, phủ 44% | Gần như không zoom — thiếu "thở" bằng zoom |
| Chuyển động cục bộ | **18%** cảnh residual ≥6 (chart tự mọc, draw-on, chuỗi pose) | **0%** | Thiếu hẳn lớp motion-graphics/limited-animation |
| Giữ tĩnh | 15% cảnh | 0% | Không có nhịp nghỉ → mỏi mắt |
| Nhạc nền | Liên tục, side−mid **−15,5 dB**, có swell | **Không có** (side−mid −191 dB, mono tuyệt đối) | Thiếu toàn bộ |
| Loudness | −18,0 LUFS, LRA 3,9 | −20,2 LUFS, LRA 2,3 | Nhỏ hơn 2 dB, ít động lực hơn |
| FPS | 30 | 24 | Judder khi pan chậm |

Kết luận: cái "smooth" của họ **không phải i2v**. Nó là 4 lớp rẻ: cắt 5 s đúng nhịp + camera move có kiểm soát + pose/motion-graphics + nhạc ducking liền mạch.

---

## 3. Kiến trúc tổng thể

```
[URL/File tham chiếu] ─► L0 Reference Intelligence ──► analysis/refs/<id>/report.json
                                    │
                                    ▼
                      L1 Smoothness Spec (profile + bands)
                                    │
[Nhạc đã license] ─► beat grid ─┐   │
[Script + word timings] ────────┴──► L2 Timeline Compiler ──► timeline.json
                                                             (cut points, lớp chuyển động/cảnh)
                                    │
                                    ▼
                      L3 Motion Library (camera / pose / graphics / transition)
                                    │
                                    ▼
                      L4 Renderer 30fps (supersample + motion blur)
                                    │
                                    ▼
                      L5 Audio Mixer (sidechain duck + SFX + loudness)
                                    │
                                    ▼
                      L6 Smoothness QA  ──► gap report + auto-fix ops
                                    │
                                    ▼
                      L7 Calibration loop ──► cập nhật profile/trọng số
```

### L0 — Reference Intelligence (thu thập data của người ta)
- CLI: `python -m tools.reference_bench ingest <url|file>`; tải tạm → đo → **xoá file gốc**, chỉ giữ report.
- Đo: cắt cảnh, phân rã chuyển động (camera affine/homography vs residual cục bộ), nhịp cắt, fps, nhạc nền (mid/side, ducking, swell), SFX transient, loudness/LRA, palette stability.
- Gộp N video → phân phối p10/p50/p90 cho từng metric. Đây là "dữ liệu huấn luyện" thật sự của spec.

### L1 — Smoothness Spec
- `reference_profile.json`: bands cho mọi metric + trọng số lớp chuyển động theo thể loại nội dung (tin/giải thích/kể chuyện).
- Không có profile hợp lệ → pipeline dùng profile mặc định đóng gói kèm.

### L2 — Timeline Compiler (thuật toán cốt lõi)
Input: câu thoại + word timing + beat grid của nhạc.
1. **Điểm cắt ứng viên** = ranh giới mệnh đề (từ script segmentation hiện có).
2. **DP** chọn tập cắt tối thiểu hoá:
   `cost = |shot_dur − target_dur|/target_dur + w1·|t_cut − beat(t_cut)| + w2·[shot<min] + w3·[shot>max]`
   với `target_dur` lấy p50 của profile, `beat()` ≤ 100 ms là snap được.
3. **Gán lớp chuyển động** mỗi cảnh từ `{camera, pose_seq, graphic, draw_on, hold, insert}` theo trọng số profile + ràng buộc: không lặp lớp >2 cảnh liền; `graphic` chỉ khi cảnh có số/so sánh; `draw_on` khi mô tả cơ chế; `hold` ≤15% và ≤2 s.
4. **Đường cong năng lượng**: luân phiên cao/thấp để tránh đơn điệu; mục tiêu autocorrelation âm giữa các cảnh liền.
Output: `timeline.json` (cut points, lớp, tham số magnitude cho từng cảnh).

### L3 — Motion Library
- `camera`: pan/zoom eased (smoothstep), biên độ sample từ phân phối profile; ràng buộc gia tốc; đảm bảo phủ zoom ≥ band.
- `pose_seq`: 2–4 pose cùng nhân vật, xfade 0,15–0,3 s, **canh theo beat**.
- `graphic`: chart bar/line tự mọc, counter đếm số, highlight khoanh vùng — renderer bằng PIL/ffmpeg overlay, dữ liệu lấy từ script.
- `draw_on`: nét vẽ hiện dần (mask reveal) cho cảnh "cách hoạt động".
- `transition`: cắt cứng mặc định; fade 0,15 s chỉ ở chuyển chương.

### L4 — Renderer 30fps
- Chuyển `KENBURNS_FPS` 24 → 30, assembler 30.
- **Supersample**: zoompan ở canvas 2× rồi hạ về output để giảm judder do lượng tử x/y.
- **Motion blur** nhẹ (tblend/tmix có trọng số) khi pan nhanh.
- Luật: pan chậm (<30 px/s) phải có blur hoặc chuyển sang zoom, tránh "sub-pixel crawl".

### L5 — Audio Mixer
- Ducking **sidechain** theo envelope giọng (attack 80 ms, release 400 ms), sâu −15…−20 dB — thay cho volume tĩnh hiện tại.
- Swell +6 dB ở khoảng lặng >0,4 s; SFX đặt trên beat.
- Loudness: −16 ±1 LUFS, true peak ≤ −1 dBTP, LRA 3–5.

### L6 — Smoothness QA
- Chạy đúng bộ metric L0 trên bản final; xuất `smoothness_report.json` vào thư mục job.
- Vi phạm **mềm** (nhịp cắt, phủ zoom, thiếu nhạc, LRA) → warning trong scorecard + đề xuất auto-fix.
- Vi phạm **cứng** (fps ≠ 30, file hỏng, audio clip) → chặn render.
- Không đụng luật độ dài: độ dài vẫn do script/audio quyết định, band chỉ cảnh báo.

### L7 — Calibration loop
- Mỗi job lưu metric + lựa chọn lớp chuyển động.
- Khi có retention/view-duration từ YouTube: cập nhật trọng số lớp bằng bandit đơn giản (Thompson sampling trên 6 lớp, thưởng = retention tương đối).
- Không có retention → dùng QA scorecard + khoảng cách tới profile làm tín hiệu.

---

## 4. Ngưỡng mục tiêu — đo từ corpus 9 clip ≥1M view (Crayon Capital, 31/10)

Band = p10–p90 giữa các **clip** (không phải giữa các frame); p50 là tâm. Số liệu trong `analysis/refs_viral/reference_profile.json`.

| Metric | Band (p10–p90) | p50 | Loại |
|---|---|---|---|
| cuts/min | 9,2–13,7 | 10,9 | mềm |
| shot duration median | 3,7–5,5 s | 4,6 s | mềm |
| shot duration p90 | 7,5–13,0 s | 9,9 s | mềm |
| share camera trans >2 px/s | 0,27–0,77 | 0,70 | mềm |
| share zoom >0,5 %/s | 0,16–0,60 | 0,48 | mềm |
| zoom tốc độ khi có move | 0,01–2,1 %/s | 0,41 %/s | mềm |
| share strong local motion (residual≥6) | 0,08–0,22 | 0,13 | mềm |
| share static hold | 0,10–0,39 | 0,15 | mềm |
| fps | 30 hằng định | 30 | cứng |
| music bed active | 1,0 (9/9 clip) | — | mềm |
| side−mid khi voice lặng | −6,7…−4,1 dB | −5,3 dB | mềm |
| side−mid trung bình | −20,4…−15,9 dB | −17,2 dB | mềm |
| integrated loudness | −18,4…−17,1 LUFS | −17,8 | mềm |
| LRA | 3,5–4,3 LU | 3,7 | mềm |

Ghi chú khi dùng band:

- Band cũ (12,8–16,1 cuts/min) lấy từ 5 clip **dưới 1M view** nên lệch nhanh; clip viral cắt chậm hơn (median 10,9). Top 2 view cao nhất (4,3M và 3,0M) là 2 clip cắt chậm nhất (9,3 và 9,7 cuts/min) — n=9 nên chỉ xem là gợi ý, không phải luật.
- Độ "động" thay đổi rất mạnh giữa các tập (static share 0,03–0,90; zoom share 0,05–0,61). Vì vậy band chuyển động là **mục tiêu cấp tập phim** do bộ chọn lớp chuyển động phân bổ, không bắt mọi cảnh phải chạy.
- Bản render hiện tại của mình: 8,6 cuts/min, shot 6,98 s, zoom 0,44, strong-local 0,00, static 0,00, không có nhạc nền, mono, −20,2 LUFS, 24 fps.
- Kênh khác (POVrank, Cost Anatomy, Build History) nằm ở `analysis/refs_other/`, **không** trộn vào band Crayon.

---

## 5. Lộ trình + test checklist

### Phase 0 — Benchmark harness (6–8 h)
- [x] `tools/reference_bench.py`: ingest URL/file, tạo `analysis/refs/<id>/report.json`, xoá file gốc sau khi đo.
- [x] `tools/profile_build.py`: gộp ≥5 ref → `reference_profile.json` (p10/p50/p90).
- [x] Test: chạy 2 lần cùng file cho cùng số cắt; ingest 5 video cho profile có đủ phân phối.
- [x] Corpus viral: 9/9 clip ≥1M view của kênh Crayon Capital đo xong → `analysis/refs_viral/` + profile riêng; toàn kênh (14 clip) ở `analysis/refs/`; kênh khác tách ở `analysis/refs_other/`.

### Phase 1 — Smoothness QA vào scorecard (4–6 h)
- [ ] `core/smoothness/qa.py` đọc metric final, so band, ghi report vào job dir.
- [ ] Nối warning vào QA scorecard hiện có; hard-fail chỉ khi hỏng kỹ thuật.
- [ ] Test: render thiếu nhạc → warning đúng; file hỏng → fail đúng; job cũ vẫn pass.

### Phase 2 — Timeline Compiler (10–14 h)
- [ ] `pipeline/timeline_compiler.py`: DP chọn cắt + snap beat + gán lớp chuyển động.
- [ ] Test: audio tổng hợp 120 BPM → ≥60% cut lệch ≤100 ms; shot duration trong band; không cảnh <2 s.

### Phase 3 — Renderer 30fps + camera scheduler (8–12 h)
- [ ] Đổi fps pipeline lên 30; supersample; motion blur khi pan nhanh.
- [ ] Scheduler chọn hướng/biên độ move từ profile, chống lặp.
- [ ] Test: `ffprobe` = 30/1; frame trùng <2%; share zoom ≥0,50; không judder ở pan chậm.

### Phase 4 — Motion graphics + pose (16–24 h)
- [ ] `pipeline/motion/graphics.py`: chart bar/line, counter, draw-on reveal.
- [ ] Gắn vào timeline theo lớp; dữ liệu số lấy từ script.
- [ ] Test: cảnh có số → render ra chart đúng số; share strong local ≥0,12; visual check 3 cảnh.

### Phase 5 — Audio (8–10 h)
- [x] Gói nhạc nền hợp pháp 28 track CC BY 4.0 trong `assets/music/bgm/` (4 track/mood, tên file khớp `BGMManager._find_bgm_file`; giấy phép + credit ở `CREDITS.txt`, manifest ở `MANIFEST.json`) — lấy từ catalog Incompetech bằng `tools.bgm_pack.py`.
- [x] Catalog nhạc tham chiếu: 100 đoạn bed từ 13 clip + 35 preview 15 s gắn tag vai trò (`analysis/music_beds/`, mở `listen.html`) bằng `tools/music_catalog.py`.
- [ ] Ducking sidechain + swell + SFX trên beat; loudness normalize −16 LUFS.
- [ ] Test: side−mid trong band; LUFS −16±1; không clip (true peak ≤ −1 dBTP).

### Phase 6 — Calibration (6 h)
- [ ] Log metric theo job; báo cáo tuần; hook bandit khi có retention.
- [ ] Test: 1 job e2e đạt ≥4/6 band mềm mà không cần sửa tay.

**Tổng: ~58–74 h**, làm tuần tự; Phase 0–1 độc lập, có thể chạy trước mà không đổi sản phẩm.

---

## 6. Không nằm trong phạm vi

- Mua/tích hợp video model i2v (Kling/Hailuo/Seedance) — dữ liệu cho thấy không cần.
- Train/fine-tune model thị giác.
- Copy nhạc, hình, nhân vật của kênh tham chiếu.
- Thay đổi luật độ dài video hay QA scorecard hiện có.
