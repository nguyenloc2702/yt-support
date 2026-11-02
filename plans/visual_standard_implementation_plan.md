# Plan triển khai chuẩn hình ảnh & dựng hình ("Crayon standard")

Nguồn chuẩn: `plans/visual_standard.md` (đo từ 17 clip tham chiếu, 2026-10-31).
Plan này chỉ ra **sửa file nào, dòng nào, kiểm gì** để pipeline tự đạt chuẩn.

## Luật khung (không được phá)

1. **Bands là cảnh báo, không phải gate cứng** — QA scorecard (`services/algorithm_qa.py`) vẫn là nguồn chân lý; không tạo gate nào chặt hơn scorecard.
2. **Thời lượng chạy theo script/audio** — không được cắt cảnh chỉ để khớp số đo tham chiếu.
3. **Zero-fake policy** (`services/image_kenburns_provider.py:19-22`) — thẻ đen phải là **kiểu chữ có chủ đích**, không phải ảnh placeholder; phải cập nhật chú thích + QA `c_no_placeholder` khi thêm.
4. **Chỉ bản EN** — bản VI đang parked.
5. **Mọi render xác minh phải ra từ product** (`/remake` + gate), không dựng tay.

Trạng thái hiện tại (đo trên 2 render thật): 8.37 / 7.42 cảnh/phút · shot median 6.9 s · pan 17–19 px/s · zoom 0.01 %/s · static 0% · strong-local 0% · thẻ đen 0 · caption 0 · 24 fps · mono (bản đo trước khi vá bed).

---

## P0 — Thước đo chuẩn (làm trước, 1 buổi)

Không có thước thì mọi thay đổi sau không chứng minh được.

**Việc**
- `tools/standard_report.py` (mới): input = 1 file mp4 + job dir → chạy `core/smoothness/metrics.py` (đã có) + `blackdetect` (copy logic `analysis/refs_sample/collect.py:74-88`) + kiểm caption (detection vùng chữ: so sánh frame có/không lớp chữ ở 25% dưới khung hình) → xuất `standard_report.json` so với target trong `plans/visual_standard.md`, mỗi dòng `pass | warn | miss`.
- Nối vào `core/worker.py` sau bước assemble (gần `_assemble_and_finalize_documentary` `core/worker.py:395`) để **mọi job tự in report**; chỉ ghi file + log, **không chặn gate**.
- Đưa bảng report vào `Scorecard.format_telegram_report()` (`services/algorithm_qa.py:89`) dạng khối riêng "Chuẩn tham chiếu".

**Test checklist**
- [x] `tests/test_standard_report.py`: fixture mp4 tổng hợp (3 cảnh, 1 thẻ đen 1 s, có/không caption) → đúng từng dòng pass/miss. (14 test, cộng test chống drift `spec_targets()`)
- [x] Chạy trên 2 file thật đã đo: `documentary_final_remake_1793294121_e7c43a_en.mp4` và `documentary_final_en_v3_remake_1793122984_299596.mp4` → ra đúng số đã biết (8.37/7.42 cpm, 0 thẻ đen, 0 caption).
- [x] Chạy trên 1 clip tham chiếu đã cache (`analysis/refs_sample/proxies/ggFpIjvXQi4.mp4`) → 26 thẻ đen, 13.08 cpm (khớp `analysis/refs_sample/black_cards.json`).
- [x] Không có check nào trong `ALL_CHECKS` thay đổi điểm số (83 test QA/gate/approval + 30 test auditor nguyên trạng).

---

## P1 — Caption cháy sẵn (tác động lớn nhất)

Mẫu: 17/17 clip có chữ trên hầu hết khung hình. Mình: 0.

**Việc**
- `pipeline/subtitles.py`: `build_karaoke_ass` (đang hardcode 1280x720, font 40, margin_v 40) → tham số hoá theo 1920x1080 (font ≈ 62, margin_v ≈ 150) và thêm style keyword-highlight đúng luật chuẩn.
- Nguồn timing đã có sẵn: `narration_words_<lang>.json` (`pipeline/automated_factory.py:1482-1495`, key `scenes[].words[].{w,start,end}`) — viết `build_ass_from_word_timeline()` thay vì đi qua `items[].output_start/tempo` của luồng TTS cũ.
- `pipeline/documentary_assembler.py`: thêm tham số `subtitle_path=None` cho `assemble_documentary` (`:83-89`); khi có, thêm `-vf subtitles=...` vào lệnh render cuối (`:585-615`) — encoder đã là libx264 nên không đổi codec.
- `core/worker.py`: truyền `subtitle_path` xuống khi assemble (bản EN trước).
- Miễn caption cho: cảnh thẻ đen (P2) và cảnh screenshot full-bleed (nếu có).

