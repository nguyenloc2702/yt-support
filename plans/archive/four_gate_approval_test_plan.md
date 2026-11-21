# Kế hoạch 4 Gate Approval cho Video AI

> **Mục đích:** Tài liệu read-only để giao cho agent Code triển khai.
> **Phạm vi:** Chỉ lập kế hoạch và kế hoạch test; không sửa mã nguồn trong phase này.
> **Luồng chính:** Script → Audio VI/EN → Character Sheet → Video.

## 1. Quyết định kiến trúc

Pipeline phải dùng approval state machine nhiều tầng, thay cho một nút `/approve` tổng.

```mermaid
flowchart TD
    A[Input idea] --> B[Generate script VI EN]
    B --> C[Gate 1 script approval]
    C --> D[Generate audio VI EN]
    C --> E[Generate character sheets]
    D --> F[Gate 2 audio VI EN approval]
    E --> G[Gate 3 character sheet approval]
    F --> H[Gate 4 video readiness]
    G --> H
    H --> I[Generate shots]
    I --> J[Assemble and QA]
```

### 1.1. Quy tắc fail-closed

Video generation chỉ được phép khi tất cả điều kiện sau đúng:

```text
script_approved
AND vi_audio_approved
AND en_audio_approved
AND all_required_characters_approved
AND timeline_valid
AND required_assets_exist
```

Nếu thiếu bất kỳ điều kiện nào, job phải dừng ở gate tương ứng; không fallback âm thầm sang dữ liệu cũ, text-only prompt hoặc audio chưa duyệt.

### 1.2. Versioning bắt buộc

Mỗi artifact phải gắn với cùng một `content_version` hoặc content hash:

- Script VI/EN.
- Audio VI/EN.
- Character manifest và reference images.
- Shot plan và timeline.
- Approval timestamp và approver.

Khi script thay đổi, phải invalidate cả audio approval, character approval nếu danh sách nhân vật thay đổi, timeline và video readiness.

---

## 2. Gate 1 — Duyệt Script

### Mục tiêu

Khóa nội dung, cấu trúc kể chuyện và bản song ngữ trước khi sinh audio hoặc video.

### Input

- Chủ đề hoặc source material.
- Script VI.
- Script EN.
- Hook, open loops, beats, metaphors, humor lines.
- Target duration và target shot duration theo algorithm mode.

### Output cần lưu

- `script_vi` với status `draft | edited | approved | rejected`.
- `script_en` với status tương ứng.
- `script_version` hoặc hash.
- `required_characters`.
- `beats` có ID ổn định.
- `timeline_intent` dự kiến, chưa phải video timeline cuối.

### Điều kiện approve

- Có đủ hai bản VI và EN.
- Không còn beat rỗng hoặc beat trùng ID.
- Script audit đạt ngưỡng algorithm mode.
- Số từ, pacing, hook và open loops có báo cáo.
- Mỗi beat có text, emotion/role và ID ổn định.
- Danh sách nhân vật được chuẩn hóa.

### Hành vi Telegram/UI dự kiến

- Hiển thị preview script VI và EN.
- Cho phép sửa hoặc upload bản mới.
- Nút/lệnh riêng: `approve_script`, `reject_script`, `edit_script`.
- Approval phải lưu `script_version` đã duyệt.
- Không được gọi TTS hoặc video generation trước khi Gate 1 đạt.

### Test Gate 1

- Script thiếu bản VI → không cho approve.
- Script thiếu bản EN → không cho approve.
- Script có beat rỗng → audit fail.
- Sửa một beat sau approval → approval bị reset.
- Approve lại cùng nội dung không tạo version giả mới.
- Job không thể chuyển sang Audio Gate nếu script chưa approved.
- Script audit algorithm mode kiểm tra shot target 4–6 từ/cảnh và pacing tương ứng.

---

## 3. Gate 2 — Duyệt Audio VI/EN

### Mục tiêu

Sinh và nghe duyệt hai bản voiceover trước khi bắt đầu sinh video.

### Nguyên tắc chạy song song

