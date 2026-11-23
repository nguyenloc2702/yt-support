# Luồng nghiệp vụ — 2 agent tự chốt kịch bản → tự tạo job → chia shot → gen → ráp

**Ngày:** 2026-11-23 · **Trạng thái:** DRAFT để Owner duyệt 3 điểm ở §12
**Nền:** `agent_comms_decision.md` v1.1 (chat + claim + review + registry/mention/wake)
**Nguyên tắc:** chat để **thảo luận**, record để **chốt**. Không quyết định nào chỉ nằm trong tin nhắn.

**Đã triển khai (2026-11-23):** Phase 0–1 — `job_briefs`, `script_proposals`,
`script_findings`, `script_signatures`, validator máy, agreement 2 chữ ký.
API: `POST/GET /api/v1/briefs`, `POST /api/v1/script-proposals`, findings, sign,
`GET /briefs/{id}/agreement`. Test: `tests/test_brief_script_flow.py` (17 case).
Phase 2 (auto job + TTS) và Phase 3–5 chưa code.

---

## 0. Một câu

> Owner đưa **brief** (hoặc chọn từ content calendar) → 2 agent **đấu kịch bản có cấu trúc**
> → ký **agreement gắn hash** → hệ **tự tạo job** (audio + timed shots) → manager **chia
> dải shot** → mỗi agent claim phần mình → **review chéo bắt buộc** → ráp khi hết shortfall
> và không còn block → Owner duyệt publish.

---

## 1. State machine (một item đi qua)

```
brief.draft ──► brief.approved ──► script.drafting ──► script.reviewing
     │                                                        │
     │                                              ┌─────────┴─────────┐
     │                                              ▼                   ▼
     │                                        script.agreed       script.deadlock
     │                                              │                   │
     │                                              ▼                   ▼
     │                                        job.building         owner.decides
     │                                              │                   │
     │                                              ▼                   └──► drafting
     │                                        item.todo
     │                                              │
     │                                              ▼
     │                                    assigned ──► generating ⇄ review
     │                                              │        │
     │                                              │        └─► held (blocking)
     │                                              ▼
     │                                        assembling ──► ready ──► published
     └──────────────────────────────────────────────────────────────────────
```

Trạng thái item dùng lại `ItemStatus` hiện có (`todo`, `generating`, `held_review`,
`assembling`, `ready`, `completed`). Các trạng thái `brief.*`, `script.*`, `job.building`
là **bảng riêng**, không nhồi vào `items.status`.

---

## 2. Phase 0 — Brief (1 lần/job)

**Ai tạo:** Owner (chat/@tag hoặc digest submission) hoặc Manager đề xuất.

**Record `job_briefs`:**

| Cột | Ý nghĩa |
|---|---|
| `id`, `title`, `angle` | chủ đề + góc kể |
| `target_duration_sec` | 600–900s (mặc định 780) |
| `language`, `style_lock_version` | `en`, `v3` |
| `source_links_json` | nguồn bắt buộc cho tin thời sự |
| `deadline_at` | mặc định 18:00 cùng ngày |
| `budget_json` | trần token / số lần gen / số shot |
| `status` | `draft → approved → in_progress → done / cancelled` |
| `approved_by`, `approved_at` | owner hoặc `calendar` |

**3 chế độ duyệt (chọn 1, xem §12-Q1):**
- **A. Calendar pre-approval:** Owner duyệt trước danh sách chủ đề tuần → agent chọn trong danh sách, **không cần duyệt lại từng job**. Tự chủ cao nhất.
- **B. Objection window:** Brief do agent đề xuất, tự động `approved` sau **30 phút** nếu Owner không phản đối (trong giờ làm việc).
- **C. Hard gate:** Owner bấm duyệt từng brief. Chậm nhất, an toàn nhất.

> Đề xuất: **A cho chủ đề trong calendar, B cho chủ đề mới, C cho tin nhạy cảm** (chính trị/người thật/số liệu tài chính).

---

## 3. Phase 1 — Script duel (2 agent thảo luận & chốt)

### 3.1 Vai

