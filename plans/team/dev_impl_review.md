# Senior Dev — review Phase 0–1 (brief → script duel)

## Verdict: BLOCK

17 test hiện có đều pass nhưng không chạm vào phần nguy hiểm. Tôi dựng lại bằng script ngoài repo: 2 request đồng thời tạo proposal → **`sqlite3.IntegrityError` không bắt → HTTP 500**; một key `ingest` bất kỳ ký pass cùng author → **agreed dù không phải critic**; sau khi agreed, một lệnh `block` đổi proposal thành **rejected**; `MAX_ROUNDS=2` không được enforce (tạo tới v4). Chưa an toàn để merge.

## Must-fix

| # | file:dòng | Bug / rủi ro | Cách sửa | Test cần thêm |
|---|---|---|---|---|
| 1 | `service_script_duel.py:201-216` | `MAX(version)+1` đọc **ngoài** transaction → 2 request cùng version → IntegrityError 500 (đã tái hiện) | Đọc MAX **trong** `BEGIN IMMEDIATE` hoặc `INSERT ... SELECT COALESCE(MAX)+1`; bắt IntegrityError → 409 `version_conflict` | 2 thread barrier, assert 1×201 + 1×409, không 500 |
| 2 | `main.py:201-243` | Key chỉ `identity:key`; `method/path` lưu nhưng **không so** → dùng lại key ở endpoint khác trả payload cũ 201 | Key = `identity:method:path:key` + hash body; lệch → 422 `key_reused` | Cùng key POST /briefs rồi /script-proposals → 422 |
| 3 | `main.py:1913,2005,2028`; `service_briefs.py:67` | Mọi key `ingest` tạo brief `calendar` (tự duyệt), ký, finding; không check author/critic → **agreed khống**, block phá bài người khác | Brief `calendar` chỉ owner/manager; sign chỉ author + critic được chỉ định; block cùng điều kiện | agent-c ký pass khi không phải critic → 403 |
| 4 | `service_script_duel.py:297,333-336` | `agreed` vẫn nhận `block` → status `rejected`, phá bất biến "agreed đóng băng" | Chặn sign khi `status='agreed'` (trừ idempotent cùng hash); gỡ chỉ qua `script_decisions` | agreed → block → 409, status giữ `agreed` |
| 5 | `service_script_duel.py:32` | `MAX_ROUNDS` khai báo nhưng không dùng → version vô hạn | Đếm độ sâu parent chain, >2 → 409 `round_limit` + event escalate | tạo v3 từ v2 → 409 |
| 6 | `service_script_duel.py:264-266,382` | Finding đổi `invalid` → `under_review`; `_next_action` trả `review_or_sign` dù validator fail | Giữ `invalid`; `_next_action` xét `validation.ok` trước status | invalid + finding → vẫn `fix_proposal` |

## 8 điểm

**1. Race version.** Đã chứng minh: barrier sau khi đọc MAX ⇒ 1×OK v1, 1×IntegrityError. Fix như #1; thêm retry 1 lần thay vì để client tự đoán.

**2. Idempotency.** `_replay_scoped` (`main.py:212-222`) và `_scoped_idempotent` (`:225-243`) chỉ tra theo key; `method`/`path` là cột chết. Key dùng lại ở endpoint khác trả **payload cũ với 201** — client tưởng đã tạo proposal nhưng không có gì. Cùng key + body khác cũng replay. Fix: composite key + body hash.

**3. Authorization.** Không có khái niệm critic được chỉ định; `sign` chỉ loại self-review ở `add_finding`, còn `sign` không loại gì. Trên prod: một agent lạ ký pass → kịch bản "chốt" không ai review; hoặc `block` bất kỳ proposal → treo việc người khác (DoS). Nặng hơn: brief `calendar` tự approved khi tạo → **bỏ qua cổng owner**.

**4. Validator.** `WORDS_PER_MINUTE=150` cứng (`:30`) bỏ qua `language` và tốc độ voice TTS thật → ước lượng lệch, chặn nhầm hoặc lọt. `_NUMBER_CLAIM_RE` (`:34-39`) bắt năm 1800–2099 nhưng **bỏ** "42 percent", "42 phần trăm", "1.5 million", "42 USD"; coi "1.234" là số nghìn. Nên whitelist theo `language`, thêm dạng chữ, và đọc WPM từ `AUDIO_CONFIG`.

**5. Transaction/DB.** Version + dup-check ngoài tx; `add_finding` check status ngoài tx rồi mới ghi (TOCTOU: finding rơi vào proposal vừa agreed); `briefs.approve` UPDATE không CAS → approve sau cancel làm sống lại brief. `_event` nuốt lỗi chỉ mất audit, chấp nhận được.

**6. State machine.** `invalid→finding→under_review` làm mất dấu lỗi validator (#6). `rejected→pass` chặn đúng; `superseded→sign` chặn đúng; `_maybe_agree` CAS `draft|under_review` đúng, nhưng `sign` thiếu chặn `agreed` (#4). `rejected→block` lặp vô hại.

**7. Test gaps.** Thiếu: concurrency, key reuse, agreed→block, round cap, owner override (`script_decisions` **chưa có endpoint nào dùng** — bảng chết), migration trên DB cũ, authz co-sign. Test `test_author_must_sign_for_agreement` đang **hợp thức hoá** lỗ hổng: agent-c ký pass mà không ai kiểm tra vai.

**8. Migration.** 5 bảng nối vào `_SCHEMA` bằng `CREATE TABLE IF NOT EXISTS`, `executescript` mỗi lần boot → additive, an toàn với DB prod, không cần ALTER. Index đủ cho đường đọc hiện tại; thiếu unique `script_findings(proposal_id, reviewer, category)` (spam finding) và index `idempotency(created_at)` cho job dọn.

## 3 câu hỏi cho PO

1. Ai được tạo brief `approval_mode=calendar` (tự duyệt ngay)? Nếu chỉ owner/manager, tôi siết route; nếu agent được, phải bỏ auto-approve.
2. "Critic" của một proposal được chỉ định ở đâu — registry role, `brief.critic`, hay bất kỳ agent nào khác author? Quyết định schema và mã 403.
3. Agreed có được mở lại không, và bằng đường nào — owner override qua `script_decisions`, hay bắt buộc brief mới? Quyết định state machine.