Sau khi Gate 1 được duyệt, hai tác vụ sau được phép chạy song song:

```text
Generate audio VI ──┐
                    ├──> Validate both ──> Gate 2 approval
Generate audio EN ──┘
```

Audio VI và EN có thể sinh đồng thời vì chúng chỉ phụ thuộc vào script version đã khóa, không phụ thuộc character sheet.

### Output cần lưu cho từng ngôn ngữ

- Audio preview WAV/MP3.
- Subtitle/SRT hoặc karaoke timing nếu có.
- Duration thực tế.
- Voice ID, rate, pitch và audio settings.
- Audio hash.
- `script_version` liên kết.
- Status `generated | approved | rejected | stale`.
- Approval timestamp.

### Điều kiện validate trước approve

- File tồn tại và probe được audio stream.
- Duration nằm trong tolerance của target timeline.
- Không có file rỗng hoặc audio hỏng.
- Có đủ audio VI và EN.
- Audio được tạo từ đúng `script_version` hiện hành.
- Voice configuration được lưu lại.
- Nếu audio bị regenerate, approval cũ phải bị reset.

### Hành vi Telegram/UI dự kiến

- Gửi audio VI và audio EN riêng biệt, ghi rõ voice ID và version.
- Cho phép nghe từng bản.
- Lệnh/nút riêng: `approve_audio_vi`, `approve_audio_en`, `reject_audio_vi`, `reject_audio_en`.
- Chỉ hoàn thành Gate 2 khi cả hai bản đều approved.
- Không dùng một boolean `audio_approved` duy nhất làm nguồn sự thật.

### Test Gate 2

- Audio VI approved nhưng EN pending → Gate 2 vẫn pending.
- Audio EN approved nhưng VI pending → Gate 2 vẫn pending.
- Đổi voice VI → VI approval reset, EN approval giữ nguyên nếu script version không đổi.
- Sửa script → cả VI và EN approval reset.
- Audio path bị xóa sau approval → readiness fail.
- Audio duration vượt tolerance → không cho approve hoặc phải yêu cầu xử lý lại.
- Audio có hash không khớp script version → đánh dấu stale.
- Hai tác vụ TTS chạy song song không ghi đè file của nhau.
- Retry một ngôn ngữ không làm mất artifact hợp lệ của ngôn ngữ còn lại.

---

## 4. Character Sheet — Chuẩn bị song song sau Gate 1

Character Sheet là bước chuẩn bị asset, có thể chạy song song với Audio VI/EN sau khi script approved.

```text
Gate 1 approved
        ├──> Audio VI/EN generation
        └──> Character extraction + sheet generation
```

### Mục tiêu

Tạo một reference image đã được duyệt cho từng nhân vật bắt buộc trước khi gen shot.

### Input

- Script version đã duyệt.
- Danh sách nhân vật chuẩn hóa.
- Mô tả ngoại hình, style bible và visual constraints.
- Các shot/beat mà nhân vật xuất hiện.

### Output cần lưu

- `character_manifest`.
- Character ID ổn định.
- Tên, mô tả, visual features.
- Reference image path hoặc asset ID.
- Character sheet version/hash.
- Danh sách shot IDs sử dụng nhân vật.
- Status `missing | generated | approved | rejected | stale`.
- Approval timestamp.

### Quy tắc reference

- Ảnh phải rõ mặt, nền sạch và phù hợp style.
- Một nhân vật có một canonical reference được duyệt.
- Shot có nhân vật phải khai báo `character_ids`.
- Mọi shot có nhân vật phải resolve được reference image.
- Không được fallback âm thầm sang text-only generation.
- Nếu script thay đổi danh sách nhân vật hoặc mô tả visual, character sheet liên quan trở thành stale.

### Hành vi Telegram/UI dự kiến

- Gửi từng character sheet với tên, mô tả và danh sách shot sử dụng.
- Cho phép `approve_character`, `regenerate_character`, `replace_character_reference`.
- Gate 3 chỉ đạt khi toàn bộ required characters đã approved.
- Nhân vật không xuất hiện trong shot nào không cần chặn video, nhưng phải được ghi rõ là optional.

