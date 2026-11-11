# VIDEO QA CHECKLIST
## Bảng kiểm tra chất lượng video trước khi xuất bản

> **Phiên bản:** `v1.0.0`
> **Áp dụng cho:** Tất cả video giải thích, tiểu sử, lịch sử, phân tích
> **Dựa trên:** [`UNIVERSAL_VIDEO_ALGORITHM.md`](UNIVERSAL_VIDEO_ALGORITHM.md)

---

## 📋 HƯỚNG DẪN SỬ DỤNG

1. **Trước khi render:** Kiểm tra phần **Pre-Render**
2. **Sau khi render:** Kiểm tra phần **Post-Render**
3. **Trước khi xuất bản:** Kiểm tra phần **Final Approval**
4. **Mỗi mục:** Đánh dấu [x] nếu đạt, [ ] nếu chưa
5. **Ngưỡng:** Nếu có ≥ 1 mục **CRITICAL** chưa đạt → **KHÔNG XUẤT BẢN**

---

## 🚨 CRITICAL CHECKLIST (Bắt buộc 100%)

### Story (Kịch bản)

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| S1 | Hook ≤ 30 giây | ≤ 30s | [ ] | Thời gian từ đầu đến câu hỏi đầu tiên |
| S2 | Hook có nghịch lý/so sánh/câu hỏi | Phải có | [ ] | Không được mở bằng giới thiệu chung |
| S3 | ≥ 3 open loops | ≥ 3 | [ ] | 1 loop lớn + 2–4 loop trung |
| S4 | Tất cả loops có closed_at | 100% | [ ] | Không loop nào bị bỏ quên |
| S5 | ≥ 45% câu ngắn (≤6 từ) | ≥ 45% | [ ] | Đếm từ transcript |
| S6 | WPM: 160–200 | 160–200 | [ ] | Tính trên thời gian nói thật |
| S7 | ≥ 8 khoảng lặng kịch tính/phút (≥600ms) | ≥ 8/phút | [ ] | Đo từ audio waveform |
| S8 | ≥ 3 ẩn dụ | ≥ 3 | [ ] | Mỗi khái niệm khó có 1 ẩn dụ |
| S9 | ≥ 3 câu hài hước | ≥ 3 | [ ] | Câu đùa, châm biếm, hoặc understatement |
| S10 | Kết thúc có callback đến hook | Phải có | [ ] | Trả lời câu hỏi ban đầu |

### Visual (Hình ảnh)

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| V1 | Mean shot duration: 4.0–7.0s | 4.0–7.0s | [ ] | Từ shots.json |
| V2 | ≥ 1 visual change per shot | ≥ 1 | [ ] | Ken Burns, pop-in, slide-in, v.v. |
| V3 | Negative space: ≥ 50% | ≥ 50% | [ ] | Đối tượng chiếm ≤ 50% khung hình |
| V4 | Elements per frame: ≤ 4 | ≤ 4 | [ ] | Nhân vật + vật thể + chữ |
| V5 | Background tone phản ánh cảm xúc | Phải có | [ ] | Nền đổi theo beat: sáng/tối/ấm/lạnh |

### Audio (Âm thanh)

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| A1 | Loudness: -23 to -16 LUFS | -23 to -16 | [ ] | Đo bằng EBU R128 (ffmpeg) |
| A2 | LRA: ≤ 6 LU | ≤ 6 LU | [ ] | Loudness Range |
| A3 | True peak: ≤ -1 dBFS | ≤ -1 dBFS | [ ] | Đỉnh âm thanh không vượt quá |
| A4 | Speech time ratio: 70–85% | 70–85% | [ ] | Thời gian có lời / tổng thời lượng |
| A5 | BGM: -16 to -24 dB dưới voice | -16 to -24 dB | [ ] | Đo bằng waveform |
| A6 | BGM đổi mood ở mỗi beat | Phải có | [ ] | Mỗi đoạn cảm xúc có nhạc phù hợp |
| A7 | SFX density: 6–12/phút | 6–12/phút | [ ] | Onset detection |

---

## ⚠️ HIGH PRIORITY CHECKLIST (Khuyến nghị 100%)

### Story

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| S11 | Hook type: paradox | Ưu tiên | [ ] | So sánh toán học gây sốc |
| S12 | Median sentence length: 5–7 từ | 5–7 | [ ] | Câu ngắn là chủ đạo |
| S13 | Mean gap between sentences: 600–800ms | 600–800ms | [ ] | Nhịp nói tự nhiên |
| S14 | Open loops lồng nhau | Ưu tiên | [ ] | Loop sau mở trước khi loop trước đóng |
| S15 | Metaphors: 1/3–5 phút | 1/3–5 phút | [ ] | Mật độ ẩn dụ hợp lý |

