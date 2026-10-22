# VIDEO PRODUCTION SKILL
## Kỹ năng sản xuất video AI cuốn hút (Dựa trên thuật toán UNIVERSAL_VIDEO_ALGORITHM v1.0)

> **Tên skill:** `video_production_expert`
> **Phiên bản:** `v1.0.0`
> **Mục đích:** Hướng dẫn AI hoặc con người sản xuất video giải thích/tiểu sử/history có độ cuốn hút cao
> **Nguồn:** Đúc kết từ phân tích video Crayon Capital (384K views) + thuật toán [`UNIVERSAL_VIDEO_ALGORITHM.md`](UNIVERSAL_VIDEO_ALGORITHM.md)

---

## 🎯 KHẢ NĂNG CỐT LÕI

Skill này cho phép:
1. **Phân tích video mẫu** - Reverse-engineer bất kỳ video nào để rút ra Style DNA
2. **Thiết kế kịch bản** - Viết kịch bản theo thuật toán 8 bước
3. **Tối ưu nhịp** - Đảm bảo WPM, câu ngắn, khoảng lặng đạt ngưỡng
4. **Thiết kế hình ảnh** - Áp dụng Master Style Prompt và visual changes
5. **Thiết kế âm thanh** - BGM mood shifts, SFX mapping, audio ducking
6. **QA tự động** - Kiểm tra tất cả metrics trước khi xuất bản

---

## 📚 KIẾN THỨC NỀN TẢNG

### 1. Nguyên lý tâm lý giữ chân người xem

**"Người xem không rời đi vì họ muốn biết câu trả lời cho câu hỏi bạn đã đặt ra."**

- **Hook:** Tạo ra câu hỏi/nghịch lý trong 15–30 giây đầu
- **Open Loops:** Mở các vòng tò mò lồng nhau, trì hoãn câu trả lời
- **Callback:** Trả lời câu hỏi ban đầu ở phần kết

### 2. Cấu trúc câu chuyện hiệu quả

```
HOOK (15-30s)
├── Paradox / Shock comparison / Question
└── Open main loop

SETUP (20-30% thời lượng)
├── Introduce character/goal
└── Open loop 1

RISING ACTION (30-40%)
├── Loop 1 resolution
├── Open loop 2
└── Open loop 3

CRISIS (10-15%)
├── All loops open
└── Highest tension

BREAKTHROUGH (10-15%)
├── Close loop 3
├── Close loop 2
└── Close loop 1

PAYOFF + CALLBACK (5-10%)
└── Answer the hook question
```

### 3. Nhịp kịch bản đo được

| Chỉ số | Ngưỡng | Cách đạt được |
| :--- | :--- | :--- |
| Short sentences (≤6 từ) | ≥ 45% | Viết câu ngắn xen kẽ câu dài |
| WPM | 160–200 | Điều chỉnh tốc độ đọc |
| Dramatic pauses (≥600ms) | ≥ 8/phút | Chèn khoảng lặng trước punchline |
| Open loops | ≥ 3 | Mỗi đoạn có câu hỏi chưa trả lời |

---

## 🛠️ CÔNG CỤ & THƯ VIỆN SỬ DỤNG

### Công cụ có sẵn trong repo

