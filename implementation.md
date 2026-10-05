# SPEC.md — Local AI Video Rough-Cut Assistant

## 1. Tổng quan

Xây dựng một **web app chạy nội bộ** để phân tích và biên tập video mà người dùng sở hữu hoặc có quyền sử dụng.

Ứng dụng thực hiện các tác vụ:

1. Tạo project và nhập video.
2. Ghi nhận thông tin quyền sử dụng.
3. Tạo proxy để xem trước.
4. Tách âm thanh.
5. Chuyển lời nói thành transcript có timestamp.
6. Phát hiện:
   - Cảnh quay.
   - Khoảng im lặng.
   - Câu lặp hoặc đoạn ít thông tin.
7. Đề xuất một hoặc nhiều rough-cut timeline.
8. Cho phép người dùng chỉnh điểm In/Out và thứ tự clip.
9. Render preview chất lượng thấp.
10. Sau khi người dùng duyệt, render video chính thức.

Ứng dụng không được thiết kế để né Content ID, watermark, kiểm duyệt hoặc cơ chế bảo vệ bản quyền.

---

# 2. Mục tiêu MVP

MVP cần hoàn thành được luồng sau:

```text
Tạo project
    ↓
Nhập video
    ↓
Xác nhận quyền sử dụng
    ↓
Phân tích video
    ↓
Sinh transcript có timestamp
    ↓
Phát hiện cảnh và khoảng im lặng
    ↓
Đề xuất rough-cut timeline
    ↓
Người dùng chỉnh sửa timeline
    ↓
Render preview
    ↓
Duyệt preview
    ↓
Render final
```

## 2.1 Chức năng bắt buộc

- Chạy local trên Windows, macOS và Linux.
- Giao diện bằng Streamlit.
- Xử lý video bằng FFmpeg/FFprobe.
- Transcript bằng `faster-whisper`.
- Phát hiện cảnh bằng `PySceneDetect`.
- Phát hiện im lặng bằng FFmpeg.
- Lưu metadata bằng SQLite.
- Lưu timeline dạng JSON.
- Có thể chỉnh thủ công:
  - Điểm bắt đầu.
  - Điểm kết thúc.
  - Thứ tự clip.
  - Nhãn clip.
  - Bật/tắt clip.
- Render preview 720p.
- Render final 1080p.
- Có log và báo lỗi rõ ràng.
- Không tự động đăng video lên nền tảng.

## 2.2 Chưa cần làm trong MVP

- Timeline kéo thả giống Premiere.
- Nhiều người dùng.
- Đăng nhập.
- Chạy nhiều worker phân tán.
- Cloud storage.
- Tự động đăng YouTube/TikTok/Facebook.
- Nhận diện khuôn mặt.
- Theo dõi chủ thể để auto-crop.
- Tự động tạo nội dung nhằm né hệ thống bản quyền.
- Phân tích bản quyền hoặc cam kết “fair use”.
- Mobile app.

---

# 3. Đối tượng sử dụng

- Một người dùng trên máy local.
- Người dựng video giáo dục, podcast, phỏng vấn hoặc nội dung do chính họ sở hữu.
- Team nhỏ có thể chạy ứng dụng trong mạng LAN ở giai đoạn sau.

---

# 4. Ràng buộc về quyền sử dụng

Trước khi phân tích, người dùng phải chọn một trong các loại quyền:

```text
- Nội dung do tôi sở hữu.
- Tôi có giấy phép sử dụng.
- Nội dung public domain.
- Nội dung Creative Commons phù hợp.
```

Phải có checkbox:

```text
[ ] Tôi xác nhận có quyền sử dụng và xử lý nội dung này.
```

Không cho phép bắt đầu phân tích nếu checkbox chưa được chọn.

Có thể đính kèm:

- File giấy phép.
- Hóa đơn.
- Email cho phép.
- URL nguồn.
- Ghi chú quyền sử dụng.

Ứng dụng chỉ lưu hồ sơ; không tự xác minh tính hợp pháp.

---

# 5. Công nghệ sử dụng

## 5.1 Stack chính

| Thành phần | Công nghệ |
|---|---|
| UI | Streamlit |
| Ngôn ngữ | Python 3.11 |
| Video processing | FFmpeg + FFprobe |
| Transcript | faster-whisper |
| Scene detection | PySceneDetect |
| Database | SQLite + SQLAlchemy |
| Validation | Pydantic |
| Audio analysis | FFmpeg silencedetect |
| Data interchange | JSON |
| Logging | Python logging |
| Test | pytest |
| Formatting | Ruff + Black |

## 5.2 Yêu cầu hệ thống

Tối thiểu:

- Python 3.11.
- FFmpeg và FFprobe có trong `PATH`.
- RAM 8 GB.
- SSD còn trống ít nhất 30 GB.

Khuyến nghị:

- RAM 16 GB.
- CPU 6 nhân.
- SSD còn trống 100 GB.
- NVIDIA GPU là tùy chọn.

---

# 6. Kiến trúc tổng thể

```text
┌──────────────────────┐
│ Streamlit UI         │
│                      │
│ Projects             │
│ Analysis             │
│ Transcript           │
│ Timeline             │
│ Preview / Export     │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Application Services │
│                      │
│ ProjectService       │
│ AnalysisService      │
│ TimelineService      │
│ RenderService        │
│ JobService           │
└──────────┬───────────┘
           │
           ▼
┌─────────────────────────────────┐
│ Processing Pipeline             │
│                                 │
│ FFprobe                         │
│ Proxy Generator                 │
│ Audio Extractor                 │
│ Whisper Transcriber             │
│ Scene Detector                  │
│ Silence Detector                │
│ Segment Builder                 │
│ Content Ranker                  │
│ Timeline Builder                │
│ FFmpeg Renderer                 │
└──────────┬──────────────────────┘
           │
           ▼
┌───────────────────────────────┐
│ Storage                       │
│                               │
│ SQLite                       │
│ projects/<project_id>/       │
│ JSON metadata                │
│ Source/proxy/preview/export  │
└───────────────────────────────┘
```

---

# 7. Cấu trúc thư mục

```text
local-video-editor/
├── app.py
├── README.md
├── SPEC.md
├── pyproject.toml
├── requirements.txt
├── .env.example
├── .gitignore
│
├── config/
│   ├── settings.py
│   └── presets.json
│
├── pages/
│   ├── 01_projects.py
│   ├── 02_analysis.py
│   ├── 03_transcript.py
│   ├── 04_timeline.py
│   └── 05_export.py
│
├── core/
│   ├── database.py
│   ├── models.py
│   ├── schemas.py
│   ├── enums.py
│   ├── exceptions.py
│   └── logging_config.py
│
├── services/
│   ├── project_service.py
│   ├── asset_service.py
│   ├── analysis_service.py
│   ├── timeline_service.py
│   ├── render_service.py
│   └── job_service.py
│
├── pipeline/
│   ├── ffmpeg.py
│   ├── probe.py
│   ├── proxy.py
│   ├── audio.py
│   ├── transcribe.py
│   ├── scenes.py
│   ├── silence.py
│   ├── segments.py
│   ├── ranker.py
│   ├── timeline_builder.py
│   ├── captions.py
│   └── renderer.py
│
├── utils/
│   ├── files.py
│   ├── timestamps.py
│   ├── subprocesses.py
│   ├── json_io.py
│   └── validators.py
│
├── tests/
│   ├── fixtures/
│   ├── test_timestamps.py
│   ├── test_segments.py
│   ├── test_ranker.py
│   ├── test_timeline_builder.py
│   └── test_renderer.py
│
├── data/
│   └── app.sqlite3
│
├── projects/
│   └── .gitkeep
│
└── logs/
    └── app.log
```

---

# 8. Cấu trúc thư mục của một project

```text
projects/<project_id>/
├── project.json
├── source/
│   └── input.mp4
├── license/
│   ├── license.pdf
│   └── metadata.json
├── proxy/
│   └── proxy_720p.mp4
├── audio/
│   └── speech.wav
├── analysis/
│   ├── media_info.json
│   ├── transcript.json
│   ├── scenes.json
│   ├── silences.json
│   ├── segments.json
│   └── analysis_summary.json
├── timelines/
│   ├── timeline_v1.json
│   ├── timeline_v2.json
│   └── current.json
├── subtitles/
│   ├── transcript.srt
│   └── transcript.vtt
├── previews/
│   └── preview_v1.mp4
├── exports/
│   └── final_v1.mp4
├── temp/
└── logs/
    └── pipeline.log
```

---

# 9. Trạng thái project

Dùng enum:

```python
class ProjectStatus(str, Enum):
    CREATED = "created"
    SOURCE_READY = "source_ready"
    ANALYZING = "analyzing"
    ANALYSIS_READY = "analysis_ready"
    TIMELINE_READY = "timeline_ready"
    PREVIEW_RENDERING = "preview_rendering"
    PREVIEW_READY = "preview_ready"
    APPROVED = "approved"
    FINAL_RENDERING = "final_rendering"
    COMPLETED = "completed"
    FAILED = "failed"
```

Luồng trạng thái hợp lệ:

```text
CREATED
  → SOURCE_READY
  → ANALYZING
  → ANALYSIS_READY
  → TIMELINE_READY
  → PREVIEW_RENDERING
  → PREVIEW_READY
  → APPROVED
  → FINAL_RENDERING
  → COMPLETED
```

Nếu lỗi:

```text
Bất kỳ trạng thái xử lý nào → FAILED
```

Cho phép retry từ bước lỗi gần nhất.

---

# 10. Database schema

## 10.1 Bảng `projects`

```text
id                  TEXT PRIMARY KEY
name                TEXT NOT NULL
description         TEXT
status              TEXT NOT NULL
source_filename     TEXT
source_path         TEXT
duration_seconds    REAL
width               INTEGER
height              INTEGER
fps                  REAL
created_at           DATETIME
updated_at           DATETIME
```

## 10.2 Bảng `rights_records`

```text
id                  INTEGER PRIMARY KEY AUTOINCREMENT
project_id          TEXT NOT NULL
rights_type         TEXT NOT NULL
confirmed           BOOLEAN NOT NULL
source_url          TEXT
license_file_path   TEXT
notes               TEXT
created_at          DATETIME
```

## 10.3 Bảng `jobs`

```text
id                  TEXT PRIMARY KEY
project_id          TEXT NOT NULL
job_type            TEXT NOT NULL
status              TEXT NOT NULL
progress            INTEGER DEFAULT 0
current_step        TEXT
error_message       TEXT
started_at          DATETIME
finished_at         DATETIME
created_at          DATETIME
```

## 10.4 Bảng `renders`

```text
id                  TEXT PRIMARY KEY
project_id          TEXT NOT NULL
timeline_version    INTEGER NOT NULL
render_type         TEXT NOT NULL
preset_name         TEXT NOT NULL
output_path         TEXT
status              TEXT NOT NULL
created_at          DATETIME
finished_at         DATETIME
```

MVP có thể lưu transcript, scene và timeline bằng JSON thay vì đưa toàn bộ vào SQLite.

---

# 11. Data models

## 11.1 `project.json`

```json
{
  "id": "proj_01HXYZ",
  "name": "Podcast tập 01",
  "description": "Rút gọn podcast thành video 3 phút",
  "status": "analysis_ready",
  "created_at": "2026-01-01T10:00:00Z",
  "updated_at": "2026-01-01T10:20:00Z",
  "source": {
    "filename": "podcast.mp4",
    "path": "source/input.mp4",
    "duration_seconds": 1845.52,
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
    "video_codec": "h264",
    "audio_codec": "aac"
  },
  "rights": {
    "type": "owned",
    "confirmed": true,
    "source_url": null,
    "license_file": null,
    "notes": "Video do người dùng tự quay"
  }
}
```

## 11.2 Transcript schema

```json
{
  "language": "vi",
  "model": "small",
  "duration_seconds": 1845.52,
  "segments": [
    {
      "id": 0,
      "start": 0.42,
      "end": 4.81,
      "text": "Hôm nay chúng ta sẽ nói về...",
      "words": [
        {
          "start": 0.42,
          "end": 0.83,
          "word": "Hôm"
        }
      ],
      "confidence": 0.93
    }
  ]
}
```

Nếu word timestamp làm quá chậm, MVP cho phép chỉ lưu timestamp theo segment.

## 11.3 Scene schema

```json
{
  "detector": "content",
  "threshold": 27.0,
  "scenes": [
    {
      "id": 0,
      "start": 0.0,
      "end": 8.42,
      "duration": 8.42
    }
  ]
}
```

## 11.4 Silence schema

```json
{
  "noise_threshold_db": -35,
  "minimum_duration": 0.6,
  "silences": [
    {
      "start": 13.24,
      "end": 14.81,
      "duration": 1.57
    }
  ]
}
```

## 11.5 Candidate segment schema

```json
{
  "segments": [
    {
      "id": "seg_001",
      "source_start": 8.2,
      "source_end": 21.4,
      "duration": 13.2,
      "text": "Đây là vấn đề quan trọng...",
      "scene_ids": [1, 2],
      "silence_ratio": 0.02,
      "word_count": 38,
      "scores": {
        "relevance": 0.91,
        "information_density": 0.84,
        "hook": 0.76,
        "clarity": 0.88,
        "redundancy": 0.1,
        "overall": 0.85
      },
      "flags": [],
      "reason": "Nêu trực tiếp vấn đề chính"
    }
  ]
}
```

## 11.6 Timeline schema

```json
{
  "id": "timeline_001",
  "project_id": "proj_01HXYZ",
  "version": 1,
  "name": "Tóm tắt 90 giây",
  "mode": "summary",
  "target_duration": 90,
  "actual_duration": 87.4,
  "output_aspect_ratio": "9:16",
  "created_at": "2026-01-01T10:30:00Z",
  "clips": [
    {
      "id": "clip_001",
      "source_start": 8.2,
      "source_end": 21.4,
      "order": 1,
      "enabled": true,
      "label": "hook",
      "reason": "Giới thiệu trực tiếp vấn đề",
      "score": 0.91,
      "transition": "cut"
    },
    {
      "id": "clip_002",
      "source_start": 65.0,
      "source_end": 102.5,
      "order": 2,
      "enabled": true,
      "label": "main_point",
      "reason": "Ý chính số một",
      "score": 0.84,
      "transition": "cut"
    }
  ],
  "render_options": {
    "resolution": "1080x1920",
    "video_codec": "h264",
    "audio_codec": "aac",
    "burn_subtitles": true,
    "normalize_audio": true
  }
}
```

