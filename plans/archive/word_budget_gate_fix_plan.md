# Plan: Thống nhất ngân sách từ + reaper job mồ côi

Ngày: 2026-10-23 · Build hiện trường: `worker:653c437` · Job bị giết: `remake_1792772836_6d57f1`
Ủy quyền: "fix tất cả những gì bạn cho là đúng".

## Bối cảnh — tại sao sửa cái này

Ba job chết liên tiếp với cùng chữ ký `All script variants were invalid across all rounds`
(`4bb99d`, `f6445f`, `6d57f1`). Đo trên `6d57f1`: 174 cảnh, model đạt 114/174 (65%) và 95/174 (55%)
số cảnh nằm trong cửa sổ 18–24 từ._gate_ là AND cứng trên toàn bộ 174 biến ngẫu nhiên, nên
P(thắng) ≈ 0.65^174 ≈ 1e-32. Đổi model, chia chunk, thêm round sửa đều không đổi được bài toán.

Và cửa sổ 18–24 đó **không tầng nào downstream đòi**: QA scorecard chỉ check S09 duration 4–8s,
S10 trung bình 4.5–6.5s, S08 WPM 150–210, S15 ≥3 từ/cảnh. Auditor khai báo 12–28.
Bốn bộ ngân sách mâu thuẫn cho cùng một trường.

## Nguyên tắc thiết kế

QA scorecard là luật. Mọi gate trung gian phải **dẫn xuất** từ luật đó, không được tự đặt số phẳng
chặt hơn luật. Không gate nào đòi N phép thử ngẫu nhiên cùng đạt trong một cửa sổ hẹp.

---

## W1 — Một nguồn chân lý cho cửa sổ từ, dẫn xuất từ duration ✅

| # | Việc | File |
|---|---|---|
| W1.1 | `GENERATED_WORD_WINDOW = (ceil(MIN_SCENE_DUR * WPS), floor(MAX_SCENE_DUR * WPS))` → (14, 28), định nghĩa duy nhất trong auditor; factory chỉ import | `services/script_auditor.py:40-57` · `services/script_factory.py:61` |
| W1.2 | Slicer tính duration từ số từ VI bằng đúng công thức `words / SCENE_WORDS_PER_SECOND` clamp [4,8], thay vì tin `estimated_duration` model đoán | `services/script_auditor.py` (vòng gán duration) |
| W1.3 | Prompt rewrite + prompt sửa + prompt slicer + copy gửi owner (Gate 1, `/custom_script`, txt export) đều dùng biến dẫn xuất, không literal | `services/script_factory.py` · `services/script_auditor.py:662`, `:218` · `services/telegram_bot.py` |
| W1.4 | Steering ≠ gating: prompt **xin** 18–24 (`RECOMMENDED_WORDS_PER_SHOT_ALGORITHM`) nhưng chỉ **chặn** 14–28 | `services/script_auditor.py:52` |

**Checklist test song song**
- [x] `test_word_window_is_derived_from_the_qa_duration_band` — window == `SCENE_DUR_RANGE × 3.5`.
- [x] `test_steering_band_sits_inside_the_enforced_window` — band xin nằm trong band chặn.
- [x] `test_gate_window_maps_into_qa_scene_duration_band` — mọi cảnh đạt window đều vào S09.
- [x] `test_slicer_derives_scene_duration_from_word_count` — 21 từ → 6.0s; 8 từ → 4.0s (clamp dưới); 40 từ → 8.0s (clamp trên); EN mirror VI.
- [x] `test_slicer_prompt_states_the_derived_budget_not_a_hardcoded_one` — literal 18/24/16 phải xuất hiện *từ biến*, không từ string cứng.

**Done when:** không còn chữ số 18/24 xuất hiện độc lập ở 2 file. ✅

## W2 — Accept theo tỷ lệ + ràng buộc tổng, thay cho AND-174 ✅

| # | Việc | File |
|---|---|---|
| W2.1 | `_budget_offenders` → `_acceptance_report` trả breakdown `vi_low / vi_high / en_high / ratio / wpm / mean_scene_duration` | `services/script_factory.py:360+` |
| W2.2 | Predicate mới: `ratio >= MIN_BUDGET_RATIO (0.95)` **VÀ** duration trung bình ∈ `MEAN_DUR_RANGE` (4.5–6.5). **WPM tổng chỉ ghi nhận, không veto** — see below | `services/script_factory.py:69`, `:392` |
| W2.3 | Repair loop thoát sớm ngay khi predicate đạt (đỡ đốt call) + không chạy gì nếu variant đã sạch | `services/script_factory.py` (`_repair_word_budget` early-return) |
| W2.4 | Log failure nêu rõ tỷ lệ + từng lớp vi phạm + WPM/avg đo được | WARNING `Word-budget repair left ... vi_low=%d vi_high=%d en_high=%d WPM=%.0f mean=%.2fs` |
| W2.5 | `budget_report` được attach vào variant và nổi lên `algorithm_result` để QA/downstream đọc | `services/script_factory.py` (`run` return) |