| Nhiệm vụ | Công cụ | File | Mô tả |
| :--- | :--- | :--- | :--- |
| Tải video YouTube | yt-dlp | [`pipeline/youtube.py`](pipeline/youtube.py) | Tải video, audio, subtitle |
| Transcribe | Faster-Whisper | [`pipeline/transcribe.py`](pipeline/transcribe.py) | Chuyển giọng nói thành văn bản |
| Phân tích shot | PySceneDetect | [`pipeline/shot_analysis.py`](pipeline/shot_analysis.py) | Cắt cảnh tự động |
| Trích keyframe | OpenCV/FFmpeg | [`pipeline/keyframes.py`](pipeline/keyframes.py) | Lấy khung hình đại diện |
| VLM captioning | xKiro/Gemini | [`pipeline/vlm_captioning.py`](pipeline/vlm_captioning.py) | Mô tả hình ảnh |
| LLM analysis | xKiro/Gemini | [`pipeline/llm_client.py`](pipeline/llm_client.py) | Phân tích kịch bản |
| TTS | xKiro/Edge-TTS | [`pipeline/tts.py`](pipeline/tts.py) | Chuyển văn bản thành giọng nói |
| Phân tích audio | FFmpeg | [`pipeline/audio_analysis.py`](pipeline/audio_analysis.py) | Đo loudness, WPM |
| Ghép video | FFmpeg | [`pipeline/renderer.py`](pipeline/renderer.py) | Render video hoàn chỉnh |
| Ken Burns | FFmpeg | [`services/image_kenburns_provider.py`](services/image_kenburns_provider.py) | Hiệu ứng zoom/pan |

---

## 📝 QUY TRÌNH SẢN XUẤT (8 Bước)

### Bước 1: Xác định chủ đề + Hook

**Input:** Chủ đề (ví dụ: "Lịch sử của Nvidia")

**Công việc:**
1. Nghiên cứu chủ đề
2. Tìm số liệu gây sốc
3. Tạo so sánh bất ngờ
4. Viết hook ≤ 30 giây

**Công thức:**
```
hook = f"[Số liệu khủng] + [So sánh bất ngờ] + [Câu hỏi treo]"
```

**Ví dụ:**
```
"Năm 2024, Nvidia có giá trị gấp 10 lần GDP của Việt Nam. 
Làm sao một công ty chỉ bán card màn hình lại có thể mạnh như vậy?"
```

**Kiểm tra:**
- [ ] Hook ≤ 30 giây
- [ ] Có số liệu cụ thể
- [ ] Có so sánh bất ngờ
- [ ] Có câu hỏi chưa có lời giải

---

### Bước 2: Phân rã thành Open Loops

**Input:** Chủ đề + Hook

**Công việc:**
1. Xác định loop chính (toàn video)
2. Chia thành 3–5 loop trung
3. Mỗi loop có câu hỏi + câu trả lời

**Cấu trúc:**
```json
{
  "open_loops": [
    {
      "opened_at": 18.0,
      "closed_at": 1140.0,
      "question": "Làm sao Nvidia trở thành gã khổng lồ?",
      "resolution": "Nhờ AI, game, và data center"
    },
    {
      "opened_at": 120.0,
      "closed_at": 300.0,
      "question": "Liệu GPU có thể dùng cho game 3D?",
      "resolution": "Có, và nó thay đổi ngành công nghiệp"
    }
  ]
}
```

**Kiểm tra:**
- [ ] ≥ 3 open loops
- [ ] Loop sau mở trước khi loop trước đóng
- [ ] Tất cả loops đều có closed_at

---

### Bước 3: Viết kịch bản theo nhịp 54/46

**Input:** Open loops + nghiên cứu

**Công việc:**
1. Viết câu ngắn (≤6 từ) cho 54% kịch bản
2. Viết câu dài (7–15 từ) cho 46%
3. Chèn khoảng lặng kịch tính (≥600ms) trước punchline
4. Thêm ẩn dụ cho khái niệm khó
5. Thêm câu hài hước

**Kỹ thuật:**
- **Câu ngắn chốt:** Sau đoạn giải thích dài
  - ❌ "Và sau đó anh ta đã mất hết tiền"
  - ✅ "Hết tiền."
- **Câu hỏi tu từ:** Mở loop
  - ❌ "Anh ta gặp khó khăn"
  - ✅ "Vậy làm sao anh ta sống sót?"
- **Ẩn dụ:** Giải thích khái niệm
  - ❌ "GPU tính toán song song"
  - ✅ "Tưởng tượng 1000 người làm việc cùng lúc thay vì 1 người"