---

# 12. Các màn hình giao diện

## 12.1 Trang Projects

Hiển thị:

- Danh sách project.
- Tên project.
- Trạng thái.
- Ngày tạo.
- Thời lượng video.
- Nút mở.
- Nút xóa.
- Nút tạo project mới.

Form tạo project:

```text
Tên project:               [________________]
Mô tả:                     [________________]
Video đầu vào:             [Chọn file]
Loại quyền sử dụng:        [Dropdown]
URL nguồn:                 [________________]
File giấy phép:            [Chọn file]
Ghi chú:                   [________________]

[ ] Tôi xác nhận có quyền sử dụng video này.

[Tạo project]
```

Validation:

- Chỉ chấp nhận định dạng cấu hình cho phép.
- Tên không được rỗng.
- Phải xác nhận quyền.
- Giới hạn dung lượng mặc định: 10 GB.
- Không dùng trực tiếp tên file người dùng làm đường dẫn.
- Sinh `project_id` và tên file nội bộ an toàn.

## 12.2 Trang Analysis

Hiển thị:

- Metadata video.
- Chọn model Whisper:
  - `tiny`
  - `base`
  - `small`
  - `medium`
- Chọn thiết bị:
  - `auto`
  - `cpu`
  - `cuda`
- Ngôn ngữ:
  - `auto`
  - `vi`
  - `en`
- Scene threshold.
- Silence threshold.
- Minimum silence duration.
- Thanh tiến độ.
- Log bước đang chạy.

Nút:

```text
[Bắt đầu phân tích]
[Hủy]
[Chạy lại bước lỗi]
```

Các bước tiến độ:

```text
1. Kiểm tra media
2. Tạo proxy
3. Tách audio
4. Tạo transcript
5. Phát hiện cảnh
6. Phát hiện im lặng
7. Xây candidate segments
8. Chấm điểm
9. Sinh timeline đề xuất
```

## 12.3 Trang Transcript

Bố cục:

```text
┌───────────────────────────────┬───────────────────────────────┐
│ Video player                  │ Transcript                    │
│                               │                               │
│                               │ [00:00:08] Nội dung câu...    │
│                               │ [00:00:14] Nội dung câu...    │
└───────────────────────────────┴───────────────────────────────┘
```

Yêu cầu:

- Hiển thị timestamp.
- Có ô tìm kiếm transcript.
- Có filter theo từ khóa.
- Click một segment để chọn.
- Có nút thêm segment vào timeline.
- Cho phép sửa text transcript.
- Lưu bản transcript đã sửa riêng, không ghi đè file raw.

MVP không bắt buộc player phải tự seek chính xác khi click timestamp nếu Streamlit component chưa hỗ trợ. Có thể cung cấp nút tạo clip xem nhanh cho segment.

## 12.4 Trang Timeline

Hiển thị danh sách clip dạng bảng:

| Bật | Thứ tự | In | Out | Thời lượng | Nhãn | Điểm | Lý do |
|---|---:|---:|---:|---:|---|---:|---|

Chức năng:

- Thêm clip.
- Xóa clip.
- Bật/tắt clip.
- Chỉnh In/Out.
- Di chuyển lên.
- Di chuyển xuống.
- Đặt nhãn.
- Xem transcript tương ứng.
- Xem tổng thời lượng.
- Lưu version mới.
- Khôi phục version cũ.
- Kiểm tra timeline.

Validation:

- `source_start >= 0`.
- `source_end > source_start`.
- `source_end <= source_duration`.
- Clip tối thiểu 0.5 giây.
- Không cho phép giá trị `NaN`.
- Hiển thị cảnh báo nếu cắt giữa một từ hoặc giữa câu.
- Clip trùng nhau được phép nhưng phải cảnh báo.
- Đổi thứ tự clip được phép nhưng phải cảnh báo nguy cơ sai ngữ cảnh.

## 12.5 Trang Preview & Export

Tùy chọn:

```text
Preset:
- Preview 720p
- YouTube 1080p
- Shorts/TikTok/Reels 1080x1920
- Square 1080x1080

[ ] Burn subtitles
[ ] Chuẩn hóa âm lượng
[ ] Thêm logo
[ ] Giữ âm thanh nguồn
```

Nút:

```text
[Render preview]
[Phê duyệt preview]
[Render final]
```

Không cho render final nếu chưa có:

- Timeline hợp lệ.
- Quyền sử dụng đã xác nhận.
- Preview đã được phê duyệt.

---

# 13. Processing pipeline

## 13.1 Bước 1 — Probe media

Dùng FFprobe lấy:

- Duration.
- Width.
- Height.
- Frame rate.
- Video codec.
- Audio codec.
- Sample rate.
- Số audio stream.
- Rotation metadata.

Ví dụ:

```bash
ffprobe \
  -v quiet \
  -print_format json \
  -show_format \
  -show_streams \
  input.mp4
```

Hàm cần triển khai:

```python
def probe_media(input_path: Path) -> MediaInfo:
    ...
```

Yêu cầu:

- Timeout.
- Kiểm tra return code.
- Parse JSON an toàn.
- Báo lỗi nếu không có video stream.
- Không dựng câu lệnh bằng chuỗi shell không kiểm soát.

## 13.2 Bước 2 — Tạo proxy

Proxy dùng để xem trước và phân tích nhanh:

```text
Resolution: tối đa 1280x720
Video codec: H.264
Audio codec: AAC
FPS: giữ nguyên, tối đa 30
Faststart: bật
```

Ví dụ:

```bash
ffmpeg -y \
  -i input.mp4 \
  -vf "scale='min(1280,iw)':-2" \
  -r 30 \
  -c:v libx264 \
  -preset veryfast \
  -crf 28 \
  -c:a aac \
  -b:a 128k \
  -movflags +faststart \
  proxy_720p.mp4
```

Cần xử lý riêng video dọc để không làm chiều ngang vượt kích thước.

## 13.3 Bước 3 — Tách audio

Whisper input:

```text
Mono
16 kHz
PCM 16-bit
WAV
```

Ví dụ:

```bash
ffmpeg -y \
  -i input.mp4 \
  -vn \
  -ac 1 \
  -ar 16000 \
  -c:a pcm_s16le \
  speech.wav
```

## 13.4 Bước 4 — Transcript

Dùng `faster-whisper`.

Interface:

```python
@dataclass
class TranscriptionOptions:
    model_size: str = "small"
    language: str | None = None
    device: str = "auto"
    compute_type: str = "auto"
    word_timestamps: bool = True


def transcribe_audio(
    audio_path: Path,
    options: TranscriptionOptions
) -> Transcript:
    ...
```

Thiết lập mặc định:

```text
CPU:
  model = small
  compute_type = int8

CUDA:
  model = small
  compute_type = float16
```

Yêu cầu:

- Model chỉ load một lần trong cùng process nếu có thể.
- Cập nhật progress theo số segment.
- Lưu transcript ngay cả khi bước sau bị lỗi.
- Nếu phát hiện ngôn ngữ tự động, lưu language và probability.
- Không gửi audio lên dịch vụ ngoài trong cấu hình mặc định.

## 13.5 Bước 5 — Scene detection

Dùng PySceneDetect `ContentDetector`.

Interface:

```python
def detect_scenes(
    video_path: Path,
    threshold: float = 27.0,
    min_scene_length_frames: int = 15
) -> SceneDetectionResult:
    ...
```

Yêu cầu:

- Dùng proxy để tăng tốc.
- Timestamp phải quy đổi về timeline nguồn.
- Vì proxy giữ nguyên duration nên timestamp có thể dùng trực tiếp.
- Nếu không phát hiện scene, tạo một scene bao phủ toàn bộ video.

## 13.6 Bước 6 — Silence detection

Ví dụ:

```bash
ffmpeg -i speech.wav \
  -af "silencedetect=noise=-35dB:d=0.6" \
  -f null -
```

Parse stderr:

```text
silence_start: 13.24
silence_end: 14.81 | silence_duration: 1.57
```

Interface:

```python
def detect_silences(
    audio_path: Path,
    noise_db: float = -35.0,
    minimum_duration: float = 0.6
) -> SilenceDetectionResult:
    ...
```

Edge cases:

- Silence bắt đầu nhưng không có `silence_end`.
- Video hoàn toàn im lặng.
- Không có audio stream.
- Duration khác nhẹ do audio resampling.

## 13.7 Bước 7 — Xây candidate segments

Nguồn dữ liệu:

- Transcript segments.
- Scene boundaries.
- Silence intervals.

Mục tiêu:

- Không cắt giữa câu nếu tránh được.
- Candidate mặc định dài từ 3–30 giây.
- Ghép các transcript segment liên tiếp nếu quá ngắn.
- Tách khi:
  - Có khoảng im lặng đủ dài.
  - Kết thúc câu.
  - Đổi cảnh gần biên câu.
  - Candidate vượt thời lượng tối đa.

Cấu hình:

```python
@dataclass
class SegmentBuildOptions:
    min_duration: float = 3.0
    preferred_duration: float = 12.0
    max_duration: float = 30.0
    boundary_tolerance: float = 0.5
    sentence_endings: tuple[str, ...] = (".", "?", "!", "。", "？", "！")
```

Thuật toán gợi ý:

```text
1. Duyệt transcript theo thứ tự thời gian.
2. Mở candidate tại start của segment đầu tiên.
3. Cộng transcript cho đến khi:
   - Gặp dấu kết câu và candidate đủ min_duration; hoặc
   - Có silence dài ngay sau transcript; hoặc
   - Candidate đạt max_duration.
4. Điều chỉnh biên gần nhất theo:
   - Transcript boundary.
   - Silence boundary.
   - Scene boundary.
5. Clamp trong [0, source_duration].
6. Loại candidate rỗng.
```

## 13.8 Bước 8 — Chấm điểm candidate

MVP phải chạy được mà không cần API AI bên ngoài.

### Điểm heuristic

Mỗi segment có:

```text
relevance
information_density
hook
clarity
redundancy
overall
```

#### Information density

Dựa trên:

- Số từ trên giây.
- Tỷ lệ thời gian có lời nói.
- Tỷ lệ im lặng.
- Số từ mang nội dung.

Ví dụ chuẩn hóa:

```python
speech_ratio = 1.0 - silence_ratio
word_rate_score = clamp(words_per_second / 3.0, 0.0, 1.0)

information_density = (
    0.6 * speech_ratio +
    0.4 * word_rate_score
)
```

#### Hook score

Tăng điểm khi segment:

- Nằm trong phần đầu video.
- Có câu hỏi.
- Có từ khóa do người dùng nhập.
- Giới thiệu vấn đề trực tiếp.

Không sử dụng clickbait làm tiêu chí bắt buộc.

#### Clarity score

Giảm điểm khi:

- Confidence transcript thấp.
- Câu không hoàn chỉnh.
- Segment bắt đầu hoặc kết thúc giữa từ.
- Silence ratio quá cao.

#### Redundancy score

Dùng TF-IDF hoặc cosine similarity giữa các candidate.

- Nếu segment quá giống một segment điểm cao hơn, tăng redundancy.
- MVP dùng `scikit-learn`.
- Không cần embedding cloud.

#### Overall score

```python
overall = (
    0.30 * relevance +
    0.25 * information_density +
    0.20 * clarity +
    0.15 * hook +
    0.10 * visual_score
    - 0.25 * redundancy
)
```

Clamp về `[0, 1]`.

`visual_score` trong MVP có thể mặc định là `0.5`.

### Relevance score

Người dùng nhập:

- Chủ đề.
- Từ khóa.
- Mục tiêu video.

MVP dùng TF-IDF similarity giữa:

```text
candidate.text
```

và:

```text
topic + keywords + objective
```

Nếu người dùng không nhập mục tiêu:

```text
relevance = 0.5
```

### Tích hợp LLM tùy chọn

Thiết kế interface nhưng không bắt buộc triển khai đầy đủ:

```python
class ContentRanker(Protocol):
    def rank(
        self,
        candidates: list[CandidateSegment],
        objective: EditObjective
    ) -> list[CandidateSegment]:
        ...
```

Implementations:

```text
HeuristicContentRanker
OptionalLLMContentRanker
```

LLM không được tự sửa timestamp tùy ý. Nó chỉ được:

- Gắn nhãn.
- Tóm tắt.
- Chấm mức liên quan.
- Đề xuất lý do giữ/bỏ.

Timestamp cuối cùng phải lấy từ candidate thực tế.

---

# 14. Timeline builder

## 14.1 Chế độ `remove_silence`

Mục đích:

- Giữ nguyên thứ tự nguồn.
- Loại khoảng im lặng dài.
- Không đảo ý.

Quy tắc:

- Chỉ loại silence dài hơn ngưỡng.
- Giữ padding trước/sau câu, mặc định `0.15s`.
- Không tạo clip ngắn hơn `0.5s`.
- Merge clip gần nhau nếu khoảng cách dưới `0.25s`.

## 14.2 Chế độ `shorten`

Mục đích:

- Giữ nguyên thứ tự nguồn.
- Chọn các candidate điểm cao.
- Đạt gần target duration.

Thuật toán:

```text
1. Lọc candidate có overall >= minimum_score.
2. Xếp hạng theo overall.
3. Chọn candidate cho tới gần target duration.
4. Sắp lại candidate đã chọn theo source_start.
5. Loại candidate quá trùng ý.
6. Mở rộng/thu biên để không cắt giữa câu.
7. Tính actual_duration.
```

## 14.3 Chế độ `summary`

Mục đích:

- Tạo cấu trúc:
  - Hook.
  - Context.
  - Main points.
  - Conclusion.

Đảo thứ tự chỉ là đề xuất và phải được cảnh báo trong UI.

Nhãn:

```text
hook
context
main_point
example
conclusion
other
```

Quy tắc:

- Hook tối đa 20% target duration.
- Context tối đa 25%.
- Main points ít nhất 40%.
- Conclusion tối đa 20%.
- Không bắt buộc phải đủ mọi nhãn.
- Không cắt giữa câu.
- Không thay đổi nội dung transcript.

## 14.4 API nội bộ

```python
@dataclass
class EditObjective:
    mode: str
    target_duration: float
    topic: str | None
    keywords: list[str]
    preserve_source_order: bool
    output_aspect_ratio: str


def build_timeline(
    candidates: list[CandidateSegment],
    objective: EditObjective,
    source_duration: float
) -> Timeline:
    ...
```

---

# 15. Kiểm tra timeline

Triển khai:

```python
def validate_timeline(
    timeline: Timeline,
    source_duration: float
) -> TimelineValidationResult:
    ...
```

Kết quả:

```json
{
  "valid": true,
  "errors": [],
  "warnings": [
    {
      "clip_id": "clip_002",
      "code": "POSSIBLE_CONTEXT_CHANGE",
      "message": "Clip đã được đặt trước một đoạn xuất hiện sớm hơn trong nguồn."
    }
  ]
}
```

Error codes:

```text
INVALID_START
INVALID_END
OUT_OF_SOURCE_RANGE
CLIP_TOO_SHORT
EMPTY_TIMELINE
DUPLICATE_CLIP_ID
INVALID_ORDER
```

Warning codes:

```text
OVERLAPPING_SOURCE_CLIPS
POSSIBLE_MID_SENTENCE_CUT
POSSIBLE_CONTEXT_CHANGE
LOW_TRANSCRIPT_CONFIDENCE
HIGH_SILENCE_RATIO
TARGET_DURATION_MISMATCH
```

---

# 16. Render pipeline

## 16.1 Nguyên tắc

Không nối clip bằng `-c copy` khi:

- Clip không nằm trên keyframe.
- Có filter.
- Có caption.
- Đổi resolution.
- Đổi aspect ratio.

MVP nên re-encode để giảm lỗi timestamp.

## 16.2 Quy trình render

```text
Timeline JSON
    ↓
Validate
    ↓
Tạo từng clip tạm
    ↓
Chuẩn hóa resolution/FPS/audio
    ↓
Concat
    ↓
Burn subtitle nếu bật
    ↓
Normalize audio nếu bật
    ↓
Xuất preview/final
```

## 16.3 Tạo clip tạm

Ví dụ:

```bash
ffmpeg -y \
  -ss 8.2 \
  -to 21.4 \
  -i input.mp4 \
  -vf "<video_filter>" \
  -af "<audio_filter>" \
  -c:v libx264 \
  -preset veryfast \
  -crf 23 \
  -c:a aac \
  -b:a 192k \
  clip_001.mp4
```

Với độ chính xác timestamp, đặt `-ss` sau `-i` hoặc kiểm thử phương án seek nhanh kết hợp accurate seek.

## 16.4 Aspect ratio filter

### 16:9

```text
1920x1080
```

### 9:16

```text
1080x1920
```

Mặc định dùng `contain + background` để không cắt mất nội dung:

```text
scale=1080:1920:force_original_aspect_ratio=decrease,
pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black
```

Có thể hỗ trợ background blur ở giai đoạn sau.

Không tự crop khuôn mặt trong MVP.

### 1:1

```text
1080x1080
```

## 16.5 Concat

Tạo file:

```text
file '/absolute/path/clip_001.mp4'
file '/absolute/path/clip_002.mp4'
```

Sau đó:

```bash
ffmpeg -y \
  -f concat \
  -safe 0 \
  -i concat.txt \
  -c copy \
  joined.mp4
```

Chỉ dùng concat copy khi toàn bộ clip đã được chuẩn hóa cùng:

- Codec.
- Resolution.
- FPS.
- Time base.
- Audio sample rate.
- Channel layout.

Nếu không chắc chắn, dùng `concat` filter và re-encode.

## 16.6 Audio normalization

MVP có hai lựa chọn:

### Nhanh

```text
loudnorm=I=-16:LRA=11:TP=-1.5
```

### Hai lượt

Để sau MVP nếu việc parse measurement làm pipeline phức tạp.

Mặc định:

- Web/social video: `-16 LUFS`.
- True peak: `-1.5 dBTP`.

## 16.7 Subtitle

Tạo SRT dựa trên transcript nhưng phải remap timestamp sang timeline đầu ra.

Ví dụ:

- Source clip 1: `8.2 → 21.4`.
- Output clip 1 bắt đầu tại `0.0`.
- Transcript source timestamp `10.0` trở thành output timestamp `1.8`.

Hàm:

```python
def remap_subtitles(
    transcript: Transcript,
    timeline: Timeline
) -> list[SubtitleCue]:
    ...
```

Quy tắc:

- Chỉ giữ cue giao với clip được bật.
- Clamp cue theo biên clip.
- Chuyển source time sang output time.
- Nếu một cue giao nhiều clip, tách cue.
- Không tạo cue có duration <= 0.
- Subtitle tối thiểu 0.3 giây nếu có thể.

Burn subtitle bằng FFmpeg `subtitles` filter.

Phải escape đường dẫn subtitle đúng cách trên Windows.

## 16.8 Preview preset

```json
{
  "name": "preview_720p",
  "max_width": 1280,
  "max_height": 720,
  "video_codec": "libx264",
  "preset": "veryfast",
  "crf": 28,
  "audio_codec": "aac",
  "audio_bitrate": "128k"
}
```

## 16.9 Final preset

```json
{
  "name": "final_1080p",
  "video_codec": "libx264",
  "preset": "medium",
  "crf": 20,
  "audio_codec": "aac",
  "audio_bitrate": "192k",
  "pixel_format": "yuv420p",
  "faststart": true
}
```

## 16.10 Hardware encoding

Để sau MVP hoặc hỗ trợ bằng cấu hình:

```text
libx264
h264_nvenc
h264_qsv
h264_videotoolbox
```

Phải kiểm tra encoder có tồn tại trước khi sử dụng:

```bash
ffmpeg -encoders
```

Luôn fallback về `libx264`.

---

# 17. Job processing

MVP không cần Celery.

Dùng:

- `ThreadPoolExecutor(max_workers=1)` cho orchestration.
- FFmpeg và Whisper chạy qua subprocess/library.
- Trạng thái job ghi vào SQLite.
- UI poll trạng thái định kỳ.

Interface:

```python
class JobService:
    def submit_analysis(self, project_id: str, options: AnalysisOptions) -> str:
        ...

    def submit_preview_render(
        self,
        project_id: str,
        timeline_id: str,
        preset: str
    ) -> str:
        ...

    def submit_final_render(
        self,
        project_id: str,
        timeline_id: str,
        preset: str
    ) -> str:
        ...

    def get_job(self, job_id: str) -> Job:
        ...

    def cancel_job(self, job_id: str) -> None:
        ...
```

Lưu PID của subprocess đang chạy để hỗ trợ hủy.

Khi hủy:

1. Gửi terminate.
2. Chờ timeout.
3. Nếu chưa dừng, kill.
4. Xóa output chưa hoàn tất.
5. Giữ file phân tích đã hoàn thành.

File đang render phải dùng suffix:

```text
.output.mp4.partial
```

Chỉ rename thành `.mp4` sau khi FFmpeg thành công.

---

# 18. Progress reporting

Mỗi bước có trọng số:

```text
Probe:                5%
Proxy:               10%
Audio extraction:     5%
Transcription:       40%
Scene detection:     10%
Silence detection:    5%
Segment building:    10%
Ranking:              5%
Timeline creation:   10%
```

Progress tổng:

```python
overall_progress = completed_weight + current_step_ratio * current_step_weight
```

FFmpeg progress có thể đọc bằng:

```bash
-progress pipe:1
-nostats
```

Parse:

```text
out_time_ms
progress=continue
progress=end
```

---

# 19. Validation và bảo mật local

Dù chạy local vẫn phải xử lý input an toàn.

## 19.1 File validation

- Không tin extension.
- Probe bằng FFprobe.
- Danh sách extension gợi ý:
  - `.mp4`
  - `.mov`
  - `.mkv`
  - `.webm`
  - `.m4v`
- Giới hạn dung lượng cấu hình được.
- Đổi tên file thành UUID.
- Không cho path traversal.
- Không cho ghi file ra ngoài thư mục project.

## 19.2 Subprocess

Không dùng:

```python
subprocess.run(command, shell=True)
```

Dùng:

```python
subprocess.run(
    ["ffmpeg", "-i", str(input_path), ...],
    shell=False,
    check=True,
    capture_output=True,
    text=True
)
```

Có timeout và log stderr.

## 19.3 Xóa project

Khi xóa:

- Hiện hộp thoại xác nhận.
- Xóa record database.
- Xóa thư mục project đã resolve.
- Kiểm tra thư mục phải nằm trong `PROJECTS_ROOT`.
- Không follow symlink ra ngoài.

---

# 20. Cấu hình

File `.env.example`:

```env
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=8501

DATA_DIR=./data
PROJECTS_DIR=./projects
LOG_DIR=./logs

MAX_UPLOAD_GB=10
MAX_CONCURRENT_JOBS=1

FFMPEG_PATH=ffmpeg
FFPROBE_PATH=ffprobe

WHISPER_MODEL=small
WHISPER_DEVICE=auto
WHISPER_COMPUTE_TYPE=auto

DEFAULT_SCENE_THRESHOLD=27.0
DEFAULT_SILENCE_DB=-35
DEFAULT_SILENCE_DURATION=0.6

PREVIEW_CRF=28
FINAL_CRF=20
```

`config/presets.json`:

```json
{
  "preview_720p": {
    "width": 1280,
    "height": 720,
    "video_codec": "libx264",
    "preset": "veryfast",
    "crf": 28,
    "audio_codec": "aac",
    "audio_bitrate": "128k"
  },
  "youtube_1080p": {
    "width": 1920,
    "height": 1080,
    "video_codec": "libx264",
    "preset": "medium",
    "crf": 20,
    "audio_codec": "aac",
    "audio_bitrate": "192k"
  },
  "vertical_1080p": {
    "width": 1080,
    "height": 1920,
    "video_codec": "libx264",
    "preset": "medium",
    "crf": 20,
    "audio_codec": "aac",
    "audio_bitrate": "192k"
  },
  "square_1080p": {
    "width": 1080,
    "height": 1080,
    "video_codec": "libx264",
    "preset": "medium",
    "crf": 20,
    "audio_codec": "aac",
    "audio_bitrate": "192k"
  }
}
```

---

# 21. Python dependencies

`requirements.txt` gợi ý:

```text
streamlit>=1.40,<2.0
pydantic>=2.8,<3.0
pydantic-settings>=2.4,<3.0
sqlalchemy>=2.0,<3.0
faster-whisper>=1.0,<2.0
scenedetect[opencv]>=0.6.4,<0.7
scikit-learn>=1.5,<2.0
numpy>=1.26,<3.0
pandas>=2.2,<3.0
python-dotenv>=1.0,<2.0
filelock>=3.15,<4.0
pytest>=8.0,<9.0
ruff>=0.6
black>=24.0
```

Không dùng MoviePy trong render core nếu FFmpeg đáp ứng đủ, nhằm giảm lỗi và overhead.

---

# 22. Pydantic schemas cơ bản

```python
from pydantic import BaseModel, Field, model_validator


class TimelineClip(BaseModel):
    id: str
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    order: int = Field(ge=1)
    enabled: bool = True
    label: str = "other"
    reason: str = ""
    score: float = Field(default=0.5, ge=0, le=1)
    transition: str = "cut"

    @model_validator(mode="after")
    def validate_range(self):
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class Timeline(BaseModel):
    id: str
    project_id: str
    version: int = Field(ge=1)
    name: str
    mode: str
    target_duration: float = Field(gt=0)
    actual_duration: float = Field(ge=0)
    output_aspect_ratio: str
    clips: list[TimelineClip]
```

---

# 23. Service interfaces

## 23.1 Project service

```python
class ProjectService:
    def create_project(
        self,
        name: str,
        description: str,
        source_file,
        rights: RightsInput
    ) -> Project:
        ...

    def get_project(self, project_id: str) -> Project:
        ...

    def list_projects(self) -> list[Project]:
        ...

    def delete_project(self, project_id: str) -> None:
        ...

    def update_status(
        self,
        project_id: str,
        status: ProjectStatus
    ) -> None:
        ...
```

## 23.2 Analysis service

```python
class AnalysisService:
    def run(
        self,
        project_id: str,
        options: AnalysisOptions,
        progress_callback=None,
        cancellation_token=None
    ) -> AnalysisResult:
        ...
```

Thứ tự gọi:

```python
media_info = probe_media(source)
proxy_path = create_proxy(source)
audio_path = extract_audio(source)
transcript = transcribe_audio(audio_path, options.transcription)
scenes = detect_scenes(proxy_path, options.scene)
silences = detect_silences(audio_path, options.silence)

candidates = build_candidate_segments(
    transcript=transcript,
    scenes=scenes,
    silences=silences,
    source_duration=media_info.duration_seconds
)

ranked = rank_candidates(
    candidates=candidates,
    objective=options.objective
)

timeline = build_timeline(
    candidates=ranked,
    objective=options.objective,
    source_duration=media_info.duration_seconds
)
```

## 23.3 Timeline service

```python
class TimelineService:
    def create_suggested_timeline(...) -> Timeline:
        ...

    def get_current(self, project_id: str) -> Timeline:
        ...

    def save_new_version(
        self,
        project_id: str,
        timeline: Timeline
    ) -> Timeline:
        ...

    def list_versions(self, project_id: str) -> list[TimelineSummary]:
        ...

    def restore_version(
        self,
        project_id: str,
        version: int
    ) -> Timeline:
        ...
```

