# PLAN CODE CHI TIẾT CHO AGENT

## Mục tiêu sản phẩm

Thay quy trình hiện tại:

`AI tự viết toàn bộ script → người dùng sửa một bài dài → TTS → render`

bằng quy trình:

`Video + audio gốc → phân tích theo timecode → người dùng kiểm tra/chỉnh từng block → AI đề xuất script từng block → người dùng duyệt → upload audio riêng → kiểm tra đồng bộ → render`

Nguyên tắc bắt buộc:

1. Không được để AI tự tạo script cuối cùng mà không có bước con người duyệt.
2. Mọi nội dung phải gắn với `block_id` và khoảng thời gian.
3. Timecode lưu bằng integer milliseconds trong JSON; không dùng float làm nguồn dữ liệu chính.
4. Không được tự động chồng hai đoạn audio.
5. Không được tự động cắt nội dung lời nói mà không báo và được người dùng xác nhận.
6. Mỗi phase phải viết test và chạy test ngay trước khi chuyển phase tiếp theo.

---

# PHASE 0 — Khóa baseline trước khi sửa

## Việc cần làm

Không sửa code trước. Ghi nhận baseline hiện tại:

- Chạy `python -X utf8 -m pytest tests/ -q`.
- Ghi số test pass/fail.
- Chụp hoặc lưu smoke test các trang hiện tại.
- Ghi project test, video test, duration, fps, resolution.
- Ghi các file timeline/script/audio hiện tại để làm fixture.

## Test bắt buộc

Tạo test snapshot hoặc fixture tại [`tests/fixtures/analysis_sample/`](tests/fixtures/analysis_sample/) gồm:

- Video ngắn 10–20 giây.
- Audio gốc.
- Transcript mẫu.
- Expected JSON timecode.

Không dùng video production thật cho unit test vì test sẽ chậm và không ổn định.

## Điều kiện qua phase

- Baseline test hiện tại pass.
- Có fixture cố định.
- Có thể chạy test mà không cần mạng.

---

# PHASE 1 — Schema phân tích và JSON contract

## File mới

Tạo [`core/analysis_schemas.py`](core/analysis_schemas.py).

Định nghĩa các model Pydantic:

- `MediaInfo`
- `TimeRangeMs`
- `ShotAnalysis`
- `Speaker`
- `TranscriptWord`
- `TranscriptSegment`
- `AudioEvent`
- `ContentBlock`
- `AudioDelivery`
- `AnalysisDocument`

## Quy tắc schema

Các trường timecode chính:

```python
start_ms: int
end_ms: int
duration_ms: int
```

Validation:

- `start_ms >= 0`
- `end_ms > start_ms`
- `duration_ms == end_ms - start_ms`
- Các block cùng một timeline không được overlap nếu là block lời đọc.
- `end_ms <= media.duration_ms`.
- `block_id` duy nhất.
- `version` tăng tuần tự.

## JSON contract đề xuất

```json
{
  "schema_version": 1,
  "analysis_id": "analysis_xxx",
  "project_id": "project_xxx",
  "media": {
    "duration_ms": 60000,
    "fps_num": 30000,
    "fps_den": 1001,
    "width": 1920,
    "height": 1080,
    "audio_sample_rate": 48000,
    "audio_channels": 2
  },
  "speakers": [],
  "shots": [],
  "transcript_segments": [],
  "audio_events": [],
  "content_blocks": [],
  "review": {
    "status": "draft",
    "verified_block_ids": []
  }
}
```

Mỗi `ContentBlock` tối thiểu:

```json
{
  "block_id": "block_001",
  "start_ms": 12340,
  "end_ms": 17820,
  "source_start_ms": 12340,
  "source_end_ms": 17820,
  "shot_ids": ["shot_004"],
  "transcript_segment_ids": ["seg_007"],
  "description": "...",
  "transcript": "...",
  "speaker_ids": ["speaker_01"],
  "script_draft": "...",
  "script_final": null,
  "confidence": 0.91,
  "human_verified": false,
  "review_note": "",
  "audio_delivery": null
}
```

## Test viết ngay

Tạo [`tests/test_analysis_schemas.py`](tests/test_analysis_schemas.py):

- JSON hợp lệ load được.
- `end_ms <= start_ms` bị reject.
- `duration_ms` sai bị reject.
- block vượt duration video bị reject.
- duplicate `block_id` bị reject.
- block lời đọc bị overlap bị reject.
- milliseconds giữ nguyên sau serialize/deserialize.
- schema version không tương thích bị reject rõ ràng.

## Lệnh test

```text
python -m pytest tests/test_analysis_schemas.py -q
```

Chỉ khi phase này pass mới làm pipeline.

---

# PHASE 2 — Media probe và chuẩn hóa timebase

## File mới

Tạo [`pipeline/media_probe.py`](pipeline/media_probe.py).

Nhiệm vụ:

- Đọc video bằng FFprobe hiện có trong [`pipeline/ffmpeg.py`](pipeline/ffmpeg.py).
- Lấy duration chính xác.
- Lấy fps numerator/denominator.
- Lấy width/height.
- Lấy audio sample rate/channels/codec.
- Lấy timebase nếu có.
- Chuyển timestamp về milliseconds bằng một hàm duy nhất.

Không được tự viết nhiều cách tính duration khác nhau ở các module.

## Hàm cần có

