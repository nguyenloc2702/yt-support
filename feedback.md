## Kết luận ngắn

**Chưa thể đánh giá là đã triển khai đúng plan ở mức Production/100%.** Code đã có một **khung orchestration khá đầy đủ**, nhưng nhiều tuyên bố trong plan đang cao hơn đáng kể so với implementation thực tế. Mức đánh giá hợp lý:

- **Khung hạ tầng/queue/provider/Telegram:** khoảng **70–85%**.
- **Mode 1 end-to-end theo đúng mô tả:** khoảng **30–40%**.
- **Mode 2 end-to-end tạo và ráp documentary:** khoảng **40–55%**.
- **Production readiness tổng thể:** khoảng **50–60%**, chưa nên ghi `PRODUCTION - ĐÃ TRIỂN KHAI & ĐANG CHẠY` nếu chưa có bằng chứng vận hành thực tế tương ứng.

Đây là đánh giá **read-only**, không sửa file nào.

## Các phần đã triển khai tương đối thật

### 1. SQLite queue và lifecycle cơ bản

[`JobQueueManager`](services/job_queue.py:27) đã có:

- SQLite WAL và `busy_timeout` tại [`_get_conn()`](services/job_queue.py:33).
- Bảng job, shot, lock tại [`_init_db()`](services/job_queue.py:41).
- Approval/reject/cancel/retry.
- Shot retry, `IN_PROGRESS`, `DONE`, `FAILED`.
- Khôi phục shot bị gián đoạn qua [`reset_interrupted_shots()`](services/job_queue.py:306).

Các test queue hiện có kiểm tra lifecycle, heavy lock, retry và reset sau restart tại [`tests/test_job_queue.py`](tests/test_job_queue.py:15). Tuy nhiên đây vẫn là **unit test**, chưa chứng minh crash recovery thực tế trên container hoặc concurrent worker đa process.

### 2. Account pool và kill-switch

[`AccountPoolManager`](services/account_pool.py:40) đã có persistent state, `filelock`, daily cap, cooldown, reset UTC và kill-switch. Đây là phần bám plan khá tốt.

Nhưng cần phân biệt:

- Code đang giới hạn bằng **số shot cấu hình**, mặc định `20 shot/ngày`, tại [`MAX_SHOTS_PER_ACCOUNT_DAY`](services/account_pool.py:25).
- Đây không phải quota credit thật được đọc và tính động theo usage của Gemini.
- Việc chọn account và việc tăng `shots_today` không phải một transaction reserve atomic; nếu sau này chạy nhiều worker/process có thể cấp cùng account trước khi counter được cập nhật.

### 3. Gemini provider và atomic session write

[`GeminiWebApiProvider`](services/gemini_webapi_provider.py:114) có interface rõ, phân loại lỗi, validate session và gọi `response.videos`/`video.save()`.

Atomic persistence tại [`atomic_save_session()`](services/gemini_webapi_provider.py:82) dùng temp file, `fsync`, `os.replace` và chmod Unix `600`, phù hợp với nguyên tắc trong plan.

Tuy nhiên việc chứng minh `gemini-webapi==2.1.1` hoạt động thật chỉ nằm ở nội dung plan; trong workspace hiện thấy provider và unit-level code, chưa thấy bằng chứng probe/runtime artifact để kiểm chứng lại. Vì vậy nên ghi là **đã có implementation và có bằng chứng bên ngoài source**, không nên suy ra toàn bộ Mode 2 production-ready.

### 4. Telegram command layer

[`TelegramCommandBot`](services/telegram_bot.py:30) đã triển khai các lệnh chính: `/reup`, `/remake`, `/approve`, `/reject`, `/cancel`, `/retry`, `/status`, `/pause`, `/resume`, `/help`.

Test dispatcher có tại [`tests/test_telegram_bot.py`](tests/test_telegram_bot.py:39). Chat ID allowlist cũng có tại [`_poll_loop()`](services/telegram_bot.py:70).

Đây là command dispatcher tốt ở mức cơ bản, nhưng chưa phải remote-control production hoàn chỉnh vì chưa thấy test thật với Telegram API, rate-limit `429`, network disconnect, duplicate update, restart polling hoặc command idempotency.

### 5. CI/CD isolation ở mức cấu hình

[`docker-compose.prod.yml`](docker-compose.prod.yml:1) có label, volume persistence, `stop_grace_period`, CPU/RAM limit.