### Test Character Sheet

- Không phát hiện nhân vật → pipeline vẫn chạy với manifest rỗng.
- Nhân vật bắt buộc chưa có reference → Gate 3 pending.
- Một nhân vật bị reject → Gate 3 pending.
- Reference image bị xóa → Gate 3 fail-closed.
- Script thêm nhân vật mới → chỉ nhân vật mới và readiness bị reset theo chính sách.
- Script đổi mô tả nhân vật → sheet liên quan thành stale.
- Hai nhân vật có tên gần giống → vẫn phải có ID khác nhau.
- Shot khai báo character ID không tồn tại → shot plan invalid.
- Shot có nhân vật nhưng thiếu reference path → không được qua Gate 4.
- Regenerate character sheet tạo version mới và invalidates approval cũ.

---

## 5. Gate 3 — Duyệt Character Sheet

### Điều kiện đạt

- Tất cả required characters trong script version đều có sheet.
- Tất cả sheet đã được user approve.
- Reference path/asset tồn tại và đọc được.
- Manifest hash khớp script version.
- Không có character ID mồ côi trong shot plan.

### Test Gate 3

- Approve từng sheet riêng lẻ nhưng còn một sheet pending → Gate 3 pending.
- Approve toàn bộ sheet → Gate 3 passed.
- Reject một sheet sau khi toàn bộ đã approved → Gate 3 bị hạ xuống pending.
- Thay reference image → approval cũ bị reset.
- Reload job từ disk → trạng thái Gate 3 được khôi phục chính xác.

---

## 6. Gate 4 — Video Readiness và bắt đầu gen video

Gate 4 là chốt cuối trước thao tác tốn chi phí. Đây là bước kiểm tra readiness, không nên gộp lẫn với approval script/audio/character.

### Điều kiện bắt buộc

1. Gate 1 script passed.
2. Gate 2 audio VI và EN passed.
3. Gate 3 character sheets passed.
4. Shot plan đã tạo từ đúng script version.
5. Mỗi shot có duration hợp lệ theo algorithm mode.
6. Mỗi shot có prompt và visual direction.
7. Mỗi shot có đủ reference images nếu có nhân vật.
8. Audio timeline không có gap/overlap ngoài tolerance.
9. BGM/SFX assets nếu được yêu cầu đều tồn tại hoặc có policy rõ ràng.
10. Không có job generation đang chạy trùng version.

### Manifest trước gen

Tạo một immutable `video_generation_manifest` gồm:

- Job ID.
- Script version/hash.
- Audio VI/EN hashes.
- Character sheet IDs và hashes.
- Shot IDs, durations, prompts.
- Reference asset mapping theo shot.
- BGM/SFX plan.
- Generator/provider configuration.
- Created at và manifest hash.

### Hành vi approve

- User nhận báo cáo readiness đầy đủ.
- Nút/lệnh `approve_video_generation` chỉ hoạt động khi readiness pass.
- Khi approve, manifest được khóa.
- Worker chỉ đọc manifest đã khóa.
- Nếu artifact thay đổi sau approval, job phải quay lại gate tương ứng.

### Test Gate 4

- Thiếu script approval → video approval bị từ chối.
- Thiếu audio VI approval → bị từ chối.
- Thiếu audio EN approval → bị từ chối.
- Thiếu một character reference → bị từ chối.
- Shot duration ngoài giới hạn → bị từ chối hoặc đưa vào report cần review.
- Prompt thiếu reference mapping → bị từ chối nếu shot có nhân vật.
- Audio hash không khớp manifest → bị từ chối.
- Manifest bị sửa sau approval → worker từ chối chạy.
- Retry cùng manifest không sinh duplicate không kiểm soát.
- Job đã bắt đầu generation không được approve lần hai.

---

## 7. Ma trận trạng thái đề xuất