- `probe_media(path) -> MediaInfo`
- `seconds_to_ms(value) -> int`
- `timestamp_to_ms(value, time_base) -> int`
- `validate_time_range(start_ms, end_ms, duration_ms)`

## Test viết ngay

Tạo [`tests/test_media_probe.py`](tests/test_media_probe.py):

- Probe fixture video.
- Duration nằm trong sai số cho phép.
- FPS đúng.
- `seconds_to_ms(1.2345)` cho kết quả ổn định.
- Không bị lỗi do float cộng dồn.
- Video không có audio vẫn probe được.
- File không tồn tại trả lỗi chuẩn.
- Timestamp cuối không vượt duration.

## Điều kiện qua phase

- Tất cả timestamp nội bộ từ đây dùng integer milliseconds.
- Không có code mới dùng float seconds cho dữ liệu phân tích.

---

# PHASE 3 — Phân tích shot và trích frame

## File mới

Tạo [`pipeline/shot_analysis.py`](pipeline/shot_analysis.py).

Tái sử dụng scene detection hiện có trong [`pipeline/segments.py`](pipeline/segments.py) nếu phù hợp, không tạo detector thứ hai không đồng nhất.

Nhiệm vụ:

1. Nhận video path và `MediaInfo`.
2. Phát hiện shot boundary.
3. Chuyển boundary sang `start_ms`/`end_ms`.
4. Trích frame đại diện giữa shot.
5. Có thể trích thêm frame đầu/cuối nếu shot có chuyển động.
6. Lưu frame vào thư mục tạm theo `analysis_id`.
7. Tạo `ShotAnalysis` với confidence và thumbnail path.

Không gọi model vision cho từng frame hàng nghìn lần. Mỗi shot chỉ gửi số frame tối thiểu cần thiết.

## Test viết ngay

Tạo [`tests/test_shot_analysis.py`](tests/test_shot_analysis.py):

- Fixture có 2–3 shot cho ra boundary hợp lệ.
- Shot nối tiếp nhau không overlap.
- Tổng shot bao phủ đúng duration hoặc có gap được đánh dấu.
- Frame path tồn tại.
- `start_ms`/`end_ms` là integer.
- Video một shot vẫn tạo được một shot.
- Detector lỗi trả warning chứ không làm mất toàn bộ document.

## Test integration

Tạo test chạy fixture thật nhưng không gọi API bên ngoài. Model vision sẽ mock.

---

# PHASE 4 — Phân tích audio gốc

## File mới

Tạo [`pipeline/audio_analysis.py`](pipeline/audio_analysis.py).

Tái sử dụng transcript và pipeline hiện có trong [`pipeline/transcribe.py`](pipeline/transcribe.py), [`pipeline/transcript_quality.py`](pipeline/transcript_quality.py) và [`services/analysis_service.py`](services/analysis_service.py).

Nhiệm vụ:

- Chạy ASR trên audio gốc.
- Lưu segment và word timing nếu engine hỗ trợ.
- Chuẩn hóa tất cả timing về ms.
- Phát hiện silence.
- Đánh dấu các audio event cơ bản.
- Gán speaker nếu pipeline hiện tại hỗ trợ diarization.
- Nếu chưa có diarization, để `speaker_id: null`, không đoán bừa.
- Lưu confidence.

## Không được làm

- Không tự coi transcript là đúng tuyệt đối.
- Không gán nhân vật dựa riêng vào vị trí khuôn mặt.
- Không tạo `speaker_id` giả nếu không có dữ liệu.
- Không làm tròn từng segment độc lập gây gap/overlap.

## Test viết ngay

Tạo [`tests/test_audio_analysis.py`](tests/test_audio_analysis.py):

- Mock ASR response có word timing.
- Chuẩn hóa seconds sang ms.
- Segment nằm trong media duration.
- Word nằm trong segment tương ứng.
- Segment overlap được phát hiện.
- Silence event không overlap sai.
- ASR failure tạo document warning rõ ràng.
- Speaker không chắc chắn phải là `null` hoặc confidence thấp.

---

# PHASE 5 — Hợp nhất hình ảnh và audio thành content blocks

## File mới

Tạo [`pipeline/content_analysis.py`](pipeline/content_analysis.py).

Đây là module quyết định block nội dung, nhưng không tự làm mất dữ liệu gốc.

Nhiệm vụ:

1. Nhận `shots`, `transcript_segments`, `audio_events`.
2. Giao nhau theo time range.
3. Tạo các `content_blocks`.
4. Mỗi block tham chiếu `shot_ids` và `transcript_segment_ids`.
5. Gọi vision/LLM để tạo `description` dựa trên frame + transcript.
6. Chỉ tạo `script_draft`, không tự đánh dấu verified.
7. Lưu evidence frame và nguồn transcript.
8. Gắn confidence theo từng loại dữ liệu.

## Quy tắc block

- Block không được vượt media duration.
- Block có thể chứa nhiều shot nếu câu nói kéo dài qua shot, nhưng phải giữ danh sách shot.
- Không tự gộp hai đoạn không liên quan chỉ vì gần nhau.
- Cho phép block không có transcript, ví dụ đoạn hành động không lời.
- Cho phép block có transcript nhưng không có người nói xác định.

## Test viết ngay

Tạo [`tests/test_content_analysis.py`](tests/test_content_analysis.py):