**Kiểm tra:**
- [ ] ≥ 45% câu ngắn
- [ ] WPM: 160–200
- [ ] ≥ 8 khoảng lặng kịch tính/phút
- [ ] ≥ 3 ẩn dụ
- [ ] ≥ 3 câu hài hước

---

### Bước 4: Chia shot và gán visual change

**Input:** Kịch bản

**Công việc:**
1. Chia kịch bản thành shots 4.0–7.0 giây
2. Mỗi shot có 1–3 visual changes
3. Xác định background tone theo cảm xúc

**Cấu trúc:**
```json
{
  "shots": [
    {
      "shot_id": 1,
      "start": 0.0,
      "end": 5.0,
      "duration": 5.0,
      "script": "Câu nói trong shot",
      "visual_changes": [
        {"time": 1.5, "type": "ken_burns_zoom", "amount": 1.08},
        {"time": 3.0, "type": "pop_in", "element": "icon"}
      ],
      "background_tone": "off-white",
      "is_key_scene": true
    }
  ]
}
```

**Visual change types:**
- `ken_burns_zoom`: Phóng to 1.05–1.15x
- `ken_burns_pan`: Di chuyển 5–15%
- `pop_in`: Hiện ra với scale 0→1.1→1.0
- `slide_in`: Trượt từ cạnh
- `draw_on`: Hiệu ứng vẽ dần
- `color_shift`: Thay đổi tông nền

**Kiểm tra:**
- [ ] Mean shot duration: 4.0–7.0s
- [ ] ≥ 1 visual change per shot
- [ ] Background tone phản ánh cảm xúc

---

### Bước 5: Thiết kế âm thanh

**Input:** Kịch bản + Shots

**Công việc:**
1. Tách voice cues
2. Thiết kế BGM mood shifts
3. Ánh xạ SFX events

**Cấu trúc:**
```json
{
  "voice": {
    "cues": [
      {"text": "Câu nói", "start": 0.0, "end": 2.5, "pause_after": 0.7},
      {"text": "Punchline!", "start": 3.2, "end": 4.0, "pause_after": 0.0, "is_punchline": true}
    ]
  },
  "bgm": {
    "volume_db": -18.0,
    "mood_shifts": [
      {"time": 0.0, "mood": "mysterious"},
      {"time": 32.4, "mood": "nostalgic"},
      {"time": 450.0, "mood": "tense"}
    ]
  },
  "sfx": [
    {"time": 1.5, "type": "pop", "trigger": "icon_appear"},
    {"time": 5.0, "type": "whoosh", "trigger": "transition"},
    {"time": 10.0, "type": "ka_ching", "trigger": "money_value"}
  ]
}
```

**BGM mood mapping:**
| Cảm xúc | Thể loại nhạc | Ví dụ |
| :--- | :--- | :--- |
| Mysterious | Pizzicato strings, ticking clock | Hook, crisis |
| Nostalgic | Acoustic guitar, light xylophone | Childhood, setup |
| Tense | Low cello, staccato strings | Rising action, crisis |
| Energetic | Bouncy bassline, typewriter clicks | Startup hustle |
| Triumphant | Indie-pop, celebratory synth | Breakthrough, payoff |
| Reflective | Calm acoustic guitar | Payoff, moral |

**SFX mapping:**
| Sự kiện | SFX | Ví dụ |
| :--- | :--- | :--- |
| Element xuất hiện | pop/plop | Icon, text |
| Chuyển cảnh | whoosh | Transition |
| Tiền bạc | ka-ching | $100M, $1B |
| Thất bại | buzzer | Explosion, bankruptcy |
| Punchline | record_scratch | Câu chốt |
| Vẽ hình | crayon_scribble | Draw-on effect |

**Kiểm tra:**
- [ ] BGM: -16 to -24 dB dưới voice
- [ ] BGM đổi mood ở mỗi beat
- [ ] SFX density: 6–12/phút