### Visual

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| V6 | Shot duration median: 4.0–6.0s | 4.0–6.0s | [ ] | Phân phối shot hợp lý |
| V7 | Shot duration P90: ≤ 10s | ≤ 10s | [ ] | 90% shot ≤ 10s |
| V8 | Visual changes: 2–3 per shot | 2–3 | [ ] | Càng nhiều càng tốt (nhưng không quá tải) |
| V9 | Color palette: ≤ 8 màu chủ đạo | ≤ 8 | [ ] | Màu sắc đồng nhất |
| V10 | No 3D shading | 0% | [ ] | Flat 2D only |

### Audio

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| A8 | BGM volume consistency | ±2 dB | [ ] | Âm lượng BGM ổn định |
| A9 | SFX sync with visual events | 100% | [ ] | SFX đúng lúc element xuất hiện |
| A10 | Voice clarity | Clear | [ ] | Không noise, không clipping |
| A11 | Audio format: Stereo 48kHz | Stereo 48kHz | [ ] | Chuẩn phát sóng |

---

## 📝 MEDIUM PRIORITY CHECKLIST (Khuyến nghị ≥ 80%)

### Story

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| S16 | Narrator addresses viewer directly | Ưu tiên | [ ] | "Bạn", "Chúng ta" |
| S17 | Signature phrases | Có | [ ] | Câu đặc trưng lặp lại |
| S18 | Rule of three | Ưu tiên | [ ] | 3 ví dụ, 3 lý do, 3 bước |
| S19 | Understatement for humor | Ưu tiên | [ ] | Nói giảm nói tránh hài hước |

### Visual

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| V11 | Consistent character design | 100% | [ ] | Nhân vật lặp có cùng kiểu dáng |
| V12 | Text on screen: ≤ 10 từ | ≤ 10 | [ ] | Chữ ngắn gọn |
| V13 | Font: Clear, readable | Phải có | [ ] | Không font rườm rà |
| V14 | Transitions: Smooth | Smooth | [ ] | Không giật, không nháy |

### Audio

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| A12 | Music fade in/out: 0.5–1.0s | 0.5–1.0s | [ ] | Fade mượt mà |
| A13 | SFX volume: -12 to -6 dB | -12 to -6 dB | [ ] | SFX nghe rõ nhưng không át |
| A14 | Voice EQ: Balanced | Balanced | [ ] | Tần số voice cân bằng |

---

## 🎯 FINAL APPROVAL CHECKLIST

### Technical (Kỹ thuật)

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| T1 | Video resolution: ≥ 720p | ≥ 720p | [ ] | 1280x720 hoặc cao hơn |
| T2 | Frame rate: 24–30 fps | 24–30 fps | [ ] | Chuẩn YouTube |
| T3 | Video format: MP4 | MP4 | [ ] | Container chuẩn |
| T4 | Video codec: H.264 | H.264 | [ ] | Codec phổ biến |
| T5 | Audio codec: AAC | AAC | [ ] | Chuẩn YouTube |
| T6 | File size: ≤ 500MB (10 phút) | ≤ 500MB | [ ] | Tối ưu upload |
| T7 | No black frames at start/end | 0 | [ ] | Cắt sạch đầu đuôi |

### Metadata (Thông tin)

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| M1 | Title: ≤ 70 ký tự | ≤ 70 | [ ] | YouTube title limit |
| M2 | Title: Có keyword chính | Phải có | [ ] | SEO friendly |
| M3 | Description: ≥ 200 ký tự | ≥ 200 | [ ] | Mô tả đầy đủ |
| M4 | Tags: 5–10 tags | 5–10 | [ ] | Tag liên quan |
| M5 | Thumbnail: 1280x720 | 1280x720 | [ ] | Chuẩn YouTube |
| M6 | Thumbnail: Clear, readable | Clear | [ ] | Nhìn rõ trên mobile |

### Legal (Pháp lý)

| # | Mục | Ngưỡng | Đạt? | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| L1 | No copyrighted music | 100% | [ ] | Nhạc có license |
| L2 | No copyrighted images | 100% | [ ] | Hình tự sinh hoặc có license |
| L3 | No trademark violation | 100% | [ ] | Không xâm phạm thương hiệu |
| L4 | Fair use compliance | 100% | [ ] | Tuân thủ luật bản quyền |
| L5 | No misleading content | 100% | [ ] | Thông tin chính xác |

---

## 📊 BẢNG TÓM TẮT ĐIỂM