- Shot và transcript giao nhau đúng.
- Câu nói kéo qua hai shot được tham chiếu cả hai shot.
- Block không làm mất transcript.
- Block không overlap sai.
- LLM mock trả description và draft.
- LLM timeout vẫn giữ block với description fallback.
- Confidence được giữ nguyên.
- `human_verified` luôn là `false` sau phân tích mới.

---

# PHASE 6 — Service lưu và version hóa analysis document

## File mới

Tạo [`services/media_analysis_service.py`](services/media_analysis_service.py).

Nhiệm vụ:

- `create_analysis(project_id)`.
- `run_analysis(project_id, options)`.
- `get_current_analysis(project_id)`.
- `get_analysis_version(project_id, version)`.
- `save_reviewed_analysis(document)`.
- `export_json(project_id, version)`.
- `import_json(project_id, file)`.
- `validate_document(document)`.

## Nơi lưu

Theo cấu trúc project hiện tại:

```text
projects/<project_id>/analysis/<analysis_id>/analysis.json
projects/<project_id>/analysis/<analysis_id>/frames/
projects/<project_id>/analysis/<analysis_id>/audio/
projects/<project_id>/analysis/current.json
```

Không ghi đè analysis gốc. Bản review tạo version mới hoặc file review riêng.

## Test viết ngay

Tạo [`tests/test_media_analysis_service.py`](tests/test_media_analysis_service.py):

- Tạo analysis directory.
- Save/load giữ nguyên JSON.
- Version tăng đúng.
- Current pointer đúng.
- Import JSON invalid bị từ chối.
- Import JSON khác project bị từ chối.
- Export/import round-trip không đổi timecode.
- Không ghi file nửa chừng khi process bị lỗi.

---

# PHASE 7 — Giao diện human-in-the-loop

## File mới hoặc thay đổi

Tạo trang mới [`pages/05_media_analysis.py`](pages/05_media_analysis.py).

Cập nhật điều hướng trong [`app.py`](app.py) hoặc nơi đang khai báo page navigation.

Không xóa ngay [`pages/05_script_review.py`](pages/05_script_review.py). Giữ compatibility trong phase đầu.

## Giao diện bắt buộc

### Khu vực 1: Chọn project và analysis

- Chọn project.
- Chọn analysis version.
- Nút “Phân tích video + audio gốc”.
- Nút “Tải JSON phân tích”.
- Nút “Import JSON đã chỉnh”.

### Khu vực 2: Player + timeline

- `st.video` cho video.
- Timeline dạng bảng hoặc component custom.
- Danh sách shot.
- Danh sách transcript.
- Waveform nếu khả thi; nếu chưa có component thì dùng bảng timecode trước, không block phase.
- Click block để chọn nội dung tương ứng.

### Khu vực 3: Review block

Mỗi block hiển thị:

- `block_id`.
- Start/end ms và dạng `MM:SS.mmm`.
- Thumbnail.
- Mô tả hình ảnh.
- Transcript audio gốc.
- Speaker/confidence.
- Script draft.
- Text area script final.
- Nút nghe đoạn gốc.
- Nút “Đã kiểm tra”.
- Ghi chú review.
- Nút tách block.
- Nút gộp block.

### Khu vực 4: Sinh lại draft có kiểm soát

Nút AI chỉ nhận:

- block hiện tại.
- description.
- transcript.
- tone.
- giới hạn ký tự/thời lượng.

AI chỉ được cập nhật `script_draft`, không được ghi đè `script_final` và không được đổi timecode.

## Test viết ngay

Tạo [`tests/test_media_analysis_page.py`](tests/test_media_analysis_page.py):

- Page import được.
- Không có project hiển thị thông báo đúng.
- Analysis chưa có hiển thị nút chạy phân tích.
- Block render đúng timecode.
- Sửa script không đổi `start_ms`/`end_ms`.
- Check verified cập nhật đúng block.
- Import invalid JSON báo lỗi.
- Generate draft mock không ghi đè final.

Cập nhật [`tests/test_pages_smoke.py`](tests/test_pages_smoke.py) để thêm page mới.

## Smoke test thủ công ngay sau page

Dùng Playwright:

1. Mở app.
2. Chọn project fixture.
3. Chạy hoặc load analysis fixture.
4. Click block.
5. Sửa script.
6. Đánh dấu verified.
7. Reload.
8. Xác nhận dữ liệu vẫn giữ nguyên.

---

# PHASE 8 — Chuyển script hiện tại sang block-based workflow

## Service cần cập nhật

Cập nhật [`services/script_service.py`](services/script_service.py).

Thêm các method:

- `generate_block_draft(project_id, block_id, instructions)`.
- `save_block_script(project_id, block_id, script_final)`.
- `approve_reviewed_blocks(project_id)`.
- `build_script_from_analysis(project_id)`.

Không bỏ ngay `generate()` hiện tại. Đánh dấu legacy cho đến khi workflow mới ổn định.

## Quy tắc approve

Chỉ approve khi:

- Có analysis hiện tại.
- Các block cần lời đọc đã có `script_final`.
- Các block bắt buộc đã `human_verified`.
- Không có time range invalid.
- Không có narration overlap.
- Không có script ngoài media duration.

## Test viết ngay

Tạo [`tests/test_block_script_workflow.py`](tests/test_block_script_workflow.py):

- Draft không thể approve.
- Script final chưa verified không thể approve.
- Một block thiếu script bị reject nếu bắt buộc.
- Script final giữ nguyên block mapping.
- Generate draft không đổi final.
- Edit một block không làm đổi block khác.
- Approve tạo đúng timeline.