[`deploy.yml`](.github/workflows/deploy.yml:81) có test → build → deploy và prune theo label. Việc loại bỏ global prune là đúng hướng.

Nhưng workflow chỉ **cài ruff** tại [`deploy.yml`](.github/workflows/deploy.yml:27), chưa thực sự chạy `ruff check`. Vì vậy tiêu đề `Lint & Test Suite` chưa chính xác; hiện tại chủ yếu là pytest.

## Các điểm lệch lớn giữa plan và code

### 🔴 1. Mode 1 chưa triển khai đúng mô tả

Plan nói Mode 1 có LLM rewrite, TTS tiếng Việt, auto-ducking BGM, `script_matcher`, render final.

Trong implementation, [`run_smart_reup()`](pipeline/automated_factory.py:114) chỉ:

- Download video.
- Lấy subtitle hoặc Whisper.
- Tạo summary bằng chuỗi hardcoded tại [`automated_factory.py`](pipeline/automated_factory.py:162).
- Ghi `script.md`.
- Chờ approval.

Sau approval, [`execute_approved_reup()`](pipeline/automated_factory.py:202) chỉ chạy FFmpeg cắt từ timestamp `0` trong thời lượng mục tiêu tại [`automated_factory.py`](pipeline/automated_factory.py:217).

Không thấy trong flow này:

- LLM client thực sự.
- Edge-TTS hoặc xKiro TTS.
- BGM/auto-ducking.
- `script_matcher`.
- Timeline theo lời thoại.
- `render_service.render_final`.

Đây là gap chức năng lớn nhất. **Mode 1 hiện gần với “download + hardcoded summary + cắt đoạn đầu + upload” hơn là Smart Reup & Review như plan.**

### 🔴 2. Mode 2 chưa tạo full documentary như plan

Trong [`run_ai_genesis()`](pipeline/automated_factory.py:272), số shot bị giới hạn rõ ràng:

```python
num_shots = min(10, len(sub_segments)) if sub_segments else 6
```

và `is_key_scene` chỉ được gán bằng quy tắc xen kẽ tại [`automated_factory.py`](pipeline/automated_factory.py:307).

Điều này khác với mô tả “phân rã toàn bộ documentary thành granular shots”. Đây giống **demo pipeline tối đa 10 shot** hơn là production decomposition.

Ngoài ra, [`VideoAutomationWorker.run()`](core/worker.py:102) chỉ sinh từng shot rồi upload raw shot lên Drive tại [`worker.py`](core/worker.py:166). Không thấy worker gọi:

- `assemble_full_documentary.py`.
- Một final renderer cho toàn bộ shot.
- Final output validator cho video documentary hoàn chỉnh.
- Upload final documentary.

Thông báo batch cũng chỉ nói B-roll đã xong tại [`notify_batch_completed()`](services/notifier.py:131), không phải video cuối đã được ráp và upload. Vì vậy claim “Mode 2 end-to-end / ghép phim tài liệu hoàn chỉnh” hiện **chưa được code chứng minh**.

### 🔴 3. Fallback Ken Burns không luôn là ảnh chất lượng cao

[`ImageKenBurnsProvider`](services/image_kenburns_provider.py:22) có thử sinh image qua Gemini, nhưng khi thất bại sẽ tạo ảnh màu gradient FFmpeg tại [`image_kenburns_provider.py`](services/image_kenburns_provider.py:64).

Do đó claim “ảnh chất lượng cao + Ken Burns” chỉ đúng khi image generation thành công. Trong trường hợp lỗi API/session, output fallback có thể là **color card**, không phải visual documentary usable.

Worker kích hoạt fallback từ lần thử thứ ba tại [`worker.py`](core/worker.py:139), tương đối phù hợp ý tưởng 3 retries, nhưng fallback vẫn truyền cùng session account và không có chiến lược image-provider/account riêng.

### 🔴 4. Upload fail có thể bị đánh dấu thành công khi rclone không tồn tại

Trong [`StorageUploader.upload_and_clean()`](services/storage_uploader.py:32), nếu rclone không có trong PATH, code trả về:

```python
return True, str(local_file)
```

tại [`storage_uploader.py`](services/storage_uploader.py:52).