| Vòng | Author | Critic |
|---|---|---|
| 1 | Agent A | Agent B |
| 2 | Agent A (sửa) | Agent B (review lại) |
| Nếu bế tắc | Manager quyết | Owner nếu manager vắng |

Đảo vai cho job kế tiếp để cả 2 cùng luyện; không đảo trong cùng job (tránh tranh authorship).

### 3.2 Cấu trúc một vòng

```
A: POST /script-proposals            {brief_id, shots[], script_text, sources[]}
   → server chạy validator máy (duration, shot count, style, thiếu field) → pass/fail ngay
B: POST /script-proposals/{id}/findings
   {category: fact|structure|style|duration, severity, evidence, blocking}
A: POST /script-proposals            {parent_id, version+1, shots[] đã sửa}
B: POST /script-proposals/{id}/sign  {verdict: pass|block}
A: POST /script-proposals/{id}/sign  {verdict: pass}
→ đủ 2 chữ ký cùng hash + validator pass + critic LLM pass ⇒ script.agreed
```

### 3.3 Luật chốt

- **Agreement = 2 chữ ký trên cùng một `content_hash`** + validator máy pass + critic LLM pass.
- Tối đa **2 vòng** sửa (khớp trần critic hiện có). Vòng 3 → `script.deadlock`.
- Deadlock: Manager quyết trong **15 phút**; manager vắng → Owner quyết; quyết định ghi
  `script_decisions` + event, kèm lý do.
- **Không được tự ý sửa sau khi ký.** Hash đổi ⇒ agreement cũ `stale`, phải ký lại.
- Chat job room chỉ để giải thích; **nội dung kịch bản nằm trong `script_proposals`**,
  không parse từ chat.

### 3.4 Validator máy (chạy trước khi cho ký)

| Check | Ngưỡng |
|---|---|
| Tổng thời lượng ước tính | 600–900s (words / 150 wpm ±10%) |
| Số shot | 12–18s/shot ⇒ 40–75 shot cho 780s |
| Shot thiếu `narration` hoặc `visual_prompt` | 0 |
| Style lock | đúng `v3`, character sheet khớp |
| Nguồn | mỗi claim số liệu/ngày/tên phải có `source_url` |
| Trùng tin | không lặp chủ đề trong cùng brief |

Validator fail ⇒ không cho ký, trả danh sách lỗi để author sửa.

---

## 4. Phase 2 — Tự tạo job (sau khi agreed)

### 4.1 Vì sao phải async

`create_from_script` hiện **chạy đồng bộ trong request** (`service_todos.py:57`), bên trong
gọi TTS + tạo audio/srt/ass + timed shots (`_finalize_preproduction_to_todo`,
`service_todos.py:201`). Với 780s audio, TTS có thể mất vài phút → HTTP timeout nếu gọi trực tiếp.

### 4.2 Luồng

```
POST /jobs/from-agreement {agreement_id}
  → 202 {build_id, status: queued}
  → background builder (thread/scheduler, không cần broker):
      1. verify agreement: 2 chữ ký, hash khớp, chưa dùng
      2. create item + script + TTS + timed shots  (tái dùng create_from_script)
      3. lưu item_id vào job_builds, event job.built
      4. lỗi → status failed + lý do + escalate; retry tối đa 2, cùng build_id
GET /jobs/{build_id} → {status, item_id?, error?}
```

- **Idempotency:** `UNIQUE(agreement_id)` trên `job_builds`. Gọi lại trả cùng `build_id`/`item_id`,
  **không bao giờ tạo item thứ 2**.
- Job build là **1 transaction logic**; nếu TTS fail giữa chừng → item `failed` + build `failed`,
  không để item mồ côi ở `processing_input`.
- Sau khi build xong: `item.todo` + `GET /items/{id}` trả script/timings/shots như agent 7 bước vẫn dùng.

---

## 5. Phase 3 — Chia shot & assignment

### 5.1 Chia dải (đề xuất)

- Chia **2 dải liền mạch**, cân theo **tổng ký tự narration** (không cân theo số shot), vì
  shot dài/ngắn khác nhau ⇒ cân thời gian gen thực tế.
- Ví dụ 66 shot, A 52% narration → A nhận shot 1–34, B nhận 35–66. Lưu `assignment_plan` +
  `plan_version` để audit và re-split.