---

# PHASE 9 — Audio upload theo từng block

## File mới

Tạo [`services/audio_delivery_service.py`](services/audio_delivery_service.py).

Tạo trang mới [`pages/06_audio_delivery.py`](pages/06_audio_delivery.py).

Nếu app đang có page timeline/export đánh số khác, điều chỉnh số page nhưng không đổi trách nhiệm module.

## Service methods

- `upload_block_audio(project_id, block_id, uploaded_file)`.
- `probe_block_audio(path)`.
- `trim_edge_silence(path)`.
- `validate_block_audio(block, audio_info)`.
- `set_fit_mode(block_id, fit_mode)`.
- `approve_block_audio(block_id)`.
- `get_delivery_report(project_id)`.

## Audio validation

Mỗi audio delivery lưu:

```json
{
  "block_id": "block_001",
  "file_path": "audio/block_001.wav",
  "actual_duration_ms": 4210,
  "expected_duration_ms": 5480,
  "expected_start_ms": 12340,
  "expected_end_ms": 17820,
  "fit_mode": "preserve_speed",
  "overlap_ms": 0,
  "gap_ms": 80,
  "status": "warning",
  "approved": false
}
```

## Fit mode mặc định

`preserve_speed`.

Nếu audio dài hơn window:

- Không tự cắt.
- Không tự tăng tốc vượt giới hạn.
- Báo warning/error để người dùng chọn.

Các mode:

- `preserve_speed`.
- `trim_edge_silence`.
- `fit_95_105_percent`.
- `manual_trim`.

## Test viết ngay

Tạo [`tests/test_audio_delivery_service.py`](tests/test_audio_delivery_service.py):

- Upload file hợp lệ.
- File không phải audio bị reject.
- Probe duration đúng.
- Trim silence không làm mất phần lời chính trong fixture.
- Audio ngắn tạo gap warning.
- Audio dài tạo overflow warning.
- Overlap giữa hai block bị reject khi approve.
- Không tự cắt audio ở preserve mode.
- File block A không thể map cho block B nếu metadata không khớp.
- Audio approval invalidates previous render/audio approval.

---

# PHASE 10 — Render audio block-based, không chồng tiếng

## File cần cập nhật

Cập nhật [`pipeline/tts.py`](pipeline/tts.py) nhưng không dùng TTS làm nguồn mặc định nữa.

Tách logic mới thành file rõ ràng hơn:

Tạo [`pipeline/audio_timeline.py`](pipeline/audio_timeline.py).

Nhiệm vụ:

- Nhận danh sách `AudioDelivery` đã approved.
- Đặt từng file theo `start_ms`.
- Validate overlap trước FFmpeg.
- Mix pairwise hoặc concat theo pattern đã ổn định hiện tại.
- Pad/trim track theo tổng duration.
- Validate duration output.
- Sinh sync report.

`pipeline/tts.py` chỉ giữ vai trò optional fallback hoặc preview, không được tự ghi đè audio upload đã duyệt.

## Renderer cần cập nhật

Cập nhật [`pipeline/renderer.py`](pipeline/renderer.py):

- Thêm audio source mode: `uploaded_blocks`, `tts`, `original`.
- Nếu `uploaded_blocks`, ưu tiên audio block.
- Không chạy cả uploaded audio và TTS cùng lúc trừ khi người dùng chủ động bật mix.
- Nếu audio block thiếu, fail rõ ràng hoặc yêu cầu xác nhận fallback.

Cập nhật [`services/render_service.py`](services/render_service.py):

- Validate approval trước render.
- Gắn `analysis_version` và `audio_delivery_version` vào render record.
- Chặn render nếu delivery report có lỗi overlap.

## Test viết ngay

Tạo [`tests/test_audio_timeline.py`](tests/test_audio_timeline.py):

- Một block đặt đúng offset.
- Nhiều block đặt đúng offset.
- Block không overlap tạo track hợp lệ.
- Block overlap bị reject trước FFmpeg.
- Gap giữ nguyên silence.
- Track duration khớp video.
- Audio cuối bị cắt đúng tại duration video, không cắt giữa block nếu chưa được phép.
- Output không có hai voice layer ngoài chủ đích.
- Empty audio delivery không tạo silent render giả.

Tạo integration test:

- Fixture video + hai WAV ngắn.
- Render.
- FFprobe duration.
- Kiểm tra audio stream.
- Kiểm tra report overlap.

---

# PHASE 11 — Export/import JSON cho người dùng

## UI cập nhật

Cập nhật [`pages/07_export.py`](pages/07_export.py):

- Nút tải analysis JSON.
- Nút tải reviewed script JSON.
- Hiển thị analysis version.
- Hiển thị audio delivery status.
- Chỉ mở nút render khi content review và audio delivery đạt điều kiện.
- Không để quá nhiều nút giống nhau; chỉ giữ Preview và Final.

## Test viết ngay

Cập nhật [`tests/test_pages_smoke.py`](tests/test_pages_smoke.py):

- Export page hiển thị status analysis.
- Export page khóa khi chưa approve block.
- Export page khóa khi audio overlap.
- Export page mở khi toàn bộ gate pass.
- Download JSON có đủ schema version và block IDs.

---

# PHASE 12 — Database/versioning nếu cần truy vấn nhanh

Ban đầu có thể lưu JSON theo project để giảm rủi ro migration database.