Sau đó worker sẽ đánh dấu shot `DONE` tại [`worker.py`](core/worker.py:173), dù file chưa được upload lên Drive. Điều này trái với nguyên tắc trong plan: upload thất bại thì không được DONE.

Ngoài ra, sau `rclone copyto`, code chỉ dựa vào return code; chưa có bước verify remote object/size/checksum thực sự. Vì vậy phần “upload ➜ xác minh ➜ xóa” mới là **xác nhận command thành công**, chưa phải remote verification đầy đủ.

### 🔴 5. Heavy lock chưa bảo vệ toàn bộ tác vụ nặng

Plan nói chỉ một tác vụ nặng Whisper hoặc FFmpeg Render chạy tại một thời điểm.

Mode 1 có acquire lock trước Whisper tại [`automated_factory.py`](pipeline/automated_factory.py:147) và trước final render tại [`automated_factory.py`](pipeline/automated_factory.py:212).

Nhưng worker Mode 2 gọi Gemini provider và Ken Burns FFmpeg tại [`worker.py`](core/worker.py:139) mà không acquire heavy lock. Vì vậy nếu Mode 1 đang render, Mode 2 vẫn có thể chạy FFmpeg Ken Burns hoặc các tác vụ nặng khác.

Ngoài ra, implementation lock hiện làm `SELECT` rồi mới `INSERT OR REPLACE` tại [`job_queue.py`](services/job_queue.py:90). Với nhiều process thật sự cạnh tranh, thao tác này không phải compare-and-set atomic hoàn chỉnh.

### 🔴 6. Job có thể bị treo sau khi shot cuối cùng thất bại

Worker gọi [`mark_shot_failed()`](services/job_queue.py:290). Khi shot đạt max attempts, shot chuyển `FAILED`, nhưng không thấy logic chuyển job cha sang `FAILED` khi còn shot failed.

Điều kiện hoàn thành chỉ kiểm tra toàn bộ shot `DONE` tại [`worker.py`](core/worker.py:179). Do đó một job có shot `FAILED` có thể:

- Không bao giờ thành `COMPLETED`.
- Không chắc chuyển sang `FAILED`.
- Không được báo final job failure rõ ràng.

Đây là gap state machine quan trọng đối với `/status`, `/retry` và vận hành thực tế.

### 🟠 7. Retry/fallback semantics chưa khớp hoàn toàn với mô tả

Worker quyết định fallback dựa trên `attempts >= 2` tại [`worker.py`](core/worker.py:141), tức là lần gọi thứ ba dùng fallback, trước khi shot được đánh dấu thất bại lần thứ ba. Điều này có thể chấp nhận được, nhưng khác cách diễn đạt “sau 3 lần retry thất bại”.

Đáng chú ý hơn, khi upload thất bại, code gọi `mark_shot_failed()` và có thể retry như một lỗi generation; trong khi upload failure nên có retry/backoff riêng, giữ file local và không tiêu hao quota Gemini.

### 🟠 8. Production container không dùng file requirements-worker như plan mô tả

[`requirements-worker.txt`](requirements-worker.txt:1) được mô tả là dependency riêng cho worker, nhưng [`Dockerfile`](Dockerfile:21) lại copy và install [`requirements.txt`](requirements.txt:21), không phải `requirements-worker.txt`.

Điều này tạo ra rủi ro:

- Image production không đúng dependency set được plan tuyên bố.
- Có thể kéo thêm dependency không cần thiết.
- Claim “worker image không có Playwright” chưa được chứng minh từ Dockerfile hiện tại.

Đây là một mâu thuẫn trực tiếp giữa kiến trúc tài liệu và build path thực tế.

### 🟠 9. URL security guard có tồn tại nhưng không nằm trong download path

[`validate_public_url()`](pipeline/youtube.py:41) đã có guard chống loopback/private/link-local address.

Tuy nhiên [`download_video()`](pipeline/youtube.py:91) chỉ kiểm tra URL bắt đầu bằng `http(s)` tại [`youtube.py`](pipeline/youtube.py:108), không gọi `validate_public_url()`.

Các entrypoint Telegram gọi trực tiếp pipeline tại [`telegram_bot.py`](services/telegram_bot.py:153), nên guard SSRF hiện không được chứng minh là nằm trên đường đi thực tế của user input. Nếu hệ thống chỉ nhận YouTube URL thì rủi ro thấp hơn, nhưng implementation và claim security chưa đồng nhất.