Mỗi lần lưu chỉnh sửa phải tăng version, không ghi đè version cũ.

## 23.4 Render service

```python
class RenderService:
    def render_preview(
        self,
        project_id: str,
        timeline: Timeline,
        options: RenderOptions,
        progress_callback=None
    ) -> Path:
        ...

    def render_final(
        self,
        project_id: str,
        timeline: Timeline,
        options: RenderOptions,
        progress_callback=None
    ) -> Path:
        ...
```

---

# 24. Logging

Log format:

```text
2026-01-01 10:20:30 | INFO | project=proj_01 | job=job_01 | step=transcribe | message
```

Không log:

- API key.
- Nội dung giấy phép đầy đủ.
- Dữ liệu nhạy cảm không cần thiết.

Mỗi project có pipeline log riêng.

Khi subprocess lỗi, lưu:

- Command đã được sanitize.
- Return code.
- 50–100 dòng stderr cuối.
- Step.
- Input/output path tương đối.

---

# 25. Error handling

Custom exceptions:

```python
class AppError(Exception):
    pass


class DependencyNotFoundError(AppError):
    pass


class InvalidMediaError(AppError):
    pass


class ProjectNotFoundError(AppError):
    pass


class AnalysisError(AppError):
    pass


class TimelineValidationError(AppError):
    pass


class RenderError(AppError):
    pass


class JobCancelledError(AppError):
    pass
```

Thông báo UI phải có:

- Mô tả dễ hiểu.
- Bước bị lỗi.
- Gợi ý xử lý.
- Nút retry nếu phù hợp.

Ví dụ:

```text
Không thể tạo transcript.

Nguyên nhân: máy không đủ bộ nhớ cho model "medium".
Gợi ý: chọn model "small" hoặc "base", sau đó chạy lại bước transcript.
```

---

# 26. Kiểm thử

## 26.1 Unit tests

Bắt buộc kiểm thử:

- Chuyển timestamp.
- Timeline validation.
- Merge candidate.
- Tính silence ratio.
- Ranking clamp `[0, 1]`.
- Timeline không vượt source duration.
- Subtitle timestamp remapping.
- Project path traversal.
- Timeline versioning.

## 26.2 Integration tests

Dùng video fixture ngắn 10–30 giây.

Kiểm thử:

```text
Probe → Audio → Transcript giả lập → Scene → Silence → Timeline → Render
```

Không bắt buộc chạy Whisper thật trong toàn bộ test CI.

Dùng mock:

```python
FakeTranscriber
FakeSceneDetector
FakeContentRanker
```

## 26.3 Render smoke test

Sau khi render:

- Output tồn tại.
- Output size > 0.
- FFprobe đọc được.
- Duration gần timeline duration, tolerance khoảng `0.5s`.
- Có video stream.
- Có audio stream nếu nguồn có audio.

---

# 27. Acceptance criteria MVP

MVP được xem là hoàn thành khi:

1. Người dùng tạo được project bằng giao diện.
2. Không thể phân tích nếu chưa xác nhận quyền sử dụng.
3. Upload video được lưu an toàn trong thư mục project.
4. FFprobe đọc và hiển thị metadata.
5. Ứng dụng tạo được proxy 720p.
6. Ứng dụng tạo được transcript có timestamp.
7. Ứng dụng phát hiện được scene.
8. Ứng dụng phát hiện được silence.
9. Ứng dụng tạo được candidate segments.
10. Ứng dụng đề xuất được timeline theo target duration.
11. Người dùng chỉnh được In/Out và thứ tự clip.
12. Timeline lỗi không được render.
13. Người dùng render được preview 720p.
14. Người dùng xem và phê duyệt preview.
15. Chỉ sau khi duyệt mới render được final.
16. Final có duration gần tổng duration của clip.
17. Có thể burn subtitle.
18. Có thể xuất 16:9 và 9:16.
19. Job lỗi hiển thị thông báo và log.
20. Có ít nhất các unit test quan trọng.

---

# 28. Thứ tự triển khai cho coding agent

Agent phải triển khai theo từng phase, không làm toàn bộ cùng lúc.

## Phase 1 — Foundation

- Tạo project Python.
- Thiết lập config.
- Kiểm tra FFmpeg/FFprobe.
- SQLite.
- Project model.
- Project CRUD.
- Upload an toàn.
- Rights confirmation.

Definition of done:

```text
Có thể tạo, xem và xóa project.
Video được lưu đúng thư mục.
Metadata quyền sử dụng được lưu.
```

## Phase 2 — Basic media pipeline

- FFprobe.
- Proxy.
- Audio extraction.
- Progress.
- Logging.
- Error handling.

Definition of done:

```text
Một video hợp lệ tạo được media_info.json, proxy và speech.wav.
```

## Phase 3 — Analysis

- faster-whisper.
- PySceneDetect.
- Silence detection.
- JSON schemas.
- Cache kết quả.

Definition of done:

```text
Tạo được transcript.json, scenes.json và silences.json.
```

## Phase 4 — Rough-cut planning

- Candidate builder.
- Heuristic ranker.
- Timeline builder.
- Timeline validation.
- Timeline versioning.

Definition of done:

```text
Tạo được current.json với timeline hợp lệ và gần target duration.
```

## Phase 5 — Timeline UI

- Bảng clip.
- Chỉnh In/Out.
- Bật/tắt.
- Di chuyển thứ tự.
- Lưu version.
- Hiển thị warnings.
- Tổng duration.

Definition of done:

```text
Người dùng chỉnh timeline mà không cần sửa JSON thủ công.
```

## Phase 6 — Rendering

- Clip extraction.
- Standardization.
- Concat.
- Preview preset.
- Final preset.
- Aspect ratio.
- Subtitle remapping.
- Audio normalization.

Definition of done:

```text
Render được preview và final có audio/video hợp lệ.
```

## Phase 7 — Hardening

- Tests.
- Cancellation.
- Retry.
- Cleanup temp.
- Documentation.
- Cross-platform path handling.

---

# 29. Quy tắc dành cho coding agent

1. Đọc toàn bộ `SPEC.md` trước khi code.
2. Mỗi phase phải chạy được độc lập.
3. Không tạo tính năng né Content ID hoặc watermark.
4. Không tự động upload lên nền tảng.
5. Không dùng shell command nối chuỗi từ input người dùng.
6. Tất cả path phải dùng `pathlib.Path`.
7. Tất cả JSON phải validate bằng Pydantic.
8. Không ghi đè timeline version cũ.
9. Mỗi bước pipeline phải có cache:
   - Nếu output hợp lệ đã tồn tại, có thể skip.
   - Có tùy chọn force rerun.
10. File output chỉ được xem là hoàn tất sau khi:
    - Process trả code 0.
    - File tồn tại.
    - FFprobe xác minh được.
11. Tạo test cùng lúc với module.
12. Trước khi kết thúc phase, chạy:

```bash
ruff check .
black --check .
pytest
```

13. Nếu yêu cầu trong spec chưa rõ:
    - Chọn giải pháp đơn giản nhất.
    - Ghi quyết định vào `DECISIONS.md`.
    - Không tự thêm chức năng ngoài phạm vi lớn.

---

# 30. Câu lệnh cài đặt

## Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Ứng dụng mặc định mở tại:

```text
http://localhost:8501
```

---

# 31. Startup checks

Khi app khởi động:

1. Kiểm tra thư mục data/projects/logs.
2. Tạo thư mục nếu chưa tồn tại.
3. Kiểm tra `ffmpeg`.
4. Kiểm tra `ffprobe`.
5. Khởi tạo database.
6. Kiểm tra quyền ghi thư mục.
7. Hiển thị cảnh báo nếu không có GPU nhưng không coi là lỗi.
8. Không tải Whisper model cho đến khi người dùng bắt đầu transcript.

Pseudo-code:

```python
def startup_check() -> StartupStatus:
    ensure_directories()
    check_binary("ffmpeg")
    check_binary("ffprobe")
    initialize_database()
    check_write_permissions()
    detect_optional_gpu()
    return status
```

---

# 32. Definition of Done tổng thể

Sản phẩm đạt yêu cầu khi một người dùng không biết câu lệnh FFmpeg có thể:

```text
1. Mở web app local.
2. Tạo project.
3. Chọn video và khai báo quyền sử dụng.
4. Chọn mục tiêu video.
5. Nhấn Phân tích.
6. Nhận timeline đề xuất.
7. Sửa timeline trong giao diện.
8. Render preview.
9. Duyệt preview.
10. Xuất final.
```

Toàn bộ dữ liệu phải nằm trên máy local theo mặc định.

Không có bước nào tự động công bố nội dung hoặc bảo đảm nội dung không bị claim bản quyền.



*******Plan upgrade part 1*******
# PLAN_AUDIO_POSTPRODUCTION.md

# Audio Post-production & Versioned Render Configuration

## 1. Mục tiêu

Mở rộng **Local Video Editor** hiện tại với một bước hậu kỳ âm thanh trước khi xuất video.

Người dùng có thể:

1. Giữ hoặc tắt âm thanh nguồn.
2. Điều chỉnh âm lượng giọng nói/âm thanh nguồn.
3. Upload và chọn một file nhạc nền hợp lệ.
4. Điều chỉnh âm lượng nhạc nền.
5. Chọn cách xử lý khi nhạc ngắn hơn video:
   - Lặp lại nhạc.
   - Dừng khi hết nhạc.
6. Chọn vị trí bắt đầu phát nhạc trên timeline.
7. Chọn vị trí bắt đầu đọc trong file nhạc.
8. Thêm fade in và fade out.
9. Chuẩn hóa âm lượng đầu ra.
10. Render thử một đoạn 10–30 giây.
11. Lưu cấu hình render theo version.
12. Render preview và final bằng cấu hình đã lưu.

Tất cả video và asset âm thanh phải là nội dung người dùng sở hữu hoặc có quyền sử dụng.

---

# 2. Nguyên tắc kiến trúc

Giữ nguyên kiến trúc hiện tại:

```text
Streamlit UI
    ↓
Application Services
    ↓
Media Pipeline
    ↓
FFmpeg / FFprobe
    ↓
Project filesystem + SQLite
```

Không thay đổi sang:

- FastAPI.
- React.
- Celery.
- Redis.
- Microservices.
- Cloud storage.

Không viết lại các module đang hoạt động nếu không cần thiết.

Các chức năng mới phải tích hợp với:

- `services/job_service.py`
- `services/render_service.py`
- `pipeline/renderer.py`
- `pipeline/ffmpeg.py`
- `core/models.py`
- `core/enums.py`
- `config/settings.py`
- `pages/05_export.py`

---

# 3. Phạm vi triển khai

## 3.1 Trong phạm vi

- Upload và quản lý nhạc nền.
- Kiểm tra audio asset bằng FFprobe.
- Metadata và thông tin quyền sử dụng của nhạc.
- Điều chỉnh gain của âm thanh nguồn.
- Điều chỉnh gain của nhạc nền.
- Loop hoặc không loop nhạc.
- Offset trong file nhạc.
- Vị trí bắt đầu nhạc trên output timeline.
- Fade in/fade out.
- Mix source audio với music.
- Chuẩn hóa loudness một lượt.
- Preview thử 10–30 giây.
- Cấu hình render có version.
- Background render job.
- Cancellation.
- Progress reporting.
- `.partial` output.
- FFprobe validation.
- Automated QC cơ bản.
- Unit test và integration test.

## 3.2 Ngoài phạm vi của plan này

Chưa triển khai:

- Music ducking.
- Sidechain compression.
- Voice isolation.
- Speech enhancement bằng AI.
- Per-clip audio gain.
- Audio keyframe.
- Equalizer nhiều band.
- Noise reduction nâng cao.
- Subtitle burn-in.
- Logo.
- B-roll.
- Intro/outro.
- Crossfade giữa các clip.
- Smart reframing.
- Tự động upload lên nền tảng.
- Tính năng né Content ID hoặc watermark.

Các chức năng ngoài phạm vi không được agent tự ý bổ sung.

---

# 4. Hiện trạng cần bảo toàn

Hệ thống hiện có:

- Streamlit multipage.
- SQLite/SQLAlchemy.
- `Project`, `RightsRecord`, `Job`, `Render`.
- Upload video local.
- Tải video từ URL bằng yt-dlp.
- faster-whisper.
- Scene detection.
- Silence detection.
- Candidate ranking.
- Suggested timeline.
- Timeline version.
- Background job.
- Cancellation token.
- Stage-skip.
- Transcription checkpoint.
- Render từng clip.
- Concat clip.
- `.partial` rồi atomic rename.
- FFmpeg progress parser.
- Encoder auto-selection.
- Disk-space validation.

Không được làm hỏng các luồng hiện tại:

```text
Create project
→ Analysis
→ Timeline
→ Preview
→ Final
```

Video không dùng nhạc nền phải tiếp tục render được như trước.

---

# 5. Trải nghiệm người dùng mục tiêu

Trang Export được chia thành các nhóm.

## 5.1 Preset

```text
Preset đầu ra:
- Preview 720p
- YouTube 1080p
- Vertical 1080x1920
- Square 1080x1080
```

## 5.2 Source Audio / Voice

```text
▼ Âm thanh nguồn

[✓] Giữ âm thanh nguồn

Âm lượng:
[────────●──] 100%
Tương đương: 0.0 dB

[✓] Chuẩn hóa âm lượng đầu ra
Target loudness: -16 LUFS
True peak:       -1.5 dBTP
```

## 5.3 Background Music

```text
▼ Nhạc nền

[✓] Thêm nhạc nền

Asset:
[Chọn nhạc đã upload ▼] [Upload nhạc mới]

Tên gốc: background.mp3
Thời lượng: 03:42
Nguồn/license: ...

Âm lượng:
[──●────────] 15%
Tương đương: khoảng -16.5 dB

Nhạc bắt đầu tại output:
[00:00:00.000]

Bắt đầu đọc từ file nhạc:
[00:00:00.000]

Nếu nhạc ngắn hơn video:
○ Lặp lại
○ Dừng khi hết

Fade in:
[2.0] giây

Fade out:
[3.0] giây
```