Chỉ thêm bảng SQL khi cần search/report nhiều:

- `analysis_documents`.
- `analysis_versions`.
- `audio_deliveries`.
- `audio_delivery_items`.

Nếu thêm SQLAlchemy model, phải cập nhật migration/init database và test round-trip. Không trộn migration database vào phase đầu.

---

# TEST STRATEGY BẮT BUỘC

## Sau mỗi phase

Agent phải chạy theo thứ tự:

1. Test file mới.
2. Test các file liên quan.
3. Toàn bộ `tests/`.
4. Ruff/format check cho file vừa sửa.
5. Nếu có UI: smoke test Playwright.

Không được code hết tất cả phase rồi mới test.

## Ma trận test

### Unit test

- Schema validation.
- Time conversion.
- Range validation.
- Shot merge.
- Transcript merge.
- Content block generation.
- Audio duration validation.
- Overlap validation.
- JSON round-trip.

### Integration test

- Video fixture → media probe → shots.
- Audio fixture → ASR mock → transcript.
- Shots + transcript → content blocks.
- Content blocks → reviewed script.
- Reviewed script + WAV → audio delivery.
- Audio delivery → final track.
- Final track + video → render.

### UI smoke test

- Mở trang phân tích.
- Load JSON.
- Chọn block.
- Sửa script.
- Verify block.
- Upload audio.
- Xem warning duration.
- Approve audio.
- Mở export.
- Render preview.

### Regression test

Phải giữ các test hiện tại trong [`tests/`](tests/), đặc biệt:

- [`tests/test_tts.py`](tests/test_tts.py)
- [`tests/test_script_pipeline.py`](tests/test_script_pipeline.py)
- [`tests/test_script_timeline_planner.py`](tests/test_script_timeline_planner.py)
- [`tests/test_render_service_flow.py`](tests/test_render_service_flow.py)
- [`tests/test_pages_smoke.py`](tests/test_pages_smoke.py)

## Lệnh test chuẩn

```text
python -m pytest tests/test_analysis_schemas.py -q
python -m pytest tests/test_media_probe.py tests/test_shot_analysis.py -q
python -m pytest tests/test_audio_analysis.py tests/test_content_analysis.py -q
python -m pytest tests/test_media_analysis_service.py -q
python -m pytest tests/test_media_analysis_page.py tests/test_pages_smoke.py -q
python -m pytest tests/test_audio_delivery_service.py tests/test_audio_timeline.py -q
python -m pytest tests/ -q
```

Lưu ý: không chạy `pytest` ở root nếu các file `scripts_tmp_*_test.py` có thể bị pytest collect nhầm. Luôn chạy `pytest tests/`.

---

# Tiêu chí nghiệm thu cuối cùng

Agent chỉ được báo hoàn thành khi tất cả điều kiện sau đạt:

1. JSON phân tích load/save/import/export được.
2. Tất cả timecode chính dùng integer milliseconds.
3. Có shot, transcript, audio event, content block.
4. Người dùng sửa được từng block.
5. AI không ghi đè script final.
6. Có human verification trước approve.
7. Audio upload map bằng `block_id`.
8. Có cảnh báo audio ngắn/dài.
9. Overlap bị chặn trước render.
10. Không tự cắt nội dung hoặc tự tăng tốc ngoài lựa chọn của người dùng.
11. Render dùng đúng audio đã upload, không chạy thêm TTS ngoài ý muốn.
12. Preview/Final bị khóa khi gate chưa đạt.
13. Có sync report sau render.
14. Test suite hiện tại không regression.
15. Có UI smoke test thực tế.
16. Agent báo rõ từng phase: file đã sửa, test đã chạy, kết quả, lỗi còn lại.

## Format báo cáo bắt buộc sau mỗi phase

```text
PHASE: <số và tên>
FILES ADDED:
FILES MODIFIED:
TEST COMMANDS:
TEST RESULTS:
MANUAL CHECK:
KNOWN LIMITATIONS:
NEXT PHASE:
```

Không chuyển phase nếu test phase hiện tại chưa pass hoặc chưa ghi rõ lý do ngoại lệ.


*****AUDIT 2*******************
Dưới đây là nội dung có thể copy giao trực tiếp cho agent. **Chỉ là plan thực hiện, chưa sửa code trong lượt này.**

# PLAN CẢI THIỆN BẮT BUỘC

## Mục tiêu cuối

Hoàn thiện workflow:

`Video + audio gốc → analysis theo block → human review → AI draft từng block → human approve → upload audio từng block → validate sync/overlap → build audio track → render bằng uploaded audio → export/report`

Không được coi test regression xanh là hoàn thành nếu chưa đạt các acceptance criteria bên dưới.

---

## Quy tắc chung cho agent

1. Đọc [`enhance_1.md`](enhance_1.md) và audit hiện trạng trước khi sửa.
2. Không xóa workflow legacy ngay; giữ compatibility cho đến khi workflow block-based chạy ổn định.
3. Mọi timecode nghiệp vụ phải dùng integer milliseconds.
4. Không để AI ghi đè `script_final`.
5. Không tự cắt hoặc tự tăng tốc audio nếu user chưa chọn fit mode.
6. Không cho approve audio có overlap.
7. Không chạy uploaded audio và TTS đồng thời nếu user chưa chủ động chọn mix.
8. Mỗi phase phải sửa test trước hoặc đồng thời với code.
9. Sau mỗi phase chạy: test phase → test liên quan → toàn bộ [`tests/`](tests/) → Ruff/format → Playwright nếu có UI.
10. Cuối mỗi phase báo theo format trong [`enhance_1.md`](enhance_1.md): `PHASE`, `FILES ADDED`, `FILES MODIFIED`, `TEST COMMANDS`, `TEST RESULTS`, `MANUAL CHECK`, `KNOWN LIMITATIONS`, `NEXT PHASE`.