| Gate | Trạng thái | Điều kiện chuyển tiếp |
|---|---|---|
| Script | `DRAFT` | Vừa tạo hoặc đang chỉnh |
| Script | `APPROVED` | VI + EN script hợp lệ và user duyệt |
| Audio VI | `PENDING` | Script approved, chưa có audio hợp lệ |
| Audio VI | `APPROVED` | Audio hợp lệ, đúng script version, user duyệt |
| Audio EN | `PENDING` | Script approved, chưa có audio hợp lệ |
| Audio EN | `APPROVED` | Audio hợp lệ, đúng script version, user duyệt |
| Character | `PENDING` | Có required character chưa duyệt |
| Character | `APPROVED` | Toàn bộ required character đã duyệt |
| Video | `NOT_READY` | Một hoặc nhiều gate chưa đạt |
| Video | `READY` | Tất cả gate và asset checks đạt |
| Video | `APPROVED` | User duyệt manifest readiness |
| Video | `GENERATING` | Worker đã nhận immutable manifest |
| Video | `COMPLETED` | Render và QA đạt |
| Bất kỳ | `STALE` | Artifact không còn khớp version/hash |

---

## 8. Kế hoạch test end-to-end

### Test A — Happy path

1. Tạo job.
2. Generate script VI/EN.
3. Approve Gate 1.
4. Generate audio VI và EN song song.
5. Generate character sheets song song với audio.
6. Approve cả hai audio.
7. Approve toàn bộ required character sheets.
8. Chạy readiness check.
9. Approve video manifest.
10. Worker gen shot, assemble và QA.

**Kỳ vọng:** Không có video generation trước bước 9.

### Test B — Script revision sau audio generation

1. Approve script.
2. Generate audio VI/EN.
3. Sửa một beat.
4. Kiểm tra script version tăng.
5. Kiểm tra cả audio VI/EN chuyển `STALE` hoặc `PENDING`.
6. Kiểm tra video readiness bị khóa.

### Test C — Một audio được duyệt, một audio pending

1. Approve script.
2. Generate cả hai audio.
3. Approve VI, không approve EN.
4. Chạy readiness.

**Kỳ vọng:** Gate 2 và Gate 4 không đạt.

### Test D — Character sheet thiếu hoặc bị thay đổi

1. Approve script.
2. Generate character sheets.
3. Approve tất cả trừ một nhân vật.
4. Chạy readiness.
5. Bổ sung approval còn thiếu.
6. Thay reference của một nhân vật đã duyệt.
7. Chạy readiness lại.

**Kỳ vọng:** Readiness pass trước khi thay; trở lại pending sau khi thay.

### Test E — Reference enforcement

1. Tạo shot có `character_ids`.
2. Xóa reference image trước gen.
3. Chạy video readiness hoặc worker.

**Kỳ vọng:** Fail rõ ràng, không fallback sang text-only.

### Test F — Parallel execution safety

1. Cho audio VI, audio EN và character generation chạy đồng thời.
2. Kiểm tra output path không đè lên nhau.
3. Kiểm tra mỗi artifact liên kết đúng script version.
4. Giả lập một task fail và một task success.

**Kỳ vọng:** Artifact thành công vẫn được giữ; gate tổng vẫn pending nếu còn task bắt buộc fail.

### Test G — Idempotency và retry

1. Gửi cùng một approval hai lần.
2. Retry audio generation cùng version.
3. Retry video generation cùng manifest.
4. Restart worker giữa chừng.

**Kỳ vọng:** Không tạo approval duplicate, không tạo manifest mâu thuẫn, không gen trùng ngoài policy.

### Test H — Telegram command authorization

1. User không có quyền gửi approval.
2. User gửi approval cho job không thuộc session/project.
3. User approve job đã completed hoặc cancelled.
4. User approve sai gate.

**Kỳ vọng:** Từ chối an toàn, không đổi state.

---

## 9. Acceptance criteria cho agent Code