## 5.4 Test Preview

```text
▼ Kiểm tra nhanh

Bắt đầu tại output:
[00:00:20.000]

Thời lượng:
[20] giây

[Render test preview]
```

## 5.5 Render

```text
[Lưu cấu hình]
[Render Preview 720p]
[Render Final]
[Hủy job]
```

Không cho render nếu cấu hình không hợp lệ.

---

# 6. Quy ước âm lượng

## 6.1 Nguồn chân lý

Trong schema và FFmpeg, lưu âm lượng bằng **dB**.

UI có thể hiển thị:

- Phần trăm.
- dB tương ứng.

Không lưu đồng thời cả percent và dB để tránh hai giá trị không đồng nhất.

## 6.2 Chuyển percent sang dB

```python
import math


def percent_to_db(percent: float, floor_db: float = -60.0) -> float:
    if percent <= 0:
        return floor_db

    return max(
        floor_db,
        20.0 * math.log10(percent / 100.0),
    )
```

Ví dụ:

| Phần trăm | Gain gần đúng |
|---:|---:|
| 0% | mute hoặc `-60 dB` |
| 10% | `-20.0 dB` |
| 15% | `-16.5 dB` |
| 25% | `-12.0 dB` |
| 50% | `-6.0 dB` |
| 75% | `-2.5 dB` |
| 100% | `0.0 dB` |
| 150% | `+3.5 dB` |
| 200% | `+6.0 dB` |

## 6.3 Quy tắc mute

Không xem `-60 dB` là mute tuyệt đối.

Nếu người dùng chọn `0%`, schema hoặc UI phải chuyển thành:

```text
enabled = false
```

hoặc renderer dùng:

```text
volume=0
```

## 6.4 Giới hạn UI

Source audio:

```text
0% đến 200%
-60 dB đến +6 dB
```

Music:

```text
0% đến 100%
-60 dB đến 0 dB
```

Schema có thể cho music tối đa `+6 dB`, nhưng UI mặc định không cần mở mức này.

---

# 7. Cấu trúc thư mục mới

Trong mỗi project:

```text
projects/<project_id>/
├── assets/
│   └── music/
│       ├── manifest.json
│       ├── music_<uuid>.mp3
│       └── music_<uuid>.wav
│
├── render_configs/
│   ├── render_config_v1.json
│   ├── render_config_v2.json
│   └── current.json
│
├── previews/
│   ├── preview_v1.mp4
│   └── test_preview_<id>.mp4
│
├── exports/
│   └── final_v1.mp4
│
└── temp/
```

Không sử dụng tên file do người dùng cung cấp làm tên file nội bộ.

---

# 8. Module mới

Tạo các module sau nếu chưa tồn tại:

```text
core/render_schemas.py
pipeline/audio_mix.py
pipeline/output_validator.py
services/asset_service.py
services/render_config_service.py
utils/json_io.py
utils/paths.py
utils/audio.py
```

Có thể dùng tên module khác nếu phù hợp với convention hiện tại, nhưng phải ghi quyết định vào `DECISIONS.md`.

---

# 9. Data models

## 9.1 Music asset

```python
from datetime import datetime
from pydantic import BaseModel, Field


class MusicAsset(BaseModel):
    id: str
    project_id: str

    stored_filename: str
    original_filename: str
    relative_path: str

    duration_seconds: float = Field(gt=0)
    codec_name: str | None = None
    sample_rate: int | None = None
    channels: int | None = None

    rights_type: str
    rights_confirmed: bool
    source_url: str | None = None
    license_file_path: str | None = None
    rights_notes: str | None = None

    created_at: datetime
```

## 9.2 Voice options

```python
class VoiceOptions(BaseModel):
    enabled: bool = True

    gain_db: float = Field(
        default=0.0,
        ge=-60.0,
        le=12.0,
    )
```

Không thêm EQ hoặc noise reduction trong phase này.

## 9.3 Music options

```python
from typing import Literal


class MusicOptions(BaseModel):
    enabled: bool = False

    asset_id: str | None = None

    gain_db: float = Field(
        default=-16.5,
        ge=-60.0,
        le=6.0,
    )

    loop: bool = True

    source_offset_seconds: float = Field(
        default=0.0,
        ge=0.0,
    )

    timeline_start_seconds: float = Field(
        default=0.0,
        ge=0.0,
    )

    fade_in_seconds: float = Field(
        default=2.0,
        ge=0.0,
        le=30.0,
    )

    fade_out_seconds: float = Field(
        default=3.0,
        ge=0.0,
        le=30.0,
    )

    short_music_mode: Literal["loop", "stop"] = "loop"
```

Không lưu asset path trực tiếp trong `MusicOptions`.

Renderer phải nhận `asset_id`, sau đó resolve qua `AssetService`.

## 9.4 Loudness options

```python
class LoudnessOptions(BaseModel):
    enabled: bool = True

    target_lufs: float = Field(
        default=-16.0,
        ge=-24.0,
        le=-9.0,
    )

    loudness_range: float = Field(
        default=11.0,
        ge=1.0,
        le=20.0,
    )

    true_peak_db: float = Field(
        default=-1.5,
        ge=-6.0,
        le=0.0,
    )
```

## 9.5 Render configuration

```python
from datetime import datetime


class RenderConfiguration(BaseModel):
    id: str
    project_id: str
    version: int = Field(ge=1)

    timeline_version: int = Field(ge=1)
    preset_name: str

    voice: VoiceOptions
    music: MusicOptions
    loudness: LoudnessOptions

    created_at: datetime
    updated_at: datetime
```

## 9.6 Test preview options

```python
class TestPreviewOptions(BaseModel):
    output_start_seconds: float = Field(
        default=0.0,
        ge=0.0,
    )

    duration_seconds: float = Field(
        default=20.0,
        ge=10.0,
        le=30.0,
    )
```

## 9.7 Audio mix request

```python
class AudioMixRequest(BaseModel):
    joined_video_path: str
    output_path: str

    output_duration_seconds: float = Field(gt=0)
    output_timeline_offset_seconds: float = Field(default=0.0, ge=0)

    source_has_audio: bool

    voice: VoiceOptions
    music: MusicOptions
    loudness: LoudnessOptions
```

Path fields trong request nội bộ phải được service tạo, không lấy trực tiếp từ UI.

---

# 10. Music asset manifest

Lưu tại:

```text
projects/<project_id>/assets/music/manifest.json
```

Schema:

```json
{
  "version": 1,
  "assets": [
    {
      "id": "music_9d21c8fa",
      "project_id": "proj_abcd1234",
      "stored_filename": "music_9d21c8fa.mp3",
      "original_filename": "background.mp3",
      "relative_path": "assets/music/music_9d21c8fa.mp3",
      "duration_seconds": 222.4,
      "codec_name": "mp3",
      "sample_rate": 44100,
      "channels": 2,
      "rights_type": "licensed",
      "rights_confirmed": true,
      "source_url": "https://example.com/asset",
      "license_file_path": "license/music_9d21c8fa.pdf",
      "rights_notes": "Commercial license",
      "created_at": "2026-01-01T10:00:00Z"
    }
  ]
}
```

Manifest phải được ghi atomic.

---

# 11. AssetService

Tạo:

```python
class AssetService:
    def upload_music(
        self,
        project_id: str,
        uploaded_file,
        rights_input: MusicRightsInput,
    ) -> MusicAsset:
        ...

    def get_music_asset(
        self,
        project_id: str,
        asset_id: str,
    ) -> MusicAsset:
        ...

    def list_music_assets(
        self,
        project_id: str,
    ) -> list[MusicAsset]:
        ...

    def delete_music_asset(
        self,
        project_id: str,
        asset_id: str,
    ) -> None:
        ...

    def resolve_music_path(
        self,
        project_id: str,
        asset_id: str,
    ) -> Path:
        ...
```

## 11.1 Upload validation

Cho phép cấu hình các extension:

```text
.mp3
.wav
.m4a
.aac
.flac
.ogg
.opus
```

Không tin extension.

Sau khi lưu file tạm:

1. Gọi FFprobe.
2. Xác nhận tồn tại audio stream.
3. Lấy duration.
4. Từ chối nếu duration bằng 0 hoặc không hữu hạn.
5. Từ chối nếu không có audio stream.
6. Từ chối nếu FFprobe không đọc được.
7. Rename sang filename nội bộ sau khi validate thành công.
8. Ghi manifest atomic.

## 11.2 Rights gate

Form upload nhạc phải yêu cầu:

```text
Loại quyền:
- owned
- licensed
- public_domain
- creative_commons

[ ] Tôi xác nhận có quyền sử dụng file âm thanh này.
```

Không cho sử dụng asset nếu:

```text
rights_confirmed != true
```

AssetService không tự quyết định tính hợp pháp; chỉ lưu hồ sơ do người dùng khai báo.

## 11.3 Path safety

Không tin:

- `relative_path` từ UI.
- `asset_path` trong request.
- Filename gốc.

Quy trình resolve:

```python
root = get_project_dir(project_id).resolve()
music_root = (root / "assets" / "music").resolve()
asset_path = (music_root / asset.stored_filename).resolve()

if not asset_path.is_relative_to(music_root):
    raise InvalidMediaError("Unsafe asset path")
```

Không follow symlink ra ngoài project.

---

# 12. RenderConfigService

Tạo:

```python
class RenderConfigService:
    def get_current(
        self,
        project_id: str,
    ) -> RenderConfiguration | None:
        ...

    def save_new_version(
        self,
        project_id: str,
        configuration: RenderConfiguration,
    ) -> RenderConfiguration:
        ...

    def get_version(
        self,
        project_id: str,
        version: int,
    ) -> RenderConfiguration:
        ...

    def list_versions(
        self,
        project_id: str,
    ) -> list[RenderConfiguration]:
        ...

    def restore_version(
        self,
        project_id: str,
        version: int,
    ) -> RenderConfiguration:
        ...
```

## 12.1 Versioning

Khi lưu:

1. Resolve project directory an toàn.
2. Đọc version hiện tại.
3. Sinh version tiếp theo.
4. Validate toàn bộ config bằng Pydantic.
5. Xác nhận timeline version tồn tại.
6. Nếu music bật:
   - `asset_id` không được rỗng.
   - Asset phải tồn tại.
   - Asset phải có quyền được xác nhận.
7. Ghi `render_config_vN.json`.
8. Atomic replace `current.json`.
9. Không ghi đè version cũ.

## 12.2 Atomic JSON

Triển khai helper:

```python
def atomic_write_json(
    path: Path,
    data: dict,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary_path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    temporary_path.replace(path)
```

Khi đọc:

```python
def read_json(
    path: Path,
) -> dict:
    ...
```

Phải báo lỗi rõ ràng nếu JSON hỏng.

---

# 13. Database changes

## 13.1 Render table

Nếu phù hợp với model hiện tại, thêm:

```text
render_config_version INTEGER NULL
error_message         TEXT NULL
started_at            DATETIME NULL
finished_at           DATETIME NULL
```

Render record phải ghi:

- Project ID.
- Timeline version.
- Render config version.
- Render type.
- Preset.
- Output path.
- Status.
- Error message.
- Start/end time.

## 13.2 Migration

`Base.metadata.create_all()` không thêm cột vào bảng đã tồn tại.

Agent phải chọn một trong hai hướng:

### Hướng ưu tiên

Thêm migration tối giản, idempotent cho SQLite:

```text
PRAGMA table_info(renders)
```

Nếu thiếu cột:

```sql
ALTER TABLE renders ADD COLUMN render_config_version INTEGER;
ALTER TABLE renders ADD COLUMN error_message TEXT;
ALTER TABLE renders ADD COLUMN started_at DATETIME;
ALTER TABLE renders ADD COLUMN finished_at DATETIME;
```

Migration phải:

- Chạy nhiều lần an toàn.
- Không xóa dữ liệu.
- Có test.
- Log cột nào được thêm.

### Hướng thay thế

Nếu repository đã có Alembic thì dùng Alembic.

Không thêm Alembic chỉ cho một thay đổi nếu codebase hiện chưa sử dụng nó, trừ khi có lý do rõ ràng.

Ghi quyết định vào `DECISIONS.md`.

---

# 14. Validation cấu hình render

Triển khai:

```python
def validate_render_configuration(
    project_id: str,
    configuration: RenderConfiguration,
    timeline_duration: float,
    asset_service: AssetService,
) -> list[str]:
    ...
```

Phải kiểm tra:

- Preset tồn tại.
- Timeline version tồn tại.
- Timeline có ít nhất một clip enabled.
- Timeline duration hữu hạn và lớn hơn 0.
- Voice gain hữu hạn.
- Music gain hữu hạn.
- Fade duration hữu hạn.
- Music timeline start không âm.
- Music source offset không âm.
- Loudness values hợp lệ.
- Nếu music enabled thì asset tồn tại.
- Asset có audio stream.
- Asset có rights confirmation.
- Không nhận path trực tiếp từ UI.
- Test preview không vượt duration timeline.

Quy tắc fade:

```text
fade_in_seconds <= thời lượng nhạc thực tế xuất hiện
fade_out_seconds <= thời lượng nhạc thực tế xuất hiện
```

Nếu tổng fade dài hơn đoạn nhạc xuất hiện:

```text
Tự clamp fade in/out theo tỷ lệ
hoặc báo validation warning.
```

Hướng khuyên dùng:

```text
Clamp mỗi fade tối đa bằng 50% thời lượng nhạc xuất hiện.
```

Phải ghi warning vào log.

---

# 15. Render pipeline mới

## 15.1 Luồng tổng thể

```text
Timeline
    ↓
Validate timeline
    ↓
Render source clips
    ↓
Normalize clip video/audio streams
    ↓
Concat thành joined.mp4
    ↓
Probe joined.mp4
    ↓
Global audio post-processing
    ↓
Write output.mp4.partial
    ↓
FFprobe validation
    ↓
Automated QC
    ↓
Atomic rename output.mp4
```

