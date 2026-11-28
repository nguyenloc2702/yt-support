# Short video: audio → job → agent gen từng shot

Khảo sát ngày 2026-11-28. Mục tiêu: trả lời "đưa audio lên thành job, agent thấy từng
shot để gen video" — cái nào đã có, cái nào thiếu, làm thế nào.

## TL;DR

**80% đã có sẵn.** Job queue cho agent video ngoài (G.13) đã chạy: audio + timing +
karaoke + prompt từng shot + endpoint nộp clip từng scene + assemble. Thiếu đúng **2 mảnh**:

1. **Không có đường nạp audio của owner.** Audio hiện luôn do TTS sinh từ kịch bản
   (`_ensure_voice_timeline`), và `publish_job` **xoá sạch `short_scenes` rồi tạo lại**
   từ cue của TTS. Nạp audio ngoài vào lúc này sẽ bị ghi đè.
2. **Không có claim theo shot.** Nhiều agent cùng chạy sẽ nộp trùng `seq_num` và
   overwrite im lặng; không ai biết shot nào đang bị ai làm.

## 1. Cái đã có (đừng làm lại)

| Thành phần | Vị trí | Ghi chú |
|---|---|---|
| Tạo short từ text → LLM ra kịch bản | `service_shorts.create` (d.80) | bắt buộc `source_text`, gọi `llm.chat_json` |
| Scene = 1 cue thoại | `_sync_scenes` (d.133) | `short_scenes(seq_num, text, start_sec, end_sec, image_prompt)` |
| TTS + word timeline + cue windows + karaoke ASS/SRT | `_ensure_voice_timeline` (d.236) | 1 call edge-tts toàn bài, cắt silence, `_assign_cues_from_words` |
| Phát hành job | `publish_job` (d.660) | `scripted → queued`, event `short.job_ready` |
| Package cho agent | `get_job` (d.708) | mỗi scene: `audio_start_sec/end_sec`, `duration_sec`, `visual_prompt`, `sub_shots`, `stock_search_keywords` |
| Nộp clip từng scene | `submit_clip` (d.796) | 1 clip/scene, hoặc 1 clip gộp nhiều scene qua `covered_scene_indexes` |
| Ghép final | `assemble_job` (d.945) | trim theo window → concat → burn karaoke → mix voice+BGM → QC 1080x1920 |
| Hợp đồng agent | `AGENT_SHORT_GUIDE.md`, `skill_bundle_short.py` | 5 bước poll → get → gen → submit → assemble |

Luồng trạng thái: `scripted → queued → rendering → ready` (`domain.ShortStatus`).

Nghĩa là "agent thấy từng shot để gen video" **đã đúng ở tầng API**: `get_job` trả từng
scene kèm prompt chi tiết + `sub_shots` 2.5–3.5s + từ khoá stock.

## 2. Vì sao "nạp audio" hiện không làm được

`publish_job` (d.669) gọi `_ensure_voice_timeline`, mà hàm này:

```python
voice = tts_vi.synthesize_short_voice(texts, objects, short_id, ...)   # d.251
...
self.store.execute("DELETE FROM short_scenes WHERE short_id=?", ...)    # d.265
for i, text in enumerate(texts, start=1):                               # d.269
    INSERT INTO short_scenes (... start_sec=span["start"], end_sec=span["end"] ...)
```

3 hệ quả:
- Audio owner nạp vào sẽ bị bỏ qua, TTS đè lên.
- Scene window luôn tính từ word-boundary của edge-tts, không phải từ audio thật.
- Karaoke ASS/SRT luôn build từ `voice["words"]` — không có word timing của audio owner
  thì không burn chữ được.

`create_from_upload` (d.1195) **không phải** đường này: nó nhận MP4 thành phẩm, không
có scene, không có job.

## 3. Ba cách lấy timeline từ audio owner

| Cách | Cần gì | Ưu | Nhược |
|---|---|---|---|
| **A. ASR (faster-whisper)** | model `small` (~500MB), CPU ~5–10x realtime | có word timing thật → karaoke chuẩn; transcript dùng làm `narration`; không cần owner gõ text | thêm dependency runtime trên VPS; tiếng Việt có dấu dễ sai tên riêng; cần cache model |
| **B. Audio + text owner gõ** (forced align) | vẫn cần ASR để lấy word timing, rồi khớp với text | transcript chính xác 100% | owner phải dán đúng lời; lệch 1 chữ là align trượt |
| **C. Cắt theo khoảng lặng** (ffmpeg `silencedetect`) | không thêm gì | rẻ, deterministic, không ML | không có word timing → **karaoke mất**; chỉ hợp audio có pause rõ giữa các ý |

`faster-whisper>=1.0,<2.0` **đã có trong `requirements.txt`** nhưng chưa được dùng ở
đâu trong `backend/app` (grep `whisper` = 0 hit) và **chưa cài trong venv local**
(`ModuleNotFoundError`). Tức là dependency đã dự trù, code chưa viết.

Quan trọng: deploy prod (`deploy.yml`) **không** `pip install -r requirements.txt` mỗi lần
— nó chỉ cài thêm `mcp>=1.2` vào `/opt/video-system/.venv`. Nên coi như VPS **chưa có**
`faster-whisper`; muốn dùng phải cài tay + tải model về cache, và thêm bước đó vào
deploy script để lần sau không mất.

