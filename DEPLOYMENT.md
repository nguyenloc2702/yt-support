# Đánh giá tài nguyên và thiết kế triển khai

## 1. Kết luận nhanh

Hệ thống hiện tại **không nặng ở lớp web UI/database**, nhưng **nặng ở pipeline xử lý video và AI**. Một VPS nhỏ có thể chạy giao diện và metadata ổn định; không nên kỳ vọng VPS 1 vCPU/1 GB RAM vừa nhận nhiều video vừa chạy Whisper, FFmpeg render 1080p.

Phân loại tải:

| Thành phần | Mức tải | Nhận xét |
|---|---:|---|
| Streamlit + Python | Thấp-vừa | Mỗi session nhẹ, nhưng Streamlit rerun toàn script ở mỗi tương tác. |
| SQLite + SQLAlchemy | Thấp | Chỉ lưu metadata/job/config; không lưu blob video. Phù hợp một người hoặc một worker. |
| Upload video | Cao về network/disk tạm | `st.file_uploader` nhận file qua VPS trước khi service ghi xuống disk. |
| FFprobe | Thấp | CPU ngắn hạn, đọc metadata. |
| Proxy 720p | Vừa-cao | FFmpeg decode/encode toàn video; tạo thêm file lớn. |
| faster-whisper `small` | Cao | Ăn RAM/CPU; lần đầu còn tải/cache model. GPU sẽ giảm thời gian nhưng không làm mất nhu cầu RAM. |
| Scene/silence detection | Vừa | Decode video/audio, thường CPU-bound. |
| Render preview/final | Rất cao | Hiện render từng clip rồi concat, sau đó audio post-processing; có thể tạo nhiều bản sao tạm. |
| Disk | Cao | Một project có thể gồm source + proxy + WAV + preview + temp + export. |

Ví dụ project mẫu trong workspace đang chiếm xấp xỉ:

- source: 2.73 GB
- proxy: 519 MB
- speech WAV: 65 MB
- preview: 119 MB + 4.7 MB test preview
- metadata: vài MB trở xuống

Tổng riêng project này khoảng **3.4 GB**, chưa tính temp và bản export cuối. Đây là lý do chi phí chính là disk/network/CPU, không phải SQLite.

## 2. Các điểm gây tải trong code hiện tại

1. `ProjectService._save_source_file()` dùng `uploaded_file.read()` rồi `write()` một lần. File lớn có thể làm tăng RAM của process. Nên copy theo chunk 8 MiB và dùng upload trực tiếp/object storage nếu chạy VPS.
2. `st.file_uploader` buộc video đi qua VPS. `MAX_UPLOAD_GB=10` là giới hạn logic rất lớn cho VPS nhỏ.
3. `settings.max_concurrent_jobs=1` là cấu hình đúng cho VPS tiết kiệm tài nguyên. Không tăng nếu không có RAM/CPU tương ứng.
4. `WhisperModel` được tạo trong mỗi lần transcription. Nên cache model theo process và giới hạn chỉ một job AI chạy đồng thời.
5. Proxy luôn tạo H.264 720p; hữu ích để preview nhưng là bản sao disk lớn.
6. Renderer re-encode từng clip, giữ clip tạm, concat, rồi xử lý audio. Peak disk có thể xấp xỉ source + proxy + WAV + preview + export + tổng clip tạm.
7. Streamlit app chạy với `APP_HOST=127.0.0.1` mặc định, phù hợp bind sau reverse proxy. Không expose trực tiếp cổng 8501 ra Internet.
8. SQLite hiện chỉ là metadata; không phải nguyên nhân làm VPS nặng. Tuy nhiên cần bật WAL/busy timeout nếu có nhiều request/worker.

## 3. Có thể lưu video ở máy local người dùng không?

### Trường hợp app chạy local trên máy người dùng: Có, và đây là phương án tốt nhất

Có thể giữ toàn bộ source/proxy/audio/render trên máy người dùng, còn SQLite cũng local. Đây chính là kiến trúc hiện tại theo SPEC. Khi đó VPS chỉ cần dùng cho CI/CD hoặc không cần dùng cho xử lý video.