**Test checklist**
- [x] `tests/test_subtitles.py`: case build từ word timeline thật → cue ≤7 từ, timestamp tăng dần, không overlap, nghỉ 0.45 s sang cue mới, nghỉ ≤0.6 s giữ chữ, số liệu được highlight. (11 test)
- [x] `tests/test_integration_documentary.py`: render 2 cảnh + 1 ass → ffprobe đúng 1 video + 1 audio (không stream phụ), Δduration ≤0.1 s, ảnh diff: band dưới có >5000 px chữ, band trên 0 px.
- [x] Không còn caption nào tràn lề: cue 12 từ render ra ink cols 319–1603 trong margin 90 (trước đó libass kéo một "từ" 110 ký tự tràn 0–1919 vì WrapStyle 2 không tự xuống dòng → `_karaoke_text` giờ chia dòng `\N` theo ngân sách ký tự).
- [x] Đo lại bằng P0: coverage 6/6 job thật, caption_share 0.862–0.928 (target ≥0.85); scene coverage 100% số cảnh có lời. Render thật `remake_1793122984_299596` (40.5 s): **Phủ caption 0.856 OK** (trước: MISS 0).
- [x] Không tăng duration: caption_poc vs plain_poc trên cùng job = 40.50 s vs 40.50 s (Δ 0.000 s).
- [x] `core/worker.py::_build_caption_file` có test riêng (có timeline → ASS, không có → None).
- Ghi chú thật: burn thất bại không giết render (assembler dựng lại không chữ) và `pipeline/documentary_assembler.py` ghi `<video>.captions.json` để `tools/standard_report.py` chỉ tính caption khi đúng file đó đã burn — file ASS sót lại trong job dir không còn được tính nhầm.

---

## P2 — Nhịp cắt + thẻ đen

**Việc**
- Nhịp cắt: `pipeline/word_timing.py:24-27` — `MIN_PHYSICAL_DUR 3.0` / `MAX_PHYSICAL_DUR 10.5` là **ngưỡng hard-fail Gate 4** (ghi chú `:22-23`), còn `BAND_MIN_DUR 4.0` / `BAND_MAX_DUR 8.0` mới là band advisory. Chỉ chỉnh **band** (4.0–8.0 → 3.5–6.0) để kéo median về ~4.3 s; `MIN/MAX_PHYSICAL_DUR` giữ nguyên trừ khi đo cho thấy nó là chốt chặn thật sự — không tạo gate chặt hơn scorecard.
- Cho phép **tách cảnh trong cùng một câu** khi câu vượt band: `cut_policy="mid_pause"` hiện chỉ cắt ở nghỉ giữa cảnh → thêm policy `mid_clause` cắt tại dấu phẩy/nhịp nghỉ ngắn nhất trong câu, vẫn giữ min `MIN_PHYSICAL_DUR`.
- Thẻ đen: module mới `pipeline/title_cards.py` — `build_card(text, duration, out_path)` render bằng ffmpeg `color=black` + `drawtext` (repo chưa dùng drawtext ở đâu), font Segoe/DejaVu, căn giữa, fade 0.15 s.
- Chèn thẻ đen trong `documentary_assembler`: chọn vị trí theo luật "1.4 thẻ/phút, ưu tiên cảnh chốt ý / số lớn" — nguồn tín hiệu đã có: `scripts/apply_video_algorithm.py:114,665-666` `is_punchline` và `pipeline/sfx_manager.py:126` `punchline → dramatic_silence`.
- Cập nhật `services/image_kenburns_provider.py:19-22` (chú thích zero-fake) + `services/algorithm_qa.py` `c_no_placeholder` (`:745`) để thẻ đen chủ đích **không** bị tính là placeholder.

**Test checklist**
- [ ] `tests/test_word_timing.py`: câu 12 s → policy mới cắt thành ≥2 window, mỗi window ≥3.0 s, tổng không đổi ±0.05 s.
- [ ] `tests/test_word_timing.py`: band mới chỉ sinh warning, không sinh error.
- [ ] `tests/test_title_cards.py`: thẻ 1.2 s → `blackdetect` bắt được 1 đoạn 1.0–1.4 s, có chữ (SSIM vùng giữa khác nền đen thuần).
- [ ] `tests/test_qa_hard_gate.py`: job có 3 thẻ đen vẫn pass `c_no_placeholder`.
- [ ] Đo lại render thật: 10–14 cảnh/phút, shot median 4.0–5.0 s, thẻ đen 1.0–1.8/phút.

---

## P3 — 30 fps + chuyển động đúng chuẩn

**Việc**
- FPS: `services/image_kenburns_provider.py:65` `KENBURNS_FPS = 24` → 30; `documentary_assembler.py:150` (`fps=24` trong normalize `-vf`) và `:597-599` (`-r 24`) → 30.
- Pan/zoom: `services/image_kenburns_provider.py:133-187` `_zoompan_filter` — pan đang giữ `z=1.22` và chạy hết khung → giảm hành trình pan để median ≤10 px/s; `zoom_in`/`zoom_out` giữ nguyên biên nhưng cấp cho **~50% số cảnh** (hiện `KENBURNS_MOTIONS:75-91` = 3 zoom / 4 pan nên median zoom ≈ 0).
- Static hold: thêm preset `hold` (z cố định, crop cố định) vào registry, cấp ~15% số cảnh — hiện không có lựa chọn "đứng yên" nào.
- Ease: smoothstep `:147` giữ nguyên.
- Local motion (≥40% cảnh có động nội khung): dựa trên pose blend đã có (`POSES_PER_SHOT=3`, `_pose_graph:243-291`) — bắt buộc bật cho cảnh có nhân vật; đo `share_local` bằng P0 để biết mức đạt được. **Đây là phần dễ trượt đích nhất — tách riêng, không chặn các mục trên.**