**Vì sao WPM rút khỏi predicate (đo, không phải cảm tính):** duration mỗi cảnh được *dẫn xuất*
từ số từ (`words / 3.5`), nên một script tuân thủ window 14–28 có WPM tổng đúng bằng 210 theo
cấu trúc. Con đường duy nhất vượt trần S08 (210) chính là ≤5% cảnh lệch mà rule tỷ lệ cố tình dung
thứ — ví dụ 1 cảnh 40 từ trong kịch bản 20 cảnh → 216 WPM → gate sẽ giết một variant mà QA chấm đạt.
WPM vẫn nằm trong `budget_report`/metrics và vẫn bị QA **S08 chấm trên audio thật**, tức luật không
mất hiệu lực, chỉ bỏ được vòng lặp tự tham chiếu.

**Checklist test song song**
- [x] `test_variant_accepted_with_five_percent_outliers` — 20 cảnh, 1 lệch → valid (build cũ: invalid).
- [x] `test_variant_rejected_once_outliers_pass_the_ratio` — 3/20 lệch → invalid, reason chứa "ratio".
- [x] `test_mean_scene_duration_is_enforced_absolute` — avg ngoài 4.5–6.5 → invalid.
- [x] ~~`test_aggregate_wpm_rejects_thin_script`~~ → **thay bằng** `test_mean_scene_duration_is_enforced_absolute`, vì veto WPM mâu thuẫn rule tỷ lệ (xem W2.5).
- [x] `test_repair_never_runs_on_an_already_compliant_variant` — 0 call sửa khi đã đạt.
- [x] `test_repair_stops_as_soon_as_acceptance_holds` — sửa 1 batch là dừng.
- [x] `test_repair_failure_log_shares_the_breakdown` — WARNING chứa `vi_low=2` / `vi_high=1`.
- [x] `test_on_progress_heartbeats_while_the_script_is_being_written` — ≥6 beat/variant, `_beat(None)` không nổ.

**Done when:** một variant thật sự kém vẫn bị vứt, nhưng variant chỉ lệch đuôi nhỏ thì đi tiếp được. ✅

## W3 — Dòng EN không còn là kẻ giết variant ✅

| # | Việc | File |
|---|---|---|
| W3.1 | EN quá `EN_MAX_WORDS_ALGORITHM` (16) chỉ **tính vào tỷ lệ**, không tự vô hiệu hoá cả variant (QA chỉ đo T08 ±25% tổng thời lượng EN) | `services/script_factory.py` (`_acceptance_report`) |

**Checklist test song song**
- [x] `test_en_only_violation_counts_in_ratio_not_killer` — 100% VI đạt, 1/20 EN lệch → valid.

## W4 — `empty voiceover` đi vào đường split-retry thay vì vứt variant ✅

| # | Việc | File |
|---|---|---|
| W4.1 | `_rewrite_chunk` raise `ScriptFactoryError` khi cảnh nào `voiceover_vi` rỗng ⇒ `_rewrite_chunk_robust` halving retry | `services/script_factory.py` |
| W4.2 | Bỏ check rỗng cấp variant (`invalid_reason="empty voiceover in variant"`) vì đã có W4.1; giữ assert số cảnh | `services/script_factory.py` |

**Checklist test song song**
- [x] `test_empty_voiceover_triggers_halving_retry` — chunk 10 rỗng → halving 5+5, rewrite_calls == 3 (1×10 + 2×5), variant vẫn valid.
- [x] `test_empty_voiceover_persists_only_after_full_split` — mọi sub-chunk đều rỗng → variant invalid, lý do nêu index cảnh.
  (Sửa bug test: `if self.empty_scene_in_chunks_above` coi `0` là falsy → nay `is not None`.)

## W5 — Reaper job mồ côi (đang chặn chạy lại /remake cùng URL) ✅

| # | Việc | File |
|---|---|---|
| W5.1 | `touch_job(job_id)` bump `updated_at` — heartbeat | `services/job_queue.py:497` |
| W5.2 | `reap_stale_jobs(max_age_sec=STALE_JOB_TIMEOUT_SEC)` chuyển `ANALYZING` quá hạn → `FAILED` + `error_message` mô quy trình chết, trả danh sách | `services/job_queue.py:28`, `:510` |
| W5.3 | `heartbeat_heavy_lock` cũng bump `updated_at` (lease sống = bằng chứng process sống) | `services/job_queue.py:268` |
| W5.4 | `automated_factory` heartbeat ở mỗi phase + mỗi round script qua `partial(jq.touch_job, job_id)` → `on_progress=touch` | `pipeline/automated_factory.py:199,618,708` |
| W5.5 | Worker gọi reap lúc startup (`:98`) và trong main loop (`:188`) + báo Telegram `_reap_orphans` (`:552`) | `core/worker.py` |