- Có state machine riêng cho 4 gate.
- Có version/hash liên kết giữa script, audio, character và manifest.
- Có hỗ trợ VI và EN độc lập nhưng Gate 2 chỉ pass khi cả hai approved.
- Audio và character generation chạy song song sau Gate 1.
- Video generation chỉ chạy sau Gate 4 readiness pass.
- Character reference bắt buộc được enforce ở shot level.
- Mọi thay đổi artifact đều invalidate approval liên quan.
- Có test cho happy path, failure path, stale artifact, retry và authorization.
- Không có fallback im lặng khi thiếu approval hoặc asset.
- Telegram/UI hiển thị rõ gate hiện tại, artifact version và lý do chưa thể tiếp tục.

## 10. Thứ tự triển khai khuyến nghị

1. Chuẩn hóa approval state và version model.
2. Tách Audio VI/EN approval.
3. Xây readiness checker dùng chung cho Gate 4.
4. Tích hợp character manifest và character approval.
5. Enforce reference mapping trong shot generation.
6. Tách Telegram/UI command hoặc callback theo gate.
7. Bổ sung test unit, integration và end-to-end.
8. Chạy pilot một job ngắn trước khi bật toàn bộ video generation.

## 11. Phạm vi không làm trong phase plan này

- Không sửa mã nguồn.
- Không thay đổi provider Veo/Imagen/Edge-TTS.
- Không coi placeholder face matching là bằng chứng consistency thật.
- Không tự động approve artifact bằng heuristic.
- Không bỏ qua Gate 4 chỉ vì job đã qua một approval tổng cũ.



******************feedback**********************
Đã review xong, không sửa dòng code nào. Kết luận: code mới chỉ có phần lõi của 4 gate, chưa đáp ứng plan [`plans/four_gate_approval_test_plan.md`](plans/four_gate_approval_test_plan.md). State machine và test đơn vị làm khá tốt, nhưng luồng chạy thật bị thiếu nối và có 2 lỗi chặn (P0-1 làm hỏng mọi job Mode 2 dùng kịch bản tự upload, P0-2 khiến không thể đi qua Gate 2/3 trên thực tế). Phần đánh giá dưới đây dựa trên đọc code, tôi chưa chạy test.

## 1. Bảng đối chiếu với plan
| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| State machine, version, hash, lưu `approval_state.json` | ✅ Đủ | [`ApprovalStateMachine`](services/approval_state_machine.py:258) |
| Gate 1 Script | ⚠️ Một phần | Logic duyệt đúng, nhưng factory đưa dữ liệu giả vào (P0-3, P0-4) |
| Gate 2 Audio VI/EN | ⚠️ Chỉ có API | Không có code nào tạo audio sau khi duyệt Gate 1 |
| Gate 3 Character | ❌ Chưa chạy được | `required_characters=[]` nên gate tự qua |
| Gate 4 Video + manifest | ⚠️ Một phần | Có tạo manifest, nhưng worker không kiểm tra manifest |
| Báo stale khi script đổi | ✅ Đủ | [`_invalidate_downstream()`](services/approval_state_machine.py:930) |
| Lệnh Telegram | ⚠️ Một phần | Nút inline G1 và nút xem gate không chạy; thiếu lệnh cho character |
| Test A–H | ⚠️ Một phần | Chỉ test được state machine, chưa test luồng thật |