**Test checklist**
- [ ] `tests/test_kenburns_provider.py`: `build_kenburns_filter` sinh đúng 30 fps, có preset `hold`, tỉ lệ zoom:pan:hold ≈ 5:3:1 theo seed.
- [ ] `tests/test_integration_documentary.py`: ffprobe final = 30 fps, không tụt khung (frame count = duration × 30 ±2).
- [ ] Đo lại render thật: pan ≤10 px/s, zoom median 0.4–1.2 %/s, static 10–20%.
- [ ] Thời gian render/job không tăng quá 25% (30 fps + zoompan đắt hơn).

---

## P4 — Âm thanh: chốt bằng file thật

Bed stereo −38 LUFS đã vá xong (`pipeline/bgm_manager.py:116-119,410-415`, `documentary_assembler.py:518-521`) nhưng **số đo hiện tại là của bản render cũ**.

**Việc**
- Thêm loudness pass vào final mux (`documentary_assembler.py:585-615`): `loudnorm=I=-18:LRA=4:TP=-1.5` (hai pass hoặc single-pass + đo lại bằng ebur128), vì Mode-2 hiện **không có** loudnorm nào — chỉ có limiter chống clipping.
- Chown owner chọn track theo role (`BEAT_ROLE_POSITION`, `pipeline/bgm_manager.py:100-106`) — hiện rotation tự động theo `rotation_seed`.

**Test checklist**
- [ ] `tests/test_algorithm_qa.py` / QA `c_video_lufs` (`services/algorithm_qa.py:733-741`, band −20…−12): render mới ra −18 ±0.5 LUFS.
- [ ] ebur128 tay: integrated −18.0…−17.5, LRA 3.5–4.5, TP ≤ −1 dBFS.
- [ ] `side_minus_mid_mean_db` trong −21…−15 (mono hiện −181 → phải hết).
- [ ] Không pump: đo bed 3 s trước/sau điểm vào nhạc, lệch ≤1 dB.
- [ ] 10 s lặng cuối còn nguyên.
- [ ] Cần **một lần /remake EN thật** (bấm gate của owner) để đóng mục này.

---

## P5 — Art direction (ảnh gen)

**Việc**
- `services/prompt_sanitizer.py:115-124` `STYLE_PREFIX`: thêm điều khoản **cấm mọi chữ/số/logo trong ảnh** (hiện "No text." chỉ nằm ở đuôi prompt video/still trong `sanitize_veo_prompt:201,209,251,264`), và điều khoản "one main subject, keep 35–45% empty space in the lower third" để chỗ cho caption.
- Character consistency: cơ chế đã đủ (`pipeline/character_manager.py:244-257`, worker fail-closed `core/worker.py:236-240`) → việc còn lại là **bắt buộc mọi cảnh có nhân vật đều trỏ reference**, không cho fallback ảnh mới.
- Cảnh số liệu/biểu đồ: không gen bằng ảnh — thêm loại shot `data_graph` render bằng matplotlib/PIL layer tĩnh rồi cho Ken Burns chạy (hoặc để trắng cho tới P6).

**Test checklist**
- [ ] `tests/test_prompt_sanitizer.py`: mọi prompt ra đều chứa clause cấm chữ; prompt có chữ "chart showing 44,000" bị sạch số.
- [ ] `tests/test_style_checker.py` + QA S13 (`services/algorithm_qa.py:329`) vẫn pass.
- [ ] 39 ảnh của job e7c43a render lại bằng prompt mới → chấm tay 10 ảnh: 0 ảnh có chữ rác (đối chứng `shot_018`, `shot_035`).
- [ ] Không cảnh nào có nhân vật mà thiếu `reference_images`.

---

## P6 — Việc treo (không nằm trong 5 phase trên)

- Bản **VI** đang parked — bật lại sau khi EN đạt chuẩn.
- `share_local` ≥40% nếu P3 không đạt: cần lớp hoạt hoạ prop (không phải i2v; Veo đã chết).
- 2 defect TTS/Telegram cũ chưa sửa (ghi ở `project-analysis-state-2026-10-22`).
- Revision guard không nêu điều khoản nào fail (`project-revision-guard-blind-spot`).

## Thứ tự chạy

P0 → P1 → P4 (cần 1 render thật, gộp đo chung) → P2 → P3 → P5 → P6.
Mỗi phase kết thúc bằng: chạy `tools/standard_report.py` trên render mới + báo **một bảng** so với target, rồi mới sang phase kế.