---

# Phase A — Chuẩn hóa contract và persistence

## Files cần sửa

- [`core/analysis_schemas.py`](core/analysis_schemas.py)
- [`services/media_analysis_service.py`](services/media_analysis_service.py)
- Có thể thêm [`core/analysis_errors.py`](core/analysis_errors.py)

## Việc phải làm

1. Giữ `AnalysisDocument` là contract trung tâm.
2. Bảo đảm tất cả field timecode nghiệp vụ là `int`:
   - `start_ms`
   - `end_ms`
   - `duration_ms`
   - `expected_start_ms`
   - `expected_end_ms`
   - `actual_duration_ms`
   - `gap_ms`
   - `overlap_ms`
3. Reject rõ ràng:
   - `end_ms <= start_ms`
   - duration sai
   - block vượt media duration
   - duplicate `block_id`
   - narration overlap
   - schema version không tương thích.
4. Bổ sung API đúng tên plan:
   - `create_analysis(project_id)`
   - `run_analysis(project_id, options)`
   - `get_current_analysis(project_id)`
   - `get_analysis_version(project_id, version)`
   - `save_reviewed_analysis(document)`
   - `export_json(project_id, version)`
   - `import_json(project_id, file)`
   - `validate_document(document)`
5. Chuẩn hóa persistence:

```text
projects/<project_id>/analysis/<analysis_id>/analysis.json
projects/<project_id>/analysis/<analysis_id>/frames/
projects/<project_id>/analysis/<analysis_id>/audio/
projects/<project_id>/analysis/current.json
```

6. `current.json` phải là pointer rõ ràng đến `analysis_id`/version hiện tại.
7. Không ghi đè analysis gốc khi save review.
8. Ghi file atomic: ghi file tạm → flush/fsync nếu cần → replace.
9. Import JSON phải reject nếu `project_id` không khớp.
10. Version tăng tuần tự, không chỉ dựa vào timestamp filename.

## Tests bắt buộc

Bổ sung vào [`tests/test_analysis_schemas.py`](tests/test_analysis_schemas.py) và [`tests/test_media_analysis_service.py`](tests/test_media_analysis_service.py):

- version tăng 1, 2, 3.
- current pointer đúng.
- save/load không đổi milliseconds.
- import khác project bị reject.
- import invalid JSON bị reject.
- crash giữa lúc save không tạo document hỏng.
- mọi timecode deserialize lại vẫn là `int`.

## Done khi

Có thể tạo, lưu, load, version, restore, export, import một `AnalysisDocument` mà không mất mapping/timecode.

---

# Phase B — Hoàn thiện block script workflow

## Files cần sửa

- [`services/script_service.py`](services/script_service.py)
- [`services/media_analysis_service.py`](services/media_analysis_service.py)
- Có thể thêm [`core/script_errors.py`](core/script_errors.py)

## API bắt buộc

Implement đúng các method:

```python
generate_block_draft(project_id, block_id, instructions)
save_block_script(project_id, block_id, script_final)
approve_reviewed_blocks(project_id)
build_script_from_analysis(project_id)
```

## Quy tắc từng method

### `generate_block_draft`

- Load current analysis.
- Tìm đúng `block_id`.
- Input cho AI chỉ gồm description, transcript, tone, instruction, duration limit.
- Chỉ ghi `block.script_draft`.
- Không đổi `script_final`.
- Không đổi `start_ms`/`end_ms`.
- Không tự đổi `human_verified`.
- Lưu analysis version mới.

### `save_block_script`

- Tìm đúng block.
- Validate text không rỗng nếu block cần narration.
- Validate độ dài/thời lượng theo block.
- Chỉ ghi `script_final`.
- Reset approval/render readiness khi nội dung thay đổi.
- Không đổi block khác.

### `approve_reviewed_blocks`

Chỉ thành công khi:

- Có current analysis.
- Block cần narration có `script_final`.
- Block bắt buộc đã `human_verified=True`.
- Tất cả time range hợp lệ.
- Không có narration overlap.
- Script mapping vẫn gắn đúng `block_id`.
- Script không vượt giới hạn duration đã quy định.

Nếu fail phải trả diagnostics theo từng block, không chỉ trả một message chung.

### `build_script_from_analysis`

- Tạo timeline từ các block đã approved.
- Giữ `block_id`, `start_ms`, `end_ms` trên mọi item.
- Không lấy script legacy nếu block workflow đã tồn tại, trừ khi explicit fallback.
- Không làm mất block không có narration.

## Tests bắt buộc

Tạo [`tests/test_block_script_workflow.py`](tests/test_block_script_workflow.py):

- Draft không thể approve.
- Final chưa verified bị reject.
- Thiếu final ở block bắt buộc bị reject.
- Overlap bị reject.
- Script vượt duration bị reject.
- Generate draft không đổi final.
- Edit block A không đổi block B.
- Timeline giữ nguyên block mapping.
- Diagnostics chỉ rõ block lỗi.

---

# Phase C — Hoàn thiện audio delivery

