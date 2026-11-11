# TEST PLAN — video-system (plan v3.2)

Nguyên tắc: mỗi luật trong plan v3.2 phải có ≥1 test chứng minh. Không test,
không phải luật — chỉ là ý định.

## 1. Ma trận phủ — plan → test

| Plan mục | Luật | Test |
|---|---|---|
| §2.1 / Phụ lục A | Không nhảy qua `qc_passed` (generated→appended cấm) | `test_domain_transitions.py::test_no_jump_over_qc` |
| Phụ lục A | `appended`/`superseded` terminal | `test_appended_and_superseded_terminal` |
| Phụ lục A | Held không tự thoát (chỉ A12/A13) | `test_held_retry_groups` |
| §2.3 | Item 1 status; không script_ready+audio_ready; exported terminal | `test_item_transitions_no_status_mixture` |
| §2.2 | `text_only` không là engine hợp lệ của shot hoàn tất | `test_text_only_is_not_a_done_engine` |
| Phụ lục C | Enum held_reason đầy đủ + label VI | `test_held_reason_groups` |
| §3.2.5 | Canonical hash ổn định (key order, rounding 3 chữ số) | `test_canonical_hash_stable_under_key_order_and_rounding` |
| §3.2.2 | Không tin payload duration — probe bắt buộc | `test_import_ok_and_hash_stable` (payload nói dối 99s) |
| §3.2.2 | Drift toàn video > ±1.0s → 422 + report | `test_global_drift_rejected_with_report` |
| §3.2 | Overlap/gap giữa segment > ±0.25s → 422 | `test_segment_gap_overlaps_rejected` |
| §3.2 | on_screen_text chỉ quote/diagram | `test_on_screen_text_allowlist` |
| §3.2.6 | Cùng version khác content → 409 | `test_same_version_diff_content_conflict` |
| §3.2.6 | Version mới → cũ `superseded`, giữ lịch sử | `test_new_version_supersedes_old_never_overwrites` |
| §3.2 | Giới hạn 500 shot / body size | `test_limits_enforced` |
| §7.3 | URL qua allowlist domain | `test_url_host_allowlist` |
| §4.B.5 | OCR chưa cấu hình → held `QC_TOOL_UNAVAILABLE`, không âm thầm pass | `test_ocr_unavailable_cannot_pass` |
| §4.B.5 | QC hình học: resolution/duration | `test_qc_geometry_failures` |
| §4.B.5 | Text leak → `VIDEO_TEXT_LEAK` | `test_qc_text_allowlist` |
| §7.1 | Scope `ingest` không approve được | `test_ingest_scope_cannot_approve` |
| §4.B.7 | Prefix liên tục: held chặn, shot sau held không nối | `test_batch_contiguous_prefix_blocks` |
| A13 | Retry video-group: held→generating→produce | `test_qc_geometry_failures`, E2E |
| A12 | Retry keyframe-group: held→keyframe_pending | `test_retry_from_keyframe_held_goes_to_keyframe_pending` |
| §7.6 | Xóa khi đang gen → 409 | `test_delete_blocked_while_generating` |
| E2E | Full flow đến `appended` + engine ghi rõ | `test_full_e2e_flow_to_appended` |
| Phụ lục E | HMAC-SHA256 raw body + payload shape | `test_webhooks.py` |
| Phụ lục G — domain | ShortStatus transitions + nhãn VI | `test_short_transition_laws_domain` |
| Phụ lục G — flow | kịch bản→duyệt→produce→MP4 9:16→publish | `test_shorts_flow.py` (E2E-9) |
| Phụ lục G — fail-closed | thiếu adapter LLM/ảnh → 503/422 tiếng người | `test_short_create_fail_closed_without_adapter`, `test_short_produce_no_image_source_honest_error` |
| Phụ lục G — karaoke | ASS `{\k}` + PlayRes 1080×1920 + margin 15% | `test_karaoke_ass_geometry_and_k_tags` |
| Phụ lục G.10 | RSS parse + dedupe + Telegram skip + scopes | `test_news_sync.py` |

## 2. Hạ tầng test

- **Env tách:** mỗi run dùng `VIDEO_DATA_DIR` tmp (conftest), không đụng data thật.
- **Adapters giả:** `FakeProbe` (configurable để bẻ resolution/fps/duration),
  `FakeOcr` (queue text), `FakeGeneration` (engine_name=veo). Sau mỗi test
  registry khôi phục sạch — không stub rò rỉ sang test khác.
  Phụ lục G thêm `FakeLlm` (JSON schema đúng), `FakeImageGen` + fixture
  `short_adapters`/`short_adapters_removed`; TTS offline qua `VIDEO_OFFLINE_TTS=1`
  (WAV tổng hợp deterministic), ffmpeg fake qua `VIDEO_FAKE_FFMPEG=1`.
- **Wav sinh bằng stdlib** — test không cần ffmpeg; probe adapter thật (ffprobe)
  được kiểm ở môi trường tích hợp, không trong unit test.

## 3. Chưa phủ (nợ có chủ đích — ghí vào Giai đoạn sau)

1. **ffprobe adapter thật** — test tích hợp trên máy có ffmpeg, sinh mp4 thật
   bằng ffmpeg fixture (cần khi cắm adapter thật).
2. **Idempotency-Key replay HTTP level** — đã có middleware `_idempotent`,
   cần test riêng POST trùng key trả response cũ + header `Idempotency-Replayed`.
3. **Webhook delivery retry backoff** — dispatch thread + `next_attempt_at`;
   cần fake clock để test 4 mức backoff + give-up 24h.
4. **Concurrency CAS** — 2 thread claim cùng shot, đúng 1 thắng (SQLite WAL).
5. **UI e2e trình duyệt** — playwright smoke (list → detail → retry held).
6. **Rate limit 429 + Retry-After** (§7.2) — middleware chưa implement, test đi kèm.

## 4. Ngưỡng chặn merge

- Toàn bộ suite xanh (`pytest -q`).
- Test mới cho mỗi bug fix (regression bắt buộc).
- Không được xóa/gãy test để "đúng kế hoạch" — sửa plan trước, test theo.