- Vì sao không chia xen kẽ: mỗi lần chuyển dải là một lần đổi context/ảnh reference; dải liền
  mạch giảm lỗi và dễ bàn giao.

### 5.2 Assignment ≠ Claim

| Khái niệm | Ý nghĩa | Ai đổi |
|---|---|---|
| `work_assignments` | **Kế hoạch**: shot nào dự kiến cho ai | Manager đổi tự do khi shot **chưa** claim |
| `work_claims` | **Thực thi**: ai đang giữ, lease 20' | Agent claim đúng assignee của mình |

- Claim lệch assignee → 409 `not_assignee` (trừ khi manager đã re-assign).
- Shot đã claim mà muốn đổi người → cần Owner duyệt (giữ luật v1.1: không auto-steal).
- Agent chết (`suspect`) → shot về `unassigned`, manager re-assign các shot **chưa claim**;
  shot đang claim giữ nguyên đến khi Owner quyết takeover.

---

## 6. Phase 4 — Gen + review chéo

```
với mỗi shot trong dải của tôi:
  claim → keyframe → video → upload
  → reviewer (agent còn lại) chấm: pass | fail(+blocking)
  → pass: shot.appended
  → fail blocking: shot.held(REVIEW_BLOCKED) → author sửa → review lại
```

- **Reviewer ≠ người gen** — server chặn cứng (`reviewer != claim.holder`).
- Reviewer vắng >20' → manager chấm; manager vắng → Owner. Không bao giờ tự chấm bài mình.
- **Trần regen/shot: 2** (env). Lần 3 → `held` + escalate, không đốt tiếp.
- Mỗi 10 shot, manager tổng hợp tiến độ vào job room (1 tin, không spam từng shot).

---

## 7. Phase 5 — Ráp & publish

`confirm-assemble` hiện chỉ check video/shortfall/thumbnail (`service_assembler.py:298`).
Bổ sung **4 gate** (P3 của v1.1):

1. `shortfall_sec == 0` (hoặc Owner bấm allow-shortfall có log).
2. Không còn `shot.status = held` do `REVIEW_BLOCKED`.
3. **`script_hash` hiện tại == `agreement.content_hash`** — kịch bản bị sửa mà chưa ký lại thì không ráp.
4. Có thumbnail + `owner` approval cho publish (giữ nguyên human gate).

### 7.1 Kịch bản bị sửa giữa lúc gen (xem §12-Q3)

- **MVP (đề xuất): đóng băng kịch bản tại agreement.** Muốn sửa → agreement mới + **job mới**;
  job cũ `cancelled`, shots đã gen giữ làm reference, không ráp.
- **V2:** diff theo `shot_index + narration_hash`, chỉ invalidate các shot từ điểm sửa trở đi,
  giữ shot trước đó. Cần tool diff + test kỹ; chưa làm ở MVP.

---

## 8. API surface (additive)

| Method | Path | Ai | Việc |
|---|---|---|---|
| POST | `/briefs` | owner/manager | tạo brief |
| POST | `/briefs/{id}/approve` | owner | duyệt (mode C) |
| GET | `/briefs/{id}` | all | trạng thái |
| POST | `/script-proposals` | author | tạo version |
| POST | `/script-proposals/{id}/findings` | critic | findings có cấu trúc |
| POST | `/script-proposals/{id}/sign` | author+critic | ký theo hash |
| POST | `/jobs/from-agreement` | manager/author | 202 build |
| GET | `/jobs/{build_id}` | all | trạng thái build |
| POST | `/items/{id}/assignments` | manager | tạo/re-split plan |
| GET | `/items/{id}/pipeline` | agent | **một call biết việc kế tiếp** |
| (đã có) | claims/reviews/ack/inbox | agent | v1.1 |

---

## 9. Data model bổ sung