---

### Bước 6: Master Style Prompt

**Cấu trúc cơ bản:**
```text
A whimsical hand-drawn editorial cartoon illustration of [SUBJECT + ACTION], 
rough wax crayon and colored pencil sketch strokes, 
grainy paper texture, flat 2D minimalist composition, 
generous negative space around subject (60-70% unoccupied), 
sketchy imperfect contours, limited muted color palette with vibrant accents, 
[BACKGROUND: off-white textured sketchbook paper OR dark chalkboard OR warm terracotta], 
no gradients, no 3D shading, no photorealism, no extra fingers, no text artifacts --ar 16:9
```

**Negative Prompt:**
```text
3d render, realistic, photo, photograph, gradient shading, glossy, metallic, extra fingers, deformed hands, blurry, low resolution, watermark, text, logo, signature
```

**Background tone mapping:**
| Cảm xúc | Màu nền | HEX |
| :--- | :--- | :--- |
| Bình thường | Giấy sần ngà vàng | #edf1ee, #d9d5c5 |
| Khủng hoảng | Bảng đen chì | #252525, #0d0e16 |
| Quá khứ | Sepia ấm | #7b4f49 |
| Hy vọng | Xanh nhạt | #a3b0ac |

---

### Bước 7: Render và ghép

**Input:** Kịch bản + Shots + Audio timeline + Style prompt

**Công việc:**
1. Sinh ảnh cho mỗi shot
2. Áp dụng visual changes
3. Sinh voiceover
4. Trộn BGM
5. Chèn SFX
6. Ghép thành video

**Công cụ:**
- Sinh ảnh: DALL-E 3, Midjourney, Flux, hoặc Veo
- Voiceover: xKiro TTS, Edge-TTS
- BGM: YouTube Audio Library, Epidemic Sound
- SFX: ZapSplat, FreeSound
- Ghép: FFmpeg, CapCut, Premiere Pro

**Kiểm tra:**
- [ ] Loudness: -23 to -16 LUFS
- [ ] True peak: ≤ -1 dBFS
- [ ] Resolution: ≥ 720p
- [ ] Frame rate: 24–30 fps

---

### Bước 8: Callback + QA

**Input:** Video hoàn chỉnh

**Checklist QA (phải đạt 100%):**

#### Story QA
- [ ] Hook ≤ 30 giây
- [ ] Hook có nghịch lý/so sánh/câu hỏi
- [ ] ≥ 3 open loops (1 loop lớn + 2–4 loop trung)
- [ ] Mọi loop đều có closed_at
- [ ] ≥ 45% câu ngắn (≤6 từ)
- [ ] WPM: 160–200
- [ ] ≥ 8 khoảng lặng kịch tính/phút (≥600ms)
- [ ] ≥ 3 ẩn dụ
- [ ] ≥ 3 câu hài hước
- [ ] Kết thúc có callback đến hook

#### Visual QA
- [ ] Mean shot duration: 4.0–7.0s
- [ ] ≥ 1 visual change per shot
- [ ] Negative space: ≥ 50%
- [ ] Elements per frame: ≤ 4
- [ ] Background tone phản ánh cảm xúc
- [ ] Không vi phạm negative prompt

#### Audio QA
- [ ] Loudness: -23 to -16 LUFS
- [ ] LRA: ≤ 6 LU
- [ ] True peak: ≤ -1 dBFS
- [ ] Speech time ratio: 70–85%
- [ ] BGM: -16 to -24 dB dưới voice
- [ ] BGM đổi mood ở mỗi beat
- [ ] SFX density: 6–12/phút

---

## 🎓 VÍ DỤ THỰC HÀNH

### Chủ đề: "Lịch sử của Bitcoin"