## Files cần sửa

- [`services/audio_delivery_service.py`](services/audio_delivery_service.py)
- [`pages/06_audio_delivery.py`](pages/06_audio_delivery.py)
- [`core/analysis_schemas.py`](core/analysis_schemas.py) nếu cần bổ sung field
- [`pipeline/media_probe.py`](pipeline/media_probe.py) hoặc thêm utility audio probe

## API bắt buộc

Implement:

```python
upload_block_audio(project_id, block_id, uploaded_file)
probe_block_audio(path)
trim_edge_silence(path)
validate_block_audio(block, audio_info)
set_fit_mode(project_id, block_id, fit_mode)
approve_block_audio(project_id, block_id)
get_delivery_report(project_id)
```

Có thể giữ [`upload_audio()`](services/audio_delivery_service.py:50) làm compatibility wrapper, nhưng UI mới phải dùng API block-based.

## Quy tắc upload/probe

1. Service tự lưu file vào thư mục analysis/project, không tin path tùy ý từ client.
2. Validate extension, MIME và stream audio bằng FFprobe.
3. `probe_block_audio()` tự lấy:
   - duration ms
   - sample rate
   - channels
   - codec
   - file size/hash nếu cần.
4. Không nhận `actual_duration_ms` do UI tự nhập làm nguồn tin cậy.
5. File không có audio stream phải reject.
6. Không cho file block A map sang block B nếu metadata/hash không hợp lệ.

## Fit mode

Chỉ cho phép enum:

- `preserve_speed`
- `trim_edge_silence`
- `fit_95_105_percent`
- `manual_trim`

Quy tắc:

- Mặc định `preserve_speed`.
- Audio dài không được tự cắt.
- Audio dài không được tự tăng tốc ngoài lựa chọn của user.
- Audio ngắn phải tạo `gap_ms` warning.
- Audio dài phải tạo `overflow_ms`/warning hoặc error.
- Chỉ khi user chọn mode phù hợp mới tạo processed file.

## Approval gate

`approve_block_audio()` phải reject nếu:

- file không tồn tại.
- probe thất bại.
- block không tồn tại.
- duration invalid.
- overlap với block audio khác.
- block content chưa approved nếu policy yêu cầu content trước audio.
- fit mode chưa xử lý warning bắt buộc.

Khi upload/thay audio hoặc thay script:

- invalidate render readiness cũ.
- invalidate audio approval cũ nếu delivery thay đổi.

## Delivery report

`get_delivery_report()` phải trả:

- từng block: expected/actual/gap/overflow/overlap/status/approved.
- tổng số block cần audio.
- số pending.
- số approved.
- danh sách errors/warnings.
- `render_ready` boolean.

## Tests bắt buộc

Mở rộng [`tests/test_audio_delivery.py`](tests/test_audio_delivery.py):

- upload WAV hợp lệ.
- file không phải audio bị reject.
- probe duration chính xác.
- trim silence giữ phần lời chính.
- preserve speed không tự cắt.
- audio ngắn warning.
- audio dài warning/error.
- overlap reject khi approve.
- block A không map nhầm cho block B.
- thay audio invalidate approval/render cũ.
- report tổng hợp đúng.

---

# Phase D — Implement audio timeline thật

## Files cần sửa

- [`pipeline/audio_timeline.py`](pipeline/audio_timeline.py)
- [`pipeline/renderer.py`](pipeline/renderer.py)
- [`services/render_service.py`](services/render_service.py)
- Có thể thêm [`pipeline/audio_mix.py`](pipeline/audio_mix.py)

## Thiết kế input/output

Thiết kế rõ API, ví dụ:

```python
build_audio_timeline(
    deliveries: list[AudioDelivery],
    video_duration_ms: int,
    output_path: Path,
) -> AudioTimelineResult
```

`AudioTimelineResult` phải có:

- output audio path.
- duration_ms.
- placed items.
- gaps.
- overlaps.
- warnings/errors.
- sync report.

## Quy tắc timeline

1. Input chỉ gồm delivery đã approved.
2. Sắp xếp theo `expected_start_ms`.
3. Đặt file theo integer millisecond offset.
4. Validate overlap trước khi gọi FFmpeg.
5. Overlap > 0 phải fail, không tự mix.
6. Gap phải giữ silence và ghi report.
7. Tổng duration phải khớp video theo tolerance định nghĩa trước.
8. Không tự cắt giữa block nếu chưa có user approval.
9. Empty delivery không được biến thành silent render giả mà không có warning.
10. Nếu audio cuối vượt video duration, fail hoặc yêu cầu explicit trim permission.

## FFmpeg implementation

- Dùng `adelay`/`amix` hoặc concat theo thiết kế đã test.
- Tạo output audio track thật, không chỉ metadata `audio_overlays`.
- Validate output bằng FFprobe.
- Sinh `sync_report.json` có:
  - block_id
  - expected start/end
  - actual placement
  - duration
  - gap/overlap
  - fit mode
  - warnings/errors.

## Renderer

Bổ sung `audio_source` với enum:

- `uploaded_blocks`
- `tts`
- `original`

Quy tắc:

- `uploaded_blocks`: dùng audio track do audio timeline tạo.
- Không gọi TTS nếu đã chọn uploaded blocks.
- `tts`: chỉ dùng legacy TTS khi user chọn.
- `original`: giữ audio gốc.
- Nếu thiếu block audio: fail rõ ràng hoặc hiển thị confirmation fallback.
- Record `analysis_version`, `audio_delivery_version`, `audio_source`.