```sql
job_briefs(id, title, angle, target_duration_sec, language, style_lock_version,
           source_links_json, deadline_at, budget_json, status,
           approved_by, approved_at, created_by, created_at)

script_proposals(id, brief_id, parent_id, version, author, content_json,
                 content_hash, status(draft/under_review/passed/rejected/superseded/stale),
                 created_at)  idx(brief_id, version)

script_findings(id, proposal_id, reviewer, category, severity, evidence_json,
                blocking, created_at)  idx(proposal_id, blocking)

script_signatures(proposal_id, signer, content_hash, verdict, signed_at)
  PK(proposal_id, signer)

script_decisions(id, brief_id, decided_by, decision, reason, created_at)

job_builds(id, agreement_id UNIQUE, item_id, status(queued/building/built/failed),
           error, attempts, created_at, updated_at)

work_assignments(item_id, shot_index, assignee, plan_version, assigned_by, assigned_at)
  PK(item_id, shot_index, plan_version); idx(item_id, assignee)
```

`script_proposals.content_json` chứa `shots[]` đúng schema `from-script` để build không phải đoán.

---

## 10. Ma trận lỗi

| Tình huống | Hệ xử lý | Ai biết |
|---|---|---|
| 2 agent không chốt được sau 2 vòng | `deadlock` → manager quyết 15' → owner | Telegram + UI |
| Validator fail liên tục | Không cho ký, trả lỗi cụ thể; quá 3 lần → escalate | Manager |
| TTS fail khi build | build `failed`, item `failed`, retry 2 lần | Telegram |
| Gọi from-agreement 2 lần | Cùng `build_id`, 1 item | Audit event |
| Agent chết giữa dải | `suspect`; shot chưa claim re-assign; shot đang claim chờ owner | Telegram |
| Reviewer vắng | Manager chấm; manager vắng → owner | Escalation |
| Shot fail 2 lần | `held` + escalate, không regen tiếp | Owner |
| Kịch bản sửa sau khi ký | Hash lệch → chặn ráp; phải ký lại / job mới | UI + event |
| Vượt budget | 403 `budget_exceeded`, dừng gen | Owner |
| Owner vắng 16–18h | Job dừng ở `ready`/`held`; mất slot đăng | Telegram |

---

## 11. Tiêu chí nghiệm thu

1. Brief → agreed ≤ **30 phút** (không tính thời gian owner duyệt nếu mode C).
2. Agreement → item `todo` ≤ **5 phút** với audio 780s (TTS là phần chậm nhất, đo riêng).
3. **0 item trùng** cho cùng `agreement_id` (test gọi 5 lần song song).
4. **100% shot có assignee trước khi gen**; 0 shot bị 2 agent claim.
5. **0 self-review** (reviewer = holder → 403).
6. **0 lần ráp** khi `script_hash` lệch agreement.
7. 90% shot pass review vòng 1; shot fail 2 lần → held đúng.
8. Owner chạm ≤ **3 lần/ngày** (brief, script objection, publish).
9. Job 66 shot: 2 agent chạy song song, tổng thời gian ≤ 60% so với 1 agent.

---

## 12. Ba điểm cần Owner chốt trước khi code

**Q1 — Cổng duyệt kịch bản:**
- **A (đề xuất):** auto-proceed sau 30' objection window, chỉ hard-gate khi brief nhạy cảm.
- B: owner duyệt cứng mọi script.
- C: không gate, chỉ critic + 2 chữ ký.

**Q2 — Cách chia shot:**
- **A (đề xuất):** 2 dải liền mạch, cân theo ký tự narration.
- B: xen kẽ từng shot (chia đều số shot).
- C: chia theo batch 10 shot, ai xong batch nào nhận batch tiếp.

**Q3 — Kịch bản sửa giữa lúc gen:**
- **A (đề xuất):** đóng băng; sửa = agreement mới + job mới.
- B: invalidate từ shot bị sửa trở đi (partial regen, cần diff tool).

---

## 13. Việc KHÔNG làm ở luồng này

- Không auto-publish: Owner vẫn bấm publish.
- Không cho agent tự chấm bài mình, kể cả khi agent kia chết.
- Không tạo job thứ 2 cho cùng agreement (kể cả retry).
- Không parse kịch bản từ chat; chat chỉ là biên bản thảo luận.
- Không partial-regen ở MVP (đợi diff tool).
- Không thêm broker/queue ngoài; build job chạy bằng scheduler thread hiện có.