#### Bước 1: Hook
```
"Năm 2008, một người đàn ông ẩn danh đã tạo ra một loại tiền có thể làm sụp đổ toàn bộ hệ thống ngân hàng. 
Đó không phải là tiền giấy, không phải là vàng. Đó là những dòng code. 
Và bây giờ, nó trị giá hơn 1 nghìn tỷ đô. Làm sao chuyện đó xảy ra?"
```

#### Bước 2: Open Loops
```json
[
  {
    "opened_at": 0.0,
    "closed_at": 18:00,
    "question": "Làm sao Bitcoin từ 0 trở thành 1 nghìn tỷ?",
    "resolution": "Nhờ niềm tin, toán học, và sự thất bại của hệ thống tài chính"
  },
  {
    "opened_at": 2:30,
    "closed_at": 5:00,
    "question": "Tại sao ngân hàng không thể chặn Bitcoin?",
    "resolution": "Vì nó phi tập trung, không ai kiểm soát"
  },
  {
    "opened_at": 7:00,
    "closed_at": 12:00,
    "question": "Ai là Satoshi Nakamoto?",
    "resolution": "Không ai biết, và đó là điều làm Bitcoin mạnh mẽ"
  }
]
```

#### Bước 3: Kịch bản (trích đoạn)
```
[00:00 - 00:15] "Năm 2008... nó trị giá hơn 1 nghìn tỷ đô."
[00:15 - 00:18] "Làm sao chuyện đó xảy ra?" (dramatic pause 700ms)

[00:18 - 00:25] "Câu chuyện bắt đầu với cuộc khủng hoảng tài chính 2008."
[00:25 - 00:28] "Các ngân hàng sụp đổ."
[00:28 - 00:30] "Hàng triệu người mất tiền."
[00:30 - 00:32] "Và niềm tin vào hệ thống tan vỡ." (pause 600ms)

[00:32 - 00:40] "Tưởng tượng bạn gửi tiền cho bạn bè. Bạn cần ngân hàng làm trung gian."
[00:40 - 00:42] "Nhưng nếu ngân hàng đó phá sản?"
[00:42 - 00:45] "Tiền của bạn biến mất."
```

#### Bước 4: Shots
```json
[
  {
    "shot_id": 1,
    "start": 0.0,
    "end": 5.0,
    "script": "Năm 2008... nó trị giá hơn 1 nghìn tỷ đô. Làm sao chuyện đó xảy ra?",
    "visual_changes": [
      {"time": 1.0, "type": "draw_on", "element": "bitcoin_logo"},
      {"time": 3.0, "type": "ken_burns_zoom", "amount": 1.10}
    ],
    "background_tone": "dark_chalkboard",
    "prompt": "A whimsical hand-drawn editorial cartoon of a mysterious figure in shadow creating a glowing digital coin, rough crayon strokes, dark chalkboard background, flat 2D, no 3D shading --ar 16:9"
  }
]
```

---

## 📊 CÁC LỖI THƯỜNG GẶP & CÁCH SỬA

### Lỗi 1: Hook quá chung chung
**Triệu chứng:** "Hôm nay chúng ta sẽ tìm hiểu về Bitcoin"
**Sửa:** Thêm số liệu + so sánh + câu hỏi
**Ví dụ:** "Bitcoin có giá trị hơn GDP của 150 quốc gia. Làm sao tiền ảo có thể mạnh như vậy?"

### Lỗi 2: Quá nhiều thông tin, ít câu chuyện
**Triệu chứng:** Video tràn ngập số liệu nhưng không có nhân vật, xung đột
**Sửa:** Tạo nhân vật (con người, công ty) + mục tiêu + trở ngại
**Ví dụ:** "Satoshi muốn tạo tiền không cần ngân hàng. Nhưng làm sao thuyết phục mọi người tin tưởng?"

### Lỗi 3: Câu quá dài
**Triệu chứng:** WPM < 160, câu trung bình > 10 từ
**Sửa:** Chia câu dài thành 2–3 câu ngắn
**Ví dụ:**
- ❌ "Vào năm 2008, khi cuộc khủng hoảng tài chính xảy ra, mọi người bắt đầu mất niềm tin vào hệ thống ngân hàng"
- ✅ "Năm 2008. Khủng hoảng tài chính. Mọi người mất niềm tin."