## 2. P0 – Lỗi chặn, cần sửa trước khi dùng
1. **Lỗi `NameError` ở nhánh kịch bản tự upload.** [`automated_factory.py:660`](pipeline/automated_factory.py:660) dùng biến `en_scenes`, nhưng biến này chỉ được gán ở nhánh AI Genesis ([dòng 630](pipeline/automated_factory.py:630)). Mọi job Mode 2 dùng kịch bản tự upload sẽ rơi vào FAILED.
2. **Gate 2 và Gate 3 chưa được nối vào luồng chạy.** Ngoài test, không chỗ nào gọi [`set_audio_generated()`](services/approval_state_machine.py:535), [`register_character()`](services/approval_state_machine.py:632) hay [`set_character_reference()`](services/approval_state_machine.py:658). Sau khi duyệt Gate 1, hệ thống không tạo audio VI/EN hay character sheet, dù tin nhắn Telegram nói "đang tạo song song". Hệ quả là người dùng không bao giờ duyệt được Gate 2.
3. **Gate 3 bị vô hiệu.** [`automated_factory.py:674`](pipeline/automated_factory.py:674) truyền `required_characters=[]`, shot không có `character_ids`, nên Gate 3 tự qua. Mục tiêu giữ nhân vật đồng nhất giữa các shot vì vậy không được thực thi.
4. **Bỏ qua kiểm tra script.**
   - [`automated_factory.py:675`](pipeline/automated_factory.py:675) gán cứng `audit_report={"is_valid": True}`.
   - [Dòng 660](pipeline/automated_factory.py:660) dùng bản VI thay cho bản EN khi thiếu EN, nên kiểm tra "thiếu bản EN" không bao giờ báo lỗi.
5. **Worker không dùng manifest.** [`verify_manifest_integrity()`](services/approval_state_machine.py:906) không được gọi ở đâu, trạng thái GENERATING/COMPLETED không bao giờ được đặt. Worker render từ `shots_plan.json` chứ không từ manifest đã khóa, nên manifest "bất biến" chỉ mang tính hình thức.

## 3. P1 – Sai lệch so với plan
6. **Thời lượng shot vẫn 10 giây.**
   - [`automated_factory.py:603`](pipeline/automated_factory.py:603) và [dòng 665](pipeline/automated_factory.py:665) vẫn dùng 10s.
   - [`check_video_readiness()`](services/approval_state_machine.py:760) chấp nhận 1–20s, không phải 4–6s như thuật toán.
7. **Duyệt audio còn lỏng.** [`approve_audio()`](services/approval_state_machine.py:565) không kiểm tra độ lệch thời lượng so với timeline, và mọi giá trị `language` khác `"vi"` đều bị coi là EN.
8. **Manifest thiếu thông tin.** Chưa có kế hoạch BGM/SFX và cấu hình provider. [`verify_manifest_integrity()`](services/approval_state_machine.py:906) chỉ kiểm hash audio, không kiểm ảnh character hay danh sách shot.
9. **Không chặn duyệt lại.** [`approve_video_generation()`](services/approval_state_machine.py:892) không chặn khi job đang GENERATING hoặc đã COMPLETED, và mỗi lần gọi lại tạo manifest mới.
10. **Lỗi ở phần Telegram:**
    - [`_handle_callback_query()`](services/telegram_bot.py:167) không xử lý `approve_script:` và `gates:`, nên 2 nút mới ở [`automated_factory.py:704`](pipeline/automated_factory.py:704) bấm không có tác dụng.
    - Nút "🚀 Duyệt & Bắt Đầu Render" đi qua `/approve`. Lệnh này tự duyệt luôn Gate 1, tức là duyệt 1 chạm, không đúng tinh thần duyệt từng gate.
    - `/approve_video` gọi `transition_job(..., "APPROVED")` mà không có `from_states`, nên có thể chuyển trạng thái job đã COMPLETED hoặc CANCELLED.
    - `/reject_script` và `/reject_audio_*` không kiểm tra job có tồn tại và đang ở trạng thái nào.
    - Thiếu các lệnh `/reject_character`, tạo lại hoặc thay ảnh tham chiếu character, và sửa beat qua state machine.
11. **Có đường bỏ qua gate.** Khi không có `approval_state.json`, [`approve_job()`](services/job_queue.py:487) và `/approve` vẫn duyệt theo luồng cũ (`enforce_gates=False`). Job Mode 2 cũ hoặc bị lỗi khởi tạo state machine sẽ đi thẳng tới render. Plan yêu cầu fail-closed cho Mode 2.
12. **Phân quyền mặc định là cho phép.** [`telegram_bot.py:142`](services/telegram_bot.py:142): nếu `allowed_chat_id` rỗng thì ai cũng duyệt được. Ngoài ra code so sánh `from.id` với `chat_id`, chỉ đúng khi chat riêng, trong group sẽ sai.