| Phần | Tổng mục | Đạt | % | Trạng thái |
| :--- | :--- | :--- | :--- | :--- |
| Critical (Story) | 10 | | | |
| Critical (Visual) | 5 | | | |
| Critical (Audio) | 7 | | | |
| High Priority (Story) | 5 | | | |
| High Priority (Visual) | 5 | | | |
| High Priority (Audio) | 4 | | | |
| Medium Priority (Story) | 4 | | | |
| Medium Priority (Visual) | 4 | | | |
| Medium Priority (Audio) | 3 | | | |
| Technical | 7 | | | |
| Metadata | 6 | | | |
| Legal | 5 | | | |
| **TỔNG** | **60** | | | |

---

## 🎯 QUY TẮC CHẤM ĐIỂM

### Xuất bản (Publish)
- **✅ Đạt:** Tất cả Critical (22 mục) = 100%
- **⚠️ Cảnh báo:** Critical 100% + High Priority ≥ 80%
- **❌ Từ chối:** Critical < 100%

### Chất lượng cao (High Quality)
- **⭐⭐⭐⭐⭐ 5/5:** Critical 100% + High Priority ≥ 90% + Medium ≥ 80%
- **⭐⭐⭐⭐ 4/5:** Critical 100% + High Priority ≥ 80% + Medium ≥ 70%
- **⭐⭐⭐ 3/5:** Critical 100% + High Priority ≥ 70% + Medium ≥ 60%
- **⭐⭐ 2/5:** Critical 100% + High Priority ≥ 60%
- **⭐ 1/5:** Critical 100% (chỉ đạt tối thiểu)

---

## 📝 CÁCH GHI CHÚ

### Đối với các mục không đạt:
- Ghi rõ lý do
- Đề xuất cách sửa
- Ưu tiên sửa theo thứ tự: Critical → High → Medium

### Ví dụ:
```
| S5 | ≥ 45% câu ngắn | ≥ 45% | [ ] | Chỉ đạt 40%. Cần chia 5 câu dài thành câu ngắn hơn. |
| A1 | Loudness: -23 to -16 LUFS | -23 to -16 | [ ] | Đo được -25 LUFS. Cần normalize lên -18 LUFS. |
| V2 | ≥ 1 visual change per shot | ≥ 1 | [ ] | Shot 3–5 không có visual change. Thêm Ken Burns zoom. |
```

---

## 🔄 QUY TRÌNH QA

1. **Self-QA:** Tác giả kiểm tra theo checklist
2. **Peer-QA:** Đồng nghiệp kiểm tra chéo
3. **Final-QA:** Trưởng nhóm phê duyệt
4. **Fix:** Sửa tất cả Critical issues
5. **Re-check:** Kiểm tra lại sau khi sửa
6. **Approve:** Xuất bản

---

## 📚 TÀI LIỆU LIÊN QUAN

- [UNIVERSAL_VIDEO_ALGORITHM.md](UNIVERSAL_VIDEO_ALGORITHM.md) - Thuật toán 8 bước
- [VIDEO_PRODUCTION_SKILL.md](VIDEO_PRODUCTION_SKILL.md) - Hướng dẫn kỹ năng
- [style_bible_Crayon_Capital.md](style_bible_Crayon_Capital.md) - Style Bible ví dụ
- [video_reverse_engineering_plan.md](video_reverse_engineering_plan.md) - Kế hoạch phân tích

---

## 💡 TIPS

1. **Sử dụng công cụ:**
   - Đo WPM: [`pipeline/audio_analysis.py`](pipeline/audio_analysis.py)
   - Đo Loudness: `ffmpeg -i input.mp4 -filter_complex ebur128 -f null -`
   - Đếm từ: Python `len(text.split())`
   - Đếm shot: [`pipeline/shot_analysis.py`](pipeline/shot_analysis.py)

2. **Checklist in-code:**
   - Có thể tích hợp checklist vào pipeline tự động
   - Sử dụng [`core/render_schemas.py`](core/render_schemas.py) cho validation

3. **Lưu trữ kết quả QA:**
   - Lưu file `qa_report_<video_id>.json` cho mỗi video
   - Theo dõi xu hướng chất lượng theo thời gian

---

## ✅ TÓM LƯỢC NHANH

### 3 ĐIỀU KIỂM TRA TRƯỚC KHI XUẤT BẢN:

1. **Critical 22 mục = 100%** (Không có ngoại lệ)
2. **Loudness: -23 to -16 LUFS** (Chuẩn phát sóng)
3. **Callback đến hook** (Kết thúc hoàn chỉnh)

### 5 ĐIỀU KIỂM TRA TRƯỚC KHI RENDER:

1. **Hook ≤ 30 giây**
2. **≥ 45% câu ngắn**
3. **≥ 3 open loops**
4. **Shot duration: 4.0–7.0s**
5. **BGM: -16 to -24 dB**

---

**"Không xuất bản nếu Critical chưa đạt 100%."**