**Khuyến nghị: A, fallback C.** Lý do: sản phẩm này bán ở chỗ karaoke cháy chữ + timing
khớp từng shot. Cắt theo silence mà không có word timing thì mất luôn lợi thế đó, và
`assemble_job` vẫn cần `words` để burn ASS.

## 4. Thiết kế đề xuất

### 4.1 Nạp audio

```
POST /api/v1/shorts/{id}/audio        (multipart, scope ingest)
  file=@voice.mp3  lang=vi|en  mode=news|decode|...  beats=[...] (optional)
```

- Lưu asset `kind=audio`, `provenance_engine=owner-upload` (thêm cột
  `shorts.audio_source` = `tts|owner`).
- Probe duration bằng ffprobe (đã có `_probe_video`).
- ASR → `words[]` → ghi `words_{id}.json` → build karaoke ASS/SRT như cũ.
- Nếu owner truyền `beats` (mảng text): dùng `_assign_cues_from_words` để chia window
  theo đúng số beat. Nếu không: chia theo dấu câu + độ dài, tối đa
  `limits.max_scenes = 18`.

### 4.2 `publish_job` phải rẽ nhánh

```python
if row["audio_source"] == "owner":
    voice, scenes, ass, srt = self._timeline_from_owner_audio(short_id, row)
else:
    voice, scenes, ass, srt = self._ensure_voice_timeline(short_id, row)
```

`_timeline_from_owner_audio` **không được** `DELETE short_scenes` nếu owner đã sửa
`image_prompt` tay — chỉ cập nhật `start_sec/end_sec/text`, giữ `image_prompt`.

### 4.3 Trạng thái

Thêm `audio_ready` giữa `draft` và `scripted`? Không cần — dùng `scripted` với
`audio_source=owner` là đủ, tránh nới `SHORT_TRANSITIONS` (đang fail-closed).

### 4.4 Claim theo shot (mảnh thiếu thứ 2)

Hiện `submit_clip` cho overwrite im lặng. Đề xuất bảng `short_scene_claims`:

```
short_id, seq_num, agent, claimed_at, expires_at, PRIMARY KEY(short_id, seq_num)
```

- `POST /api/v1/shorts/{id}/scenes/{seq}/claim` → 200 kèm lease 15 phút;
  agent khác claim → 409 `scene_already_claimed` (kèm `claimed_by`, `expires_at`).
- `GET /job` thêm `claimed_by`/`claimed_until` mỗi scene.
- `submit_clip` của agent khác khi lease còn sống → 409; hết lease thì cho.
- Tái dùng đúng pattern claim lease của `service_board.py` (đã có test) thay vì tự chế.

### 4.5 Nhìn thấy từng shot trên UI

`shorts.js` hiện chỉ render danh sách scene dạng text (`short-scene-list`, d.1119).
Thêm grid kiểu Shotboard: mỗi scene 1 card (số, window, trạng thái clip, agent đang giữ,
nút xem clip đã nộp). Dữ liệu đã có trong `get_job` — chủ yếu là việc frontend.

## 5. Ước lượng

| Việc | Độ phức tạp | Ghi chú |
|---|---|---|
| Endpoint nạp audio + ASR + words/ASS | M | ~250 dòng + config; cần cài `faster-whisper` trên VPS |
| Rẽ nhánh `publish_job` + giữ `image_prompt` | S | ~40 dòng, chạm đúng chỗ d.669 |
| Claim theo shot + 409 | M | ~150 dòng + 5–6 test; copy pattern board |
| UI shot grid | M | frontend thuần, không đổi API |
| Test | M | cần audio mẫu ngắn trong `tests/fixtures` |

## 6. Rủi ro

- **VPS không đủ RAM/CPU** cho whisper: `small` cần ~1GB RAM lúc chạy. Nếu VPS yếu,
  phải chạy ASR ngoài (máy owner) rồi upload kèm `words.json` — vẫn giữ nguyên thiết kế
  nếu cho phép `POST /audio` nhận kèm file `words`.
- **Tiếng Việt có dấu**: whisper hay sai tên riêng → cho owner sửa `narration` sau khi
  ASR, và giữ nguyên `beats` do owner nhập nếu có.
- **Audio dài hơn `max_scenes`**: 1 short 60s chia 18 scene là ~3.3s/scene, sát ngưỡng
  `sub_shots` 2.5–3.5s — vừa đủ, nhưng audio > 90s nên chặn hoặc cảnh báo.
- **Karaoke lệch** nếu audio owner có nhạc nền: word timing của whisper sẽ dính nhạc.
  Cần yêu cầu audio sạch (voice-only), hoặc chạy vocal separation (ngoài scope).

## 7. Cần owner chốt

1. Audio owner là **voice sạch** hay có nhạc nền? (quyết định có cần tách nhạc không)
2. Có cần **karaoke cháy chữ** cho audio owner không? Nếu không, cách C đủ và rẻ hơn nhiều.
3. Owner có sẵn **lời thoại** để dán vào không? (nếu có → cách B chính xác nhất)
4. Có cần **nhiều agent cùng làm 1 short** không? (nếu chỉ 1 agent/job thì bỏ mảnh claim)
5. ASR chạy trên VPS hay trên máy owner?