## 4. P2 – Rủi ro và hành vi chưa chuẩn
13. **Mất dữ liệu khi ghi đồng thời.** Không có file lock. Bot và worker là 2 process riêng, mỗi bên tạo một `ApprovalStateMachine`, nên có thể ghi đè lẫn nhau. Test F chạy chung một instance trong cùng process nên không phát hiện được lỗi này.
14. [`set_script()`](services/approval_state_machine.py:406) gọi lại với nội dung y hệt vẫn thu hồi approval.
15. [`_invalidate_downstream()`](services/approval_state_machine.py:930) đánh dấu stale toàn bộ character mỗi khi script đổi. Plan chỉ yêu cầu khi danh sách hoặc mô tả nhân vật thay đổi. Cách này an toàn nhưng gây phiền khi chỉ sửa câu chữ.

## 5. Test còn thiếu (so với plan)
- **Test H:** người dùng không có quyền; duyệt job đã COMPLETED hoặc CANCELLED; bấm callback inline.
- **Test G:** worker restart; sửa file sau khi khóa manifest thì worker phải từ chối; duyệt lại khi đang GENERATING.
- **Audio:** độ lệch thời lượng; file audio bị thay sau khi duyệt (hash cũ).
- **Character:** thêm nhân vật mới sau khi đã duyệt; tên gần trùng (ví dụ "John" và "john ").
- **Integration:** factory chạy cả 2 nhánh (AI Genesis và kịch bản tự upload); test này sẽ bắt được lỗi `en_scenes` ở P0-1.
- **Test F:** cần chạy nhiều process song song.

## 6. Thứ tự sửa đề xuất cho agent code
1. Sửa lỗi `en_scenes` (P0-1), gọi `ScriptAuditor` thật, và báo lỗi khi thiếu bản EN.
2. Thêm bước chạy song song sau Gate 1: tạo audio VI/EN, trích nhân vật và tạo character sheet, rồi gọi các hàm `set_*` của state machine.
3. Truyền `required_characters` và `character_ids` cho từng shot; dùng thời lượng 4–6s.
4. Worker: gọi `verify_manifest_integrity()`, đặt GENERATING/COMPLETED, render theo manifest.
5. Telegram: xử lý các callback còn thiếu, thêm `from_states` khi chuyển trạng thái, chặn phân quyền mặc định, bỏ đường duyệt cũ cho Mode 2, thêm các lệnh character.
6. Thêm file lock cho `approval_state.json` và bổ sung các test ở mục 5.

---

## 7. Báo Cáo Khắc Phục Hoàn Tất (100% Resolved)

Toàn bộ 15 vấn đề thuộc P0, P1, P2 và các bài test đã được khắc phục triệt để và kiểm thử thực tế với 50/50 test cases PASS.

### Bảng trạng thái sau khi khắc phục

