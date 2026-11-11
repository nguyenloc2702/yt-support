# UNIVERSAL_VIDEO_ALGORITHM
## Thuật toán sản xuất video cuốn hút cho BẤT KỲ chủ đề

> **Phiên bản:** `v1.0.0`
> **Nguồn gốc:** Đúc kết từ phân tích video [`npoPedEq3hs`](https://youtu.be/npoPedEq3hs) (Crayon Capital - 384K views, 6945 likes, 508 comments)
> **Phạm vi:** Áp dụng cho video giải thích (explainer), tiểu sử (biography), lịch sử (history), phân tích (analysis)
> **Quyền sở hữu:** Dựa trên dữ liệu đo đạc, không sao chép nội dung hay phong cách độc quyền

---

## 🎯 NGUYÊN LÝ CỐT LÕI

**"Người xem không rời đi vì họ muốn biết câu trả lời cho câu hỏi bạn đã đặt ra."**

Không có yếu tố nào (hình ảnh, nhạc, giọng) có thể giữ chân người xem 19 phút nếu không có **cấu trúc câu chuyện tạo ra và trì hoãn việc trả lời các câu hỏi**. Video Crayon Capital thành công nhờ:

1. **Hook bằng nghịch lý toán học** (18 giây đầu)
2. **Chuỗi 5 vòng tò mò lồng nhau** (mở loop trước khi đóng loop trước)
3. **Ẩn dụ 3 tầng** (khái niệm khó → đồ vật quen thuộc → hành động có xung đột)
4. **Nhịp kịch bản dứt khoát** (54.4% câu ngắn ≤6 từ, 11.58 khoảng lặng kịch tính/phút)

---

## 📊 CÁC NGƯỠNG ĐO ĐƯỢC (Từ Evidence Pack)

### 1. Story Metrics (Kịch bản)

| Chỉ số | Giá trị video mẫu | Ngưỡng chấp nhận | Mức lý tưởng | Cách đo |
| :--- | :--- | :--- | :--- | :--- |
| **Hook duration** | 18–32s | ≤ 45s | ≤ 30s | Timestamp đến câu hỏi đầu tiên |
| **Hook type** | Paradox + shock comparison | Phải có | Paradox/question/stat | Phân loại từ transcript |
| **Open loops** | 5 (lồng nhau) | ≥ 3 | ≥ 4 | Đếm từ `open_loops` array |
| **Loop closure time** | 15–440s | ≤ 180s | 60–120s | `closed_at - opened_at` |
| **Short sentences (≤6 words)** | 54.4% | ≥ 45% | ≥ 50% | Từ `transcript.json` |
| **WPM (effective speech)** | 184.8 | 160–200 | 170–190 | `total_words / speaking_minutes` |
| **Dramatic pauses (≥600ms)** | 11.58/phút | ≥ 8/phút | ≥ 10/phút | Từ audio gap analysis |
| **Metaphors** | 5 (1/3.8 phút) | ≥ 1/5 phút | ≥ 1/3 phút | Đếm từ `metaphors` array |
| **Humor lines** | 5+ | ≥ 3 | ≥ 4 | Đếm từ `humor` array |
| **Callback to hook** | Có | **BẮT BUỘC** | **BẮT BUỘC** | Kiểm tra ending |

### 2. Visual Metrics (Hình ảnh)

| Chỉ số | Giá trị video mẫu | Ngưỡng chấp nhận | Mức lý tưởng | Cách đo |
| :--- | :--- | :--- | :--- | :--- |
| **Shot duration (mean)** | 5.0s | 4.0–7.0s | 4.5–6.0s | Từ `shots.json` |
| **Shot duration (median)** | 4.47s | 3.5–6.5s | 4.0–6.0s | Từ `shots.json` |
| **Cuts per minute** | 12.0 | 8–15 | 10–14 | `230 cuts / 1150s * 60` |
| **Elements per frame** | 1–3 | ≤ 4 | ≤ 3 | Từ VLM captioning |
| **Negative space** | 60–70% | ≥ 50% | ≥ 60% | Đo pixel background |
| **Edge density** | 0.039 | 0.03–0.06 | 0.04–0.05 | Canny edge detection |
| **Saturation** | 0.267 (medium_low) | 0.2–0.4 | 0.25–0.35 | HSV mean saturation |

### 3. Audio Metrics (Âm thanh)

| Chỉ số | Giá trị video mẫu | Ngưỡng chấp nhận | Mức lý tưởng | Cách đo |
| :--- | :--- | :--- | :--- | :--- |
| **Integrated loudness** | -18.3 LUFS | -23 to -16 LUFS | -18 to -20 LUFS | EBU R128 (ffmpeg) |
| **Loudness range (LRA)** | 3.8 LU | ≤ 6 LU | ≤ 5 LU | EBU R128 |
| **True peak** | 0.0 dBFS | ≤ -1 dBFS | ≤ -0.5 dBFS | EBU R128 |
| **Speech time ratio** | 77.8% | 70–85% | 75–80% | `speaking_sec / total_sec` |
| **BGM level (relative to voice)** | -18 to -22 dB | -16 to -24 dB | -18 to -22 dB | Đo từ waveform |
| **SFX density** | 8.5/phút | 6–12/phút | 8–10/phút | Onset detection |

---

## 🏗️ THUẬT TOÁN 8 BƯỚC (Áp dụng cho mọi chủ đề)

### Bước 1: Xác định chủ đề và nghịch lý lớn nhất

**Input:** Chủ đề (ví dụ: "Lịch sử của Nvidia", "Cách hoạt động của Bitcoin")

**Output:**
- `hook_paradox`: Câu nghịch lý/so sánh gây sốc
- `hook_question`: Câu hỏi chưa có lời giải
- `hook_duration`: ≤ 30 giây

**Công thức:**
```
hook_paradox = "[Số liệu khủng] + [So sánh bất ngờ] + [Câu hỏi treo]"
```

**Ví dụ:**
- ❌ Sai: "Hôm nay chúng ta sẽ tìm hiểu về Nvidia"
- ✅ Đúng: "Năm 2024, Nvidia có giá trị gấp 10 lần GDP của Việt Nam. Làm sao một công ty chỉ bán card màn hình lại có thể mạnh như vậy?"

**Kiểm tra:**
- [ ] Hook phải có thể trả lời được trong video
- [ ] Hook phải tạo ra câu hỏi trong đầu người xem
- [ ] Hook phải có số liệu cụ thể (không chung chung)

---

### Bước 2: Phân rã chủ đề thành chuỗi open loop lồng nhau

**Input:** Chủ đề + hook

**Output:** Mảng `open_loops` với cấu trúc:
```json
{
  "opened_at": 0.0,
  "closed_at": 0.0,
  "question": "Câu hỏi/điểm tò mò",
  "resolution": "Câu trả lời"
}
```

**Quy tắc:**
1. **Loop lớn** (toàn video): Mở ở hook, đóng ở kết luận
2. **Loop trung** (3–5 loop): Mỗi loop 2–4 phút
3. **Loop nhỏ** (tùy chọn): Mở và đóng trong cùng 1 đoạn

**Ví dụ từ video mẫu:**
```
Loop 0: 00:18 → 19:00 - "Làm sao một người bị sa thải khỏi 2 công ty mình lập ra lại trở thành tỷ phú đầu tiên?"
  Loop 1: 02:35 → 04:35 - "Liệu Zip2 có bị nghiền nát bởi các ông lớn truyền thông?"
    Loop 1a: 03:10 → 03:45 - "Anh ta có bị sa thải không?"
  Loop 2: 04:45 → 07:30 - "Liệu X.com có sống sót trong cuộc chiến với PayPal?"
  Loop 3: 07:45 → 12:20 - "Với 3 lần phóng thất bại, liệu Falcon 1 có thể thành công?"
  Loop 4: 10:40 → 14:00 - "Liệu Tesla có thể trả lương trước Giáng sinh 2008?"
```

**Kiểm tra:**
- [ ] Mỗi loop phải có câu hỏi rõ ràng
- [ ] Loop sau phải mở trước khi loop trước đóng
- [ ] Không có loop nào bị bỏ quên (phải có `closed_at`)

---

### Bước 3: Viết kịch bản theo nhịp 54/46

**Input:** `open_loops` + nghiên cứu chủ đề

**Output:** `script` với cấu trúc:
```json
{
  "sentences": [
    {
      "start": 0.0,
      "end": 5.0,
      "text": "Câu nói",
      "words": 8,
      "is_short": true,  // ≤6 từ
      "is_punchline": false,
      "is_question": false,
      "dramatic_pause_before": 0.0,  // giây
      "beat_role": "hook|setup|rising|crisis|breakthrough|payoff|moral"
    }
  ]
}
```

**Quy tắc 54/46:**
- **54%** câu ngắn (≤6 từ)
- **46%** câu dài (7–15 từ)
- **Mỗi 3–5 câu** phải có 1 câu ngắn chốt hạ

**Kỹ thuật viết câu:**
1. **Câu ngắn chốt:** Dùng sau đoạn giải thích dài
   - ❌ "Và sau đó anh ta đã mất hết tiền"
   - ✅ "Hết tiền."

2. **Câu hỏi tu từ:** Dùng để mở loop
   - ❌ "Anh ta gặp rất nhiều khó khăn"
   - ✅ "Vậy làm sao anh ta có thể sống sót?"

3. **Câu ẩn dụ:** Dùng để giải thích khái niệm khó
   - ❌ "Tên lửa tái sử dụng giúp giảm chi phí"
   - ✅ "Tưởng tượng bạn vứt bỏ một chiếc Boeing 747 sau mỗi chuyến bay. Đó là cách ngành hàng không hoạt động. SpaceX muốn thay đổi điều đó."

**Kiểm tra:**
- [ ] ≥ 45% câu ngắn (≤6 từ)
- [ ] WPM: 160–200 (tính trên thời gian nói thật)
- [ ] Cứ 30–45 giây có 1 khoảng lặng ≥600ms

---

### Bước 4: Chia shot và gán visual change

**Input:** `script`

**Output:** `shots` với cấu trúc:
```json
{
  "shots": [
    {
      "shot_id": 1,
      "start": 0.0,
      "end": 5.0,
      "duration": 5.0,
      "script_sentences": [0, 1, 2],  // index của câu
      "visual_changes": [
        {"time": 1.5, "type": "ken_burns_zoom", "amount": 1.08},
        {"time": 3.0, "type": "pop_in", "element": "icon_dollar"}
      ],
      "background_tone": "off-white|dark|warm|cool",
      "is_key_scene": true
    }
  ]
}
```

**Quy tắc:**
- **Shot duration:** 4.0–7.0 giây (mean)
- **Mỗi shot** phải có ít nhất 1 visual change
- **Visual change types:**
  - `ken_burns_zoom` (1.05–1.15x)
  - `ken_burns_pan` (5–15% offset)
  - `pop_in` (scale 0→1.1→1.0)
  - `slide_in` (từ trái/phải/trên/dưới)
  - `draw_on` (hiệu ứng vẽ dần)
  - `color_shift` (thay đổi tông nền)

**Kiểm tra:**
- [ ] Mean shot duration: 4.0–7.0s
- [ ] ≥ 1 visual change per shot
- [ ] Background tone phản ánh cảm xúc cảnh

---

### Bước 5: Thiết kế âm thanh theo beat

**Input:** `script` + `shots`

**Output:** `audio_timeline` với cấu trúc:
```json
{
  "voice": {
    "model": "xkiro|edge-tts",
    "rate": 1.0,
    "volume": 0.0,
    "cues": [
      {
        "text": "Câu nói",
        "start": 0.0,
        "end": 2.5,
        "pause_after": 0.0,
        "is_punchline": false
      }
    ]
  },
  "bgm": {
    "enabled": true,
    "volume_db": -18.0,
    "mood_shifts": [
      {"time": 0.0, "mood": "mysterious", "track": "pizzicato_intro"},
      {"time": 32.4, "mood": "nostalgic", "track": "acoustic_guitar"},
      {"time": 450.0, "mood": "tense", "track": "low_cello"}
    ]
  },
  "sfx": [
    {"time": 1.5, "type": "pop", "trigger": "icon_appear"},
    {"time": 5.0, "type": "whoosh", "trigger": "transition"},
    {"time": 10.0, "type": "ka_ching", "trigger": "money_value"}
  ]
}
```

**Quy tắc:**
- **BGM volume:** -18 to -22 dB dưới voice
- **BGM mood shifts:** Phải đổi mood ở mỗi beat thay đổi
- **SFX density:** 6–12 lần/phút
- **SFX triggers:**
  - `pop/plop`: Khi element mới xuất hiện
  - `whoosh`: Khi chuyển cảnh/zoom/pan
  - `ka_ching`: Khi có giá trị tiền
  - `buzzer`: Khi thất bại
  - `record_scratch`: Khi punchline
  - `crayon_scribble`: Khi vẽ chữ/hình (nếu style vẽ tay)

**Kiểm tra:**
- [ ] BGM luôn thấp hơn voice 16–24 dB
- [ ] BGM đổi mood ở mỗi beat thay đổi
- [ ] SFX density: 6–12/phút

---

### Bước 6: Master Style Prompt (Đồng nhất cho mọi cảnh)

**Cấu trúc:**
```text
[A WHIMSICAL HAND-DRAWN EDITORIAL CARTOON ILLUSTRATION] of [SUBJECT + ACTION], 
rough wax crayon and colored pencil sketch strokes, 
grainy paper texture, flat 2D minimalist composition, 
generous negative space around subject (60-70% unoccupied), 
sketchy imperfect contours, limited muted color palette, 
[BACKGROUND: off-white textured sketchbook paper OR dark chalkboard OR warm terracotta], 
no gradients, no 3D shading, no photorealism, no extra fingers, no text artifacts --ar 16:9
```

**Negative Prompt:**
```text
3d render, realistic, photo, photograph, gradient shading, glossy, metallic, extra fingers, deformed hands, blurry, low resolution, watermark, text, logo, signature
```

**Character Sheet (nếu có nhân vật lặp):**
```json
{
  "Elon Musk": {
    "descriptor": "Caricature of a determined male entrepreneur, short dark hair, wearing casual business attire (black shirt), intense focused eyes, slight smile, simple flat colors, no detailed features",
    "reference_image": "assets/characters/elon_ref.png"
  }
}
```

---

### Bước 7: Render và ghép

**Input:** `script` + `shots` + `audio_timeline` + `style_prompt`

**Output:** Video hoàn chỉnh

**Quy trình:**
1. Sinh ảnh cho mỗi shot theo `style_prompt`
2. Áp dụng visual changes (Ken Burns, pop-in, v.v.)
3. Sinh voiceover theo `voice.cues`
4. Trộn BGM theo `bgm` timeline
5. Chèn SFX theo `sfx` timeline
6. Ghép tất cả thành video

**Kiểm tra:**
- [ ] Loudness: -23 to -16 LUFS
- [ ] True peak: ≤ -1 dBFS
- [ ] Video resolution: ≥ 720p
- [ ] Frame rate: 24–30 fps

---

### Bước 8: Callback và QA

**Input:** Video hoàn chỉnh

**Checklist QA (phải đạt 100%):**

#### Story QA
- [ ] Hook ≤ 30 giây
- [ ] Hook có nghịch lý/so sánh/câu hỏi
- [ ] ≥ 3 open loops (1 loop lớn + 2–4 loop trung)
- [ ] Mọi loop đều có `closed_at`
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

## 📋 CHECKLIST TÓM TẮT (Dán trước bàn làm việc)

### ✅ Trước khi viết kịch bản
- [ ] Đã xác định hook nghịch lý
- [ ] Đã liệt kê ≥ 3 open loops
- [ ] Đã tìm được ẩn dụ cho mọi khái niệm khó

### ✅ Sau khi viết kịch bản
- [ ] ≥ 45% câu ngắn (≤6 từ)
- [ ] WPM: 160–200
- [ ] ≥ 8 khoảng lặng kịch tính/phút
- [ ] Có callback đến hook

### ✅ Trước khi render
- [ ] Mean shot duration: 4.0–7.0s
- [ ] ≥ 1 visual change per shot
- [ ] Master Style Prompt đã áp dụng
- [ ] Character Sheet (nếu có nhân vật)

### ✅ Sau khi render
- [ ] Loudness: -23 to -16 LUFS
- [ ] BGM: -16 to -24 dB dưới voice
- [ ] SFX density: 6–12/phút

---

## 🔄 QUY TRÌNH CẬP NHẬT THUẬT TOÁN

1. **Sau mỗi 10 video:**
   - Đánh giá retention rate (tỉ lệ xem hết)
   - Phân tích comments để tìm điểm hay/không hay
   - Điều chỉnh ngưỡng nếu cần

2. **Sau mỗi 50 video:**
   - Chạy A/B test giữa các phiên bản thuật toán
   - Cập nhật các ngưỡng dựa trên dữ liệu thực tế

3. **Khi thay đổi chủ đề lớn:**
   - Kiểm tra xem thuật toán có cần điều chỉnh không
   - Ví dụ: Chủ đề kỹ thuật có thể cần nhiều ẩn dụ hơn

---

## 📚 TÀI LIỆU THAM KHẢO

- [Evidence Pack - npoPedEq3hs](../../analysis/npoPedEq3hs/08_evidence_index.md)
- [Style Bible - Crayon Capital](style_bible_Crayon_Capital.md)
- [Reverse Engineering Plan](plans/video_reverse_engineering_plan.md)
- [Style Bible Template](style_bible_template.md)

---

## 🎯 TÓM LƯỢC TRONG 1 CÂU

**"Viết kịch bản có 54% câu ngắn, 5 open loops lồng nhau, ẩn dụ 3 tầng, hook nghịch lý, và callback ở cuối; chia shot 5 giây với visual change; BGM -18dB với mood shifts; SFX 8 lần/phút."**