### 🟠 10. Trạng thái vận hành thực tế đang mâu thuẫn với “24/7 production 10 account”

[`SYSTEM_STATUS.md`](SYSTEM_STATUS.md:7) ghi:

- Chỉ `1 / 10` account sẵn sàng tại [`SYSTEM_STATUS.md`](SYSTEM_STATUS.md:14).
- Telegram Bot đang chờ nạp token tại [`SYSTEM_STATUS.md`](SYSTEM_STATUS.md:15).
- Hành động tiếp theo vẫn là kích hoạt Telegram, nạp thêm account và cấu hình Drive tại [`SYSTEM_STATUS.md`](SYSTEM_STATUS.md:125).

Vì vậy tài liệu plan nói “đang chạy production với 10 tài khoản” là quá mạnh so với status hiện tại. Trạng thái thực tế hợp lý hơn là **deployed/staged, core code present, operational activation incomplete**.

## Đánh giá test coverage

Có các test tốt cho:

- Queue/lifecycle: [`tests/test_job_queue.py`](tests/test_job_queue.py:15).
- Telegram command dispatch: [`tests/test_telegram_bot.py`](tests/test_telegram_bot.py:39).
- Output validation: [`tests/test_output_validator.py`](tests/test_output_validator.py:1).
- Nhiều service/pipeline cũ của Streamlit.

Nhưng trong nhóm test đã khảo sát, chưa thấy test E2E thực sự cho:

- [`AutomatedFactory.run_smart_reup()`](pipeline/automated_factory.py:114).
- [`AutomatedFactory.execute_approved_reup()`](pipeline/automated_factory.py:202).
- [`AutomatedFactory.run_ai_genesis()`](pipeline/automated_factory.py:272).
- [`VideoAutomationWorker.run()`](core/worker.py:73).
- Worker shutdown giữa lúc provider/upload đang chạy.
- Job cha chuyển FAILED khi shot cuối cùng fail.
- Upload fail giữ file và không DONE.
- Final documentary assembly.
- Real rclone/Telegram/yt-dlp staging flow.
- Container smoke test trên VPS.

Do đó con số `353/353 tests pass` nếu đúng cũng chủ yếu chứng minh **các unit/service contract đang pass**, chưa chứng minh các claim production E2E trong plan.

## Phân loại trạng thái nên dùng trong plan

| Hạng mục | Đánh giá thực tế |
|---|---|
| SQLite queue, account state, provider abstraction | **Implemented – unit-tested** |
| Atomic cookie persistence | **Implemented – cần test crash thực tế** |
| Telegram command dispatcher | **Implemented – mock/unit-tested** |
| CI build/deploy cấu hình | **Implemented – chưa đủ staging evidence** |
| Mode 1 đúng LLM/TTS/matcher/render | **Not implemented trong flow factory hiện tại** |
| Mode 2 full documentary | **Partial/demo pipeline** |
| Veo provider runtime | **Implemented, cần runtime/staging evidence** |
| Upload verification | **Partial; có bug semantics khi thiếu rclone** |
| 10-account production operation | **Chưa đạt; status chỉ ghi 1/10 ready** |
| 24/7 production readiness | **Chưa đủ bằng chứng** |

## Kết luận ưu tiên

Plan hiện **tốt ở phần kiến trúc, rủi ro và định hướng safety**, nhưng phần “hiện trạng đã hoàn thành” đang overclaim. Nếu dùng tài liệu này để quyết định deploy, các blocker lớn nhất là:

1. Mode 1 chưa nối vào pipeline LLM/TTS/matcher/render thật.
2. Mode 2 chưa có final assembly/documentary output trong worker.
3. Upload fallback có thể đánh dấu DONE dù chưa upload cloud.
4. Job failed shot chưa chắc kết thúc ở trạng thái FAILED.
5. Dockerfile không sử dụng `requirements-worker.txt` như plan mô tả.
6. Chưa có staging E2E chứng minh Telegram → YouTube → generation/render → Drive.
7. Status vận hành hiện chỉ có 1/10 account và Telegram chưa active, mâu thuẫn với nhãn production 10-account 24/7.

**Verdict:** Codebase đã có nền tảng đáng kể và nhiều safety primitive đúng hướng, nhưng plan nên được hạ từ **“100% production completed”** xuống **“core infrastructure implemented; end-to-end production validation and several functional integrations remain incomplete.”**