## 15.2 Nguyên tắc quan trọng

Nhạc nền chỉ được mix **sau khi concat timeline**.

Không mix nhạc vào từng clip riêng.

Sai:

```text
clip 1 + music từ đầu
clip 2 + music từ đầu
clip 3 + music từ đầu
```

Đúng:

```text
clip 1 + clip 2 + clip 3
            ↓
        joined video
            ↓
     mix music một lần
```

Điều này bảo đảm nhạc không restart tại mỗi điểm cắt.

---

# 16. Các trường hợp audio phải hỗ trợ

## 16.1 Có source audio, không có music

```text
Joined source audio
    ↓
Voice gain
    ↓
Optional loudnorm
    ↓
Final audio
```

## 16.2 Có source audio và có music

```text
Source audio → voice gain ───────┐
                                 ├→ amix → loudnorm → final
Music → trim/loop/fade/gain ─────┘
```

## 16.3 Không có source audio, có music

```text
Music
    ↓
trim/loop
    ↓
fade
    ↓
gain
    ↓
optional loudnorm
    ↓
final audio
```

## 16.4 Source audio bị tắt, có music

Xử lý giống music-only.

## 16.5 Không có source audio và không có music

Output là video-only.

Không tạo audio stream giả nếu không cần thiết.

## 16.6 Có source audio nhưng source audio bị tắt, không music

Output là video-only.

---

# 17. Chuẩn hóa clip trước concat

Các clip phải cùng:

- Video codec.
- Resolution.
- Frame rate.
- Pixel format.
- Time base.
- Audio codec nếu có audio.
- Audio sample rate.
- Audio channel layout.

Preset cơ bản:

```text
Video:
- H.264
- yuv420p
- CFR theo output preset

Audio:
- AAC
- 48 kHz
- Stereo
```

Vì tất cả clip trong timeline hiện lấy từ cùng một source, trạng thái audio của clip phải đồng nhất:

```text
Tất cả có source audio
hoặc
Tất cả video-only
```

Nếu source không có audio, lệnh render clip không được map audio bắt buộc.

Phải dùng optional mapping hoặc probe trước:

```text
-map 0:v:0
-map 0:a:0?
```

---

# 18. Audio post-processing API

Tạo trong:

```text
pipeline/audio_mix.py
```

Interface:

```python
@dataclass
class AudioPostProcessResult:
    output_path: Path
    duration_seconds: float
    has_audio: bool


def apply_global_audio_mix(
    joined_video_path: Path,
    output_path: Path,
    project_id: str,
    configuration: RenderConfiguration,
    output_duration_seconds: float,
    output_timeline_offset_seconds: float = 0.0,
    progress_callback=None,
    cancellation_token=None,
) -> AudioPostProcessResult:
    ...
```

Function phải:

1. Probe joined video.
2. Xác định có source audio hay không.
3. Resolve music asset qua `AssetService`.
4. Xây input arguments.
5. Xây filter graph dựa trên trường hợp thực tế.
6. Dùng configured FFmpeg path.
7. Dùng `run_ffmpeg_with_progress`.
8. Tôn trọng cancellation token.
9. Ghi vào `.partial`.
10. Validate output.
11. Không tự rename final nếu function được gọi bên trong renderer cấp cao.

---

# 19. Chuẩn bị music input

## 19.1 Loop

Nếu `loop = true`:

```bash
-stream_loop -1 -i music.mp3
```

Luôn `atrim` theo thời lượng music thực sự cần trong output.

Không để music kéo dài output.

## 19.2 Không loop

Nếu `loop = false`:

- Music phát đến khi hết.
- Phần video còn lại chỉ có source audio hoặc im lặng.
- Final duration vẫn theo video.

`amix` phải dùng:

```text
duration=first
```

Trong đó input đầu tiên phải là audio bed có duration bằng video khi cần.

Nếu chỉ có music và music ngắn hơn video, output audio có thể ngắn hơn video nhưng output container vẫn phải giữ video đủ duration.

Nên sử dụng:

```text
-shortest
```

một cách thận trọng; không dùng `-shortest` nếu nó có thể làm video bị cắt theo nhạc.

Quy tắc bắt buộc:

```text
Video stream quyết định final duration.
Music không bao giờ quyết định final duration.
```

## 19.3 Source offset

`source_offset_seconds` là vị trí bắt đầu đọc trong file nhạc.

Có thể dùng input seek:

```bash
-ss <source_offset> -i music.mp3
```

Nếu loop được bật và offset lớn hơn music duration:

```python
effective_offset = source_offset % music_duration
```

Nếu loop tắt và offset lớn hơn hoặc bằng duration:

```text
Validation error.
```

## 19.4 Timeline start

`timeline_start_seconds` là thời điểm music bắt đầu trên output.

Dùng:

```text
adelay=<milliseconds>:all=1
```

hoặc tạo audio bed rồi overlay/mix đúng timestamp.

Ví dụ:

```text
timeline_start_seconds = 5.0
```

Music bắt đầu ở giây thứ 5 của output.

---

# 20. Fade logic

## 20.1 Fade in

Music fade in bắt đầu từ thời điểm music bắt đầu phát:

```text
afade=t=in:st=0:d=<fade_in>
```

Fade được áp dụng trước `adelay`.

## 20.2 Fade out

Fade out phải kết thúc tại điểm music dừng trong output.

Tính:

```python
music_visible_duration = min(
    available_music_duration,
    output_duration - timeline_start,
)
```

Nếu loop:

```python
music_visible_duration = output_duration - timeline_start
```

Điểm bắt đầu fade out:

```python
fade_out_start = max(
    0,
    music_visible_duration - fade_out_seconds,
)
```

Filter:

```text
afade=t=out:st=<fade_out_start>:d=<fade_out_seconds>
```

## 20.3 Test preview không được tạo fade out giả

Nếu test preview chỉ là một đoạn ở giữa video, không tự fade out tại cuối preview trừ khi:

- Preview chứa đoạn cuối thật của output.
- Hoặc người dùng yêu cầu preview fade riêng.

Mặc định:

```text
Fade được tính theo timeline đầy đủ, không theo chiều dài file test preview.
```

---

# 21. Filter graph mẫu

Các lệnh dưới đây chỉ là định hướng. Agent phải xây argument list và filter graph an toàn, không dùng `shell=True`.

## 21.1 Source audio only

```text
[0:a:0]volume=<voice_gain_db>dB[voice];
[voice]loudnorm=I=-16:LRA=11:TP=-1.5[aout]
```

Nếu loudnorm tắt:

```text
[0:a:0]volume=<voice_gain_db>dB[aout]
```

## 21.2 Music only

```text
[1:a:0]
atrim=duration=<music_visible_duration>,
asetpts=PTS-STARTPTS,
volume=<music_gain_db>dB,
afade=t=in:st=0:d=<fade_in>,
afade=t=out:st=<fade_out_start>:d=<fade_out>,
adelay=<timeline_start_ms>:all=1
[music]
```

Nếu cần audio đủ timeline duration, có thể pad:

```text
apad=whole_dur=<output_duration>
```

Sau đó trim:

```text
atrim=duration=<output_duration>
```

## 21.3 Source audio + music

```text
[0:a:0]
volume=<voice_gain_db>dB
[voice];

[1:a:0]
atrim=duration=<music_visible_duration>,
asetpts=PTS-STARTPTS,
volume=<music_gain_db>dB,
afade=t=in:st=0:d=<fade_in>,
afade=t=out:st=<fade_out_start>:d=<fade_out>,
adelay=<timeline_start_ms>:all=1
[music];

[voice][music]
amix=inputs=2:duration=first:dropout_transition=0:normalize=0
[mixed];

[mixed]
loudnorm=I=-16:LRA=11:TP=-1.5
[aout]
```

Nếu loudnorm tắt:

```text
[mixed]anull[aout]
```

## 21.4 Mapping

Video không cần encode lại trong audio post-process nếu container/codec cho phép:

```text
-map 0:v:0
-map "[aout]"
-c:v copy
-c:a aac
-b:a 192k
```

Nếu không có audio:

```text
-map 0:v:0
-an
-c:v copy
```

Luôn đặt:

```text
-movflags +faststart
```

cho MP4 final.

---

# 22. Loudness normalization

Phase này dùng one-pass:

```text
loudnorm=I=-16:LRA=11:TP=-1.5
```

Không triển khai two-pass trong task này.

Mặc định:

```text
Target integrated loudness: -16 LUFS
Loudness range:             11 LU
True peak:                  -1.5 dBTP
```

Loudnorm được áp dụng:

```text
sau gain
sau music mix
trước encode AAC
```

Không áp dụng loudnorm riêng cho voice và music trong phase này.

---

# 23. Test preview 10–30 giây

## 23.1 Mục tiêu

Người dùng thử nhanh:

- Voice volume.
- Music volume.
- Music offset.
- Timeline start.
- Fade.
- Loudness.

Không cần render toàn video.

## 23.2 Preview được tính theo output timeline

Input:

```text
output_start_seconds
duration_seconds
```

Ví dụ:

```text
Start:    20 giây
Duration: 20 giây
```

Có nghĩa là preview output timeline từ:

```text
00:20 → 00:40
```

Không phải timestamp source video.

## 23.3 Tạo derived timeline

Tạo helper:

```python
def slice_timeline_by_output_range(
    timeline: Timeline,
    output_start: float,
    duration: float,
) -> Timeline:
    ...
```

Thuật toán:

1. Duyệt các clip enabled theo `order`.
2. Tính output start/end của từng clip.
3. Tìm giao với preview range.
4. Chuyển giao điểm về `source_start/source_end`.
5. Tạo derived timeline tạm.
6. Không lưu derived timeline thành timeline version chính thức.
7. Validate derived timeline.
8. Render bằng pipeline bình thường.

## 23.4 Music alignment trong test preview

Music phải phản ánh đúng vị trí trong full output.

Truyền:

```text
output_timeline_offset_seconds = preview_start
```

Ví dụ:

```text
Full timeline preview start = 20s
Music starts at full timeline = 5s
```

Music trong test preview phải bắt đầu từ vị trí tương đương:

```text
15 giây sau thời điểm music bắt đầu
```

Nếu loop:

```python
music_playhead = (
    source_offset
    + preview_start
    - timeline_start
) % music_duration
```

Nếu music chưa bắt đầu tại preview range:

- Chèn đúng phần silence đầu.
- Music bắt đầu tại thời điểm tương ứng trong preview.

Nếu music đã kết thúc và loop tắt:

- Không có music trong test preview.

## 23.5 Output

```text
previews/test_preview_<uuid>.mp4
```

Không ghi đè preview chính.

Có thể xóa các test preview cũ theo policy cấu hình, nhưng không làm trong cùng transaction render.

---

# 24. RenderService changes

Refactor `RenderService` nhưng giữ public API tương thích nếu có thể.

Interface đề xuất:

```python
class RenderService:
    def render_test_preview(
        self,
        project_id: str,
        timeline_version: int,
        render_config_version: int,
        test_options: TestPreviewOptions,
        progress_callback=None,
        cancellation_token=None,
    ) -> Path:
        ...

    def render_preview(
        self,
        project_id: str,
        timeline_version: int,
        render_config_version: int,
        progress_callback=None,
        cancellation_token=None,
    ) -> Path:
        ...

    def render_final(
        self,
        project_id: str,
        timeline_version: int,
        render_config_version: int,
        progress_callback=None,
        cancellation_token=None,
    ) -> Path:
        ...
```

## 24.1 Render record lifecycle

Tạo Render record trước khi bắt đầu:

```text
PENDING
→ RUNNING
→ COMPLETED
```

Nếu lỗi:

```text
RUNNING
→ FAILED
```

Nếu hủy:

```text
RUNNING
→ CANCELLED
```

Record phải tồn tại kể cả khi render thất bại.

## 24.2 Output naming

Không hardcode luôn `preview_v1.mp4` hoặc `final_v1.mp4`.

Sinh version tiếp theo:

```text
previews/preview_v1.mp4
previews/preview_v2.mp4
exports/final_v1.mp4
exports/final_v2.mp4
```

Mỗi output liên kết với:

- Timeline version.
- Render config version.
- Preset.
- Render record ID.

## 24.3 Partial file

Trong quá trình render:

```text
preview_v2.mp4.partial
```

Tuy nhiên FFmpeg cần biết muxer khi extension cuối là `.partial`.

Phải chỉ định:

```text
-f mp4
```

hoặc dùng tên:

```text
preview_v2.partial.mp4
```

Khuyên dùng:

```text
preview_v2.partial.mp4
```

Sau thành công:

```python
partial_path.replace(final_path)
```

Khi lỗi/hủy:

```text
Xóa partial.
Không xóa output cũ đã hoàn tất.
```

---

# 25. JobService integration

Tạo hoặc mở rộng job type:

```text
RENDER_TEST_PREVIEW
RENDER_PREVIEW
RENDER_FINAL
```

Nếu không muốn thêm enum test preview riêng, có thể dùng metadata job, nhưng quyết định phải ghi trong `DECISIONS.md`.

Job payload phải lưu hoặc có thể phục hồi:

```json
{
  "project_id": "proj_001",
  "timeline_version": 3,
  "render_config_version": 2,
  "render_type": "test_preview",
  "test_preview": {
    "output_start_seconds": 20,
    "duration_seconds": 20
  }
}
```

Nếu bảng jobs chưa có payload, có thể:

- Thêm `payload_json` vào DB.
- Hoặc lưu payload tại project job metadata.

Hướng khuyên dùng:

```text
Thêm payload_json TEXT NULL vào bảng jobs.
```

Migration phải idempotent.

Worker không được gọi API Streamlit.

UI chỉ:

1. Submit job.
2. Poll `JobService`.
3. Hiển thị progress.
4. Gửi cancel request.

---

# 26. Progress reporting

Trọng số render đề xuất:

```text
Validate inputs:             5%
Prepare derived timeline:    5%
Render timeline clips:      45%
Concat clips:               10%
Apply global audio mix:     25%
Validate output:             5%
Automated QC:                5%
```