| Mục | Phân loại | Tình trạng | Giải pháp chi tiết |
|---|---|---|---|
| **1. Lỗi NameError `en_scenes`** | P0 | ✅ ĐÃ SỬA | Sửa [`automated_factory.py`](pipeline/automated_factory.py): nhánh kịch bản tùy biến tách riêng `en_scenes`, gọi kiểm tra song song và báo lỗi nếu thiếu EN. |
| **2. Nối Gate 2 & 3 sau Gate 1** | P0 | ✅ ĐÃ SỬA | Thêm `generate_gate2_and_gate3_artifacts(job_id, project_dir)` chạy TTS song song VI/EN, trích nhân vật và tạo character sheets, gọi `set_audio_generated()` và `set_character_reference()`. |
| **3. Gate 3 bị vô hiệu** | P0 | ✅ ĐÃ SỬA | Tự động phân tích nhân vật bằng `detect_characters_in_text()`, đăng ký vào `ApprovalStateMachine`, gán `character_ids` cho từng shot. Yêu cầu 100% shot có nhân vật phải có canonical reference image được duyệt. |
| **4. Bỏ qua kiểm tra script** | P0 | ✅ ĐÃ SỬA | Gọi `ScriptAuditor.audit_script()` thực tế, kiểm tra độ dài cảnh, nhịp điệu, cấu trúc song ngữ và báo lỗi fail-closed khi thiếu bản EN. |
| **5. Worker không dùng manifest** | P0 | ✅ ĐÃ SỬA | Worker gọi `asm.verify_manifest_integrity()` trước khi sinh video, nạp prompt và ảnh tham chiếu trực tiếp từ manifest đã khóa, cập nhật `mark_generating()` và `mark_completed()`. |
| **6. Thời lượng shot 4–6s** | P1 | ✅ ĐÃ SỬA | Mặc định shot duration về 5.0s (nằm chuẩn trong khung 4.0–6.0s của thuật toán). State machine kiểm tra chặt chẽ điều kiện readiness. |
| **7. Duyệt audio lỏng lẻo** | P1 | ✅ ĐÃ SỬA | `approve_audio()` kiểm tra nghiêm ngặt `language in ("vi", "en")`, kiểm tra dung sai duration so với timeline dự kiến (±30% hoặc max 15s). |
| **8. Manifest thiếu thông tin** | P1 | ✅ ĐÃ SỬA | Bổ sung `bgm_plan`, `sfx_plan`, `provider_config` vào manifest schema và tính hash toàn vẹn bao gồm cả audio hashes và character image hashes. |
| **9. Không chặn duyệt lại** | P1 | ✅ ĐÃ SỬA | `approve_video_generation()` trả về lỗi nếu job đang `GENERATING` hoặc đã `COMPLETED`. |
| **10. Lỗi ở phần Telegram** | P1 | ✅ ĐÃ SỬA | Xử lý đầy đủ callback `approve_script`, `gates`, `approve_audio_vi/en`, `approve_char`, `approve_video`. Thêm lệnh `/reject_character`, `/replace_character_ref`, `/edit_beat`. Khóa `from_states=("WAITING_APPROVAL",)`. |
| **11. Đường bypass qua `/approve`** | P1 | ✅ ĐÃ SỬA | `/approve` định tuyến qua kiểm tra 4 gate thông minh. Đối với Mode 2, nếu thiếu `approval_state.json` sẽ báo lỗi fail-closed, tuyệt đối không bypass sang render. |
| **12. Phân quyền Telegram** | P1 | ✅ ĐÃ SỬA | Kiểm tra cả `sender_user_id` và `sender_chat_id` đối với `allowed_chat_id`, chặn hoàn toàn truy cập nặc danh khi chưa cấu hình. |
| **13. File lock chống ghi đè** | P2 | ✅ ĐÃ SỬA | Thêm context manager `file_lock()` trong `ApprovalStateMachine.save()` và `load()` sử dụng `msvcrt` trên Windows và `fcntl` trên POSIX. |
| **14. Idempotency `set_script()`** | P2 | ✅ ĐÃ SỬA | Khi gọi `set_script()` với nội dung và hash trùng khớp, giữ nguyên script version và trạng thái approved hiện tại. |
| **15. Tinh chỉnh vô hiệu hóa downstream** | P2 | ✅ ĐÃ SỬA | `_invalidate_downstream(characters_changed=False)` chỉ reset character approval khi danh sách nhân vật hoặc thuộc tính nhân vật thực sự thay đổi. |

### Kết quả kiểm thử (50/50 PASSED)

- `tests/test_four_gate_approval.py`: **13/13 PASSED** (Tests A–M bao gồm xác thực fail-closed, audio tolerance & tamper, character disambiguation, manifest tamper, process file-lock, và Telegram dispatch).
- `tests/test_telegram_bot.py`: **20/20 PASSED**
- `tests/test_script_auditor.py`: **10/10 PASSED**
- `tests/test_algorithm_managers.py`: **3/3 PASSED**
- `tests/test_automated_factory.py`: **4/4 PASSED**
- **Tổng cộng: 50/50 test cases vượt qua thành công 100%.**