**************feedback 2********************
# Feedback vòng 2 (chỉ đọc, không sửa code)

Mình đã kiểm tra lại từng mục trong [`UPGRADE_2026_10_20_1120.md`](UPGRADE_2026_10_20_1120.md) với code hiện tại. Phần lớn các lỗi ở vòng 1 đã được sửa thật. Tuy vậy vẫn còn **một lỗi chặn đứng Mode 2**, và báo cáo UPGRADE có **vài tuyên bố code chưa làm**. Mình không chạy lại test, nên con số 359 passed là theo báo cáo, chưa được kiểm chứng.

## ✅ Các mục đã sửa đúng
- **Uploader:** khi thiếu rclone thì trả về `False`, có kiểm tra lại bằng `lsf`, có tham số `clean_local`.
- **Dockerfile:** đã chuyển sang `python:3.11-slim` và dùng `requirements-worker.txt`.
- **Chặn SSRF:** [`validate_public_url()`](pipeline/youtube.py:41) đã được gọi khi tải video.
- **Mode 1:** đã nối đủ rewrite → planner → cắt/ghép → TTS → kiểm tra → upload.
- **Mode 2:** số shot đã động (6–24) và đã có [`assemble_documentary()`](pipeline/documentary_assembler.py:20).
- **Account pool:** có giữ chỗ tài khoản (`in_flight`) bên trong filelock. Ken Burns đã chạy dưới heavy lock.
- **Trạng thái job:** khi shot sinh video lỗi hẳn, job đã chuyển sang FAILED.

## 🔴 Nghiêm trọng (cần sửa trước khi chạy production)
1. **`/approve` một job Mode 2 làm job đó FAILED.**
   - Ở [`core/worker.py:59`](core/worker.py:59), `on_approve_handler` luôn gọi [`execute_approved_reup()`](pipeline/automated_factory.py:274), hàm này không kiểm tra mode.
   - Với job Mode 2, nó chuyển sang RENDERING, không tìm thấy `script.json`, rồi đánh FAILED. Các shot sau đó không bao giờ chạy vì [`get_next_pending_shot()`](services/job_queue.py:254) chỉ lấy job ở trạng thái APPROVED/PROCESSING.
   - Hậu quả: Mode 2 không chạy được qua Telegram.
   - Cách sửa: phân nhánh theo mode. Mode 2 chỉ cần gọi `approve_job`.
2. **`BEGIN IMMEDIATE` thực ra chưa có.**
   - [`acquire_heavy_lock()`](services/job_queue.py:90) vẫn là SELECT rồi `INSERT OR REPLACE` ([dòng 108](services/job_queue.py:108)), nên vẫn có race. Báo cáo UPGRADE ghi sai chỗ này.
   - Ngoài ra, timeout 30 phút có thể tự nhả lock trong khi một job render/ráp dài vẫn đang chạy.
3. **Hybrid Veo/Ken Burns chưa được triển khai.**
   - Worker không đọc `is_key_scene`, nên shot nào cũng gọi Veo trước. Quota Veo bị dùng cho cả cảnh phụ.
   - Thêm vào đó, logic đánh dấu cảnh chính `idx % 2 != 0` khiến việc dò từ khóa gần như không có tác dụng.
4. **Ráp phim Mode 2 chưa đồng nhất các clip.**
   - Clip Veo (có thể có audio, fps khác) và clip Ken Burns (25fps, không audio) được nối thẳng qua concat demuxer, không có bước `scale`/`fps`/`-map`.
   - Báo cáo ghi "24fps 1080p" nhưng code không chuẩn hóa như vậy.
   - Bước ráp tìm file `narration.*`/`audio.*` mà không có chỗ nào tạo ra, nên phim ra **không có lời đọc và không có BGM**.
5. **Có thể mất dữ liệu ở bước ráp cuối.**
   - [`shutil.rmtree(shots)`](core/worker.py:277) chạy cả khi upload phim cuối thất bại.
   - `batch_notified_jobs` chỉ là một set trong bộ nhớ, nên nếu worker khởi động lại sau khi xong hết shot thì job đó không bao giờ được ráp.