Trong bước render clips:

```python
clip_progress = completed_clips / total_clips
```

Trong FFmpeg audio mix:

- Dùng `run_ffmpeg_with_progress`.
- Progress tính theo duration output.
- Không kill FFmpeg ngay khi nhận `progress=end`.
- Chờ process thoát và kiểm tra return code.

---

# 27. Cancellation

Cancellation phải hoạt động ở:

- Render clip.
- Concat.
- Audio mix.
- Output validation nếu có subprocess dài.

Quy trình:

1. Cancellation token được set.
2. Nếu subprocess đang chạy:
   - `terminate()`.
   - Chờ timeout.
   - `kill()` nếu chưa thoát.
3. Drain stdout/stderr.
4. Xóa `.partial`.
5. Xóa temp clip của job.
6. Giữ source, timeline, config và output hoàn tất trước đó.
7. Job chuyển `CANCELLED`.
8. Render record chuyển `CANCELLED`.

Không đánh dấu project là `FAILED` chỉ vì người dùng chủ động cancel.

---

# 28. Disk-space check

Trước render, ước tính:

```text
source clip temp
+ joined video
+ audio-mixed output
+ safety margin
```

MVP có thể dùng:

```python
required_bytes = max(
    input_size * 2.5,
    estimated_output_size * 3.0,
)
```

Gọi:

```python
ensure_free_space(...)
```

trước job.

Nếu thiếu dung lượng:

```text
Raise InsufficientDiskSpaceError
```

Không tạo Render record `RUNNING` nếu chưa vượt qua preflight, hoặc tạo record `FAILED` với thông báo rõ ràng. Chọn một cách và áp dụng nhất quán.

---

# 29. Output validation

Tạo:

```text
pipeline/output_validator.py
```

Interface:

```python
class OutputValidationResult(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]

    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None

    has_video: bool = False
    has_audio: bool = False

    video_codec: str | None = None
    audio_codec: str | None = None


def validate_rendered_output(
    output_path: Path,
    expected_duration: float,
    expected_width: int,
    expected_height: int,
    expect_audio: bool,
    duration_tolerance_seconds: float = 0.75,
) -> OutputValidationResult:
    ...
```

Phải kiểm tra:

- File tồn tại.
- File size > 0.
- FFprobe đọc được.
- Có video stream.
- Duration hữu hạn.
- Duration gần expected duration.
- Resolution đúng.
- Có audio nếu cấu hình yêu cầu.
- Không có audio vẫn hợp lệ nếu output được cấu hình video-only.

Không rename partial thành final nếu validation có error.

---

# 30. Automated QC cơ bản

Sau output validation, tạo:

```text
projects/<id>/exports/final_vN.qc.json
```

hoặc:

```text
projects/<id>/previews/preview_vN.qc.json
```

Schema:

```json
{
  "render_id": "render_001",
  "valid": true,
  "errors": [],
  "warnings": [],
  "media": {
    "duration_seconds": 89.52,
    "width": 1080,
    "height": 1920,
    "has_video": true,
    "has_audio": true,
    "video_codec": "h264",
    "audio_codec": "aac"
  },
  "audio": {
    "normalization_enabled": true,
    "target_lufs": -16.0,
    "true_peak_target_db": -1.5
  }
}
```

Trong phase này chưa bắt buộc đo chính xác integrated loudness sau render.

Nếu triển khai đo loudness bằng `ebur128` mà không làm phức tạp pipeline, có thể thêm dưới dạng enhancement tùy chọn, không phải acceptance criterion bắt buộc.

---

# 31. Export page changes

Sửa:

```text
pages/05_export.py
```

## 31.1 Page structure

Dùng:

```python
st.expander("Âm thanh nguồn", expanded=True)
st.expander("Nhạc nền", expanded=True)
st.expander("Chuẩn hóa âm lượng")
st.expander("Kiểm tra nhanh")
st.expander("Render")
```

## 31.2 Voice volume

Có thể dùng percent slider:

```python
voice_percent = st.slider(
    "Âm lượng nguồn",
    min_value=0,
    max_value=200,
    value=100,
    step=1,
)
```

Hiển thị dB:

```python
voice_db = percent_to_db(voice_percent)
st.caption(f"{voice_db:.1f} dB")
```

Nếu percent bằng 0:

```text
voice.enabled = false
```

## 31.3 Music upload

Form:

```text
File âm thanh
Loại quyền
URL nguồn
File license
Ghi chú
Checkbox xác nhận quyền
```

Sau upload:

- Validate.
- Lưu asset.
- Refresh selectbox.
- Chọn asset vừa upload.

## 31.4 Music selection

Hiển thị:

- Original filename.
- Duration.
- Codec.
- Rights type.
- Rights confirmed.
- Source URL nếu có.

Không hiển thị internal absolute path.

## 31.5 Save config

Nút:

```text
Lưu cấu hình render
```

Phải:

1. Xây Pydantic model.
2. Validate.
3. Lưu version mới.
4. Hiển thị version.
5. Không khởi động render tự động.

## 31.6 Render buttons

Nút render phải truyền:

```text
timeline_version
render_config_version
```

Không render từ dữ liệu chưa lưu chỉ nằm trong widget state.

Nếu form đã thay đổi nhưng chưa lưu:

```text
Hiển thị cảnh báo “Cấu hình đã thay đổi nhưng chưa được lưu”.
```

Có thể tính hash giữa form hiện tại và current config để phát hiện dirty state.

---

# 32. Preview/final approval behavior

Nếu hệ thống hiện đã có approval gate:

- Render config thay đổi phải làm approval cũ không còn hợp lệ.
- Timeline thay đổi phải làm approval cũ không còn hợp lệ.
- Preview approval phải gắn với:
  - `timeline_version`
  - `render_config_version`
  - `preview_render_id`

Final chỉ được render bằng đúng cặp version đã được duyệt.

Nếu hệ thống chưa có approval gate hoàn chỉnh:

- Không mở rộng scope task này quá mức.
- Nhưng phải lưu đủ metadata để thêm gate sau này.
- Ghi technical debt trong `DECISIONS.md`.

---

# 33. Cache và invalidation

## 33.1 Có thể tái sử dụng

Các artifact không phụ thuộc render config:

- Transcript.
- Scenes.
- Silences.
- Candidates.
- Ranked candidates.
- Timeline source selection.

## 33.2 Phải render lại

Nếu thay đổi:

- Preset.
- Voice gain.
- Music asset.
- Music gain.
- Music offset.
- Music timeline start.
- Loop mode.
- Fade.
- Loudness setting.

thì audio post-processing output cũ không còn hợp lệ.

## 33.3 Render fingerprint

Tạo hash ổn định:

```python
fingerprint_input = {
    "timeline_version": timeline.version,
    "render_config": configuration.model_dump(
        mode="json",
        exclude={"id", "created_at", "updated_at"},
    ),
    "music_asset_id": music_asset.id if music_asset else None,
}
```

Có thể dùng SHA-256.

Lưu fingerprint trong render metadata.

Trong phase này không bắt buộc reuse output theo fingerprint, nhưng phải thiết kế để hỗ trợ sau này.

---

# 34. Error handling

Thêm exception nếu cần:

```python
class InvalidAudioAssetError(AppError):
    pass


class RenderConfigurationError(AppError):
    pass


class AudioMixError(RenderError):
    pass


class OutputValidationError(RenderError):
    pass
```

Thông báo UI phải dễ hiểu.

Ví dụ:

```text
Không thể sử dụng file nhạc.

Nguyên nhân: FFprobe không tìm thấy audio stream.
Hãy chọn MP3, WAV, M4A, AAC, FLAC, OGG hoặc OPUS hợp lệ.
```

```text
Không thể render.

Nhạc nền đang được bật nhưng asset đã bị xóa.
Hãy chọn lại file nhạc và lưu một cấu hình render mới.
```

```text
Render đã bị hủy.

File tạm đã được dọn dẹp. Các output hoàn tất trước đó không bị xóa.
```

Không hiển thị nguyên command hoặc traceback nhạy cảm cho người dùng thông thường.

Ghi chi tiết vào log.

---

# 35. Logging

Mỗi log liên quan render nên có:

```text
project_id
job_id
render_id
render_type
timeline_version
render_config_version
step
```

Ví dụ:

```text
2026-01-01 10:20:30
INFO
project=proj_01
job=job_02
render=render_03
timeline_version=4
render_config_version=2
step=audio_mix
message="Mixing source audio and background music"
```

Không log:

- Nội dung file license.
- Absolute path nếu không cần thiết.
- API key.
- Dữ liệu nhạy cảm.

---

# 36. Security requirements

1. Không dùng `shell=True`.
2. Không ghép command string từ user input.
3. Dùng argument list.
4. Mọi path dùng `pathlib.Path`.
5. Mọi project path resolve qua helper an toàn.
6. Không tin path từ JSON hoặc UI.
7. Không tin extension.
8. Validate bằng FFprobe.
9. Không follow symlink ra ngoài project.
10. Không ghi output ra ngoài project root.
11. JSON phải dùng `allow_nan=False`.
12. Mọi schema phải validate số hữu hạn.
13. Uploaded music phải được rename bằng ID nội bộ.
14. Phải có giới hạn upload size.
15. Phải có disk-space preflight.

---

# 37. Configuration mới

Thêm vào `.env.example`:

```env
MAX_MUSIC_UPLOAD_MB=500

DEFAULT_VOICE_GAIN_DB=0.0
DEFAULT_MUSIC_GAIN_DB=-16.5

DEFAULT_TARGET_LUFS=-16.0
DEFAULT_LOUDNESS_RANGE=11.0
DEFAULT_TRUE_PEAK_DB=-1.5

DEFAULT_MUSIC_FADE_IN_SECONDS=2.0
DEFAULT_MUSIC_FADE_OUT_SECONDS=3.0

MIN_TEST_PREVIEW_SECONDS=10
MAX_TEST_PREVIEW_SECONDS=30
DEFAULT_TEST_PREVIEW_SECONDS=20

RENDER_DURATION_TOLERANCE_SECONDS=0.75
```

Thêm field tương ứng trong `config/settings.py`.

Dùng `getattr` fallback nếu cần tương thích với môi trường cũ, nhưng cấu hình chính thức phải có field khai báo rõ ràng.

---

# 38. Tests

## 38.1 Unit tests bắt buộc

Tạo tối thiểu:

```text
tests/test_audio_utils.py
tests/test_music_assets.py
tests/test_render_config.py
tests/test_render_config_versioning.py
tests/test_timeline_slice.py
tests/test_audio_mix_plan.py
tests/test_output_validator.py
tests/test_render_migrations.py
```

## 38.2 Percent-to-dB

Kiểm tra:

```text
0%   → mute/floor behavior
10%  → khoảng -20 dB
50%  → khoảng -6.0206 dB
100% → 0 dB
200% → khoảng +6.0206 dB
```

## 38.3 Music asset

Kiểm tra:

- File audio hợp lệ.
- Video-only bị từ chối làm music.
- File hỏng bị từ chối.
- File extension giả bị từ chối.
- Filename có path traversal không thoát project.
- Asset chưa xác nhận quyền không dùng được.
- Manifest ghi atomic.
- Xóa asset không được xóa file ngoài thư mục music.

## 38.4 Render config

Kiểm tra:

- Config không music.
- Music enabled nhưng không có asset ID.
- Asset ID không tồn tại.
- Gain ngoài range.
- Fade âm.
- Offset âm.
- Unknown preset.
- Timeline version không tồn tại.
- Version cũ không bị ghi đè.
- `current.json` trỏ đúng version mới nhất.

## 38.5 Timeline slice

Kiểm tra:

- Preview nằm trong một clip.
- Preview đi qua nhiều clip.
- Preview bắt đầu đúng biên clip.
- Preview kết thúc đúng biên clip.
- Preview vượt final duration thì bị clamp hoặc validation error.
- Preview start bằng final duration bị từ chối.
- Derived timeline giữ đúng source timestamp.
- Tổng duration đúng trong tolerance.

## 38.6 Audio cases

Bắt buộc có integration test cho:

```text
1. Source có audio, music tắt.
2. Source có audio, music bật.
3. Source không có audio, music bật.
4. Source audio disabled, music bật.
5. Source không audio, music tắt.
6. Source audio disabled, music tắt.
```

## 38.7 Music duration

Kiểm tra:

- Music ngắn hơn video và loop bật.
- Music ngắn hơn video và loop tắt.
- Music dài hơn video.
- Music start sau giây 0.
- Music start gần cuối video.
- Music source offset.
- Music source offset lớn hơn duration khi loop.
- Music source offset không hợp lệ khi không loop.
- Final video không bị kéo dài bởi music.

## 38.8 Fade

Kiểm tra:

- Fade bằng 0.
- Fade lớn hơn music visible duration.
- Music bắt đầu giữa timeline.
- Test preview ở giữa video không tạo fade out giả.
- Preview chứa cuối video thì fade out đúng vị trí.

## 38.9 Cancellation

Kiểm tra:

- Cancel trong lúc render clip.
- Cancel trong audio mix.
- Partial file bị xóa.
- Final cũ không bị xóa.
- Job thành `CANCELLED`.
- Render record thành `CANCELLED`.

## 38.10 Output validation

Kiểm tra:

- Valid video/audio.
- Valid video-only.
- Output thiếu video.
- Output duration sai.
- Output resolution sai.
- Output hỏng.
- Expect audio nhưng output không có audio.

---

# 39. Fixture media

Tạo fixture ngắn bằng FFmpeg trong test setup thay vì commit file media lớn.

## 39.1 Video có audio

```bash
ffmpeg -y \
  -f lavfi -i "testsrc=size=640x360:rate=30:duration=6" \
  -f lavfi -i "sine=frequency=440:duration=6" \
  -c:v libx264 \
  -pix_fmt yuv420p \
  -c:a aac \
  sample_with_audio.mp4
```

## 39.2 Video không audio