### Trường hợp người dùng mở web app trên VPS: Không thể dùng đường dẫn file local trực tiếp

Browser không cho server đọc tùy ý `C:\Videos\input.mp4` hoặc `/Users/...`. `st.file_uploader` cũng vẫn upload bytes lên server. Vì vậy không có cách an toàn để VPS xử lý file local mà **không truyền file qua mạng**, nếu chỉ dùng Streamlit thuần.

Có 3 lựa chọn:

#### A. Local-first desktop wrapper — khuyến nghị nếu muốn không upload

- Chạy Streamlit/engine trên localhost của người dùng.
- Người dùng chọn file bằng `st.file_uploader` hoặc custom component; engine đọc/ghi vào `LOCAL_PROJECTS_DIR`.
- VPS chỉ chứa source code, package artifacts, documentation, hoặc bản web demo không xử lý video.
- Có thể đóng gói bằng Docker Desktop, installer hoặc script khởi động.

#### B. Browser File System Access API — chỉ dành cho frontend custom

Một custom component JavaScript có thể xin quyền thư mục và đọc file bằng browser API. Nhưng server vẫn không thể chạy FFmpeg trên file đó nếu không gửi bytes; muốn xử lý local phải đưa FFmpeg/WASM hoặc engine native xuống máy client. Đây là một sản phẩm desktop/web-client khác, không phải thay đổi nhỏ trong Streamlit.

#### C. Hybrid VPS — source upload một lần, xử lý trên VPS

- Upload source vào temporary disk hoặc object storage.
- VPS chỉ giữ proxy/metadata trong thời gian ngắn.
- Xử lý xong cho người dùng tải export về và xóa source theo TTL.
- Không lưu video trong DB; DB chỉ lưu `source_path`, checksum, duration, status.

Mô hình C giảm disk lâu dài nhưng **không loại bỏ bandwidth và peak disk** trong lúc xử lý.

## 4. Kiến trúc VPS tiết kiệm tài nguyên

### Quy mô khuyến nghị

- **UI/metadata בלבד**: 1 vCPU, 1 GB RAM, 20–40 GB SSD.
- **Một job video tại một thời điểm**: 2–4 vCPU, 4–8 GB RAM, SSD 80–150 GB.
- **Whisper `small` + render thường xuyên**: 4 vCPU, 8 GB RAM; GPU VPS chỉ đáng tiền nếu khối lượng lớn.
- Không dùng swap để thay thế RAM cho render; có thể dùng zram/swap nhỏ làm safety net nhưng sẽ chậm.

### Runtime

- Nginx/Caddy terminate TLS và proxy tới `127.0.0.1:8501`.
- Chạy một process Streamlit duy nhất.
- `MAX_CONCURRENT_JOBS=1`.
- Dùng systemd, restart khi lỗi, giới hạn CPU/RAM/Tasks và đặt `NoNewPrivileges=true`.
- Đặt `DATA_DIR`, `PROJECTS_DIR`, `LOG_DIR` trên volume riêng; không đặt media trong image/container layer.
- Dùng quota/TTL: xóa `temp`, preview cũ, WAV sau khi transcript đã hoàn tất; giới hạn tổng dung lượng mỗi project.
- Không commit `data/`, `projects/`, `logs/`, model cache vào repository.

### Biện pháp giảm tải pipeline

1. Preview: 480p/720p, 24–30 fps, `veryfast`, CRF cao.
2. Final: chỉ render sau khi duyệt; tránh render lại nếu timeline/config chưa đổi.
3. Cache theo checksum source + options + timeline version.
4. Dùng `ffmpeg -ss` trước input khi phù hợp để seek nhanh; giữ một worker.
5. Chỉ tạo WAV khi pipeline thật sự cần audio phân tích; xóa sau khi hoàn thành.
6. Ưu tiên Whisper `base` cho VPS nhỏ, `small` cho máy 8 GB; không dùng `large-v3` trên VPS tiết kiệm.
7. Cache model trong volume persistent để không tải lại sau restart.
8. Dùng `nice`/CPU quota cho FFmpeg nếu VPS còn phục vụ UI.
9. Thêm health check có `ffmpeg -version`, disk free, database writable, worker idle/busy.