6. **Không có cờ phụ đề cho yt-dlp.**
   - Không truyền `--write-subs`/`--write-auto-subs`, nên [`try_extract_subtitles()`](pipeline/automated_factory.py:34) không bao giờ tìm thấy phụ đề, và tối ưu "bỏ qua Whisper" không bao giờ được kích hoạt.
   - Nếu bật auto-subs, file VTT có các dòng lặp cuốn chiếu, cần lọc trùng.

## 🟠 Nên sửa
- **Dùng lại file local:**
  - Chỉ kiểm tra file lớn hơn 1KB, không validate nội dung.
  - Vẫn gọi `report_success`, nên quota bị tính dù không sinh video.
- **Lỗi upload shot:** nhánh này không kiểm tra để chốt job, nên job có thể kẹt ở PROCESSING.
- **Mode 1:**
  - TTS lỗi thì vẫn copy `joined.mp4` và báo "lồng tiếng thành công".
  - Kịch bản fallback bị gán `status: approved` trước khi người dùng duyệt.
  - Các segment giả bắt đầu cố định ở 0/30/90s, có thể vượt quá độ dài video.
  - `execute_approved_reup` nên kiểm tra mode/trạng thái chặt hơn.
- **Account pool:**
  - Ghi `pool_state.json` không atomic. File hỏng thì state bị reset im lặng, mất luôn kill-switch.
  - `in_flight` không được reset khi khởi động, nên rò rỉ sau mỗi lần crash.
  - Thời gian cooldown dùng giờ local, chỗ khác dùng UTC.
  - Worker đọc `kill_switch_active` từ bộ nhớ của tiến trình, nên không thấy thay đổi từ tiến trình khác (CLI/bot).
- **Upload:**
  - Timeout 180s có thể không đủ cho phim cuối dung lượng lớn.
  - `lsf` chỉ kiểm tra file tồn tại, không kiểm tra size/checksum.
  - `gdrive://` không phải link chia sẻ được.
- **Telegram:**
  - Số thread xử lý không giới hạn.
  - Bước download không nằm dưới lock nào, nên nhiều lệnh `/reup` cùng lúc có thể làm đầy đĩa/RAM.
- **Docker/CI:**
  - CI cài `requirements.txt` chứ không phải bộ dependency của image worker. Nên thêm smoke test kiểu `docker run <image> python -c "import core.worker"`.
  - Image không có deno/node, nên yt-dlp dễ bị 403.
  - rclone cài từ apt có thể là bản cũ.
  - Container chạy bằng root.
  - CI vẫn chưa chạy ruff.
- **Test còn mỏng:** chưa có test render Mode 1, test vòng lặp worker, test ráp thật với clip lẫn lộn, hay test luồng `/approve` cho Mode 2. Nếu có test này thì lỗi số 1 đã bị bắt.

## 📄 Tuyên bố trong UPGRADE không khớp với code
| Tuyên bố | Code thực tế |
|---|---|
| `BEGIN IMMEDIATE` | Chưa có |
| Mode 1 có BGM ducking | Chỉ duck âm thanh gốc, không có file BGM |
| Mode 2 chuẩn hóa "24fps 1080p" + nhạc/lời đọc | Không chuẩn hóa, không có lời đọc |
| Hybrid key scene | Worker không dùng `is_key_scene` |

## ⚠️ Chưa kiểm tra được
- `requirements-worker.txt` có đủ cho `script_rewrite`/`llm_client`/`tts`/`settings` hay không.
- Phần bên trong `rewrite_script` và `generate_and_overlay_narration`.
- [`scripts/sync_rclone.py`](scripts/sync_rclone.py) và [`tests/test_account_pool.py`](tests/test_account_pool.py).
- Về vận hành: hiện mới có 1/10 tài khoản và Telegram chưa hoạt động, nên chưa có lần chạy end-to-end thật nào.

## Thứ tự nên sửa
1. Phân nhánh `/approve` theo mode.
2. Làm lock atomic thật bằng `BEGIN IMMEDIATE`.
3. Không xóa shot khi upload phim cuối lỗi, và thêm bước quét lại job đã xong shot nhưng chưa ráp khi worker khởi động.
4. Chuẩn hóa clip trong assembler.
5. Dùng `is_key_scene` trong worker.
6. Thêm cờ phụ đề cho yt-dlp.
7. Ghi `pool_state.json` atomic và reset `in_flight` khi khởi động.
8. Thêm smoke test image Docker và test end-to-end.
