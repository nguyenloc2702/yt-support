# Báo Cáo Nâng Cấp Luồng /remake — AI xKiro + Skill Pack (6 Phases)
**Thời gian:** 2026-10-23
**Phạm vi:** Fusion toàn bộ luồng /remake (AI Genesis) với gateway xKiro (`qwen/qwen3.8-omni-flash:free`) và bộ skill UNIVERSAL_VIDEO_ALGORITHM: dịch VI/EN, sinh kịch bản best-of-N, sinh giọng đọc, sinh ảnh (crayon), QA 60 tiêu chí thành code, hard gate trước render, feedback loop retention.
**Trạng thái hệ thống:** 🟢 **Integration-tested + Live E2E PASS** (502/502 unit/integration tests, 0 lint errors, E2E thật Gate 4 APPROVED 9.15/10).

---

## 1. Kết Quả Kiểm Thử Thực Tế

### `ruff check .`
```text
All checks passed!
```

### `pytest -q`
```text
502 passed, 1 skipped, 1 warning in ~100s
```

### Live E2E mini job (`python -X utf8 scripts/e2e_mini_remake.py`)
```text
[E2E] ScriptFactory: angle=dramatist score=8.3 rounds=1 scenes=8
[E2E] TTS: vi=44.7s en=64.2s (drift 18% cả 2 ngôn ngữ)
Scorecard: 9.15/10 — 43/47 tiêu chí ĐẠT
[E2E] GATE 4: APPROVED (hard gate passed) manifest=manifest_..._c3af2886
```
Golden artifacts đã đóng băng tại `tests/fixtures/golden_remake_20261023/`.

---

## 2. Kiến Trúc Sau Nâng Cấp

```
Transcript (Whisper)
  → build_semantic_dual_scripts_from_transcript (xKiro slicing, dynamic 4.5-8s)
  → ScriptFactory.best-of-N: 3 góc kể (analyst/dramatist/teacher) → rewrite grounded
      → LLM judge (rubric 7 tiêu chí, tổng 10đ) → regen ≤2 vòng fold feedback
      → algorithm_result.json (decision trail)
  → Gate 1 duyệt kịch bản → Gate 2 audio VI/EN (xKiro voice) + Gate 3 character
      (Imagen/DALL-E + VLM style-check ≥7.0, regen theo issue)
  → QA hard gate (stage="final"): 60 tiêu chí thành code, điểm ≥ 8.0 mới cho render
  → Gate 4 khóa manifest → Worker render (Veo/Imagen/Ken Burns)
  → Đăng video → /feedback <job> <điểm> → feedback_store → fold vào các lần sinh sau
```

---

## 3. Bảng Tổng Hợp Từng Phase

| Phase | Nội Dung | File Chính | Test Chứng Minh |
| :--- | :--- | :--- | :--- |
| **0** | Probe gateway: chat/JSON/vision OK, KHÔNG có image-gen, TTS = `xkiro-voice` | `scripts/probe_qwen38_omni.py`, `data/probe/capability_matrix.md` | — |
| **1** | xKiro hóa 4 khâu: translator batch+cache, circuit breaker, voice map xKiro-first, LLM character extraction, zero-fake Ken Burns (FATAL thay placeholder), Ken Burns duration động | `services/xkiro_translator.py`, `services/ai_breaker.py`, `pipeline/tts.py`, `services/image_kenburns_provider.py`, `pipeline/character_manager.py`, `config/settings.py` | `test_ai_breaker`, `test_xkiro_translator`, `test_tts_xkiro_fallback`, `test_kenburns_provider`, `test_character_manager_llm` |
| **2** | Script factory best-of-N + `algorithm_result.json`; duration tính bằng CODE từ số từ (3 từ/giây, clamp 4-8s) thay vì tin LLM tự ước lượng | `services/script_factory.py`, `pipeline/automated_factory.py` | `test_script_factory` (8 tests) |
| **3** | VLM style-check ảnh nhân vật theo Style Bible crayon: sàn điểm 7.0, regen kèm issue, `style_check.json` | `services/style_checker.py`, `pipeline/automated_factory.py` | `test_style_checker` (10 tests) |
| **4** | 60 tiêu chí QA thành code (17 script + 15 asset + 9 audio + 13 video + 6 quy trình), stage-aware, video checks n/a khi chưa render | `services/algorithm_qa.py` | `test_algorithm_qa` (11 tests, có render ffmpeg thật) |
| **5** | Hard gate ≥8.0 trong `approve_video_generation` (chỉ áp dụng job có `algorithm_result.json`, fail-closed khi crash); Telegram report scorecard; `/feedback` → `feedback_store` → fold vào prompt factory; self-heal API key/model khi Drive sync revert DB | `services/approval_state_machine.py`, `services/telegram_bot.py`, `services/feedback_store.py`, `pipeline/llm_client.py`, `services/script_auditor.py` | `test_qa_hard_gate` (8 tests), `test_feedback_loop` (9 tests), `test_llm_selfheal` (5 tests) |
| **6** | Golden files (fixture E2E 9.15/10), docs (báo cáo này), ruff sạch toàn repo, CI giữ nguyên (đã có test job + ffmpeg) | `tests/test_golden_remake.py`, `tests/fixtures/golden_remake_20261023/` | `test_golden_remake` (4 tests) |