```bash
ffmpeg -y \
  -f lavfi -i "testsrc=size=640x360:rate=30:duration=6" \
  -c:v libx264 \
  -pix_fmt yuv420p \
  -an \
  sample_no_audio.mp4
```

## 39.3 Music ngắn

```bash
ffmpeg -y \
  -f lavfi -i "sine=frequency=220:duration=2" \
  -c:a pcm_s16le \
  music_short.wav
```

## 39.4 Music dài

```bash
ffmpeg -y \
  -f lavfi -i "sine=frequency=330:duration=10" \
  -c:a pcm_s16le \
  music_long.wav
```

Tests yêu cầu FFmpeg có thể được đánh dấu integration và skip nếu binary không tồn tại.

---

# 40. Kế hoạch triển khai theo phase

Agent phải triển khai lần lượt. Không làm toàn bộ trong một commit lớn.

---

## Phase 0 — Repository inspection

### Công việc

1. Đọc:
   - `README.md`
   - `SPEC.md`
   - `DECISIONS.md`
   - `config/settings.py`
   - `core/models.py`
   - `core/enums.py`
   - `services/job_service.py`
   - `services/render_service.py`
   - `pipeline/renderer.py`
   - `pipeline/ffmpeg.py`
   - `pages/05_export.py`
2. Xác định:
   - Cách timeline version hiện được lưu.
   - Cách render progress hiện hoạt động.
   - Cách cancellation token được truyền.
   - Cách Render record được tạo.
   - Cách path helper hiện được triển khai.
   - Cách migration hiện hoạt động.
3. Ghi chênh lệch giữa plan và source thực tế vào `DECISIONS.md`.
4. Không code tính năng trong phase này ngoài sửa lỗi build cản trở việc inspect.

### Definition of Done

- Có mục mới trong `DECISIONS.md`.
- Có danh sách file dự kiến sửa/tạo.
- Không thay đổi behavior hiện tại.

---

## Phase 1 — Schemas, helpers và database

### Công việc

1. Thêm Pydantic schemas.
2. Thêm percent-to-dB helper.
3. Thêm atomic JSON helper nếu chưa có.
4. Thêm safe project/asset path helper nếu chưa có.
5. Thêm settings mới.
6. Thêm field database cần thiết.
7. Thêm migration idempotent.
8. Viết unit test.

### Definition of Done

- Schema validate đúng.
- Migration chạy nhiều lần không lỗi.
- Existing database vẫn mở được.
- Existing project vẫn hiển thị.
- Existing render flow chưa bị thay đổi.
- Tests phase 1 pass.

### Commit gợi ý

```text
feat(render-config): add audio option schemas and database migration
```

---

## Phase 2 — Music Asset Service

### Công việc

1. Tạo thư mục music asset.
2. Upload nhạc bằng safe filename.
3. Validate bằng FFprobe.
4. Lưu metadata.
5. Lưu rights information.
6. List/get/delete asset.
7. Atomic manifest.
8. UI upload cơ bản trong Export page.
9. Viết tests.

### Definition of Done

- Upload được MP3/WAV hợp lệ.
- File hỏng bị từ chối.
- Video không có audio bị từ chối.
- Không dùng được asset chưa xác nhận quyền.
- Không có path traversal.
- Asset hiển thị trong Export page.

### Commit gợi ý

```text
feat(assets): add project-local licensed music library
```

---

## Phase 3 — Versioned Render Configuration

### Công việc

1. Tạo `RenderConfigService`.
2. Save/list/get/restore config.
3. Validate timeline version.
4. Validate music asset.
5. Atomic version files.
6. Nâng UI Export:
   - Voice enabled.
   - Voice volume.
   - Music enabled.
   - Music asset.
   - Music volume.
   - Loop.
   - Source offset.
   - Timeline start.
   - Fade.
   - Loudness.
7. Dirty-state warning.
8. Viết tests.

### Definition of Done

- Form lưu được cấu hình.
- Mỗi lần save tạo version mới.
- Version cũ không bị ghi đè.
- Reload page khôi phục current config.
- Render chưa được thay đổi trong phase này.
- Tests pass.

### Commit gợi ý

```text
feat(render-config): add versioned post-production settings
```

---

## Phase 4 — Global Audio Mix Pipeline

### Công việc

1. Refactor renderer thành các bước rõ ràng:
   - Clip render.
   - Concat.
   - Global audio mix.
   - Validation.
   - Rename.
2. Implement `apply_global_audio_mix()`.
3. Hỗ trợ toàn bộ 6 audio cases.
4. Music loop/trim.
5. Source offset.
6. Timeline start.
7. Fade.
8. Voice/music gain.
9. One-pass loudnorm.
10. Progress.
11. Cancellation.
12. `.partial`.
13. Integration tests.

### Definition of Done

- Music không restart giữa clip.
- Final duration theo video.
- Music không kéo dài final.
- Video-only render được.
- Music-only render được.
- Voice gain có tác dụng.
- Music gain có tác dụng.
- Loop hoạt động.
- Fade hoạt động.
- Cancellation dọn partial.
- Existing no-music flow vẫn pass.

### Commit gợi ý

```text
feat(renderer): add global voice and background music mixing
```

---

## Phase 5 — Test Preview

### Công việc

1. Implement timeline slicing theo output range.
2. Implement music timeline alignment.
3. Thêm job type test preview.
4. Thêm UI start/duration.
5. Render 10–30 giây.
6. Hiển thị video kết quả.
7. Cancel.
8. Tests.

### Definition of Done

- Preview ở giữa timeline dùng đúng source clip.
- Music đúng playhead của full timeline.
- Không tạo fade out giả.
- Preview không làm thay đổi timeline/config chính.
- Có thể cancel.
- Output test preview được FFprobe validate.

### Commit gợi ý

```text
feat(preview): add short post-production test renders
```

---

## Phase 6 — Output QC và hardening

### Công việc

1. Output validator.
2. QC JSON.
3. Render record lifecycle.
4. Error sanitization.
5. Temp cleanup.
6. Disk-space preflight.
7. Full regression tests.
8. Documentation.

### Definition of Done

- Partial không rename nếu output invalid.
- Render failure có DB record.
- QC report được tạo.
- Existing upload/analysis/timeline flow không lỗi.
- Full test suite pass.

### Commit gợi ý

```text
feat(qc): validate rendered media and persist quality reports
```

---

# 41. Acceptance criteria tổng thể

Tính năng được xem là hoàn thành khi:

1. Người dùng mở một project hiện có.
2. Người dùng upload được nhạc hợp lệ.
3. Người dùng phải xác nhận quyền sử dụng nhạc.
4. Nhạc được lưu bằng filename an toàn.
5. Nhạc được validate bằng FFprobe.
6. Người dùng bật/tắt source audio.
7. Người dùng chỉnh được voice volume.
8. Người dùng bật/tắt music.
9. Người dùng chỉnh được music volume.
10. Người dùng chọn được loop hoặc stop.
11. Người dùng chỉnh được source offset.
12. Người dùng chỉnh được timeline start.
13. Người dùng chỉnh được fade in/out.
14. Người dùng bật/tắt loudness normalization.
15. Cấu hình render được lưu theo version.
16. Cấu hình cũ không bị ghi đè.
17. Test preview 10–30 giây hoạt động.
18. Music trong test preview đúng vị trí full timeline.
19. Preview full hoạt động.
20. Final render hoạt động.
21. Music không restart ở điểm cắt.
22. Music không kéo dài duration video.
23. Video không source audio vẫn render được.
24. Music-only vẫn render được.
25. Video-only vẫn render được.
26. Cancellation xóa partial.
27. Output được FFprobe validate.
28. Render record lưu timeline/config version.
29. QC report được tạo.
30. Existing analysis flow không bị regression.
31. Unit tests pass.
32. Integration tests pass.
33. Ruff pass.
34. Black check pass.

---

# 42. Quy tắc dành cho coding agent

1. Đọc toàn bộ plan trước khi code.
2. Thực hiện đúng thứ tự phase.
3. Không viết lại toàn bộ renderer trong một lần nếu có thể refactor tăng dần.
4. Không phá public API nếu không cần thiết.
5. Nếu phải đổi API, cập nhật toàn bộ caller và test.
6. Không dùng `shell=True`.
7. Không tin path từ UI.
8. Không tin extension.
9. Không hardcode `ffmpeg`; dùng settings/wrapper hiện có.
10. Không tạo FFmpeg wrapper thứ hai nếu wrapper hiện tại dùng được.
11. Không gọi Streamlit từ worker thread.
12. Không để music quyết định output duration.
13. Không mix music trên từng timeline clip.
14. Không ghi đè render config version cũ.
15. Không rename partial trước khi FFprobe validation pass.
16. Không dùng API AI/cloud cho tính năng này.
17. Không thêm music ducking trong plan này.
18. Không thêm tính năng né bản quyền.
19. Mỗi phase phải có test.
20. Ghi mọi deviation vào `DECISIONS.md`.

---

# 43. Kiểm tra sau mỗi phase

Chạy:

```bash
ruff check .
black --check .
pytest
```

Nếu repository dùng câu lệnh khác, dùng tool hiện có và ghi vào `DECISIONS.md`.

Sau Phase 4 trở đi, chạy thêm manual smoke test:

```text
Case A:
Source có audio, music tắt.

Case B:
Source có audio, music bật, loop.

Case C:
Source không audio, music bật.

Case D:
Source audio disabled, music bật.

Case E:
Source không audio, music tắt.

Case F:
Cancel trong lúc audio mix.
```

---

# 44. Manual QA checklist

## Project và asset

```text
[ ] Existing project mở bình thường.
[ ] Upload MP3 thành công.
[ ] Upload WAV thành công.
[ ] File hỏng bị từ chối.
[ ] Chưa xác nhận quyền thì không upload/use được.
[ ] Filename lạ không gây path traversal.
```

## Render config

```text
[ ] Voice 0% tạo source mute.
[ ] Voice 50% nhỏ hơn rõ ràng.
[ ] Voice 100% giữ gain gốc.
[ ] Voice 150% tăng âm lượng.
[ ] Music 10% nhỏ.
[ ] Music 25% lớn hơn 10%.
[ ] Config reload đúng sau refresh.
[ ] Save lần hai tạo version mới.
```

## Music behavior

```text
[ ] Music loop xuyên suốt.
[ ] Music không restart ở clip cut.
[ ] Music không loop thì dừng đúng.
[ ] Music start tại giây 5 hoạt động.
[ ] Source offset hoạt động.
[ ] Fade in hoạt động.
[ ] Fade out hoạt động.
[ ] Music không kéo dài video.
```

## Output

```text
[ ] Preview mở được.
[ ] Final mở được.
[ ] Resolution đúng.
[ ] Duration đúng.
[ ] Audio stream đúng theo config.
[ ] Partial được xóa sau cancel.
[ ] QC report tồn tại.
[ ] Render record có config version.
```

---

# 45. Definition of Done

Feature hoàn tất khi người dùng có thể thực hiện luồng sau mà không cần dùng câu lệnh FFmpeg:

```text
1. Mở project.
2. Vào Export.
3. Chọn preset.
4. Chỉnh âm lượng source.
5. Upload/chọn nhạc đã được cấp quyền.
6. Chỉnh âm lượng nhạc.
7. Chọn loop, offset và fade.
8. Lưu render configuration.
9. Render thử 20 giây.
10. Nghe/xem kết quả.
11. Điều chỉnh và lưu version mới.
12. Render preview đầy đủ.
13. Render final.
14. Nhận output đã được FFprobe kiểm tra.
```

Tất cả dữ liệu mặc định vẫn nằm trên máy local.

---

# 46. Lệnh giao việc ban đầu cho agent

Sử dụng prompt sau:

```text
Read PLAN_AUDIO_POSTPRODUCTION.md in full.

Start with Phase 0 and Phase 1 only.

Before coding:
1. Inspect the current repository implementation.
2. Compare actual interfaces with the plan.
3. Record deviations and implementation decisions in DECISIONS.md.
4. Preserve all current upload, analysis, timeline, job, cancellation,
   and render behavior.

For Phase 1:
- Add render/audio Pydantic schemas.
- Add percent-to-dB helpers.
- Add or reuse atomic JSON and safe path helpers.
- Add settings.
- Add the minimum idempotent database migration required for render
  configuration metadata.
- Add unit tests.

Do not implement music upload, FFmpeg audio mixing, or UI redesign yet.

At completion:
- Run ruff check .
- Run black --check .
- Run pytest.
- Report files changed, migration behavior, tests added, and any
  deviations from the plan.
```

Sau khi duyệt Phase 1, giao tiếp:

```text
Continue with Phase 2 of PLAN_AUDIO_POSTPRODUCTION.md only.

Implement the project-local music AssetService, FFprobe validation,
rights confirmation, atomic manifest storage, safe file naming, and
the minimum Export-page UI needed to upload/list/delete/select music.

Do not implement audio mixing yet.

Run lint, formatting checks, and tests. Report all changed files and
manual QA steps.
```

Sau khi duyệt Phase 2:

```text
Continue with Phase 3 of PLAN_AUDIO_POSTPRODUCTION.md only.

Implement versioned render configurations and the Export-page controls.
Do not modify FFmpeg rendering behavior yet.

Run all tests and report config compatibility with existing projects.
```

Sau khi duyệt Phase 3:

```text
Continue with Phase 4 of PLAN_AUDIO_POSTPRODUCTION.md only.

Implement the global audio post-processing pipeline after timeline
concatenation. Support all six source-audio/music combinations,
gain, loop, trim, offsets, fade, one-pass loudnorm, progress,
cancellation, partial cleanup, and output validation.

Do not implement ducking, subtitles, branding, or transitions.

Run the complete test suite and perform the six manual smoke cases.
```

Sau khi duyệt Phase 4:

```text
Continue with Phase 5 and then Phase 6 of
PLAN_AUDIO_POSTPRODUCTION.md.

Implement timeline-based short test previews, correct music playhead
alignment, output QC, render lifecycle persistence, cleanup, and
hardening. Complete one phase at a time and stop for review after each.
```