### Lỗi 4: Không có khoảng lặng
**Triệu chứng:** Video nghe như đọc tin tức, không có nhịp
**Sửa:** Thêm pause 600–700ms trước punchline
**Ví dụ:**
- ❌ "Và sau đó Bitcoin bùng nổ. Giá lên 1000 lần."
- ✅ "Và sau đó... (pause 700ms) Bitcoin bùng nổ. Giá lên 1000 lần."

### Lỗi 5: Shot quá dài không có visual change
**Triệu chứng:** Shot 10 giây chỉ có 1 hình tĩnh
**Sửa:** Chia thành 2 shots 5 giây, thêm visual change
**Ví dụ:** Thêm Ken Burns zoom, pop-in icon, hoặc draw-on effect

### Lỗi 6: BGM quá to
**Triệu chứng:** Không nghe rõ voice
**Sửa:** Giảm BGM xuống -18 to -22 dB dưới voice

### Lỗi 7: Không có callback
**Triệu chứng:** Kết thúc đột ngột, không quay lại hook
**Sửa:** Thêm câu callback
**Ví dụ:**
- Hook: "Làm sao tiền ảo có thể mạnh như vậy?"
- Callback: "Và đó là cách tiền ảo trở nên mạnh mẽ."

---

## 🔄 QUY TRÌNH CẬP NHẬT SKILL

1. **Sau mỗi 10 video:**
   - Đánh giá retention rate
   - Phân tích comments
   - Điều chỉnh ngưỡng nếu cần

2. **Sau mỗi 50 video:**
   - Chạy A/B test
   - Cập nhật thuật toán

3. **Khi có công cụ mới:**
   - Cập nhật danh sách công cụ
   - Đánh giá hiệu quả

---

## 📚 TÀI LIỆU THAM KHẢO

- [UNIVERSAL_VIDEO_ALGORITHM.md](UNIVERSAL_VIDEO_ALGORITHM.md) - Thuật toán 8 bước
- [video_reverse_engineering_plan.md](video_reverse_engineering_plan.md) - Kế hoạch phân tích
- [style_bible_Crayon_Capital.md](style_bible_Crayon_Capital.md) - Style Bible ví dụ
- [analysis/npoPedEq3hs/](../../analysis/npoPedEq3hs/) - Evidence pack

---

## ✅ CHECKLIST NHANH

### Trước khi bắt đầu
- [ ] Đã chọn chủ đề
- [ ] Đã nghiên cứu đủ thông tin
- [ ] Đã xác định hook nghịch lý

### Sau khi viết kịch bản
- [ ] ≥ 45% câu ngắn
- [ ] ≥ 3 open loops
- [ ] Có callback
- [ ] WPM: 160–200

### Sau khi render
- [ ] Loudness: -23 to -16 LUFS
- [ ] Shot duration: 4.0–7.0s
- [ ] BGM: -16 to -24 dB
- [ ] SFX: 6–12/phút

---

## 🎯 TÓM LƯỢC TRONG 10 CÂU

1. Hook bằng nghịch lý/so sánh/câu hỏi trong 30 giây
2. Tạo 3–5 open loops lồng nhau
3. 54% câu ngắn (≤6 từ), 46% câu dài
4. WPM: 160–200
5. Cứ 5 giây có 1 khoảng lặng kịch tính
6. Shot: 4.0–7.0 giây, có visual change
7. Mỗi shot có 1–3 elements, negative space ≥50%
8. BGM: -16 to -24 dB, đổi mood theo beat
9. SFX: 6–12 lần/phút
10. Kết thúc có callback đến hook

**"Áp dụng 10 nguyên tắc này, video nào cũng cuốn hút."**