---

## 4. Sự Thật Đo Được Từ Gateway (Đã Neo Vào Thiết Kế)

1. **Tốc độ giọng đọc thật (xKiro `xkiro-voice`):** VI `confident-male-vietnamese` ≈ 217 wpm; các giọng EN male ĐỀU chậm 98-112 wpm; tham số `speed` bị API bỏ qua (probe 1.5x/2.0x cho cùng 93.9s).
   → EN bị ép budget ≤ 16 từ/cảnh trong prompt; VI 12-24 từ/cảnh.
2. **LLM tự ước lượng `estimated_duration` lệch 40-85% so với TTS thật** → duration bắt buộc tính bằng code: `duration = clamp(số_từ_VI / 3.0, 4.0, 8.0)`. Trước khi sửa, Gate 2 chặn audio VI lệch 49% và EN lệch 85%.
3. **Settings DB nằm trên Google Drive và cloud sync trễ có thể revert row `llm_api_key`/`llm_model` về giá trị chết** → `pipeline/llm_client.py` tự heal: lỗi 401/403 + env key khác DB → re-pair DB từ `.env`; lỗi 503 unroutable → khôi phục model ưu tiên. Log: `"re-paired settings DB from environment key (sk-...xxxx)"`.
4. **Judge LLM dễ dãi hơn tiêu chí cơ học** (chấm 9.4 cho kịch bản WPM 244) → scorecard cơ học là lớp bảo vệ thật; hai lớp độc lập.

---

## 5. Quy Tắc Vận Hành (Runbook)

- **Gateway 401/503:** tự heal, không cần can thiệp. Nếu vẫn lỗi → kiểm tra log `"re-paired"` và file `.env` (key `XKIRO_API_KEY`).
- **Gate 4 bị chặn kèm "QA scorecard X.X/10 < 8.0":** mở `qa_scorecard.json` trong project dir, xem `Lỗi chính: Sxx/Txx...` trong tin nhắn Telegram, sửa kịch bản/audio rồi duyệt lại.
- **Feedback retention:** sau khi đăng video, gõ `/feedback <job_id> <điểm 0-10> [ghi chú]`. Bài học được fold tự động vào prompt của các job /remake sau (best/worst + ghi chú).
- **E2E smoke định kỳ:** `python -X utf8 scripts/e2e_mini_remake.py` — chạy thật slicing → factory → TTS → scorecard → Gate 4, artifacts tại `<data_dir>/e2e_mini/<timestamp>/`.
- **Ngưỡng sửa được:** `QA_GATE_MIN_SCORE = 8.0` (`services/algorithm_qa.py`), `min_score = 8.0` factory (`services/script_factory.py`).

## 6. Mục Chưa Verify (Cần Chạy Thật Trên VPS)

1. **Style-check với ảnh thật:** VLM chấm ảnh Imagen/DALL-E crayon thật (hiện chỉ smoke bằng ảnh PIL phẳng).
2. **Render video full với plan word-derived:** xác nhận assembler khớp narration↔shot khi duration tính từ số từ.
3. **Feedback loop trên nhiều job:** hiệu ứng fold feedback sau ≥3 video được chấm điểm.
4. **CI trên ubuntu-latest:** suite hiện chạy ổn trên Windows cục bộ; CI đã có sẵn test job (ffmpeg được cài trong workflow).