## Render service

Trong [`services/render_service.py`](services/render_service.py):

- chạy content gate trước render.
- chạy audio delivery gate trước render.
- chặn overlap.
- chặn unapproved delivery.
- lưu metadata version vào render record.
- preview và final dùng cùng gate, chỉ khác preset/output.

## Tests bắt buộc

Mở rộng [`tests/test_audio_timeline.py`](tests/test_audio_timeline.py):

- một block đúng offset.
- nhiều block đúng offset.
- gap giữ silence.
- overlap reject trước FFmpeg.
- duration track đúng video.
- audio cuối vượt duration xử lý đúng policy.
- không có hai voice layer ngoài chủ đích.
- empty delivery có warning.

Thêm integration test:

- fixture video + 2 WAV.
- build track.
- FFprobe có audio stream.
- duration đúng.
- report đúng.
- render với `audio_source="uploaded_blocks"` không chạy TTS.

---

# Phase E — Tích hợp export và UI gates

## Files cần sửa

- [`pages/07_export.py`](pages/07_export.py)
- [`app.py`](app.py)
- [`tests/test_pages_smoke.py`](tests/test_pages_smoke.py)
- Tạo [`tests/test_media_analysis_page.py`](tests/test_media_analysis_page.py) nếu chưa có
- Có thể giữ [`pages/08_analysis_io.py`](pages/08_analysis_io.py), nhưng phải tích hợp hoặc điều hướng rõ ràng từ export.

## Export page bắt buộc hiển thị

- analysis version hiện tại.
- content review status.
- audio delivery status.
- pending/warning/error count.
- overlap status.
- selected `audio_source`.
- Preview và Final.
- Download analysis JSON.
- Download reviewed script JSON.

## Render gate

Khóa Preview/Final nếu:

- chưa có current analysis.
- block cần narration chưa có final script.
- block chưa human verified.
- có narration overlap.
- audio delivery pending.
- audio delivery overlap.
- delivery report có error.
- uploaded audio được chọn nhưng thiếu file.
- sync report chưa pass theo policy.

Khi gate fail, hiển thị danh sách lỗi theo block, không chỉ disable button.

## Navigation

Đảm bảo các page workflow mới được expose trong [`app.py`](app.py):

- media analysis.
- script/block review nếu cần.
- audio delivery.
- export.
- analysis import/export.

Không để page tồn tại nhưng không thể truy cập từ app.

## Tests bắt buộc

Mở rộng [`tests/test_pages_smoke.py`](tests/test_pages_smoke.py):

- import tất cả page mới.
- page không có project không crash.
- export hiển thị analysis status.
- chưa approve block thì render locked.
- overlap thì render locked.
- toàn bộ gate pass thì render enabled.
- JSON download có schema version và block IDs.

Nếu môi trường cho phép, tạo Playwright smoke:

1. mở app.
2. chọn project fixture.
3. load analysis.
4. sửa script block.
5. verify block.
6. upload audio.
7. xem warning duration.
8. approve audio.
9. mở export.
10. render preview.
11. kiểm tra output có audio stream.

---

# Phase F — Test/quality gate cuối

## Chạy đúng thứ tự

```text
python -m pytest tests/test_analysis_schemas.py -q
python -m pytest tests/test_media_analysis_service.py -q
python -m pytest tests/test_block_script_workflow.py -q
python -m pytest tests/test_audio_delivery.py -q
python -m pytest tests/test_audio_timeline.py -q
python -m pytest tests/test_media_analysis_page.py tests/test_pages_smoke.py -q
python -m pytest tests/ -q
```

Sau đó chạy Ruff/format trên các file đã sửa.

## Không được báo hoàn thành nếu thiếu

- test overlap approval.
- test preserve speed không auto-cut.
- test actual uploaded audio render.
- test renderer không chạy TTS ngoài ý muốn.
- test export lock/unlock.
- Playwright hoặc manual smoke có bằng chứng.
- sync report.

# Tiêu chí nghiệm thu cuối

Agent chỉ báo hoàn thành khi chứng minh được:

1. `script_draft` và `script_final` tách biệt.
2. AI không ghi đè final.
3. Human verification bắt buộc.
4. Audio map đúng `block_id`.
5. Audio duration được probe từ file.
6. Audio ngắn/dài có warning.
7. Preserve speed không tự cắt.
8. Overlap bị reject trước FFmpeg.
9. Audio timeline tạo track thật.
10. Renderer dùng đúng uploaded audio.
11. TTS không chạy đồng thời ngoài lựa chọn.
12. Preview/Final bị khóa khi gate fail.
13. Sync report được sinh sau timeline/render.
14. Version metadata được ghi vào render record.
15. Toàn bộ regression pass.
16. Có bằng chứng UI smoke.

## Thứ tự giao việc khuyến nghị

1. Agent 1: Phase A — schema/persistence.
2. Agent 2: Phase B — block script workflow.
3. Agent 3: Phase C — audio delivery.
4. Agent 4: Phase D — audio timeline + renderer.
5. Agent 5: Phase E — export/UI gates.
6. Agent cuối: Phase F — integration test, regression và nghiệm thu.

Không nên giao agent sửa tất cả cùng lúc vì dễ tạo tình trạng test xanh cục bộ nhưng pipeline end-to-end vẫn không chạy đúng.