**Vì sao bắt buộc:** `telegram_bot.py` dùng `create_job_if_no_active`, từ chối nếu còn job
non-terminal cùng video id. `ANALYZING` là state **duy nhất** không gì resume (`get_next_runnable_job`
chỉ lấy QUEUED/APPROVED/PROCESSING) ⇒ row zombie âm thầm phủ quyết `/remake`. 3 row `ANALYZING`
của `yon5fqz6-v8` khiến lệnh `/remake` tương lai bị bỏ qua lặng lẽ. Người dùng cấm sửa DB tay ⇒
chỉ được sửa bằng code. ✅ (reaper chạy bằng code, không ai UPDATE tay.)

**Checklist test song song**
- [x] `test_touch_job_bumps_updated_at`.
- [x] `test_reap_stale_analyzing_job_fails_it` — quá ngưỡng → FAILED, `error_message` chứa "không heartbeat".
- [x] `test_reap_skips_jobs_still_heartbeating` — còn heartbeat mới → giữ nguyên.
- [x] `test_reap_leaves_paused_and_terminal_states_alone` — WAITING_APPROVAL / AWAITING_CUSTOM_SCRIPT / COMPLETED không bị đụng.
- [x] `test_reap_unblocks_relaunch_of_same_video` — **reap xong thì `create_job_if_no_active` trả job mới** (mục tiêu nghiệp vụ chính).
- [x] `test_heavy_lock_heartbeat_touches_job_row`.
- [x] `test_reap_orphans_fails_zombie_and_tells_owner` — worker reap + nhắn Telegram kèm `/remake`.
- [x] `test_worker_loop_wires_the_reaper` — `inspect.getsource(run)` phải có 2 lời gọi reap (startup + idle); reaper không ai gọi chính là bug đã chôn 3 row.

## W6 — Gate xanh, deploy, nghiêm thu lại

| # | Bước | Lệnh / bằng chứng | Trạng thái |
|---|---|---|---|
| W6.1 | Lint | `ruff check .` | ☐ |
| W6.2 | Test toàn bộ | `pytest -q --tb=short` (mốc cũ: 569 pass) | ☐ |
| W6.3 | Kiểm tra CJK lọt vào chuỗi tiếng Việt | grep `[一-鿿]` trên file đã sửa → phải rỗng | ☐ |
| W6.4 | Commit + push main (đã ủy quyền deploy) | SHA build mới | ☐ |
| W6.5 | Xác minh deploy qua SSH, không tin CI suông | `docker ps`, log `Chế độ sinh video:` | ☐ |
| W6.6 | Chạy lại `/remake yon5fqz6-v8` **bằng chính sản phẩm** | job mới, voice `confident-male-vietnamese` | ☐ |
| W6.7 | Đẩy tới Gate 2, lấy `audio_vi_full.mp3` + `audio_en_full.mp3` | 2 file audio về chat | ☐ |
| W6.8 | QA scorecard ≥ 9.5/10 (62 check) | `qa_scorecard.json` | ☐ |

---

## Quyết định đã chốt (mặc định tôi chọn, đã ship)

| Câu | Quyết định |
|---|---|
| `MIN_BUDGET_RATIO` | **0.95** — đủ rộng để thắng phương sai model, đủ chặt để QA S07/S08/S10 không vỡ |
| Ngưỡng reap | **30 phút** (`STALE_JOB_TIMEOUT_SEC = 1800`), không phải 20 phút dự kiến: round script xấu nhất đo được ~6 phút nhưng một chunk 10 cảnh retry halving + 3 variant × 2 round xếp hàng sau `heavy_lock` có thể im lặng > 20 phút. Thà chậm chôn còn hơn chôn job đang sống. |
| WPM tổng trong predicate? | **Không.** Ghi vào `budget_report`, QA S08 chấm trên audio thật. Lý do đo lường ở W2.5. |
| Có nới QA scorecard không | **Không.** Scorecard giữ nguyên, chỉ gate trung gian tự sửa cho khớp luật. |
| Copy job data lên `G:\` | Chưa làm, chờ bạn yêu cầu |

## Việc chỉ owner làm được

- Duyệt Gate 1/Gate 2 qua Telegram (approval là quyền owner, tôi không tự duyệt).
- Cung cấp cookie Google nếu muốn bật `/veo on`.
- Chấm 2 file audio bằng tai và nói có đạt "Nam Dõng Dạc" như kỳ vọng không.