## 5. CI/CD đề xuất

Pipeline chỉ build/test/deploy code; **không đưa video, SQLite hoặc model Whisper vào artifact**.

```text
Push main
  -> lint + format check
  -> pytest
  -> build wheel / package
  -> build Docker image (optional)
  -> deploy qua SSH hoặc pull image
  -> systemd restart
  -> smoke test /health hoặc Streamlit HTTP
  -> rollback release trước nếu smoke test fail
```

### Phương án không cần registry — tiết kiệm nhất

GitHub Actions/GitLab CI:

1. checkout
2. cài Python 3.11 + FFmpeg test dependency
3. `pip install -e ".[dev]"`
4. `ruff check .`
5. `black --check .`
6. `pytest -q`
7. tạo release directory `/opt/local-video-editor/releases/<commit>` trên VPS bằng `rsync`/`scp`
8. tạo symlink `/opt/local-video-editor/current`
9. cài dependency từ lock/pyproject vào virtualenv dùng chung
10. `systemctl restart local-video-editor`
11. kiểm tra HTTP trong 30–60 giây
12. lỗi thì trỏ symlink về release cũ và restart.

### Phương án Docker

Dùng multi-stage image Python slim, cài FFmpeg từ apt, chạy non-root. Không COPY các thư mục runtime. Mount:

- `/srv/local-video-editor/data`
- `/srv/local-video-editor/projects`
- `/srv/local-video-editor/logs`
- `/srv/local-video-editor/model-cache`

Docker dễ rollback nhưng image Python/ML lớn hơn; với một VPS giá rẻ, systemd + virtualenv thường ít disk/RAM hơn.

### Secrets và an toàn

- SSH deploy key chỉ có quyền deploy.
- Secret nằm ở CI secret store và `/etc/local-video-editor/app.env`, không nằm trong repo.
- Firewall chỉ mở 22, 80, 443; không mở 8501.
- Chạy reverse proxy với upload limit thấp hơn quota thực tế.
- Không cho URL downloader tùy ý nếu app public; SSRF và abuse bandwidth là rủi ro lớn.

## 6. Khuyến nghị quyết định kiến trúc

### Nếu mục tiêu là “siêu tiết kiệm VPS”

Chọn **local-first**: app và pipeline chạy trên máy người dùng; VPS chỉ phục vụ landing page, update package, hoặc remote control không chứa media. Đây là lựa chọn duy nhất vừa tránh upload source vừa tránh CPU/RAM/disk VPS.

### Nếu bắt buộc app chạy online

Chọn **hybrid ephemeral processing**: VPS 4 vCPU/8 GB, một job, source lưu temporary filesystem/object storage, xóa theo TTL, metadata SQLite nhỏ. Chấp nhận video vẫn phải upload qua mạng.

### Nếu muốn nhiều người dùng đồng thời

Không mở rộng ThreadPoolExecutor trong một Streamlit process. Tách API/queue/worker/object storage và chuyển SQLite sang PostgreSQL khi có concurrency thực sự. Đây là giai đoạn sau, không phù hợp mục tiêu MVP tiết kiệm.

## 7. Việc nên làm trước khi production

- Thay `uploaded_file.read()` bằng streaming copy theo chunk.
- Cache `WhisperModel` và quản lý lifecycle model.
- Thêm disk quota, TTL cleanup và kiểm tra free space trước upload/render.
- Pin dependency bằng lock file; hiện `pyproject.toml` mới dùng range version.
- Thêm CI workflow và deploy script có rollback.
- Tạo health/smoke check và backup metadata.
- Đưa sample media/runtime artifacts ra khỏi workspace/repository.
- Đo thực tế bằng video đại diện: peak RSS, CPU time, thời gian render, peak disk và bandwidth; không nên quyết định VPS chỉ dựa vào kích thước source code.
