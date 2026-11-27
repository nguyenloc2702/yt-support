# REVIEW C — SHOT BOARD v1.0

**Người review:** WorkBuddy (Reviewer C) · **Ngày:** 2026-11-27
**Đối tượng:** `shot-board-spec-v1.0-2026-11-27.md`
**Tính chất:** review thứ 3 sau Reviewer A (thực tế) và Reviewer B (tính làm được). Khác A/B ở chỗ: bản này **chốt luôn quyết định**, không mở thêm câu hỏi, trừ 2 điểm đánh dấu `[OWNER]`.
**Cách đọc:** mỗi mục là 1 finding đánh số `C-n`, có *Nguồn* (mục spec) → *Vấn đề* → *Đề xuất* → *Quyết định*. Các mục `C-n` là đơn vị để nhét vào changelog v1.1.

> **Đánh giá tổng:** spec này ở mức tốt hơn hẳn mặt bằng — tách được "đã có / đã chốt / đề xuất", có mã lỗi, có test nghiệm thu độc lập, và đã tự sửa đúng nhiều chỗ Reviewer A/B bắt. Các finding dưới đây **không phải lỗi cẩu thả**, mà là 3 nhóm: (1) số học của thang điểm không khớp với bảng trừ điểm, (2) tầng điều phối (dispatch) mâu thuẫn với tầng trạng thái, (3) thiếu mô hình năng lực và thiếu tính bất biến của bản đã xuất.

---

## C-1. Điểm phải do server tính từ bảng 4.2 — hiện tại số học đang tự mâu thuẫn

**Nguồn:** §4.1 (thang 10, pass > 9.5), §4.2 (bảng trừ điểm), §4.3 bước 3 (reviewer tự nhập điểm 0.0–10.0)

**Vấn đề:** §4.2 đã ghi rõ mức trừ cho từng lỗi, nhưng §4.3 lại để reviewer **tự nhập điểm**. Hai thứ này không khớp nhau, và khớp kiểu gì cũng sai:

| Trường hợp | Theo bảng 4.2 | Theo ngưỡng 9.5 | Kết quả |
|---|---|---|---|
| Mặt người phản chiếu trong kính (−0.4, **mức FAIL**) | 10 − 0.4 = **9.6** | 9.6 > 9.5 | **PASS** — trái với "mức FAIL" |
| Camera sai hướng (**FAIL 9.0**) | 10 − ? = 9.0 | 9.0 ≤ 9.5 | FAIL (đúng, nhưng không suy ra được từ bảng) |
| 2 lỗi nhẹ (−0.2 × 2) | 9.6 | pass | đúng |
| 3 lỗi nhẹ (−0.2 × 3) | 9.4 | fail | đúng |

Nghĩa là: **lỗi nặng nhất trong bảng (mặt phản chiếu, 177/218 từng dual-FAIL) nếu cộng đúng số học lại ra PASS.** Reviewer buộc phải tự nhớ "−0.4 là mức FAIL nên phải trừ thêm cho xuống dưới 9.5" — đó là lý do 177/218 thực tế bị chấm 9.2 chứ không phải 9.6. Bảng trừ điểm đang là **trang trí**, không phải luật.

Hệ quả thứ hai: điểm tự nhập + ngưỡng 9.5 + bước 0.1 nghĩa là **một nấc 0.1 quyết định pass/fail**. Không có cách nào phân biệt "9.6 vì thật sự tốt" với "9.6 vì muốn xong". §12 #6 chỉ cảnh báo khi lý do "giống hệt nhau", không bắt được lạm phát điểm có lý do khác nhau.

**Đề xuất:** reviewer **không nhập điểm**. Reviewer tick `defect_codes[]` từ bảng 4.2; server tính:

```
score  = clamp(10 − Σ deduction(code), 0, 10)
verdict = fail  nếu (có code fail_level = true)
                 hoặc pairing_ok = false
                 hoặc score ≤ 9.5
        = pass  còn lại
```

Bảng 4.2 phải thành **enum có cấu trúc**, không phải bảng markdown:

```json
{"code": "FACE_REFLECTION", "deduction": 0.4, "fail_level": true,  "label": "Mặt/người phản chiếu trong kính/visor"}
{"code": "SUBJECT_OUT_OF_FRAME", "deduction": 0.4, "fail_level": true, "label": "Subject chính ra khỏi khung khi drift"}
{"code": "CAMERA_WRONG_DIRECTION", "deduction": 1.0, "fail_level": true, "label": "Camera sai hướng"}
{"code": "INVENTED_ACTION", "deduction": 10, "fail_level": true, "label": "Hành động bịa, trái narration"}
{"code": "DUPLICATE_OBJECT", "deduction": 0.2, "fail_level": false, "range": [0.2, 0.4]}
{"code": "HELMET_BALL", "deduction": 0.2, "fail_level": false}
{"code": "COSTUME_MORPH", "deduction": 0.2, "fail_level": false}
{"code": "PANEL_COUNT_MORPH", "deduction": 0.2, "fail_level": false}
{"code": "TEXT_EMERGES", "deduction": 0.2, "fail_level": false}
{"code": "DISCLOSE_ONLY", "deduction": 0, "fail_level": false}
```

Lợi ích không chỉ là chống lạm phát điểm:
1. Ngưỡng 9.5 trở nên **có nghĩa**: 10 − 0.2 × 2 = 9.6 pass, 10 − 0.2 × 3 = 9.4 fail → 9.5 chính xác là "tối đa 2 lỗi nhẹ, không có lỗi nặng". Luật này giải thích được, không phải con số áp đặt.
2. Bất đồng giữa 2 reviewer trở thành câu hỏi **kiểm chứng được**: "cậu có thấy quả cầu thứ hai ở mép phải không" — thay vì "tôi 9.4, cậu 9.6, thôi để manager". §4.1 mục "Không chấm cho vừa" hiện dựa vào thiện chí; có defect code thì nó thành đối chất bằng bằng chứng.
3. `reasons[]` thành dữ liệu đếm được → mở đường cho C-12 (thống kê lỗi).
4. Sửa 1 chỗ trong bảng là toàn hệ đổi theo, không phải sửa MASTER-GUIDE + prompt + UI.

**Quyết định:** ✅ ÁP DỤNG. `score` và `verdict` là **server-computed, read-only**. Review body bỏ field `score`, thêm `defect_codes[]` (bắt buộc ≥ 1 khi fail, được rỗng khi pass) + `disclose_notes[]` cho các mục "không trừ, chỉ ghi chú". Giữ `note` tự do cho ngữ cảnh. Giữ `pairing_ok` là fail cứng.

---

## C-2. Vòng đỏ → brief → gen lại: cần cơ chế máy móc, không chỉ là quy trình

**Nguồn:** §3.1 (`red` = "cần hành động ngay"), §4.3 bước 5 (fail → tự tạo việc trong inbox người gen), §5.4 ("fail lần 2 cùng lỗi → đổi staging", thực thi ghi là *"Quy trình (server không đếm lần gen)"*), §11 (claim shot đỏ để re-gen)

**Vấn đề:** Ý định đã đúng và đã có trong spec — đỏ là lệnh gen lại, người gen nhận việc, được claim shot đỏ. Thiếu 3 mắt xích để nó **chạy được mà không cần người nhắc**:

1. **"Vì sao đỏ" đang bị chôn.** Nó nằm trong `reasons[]` + `note` của review, tức là phải mở chi tiết review mới thấy. Agent re-gen cần nó **ngay trên thẻ shot** và **ngay trong response claim** — nếu không, agent sẽ gen lại bằng đúng prompt cũ và fail y lỗi cũ (đây chính là vòng lặp tốn kém nhất trong sản xuất thật).
2. **Không ai bắt version mới phải trả lời brief cũ.** Upload v2 không cần khai "tôi sửa gì". Nên không đo được "brief có được sửa đúng không", và không phát hiện được "v3 vẫn lỗi đó".
3. **Luật "fail lần 2 cùng lỗi → đổi staging" đang là quy trình miệng.** §5.4 tự ghi *"server không đếm lần gen"* — nghĩa là luật quan trọng nhất để thoát khỏi vòng lặp fail **không có gì thực thi**. Với 385 shot thì đây là chỗ đốt tiền nhiều nhất.

**Đề xuất:** biến review fail thành **regen brief** có cấu trúc, và ràng buộc version mới vào nó:

```json
// review khi fail
{
  "verdict": "fail",
  "defect_codes": ["CAMERA_WRONG_DIRECTION"],
  "regen_brief": {
    "what_wrong": "pull-out bị render thành push-in, subject to dần ra",
    "do_instead": "camera lùi ra, subject nhỏ dần; ghi rõ 'camera pulls back slowly' ở đầu prompt",
    "staging_change": "camera_move: PULL_OUT → STATIC"
  },
  "pairing_ok": true
}
```

```json
// upload version mới trên shot đang đỏ
{
  "video": "<file>",
  "addresses_review_id": "rv_8f21",     // bắt buộc khi shot đang red
  "staging_changed": true,
  "change_note": "đổi sang STATIC theo brief"
}
```

Server kiểm:
- `addresses_review_id` không phải fail review còn hiệu lực trên version trước → `422 stale_brief`.
- Shot đang `red` mà upload **không** kèm `addresses_review_id` → `422 brief_required`.
- `staging_changed=false` + đây là lần upload thứ ≥ 3 trên cùng shot + `defect_codes` của fail review trước trùng → `409 must_change_staging`. **Đây là chỗ thực thi hoá luật §5.4.**
- Review version mới tick lại đúng `defect_code` đã có trong fail review trước → server tự set `recurring_defect: true` và đẩy việc "chờ manager xác nhận blocking" vào inbox manager (§4.1 đã có sẵn cơ chế confirm-blocking, chỉ cần nối vào).

**Quyết định:** ✅ ÁP DỤNG. `regen_brief` là **bắt buộc khi `verdict=fail`** và bắt buộc có `staging_change` khác `none` khi đây là fail lần ≥ 2 trên cùng `defect_code`. Hiển thị: 1 dòng trên thẻ shot (`9.0 · camera sai hướng — đổi sang STATIC`), full trong claim response, full trong `shots/next.reason`, full trong màn chấm của version mới (để reviewer biết cần kiểm gì).

---

## C-3. Tầng dispatch đang mâu thuẫn với tầng trạng thái: 3 chỗ bắt agent đi chấm shot đỏ

**Nguồn:** §8.1 `GET /shots/next` (ưu tiên 1 = `red_unreviewed`), §9.3 (`🔴 Shot đỏ cần chấm` → `red_shots[]`), §10 bước 5 (429 → *"chuyển sang CHẤM shot đỏ (không tốn quota)"*)

**Vấn đề:** đây là mâu thuẫn nội tại, không phải thiếu sót. Tầng trạng thái nói đúng (đỏ = gen lại), nhưng tầng dispatch lại bảo agent **đi chấm shot đỏ**. Với luật "2 pass + **0 fail**":

- Chấm pass một shot đỏ → vẫn đỏ (còn fail cũ). Không đổi gì.
- Chấm fail một shot đỏ → vẫn đỏ. Không đổi gì.

Cả hai chiều đều là **no-op**. Vậy `red_unreviewed` là ưu tiên #1 của sweep, `red_shots[]` là 1 trong 7 nhóm việc của inbox, và là **phương án dự phòng khi hết quota** — tức là spec đang thiết kế 3 đường dẫn agent vào một việc không thể tạo ra kết quả. Với 184 shot đỏ tiềm năng và ~770 lượt review, đây là lượng token lớn nhất bị đốt mà không sinh giá trị.

**Đề xuất:**
- Bỏ `red_unreviewed` khỏi `shots/next`. Thay bằng `red_regen` — chỉ trả về shot đỏ **do chính caller gen** (hoặc caller đang giữ claim), kèm `regen_brief`.
- `shots/next` thứ tự mới: (1) `red_regen` (của tôi) → (2) `empty_ready` → (3) `waiting_review` (shot **cam** tôi chưa chấm — đây mới là việc chấm có thể làm shot xanh) → (4) `missing_info`.
- Inbox: đổi nhóm `🔴 Shot đỏ cần chấm` → **`🔴 Shot đỏ cần gen lại`** = `red_regen_shots[]` (của tôi) + `red_unclaimed[]` (đỏ không ai giữ, ai cũng claim được) — tách 2 field, vì "của tôi" là nghĩa vụ còn "không ai giữ" là cơ hội.
- §10 bước 5 (hết quota): đổi thành **"chấm shot cam (`generated`) chưa ai chấm"** — đó là việc duy nhất vừa không tốn quota vừa có thể đổi trạng thái. Nếu hết shot cam chưa chấm thì nghỉ, đừng chấm đỏ.

**Quyết định:** ✅ ÁP DỤNG. Đây là finding tôi đánh giá nghiêm trọng nhất về mặt vận hành, vì nó không gây lỗi hiển thị mà gây **lãng phí âm thầm** — board vẫn chạy, agent vẫn bận, nhưng không tiến.

---

## C-4. Chấm mù bị rò ngay ở tầng API

**Nguồn:** §4.1 (chấm mù, "Server + UI"), §8.1 (`GET /shots/{index}` trả `reviews[]` đầy đủ), §3.2 (lưới hiện "10.0 · 9.6")

**Vấn đề:** "mù" hiện chỉ đúng với người bấm UI. Đối tượng chính của board là **agent gọi API** — mà `GET /shots/{index}` trả full `reviews[]` với điểm, và thẻ trên lưới hiện luôn điểm của 2 người trước. Một agent đọc detail trước khi chấm là thấy hết. Chấm mù thành hình thức.

**Đề xuất:** server redact theo caller:
- `reviews[].score` và `defect_codes` trả `null` nếu caller chưa submit review còn hiệu lực trên `video_hash` hiện tại của shot đó. Trả full sau khi submit (đúng cơ chế reveal §4.1).
- `scores_summary` cũng redact cùng điều kiện — kể cả trên lưới. Chỉ manager/owner thấy điểm mọi shot.
- Thêm query `?reveal=1` chỉ có tác dụng với manager/owner.

**Quyết định:** ✅ ÁP DỤNG. Không có cái này thì C-1 (điểm tính từ bảng) vẫn bị nhiễu bởi neo điểm.

---

## C-5. Cấm tự chấm bị lách qua "upload hộ"

**Nguồn:** §5.2 + §11 (manager/writer upload được khi không có claim active), §4.1 (`reviewer != generated_by`), §12 #2

**Vấn đề:** `generated_by` = người **upload**, không phải người **gen**. Kịch bản: A gen → B upload hộ (được phép) → `generated_by = B` → **A chấm shot do chính A gen**, server thấy `reviewer(A) != generated_by(B)` nên cho qua. `generator_held_score` là tự khai nên không cứu được.

**Đề xuất:** thêm `declared_generator` (bắt buộc khi upload, mặc định = caller), tách khỏi `uploaded_by`. Chặn review theo **cả hai**: `reviewer ∉ {uploaded_by, declared_generator}`. Upload hộ vẫn được, nhưng phải khai đúng người gen — và đây cũng là dữ liệu để tính đúng "ai gen bao nhiêu shot" ở §9.4.

**Quyết định:** ✅ ÁP DỤNG.

---

## C-6. Với job thí điểm ISS, cờ xanh gần như không gác gì

**Nguồn:** §3.2 (chỉ shot **đỏ** chặn assemble), §13.1 test #1 (dữ liệu cũ: 0 xanh, 0 đỏ)

**Vấn đề:** 201 shot cũ vào board ở trạng thái `generated`, và `generated` **không chặn** assemble. Nên với đúng cái job đang chạy (ISS 385 shot), board không thay đổi gì về cổng chất lượng: assemble vẫn được như trước, chỉ trừ khi có shot đỏ — mà đỏ chỉ sinh ra từ review, mà review 201 shot đó cần 402 lượt. Đây là câu hỏi chưa được đặt ra trong §15.

**Đề xuất (và quyết định):** không bắt review toàn bộ 201 shot — chi phí không tương xứng, và phần lớn shot cũ đã qua mắt người khi sản xuất. Thay vào đó:
1. Đánh dấu `grandfathered: true` cho video có trước ngày bật board. `grandfathered` **không chặn assemble**, nhưng **không được tính là xanh** — header hiện riêng "Xanh n · Cũ (chưa chấm) n · Đỏ n".
2. Chạy **chiến dịch review có mục tiêu**: chỉ review tier A (rủi ro cao — xem C-7) của 201 shot cũ. Ước tính ~60–80 shot thay vì 201, tức ~150 review thay vì 402.
3. Từ ngày bật board: **mọi video mới đều phải qua 2 review mới assemble được** — nếu không thì bỏ luôn cổng xanh, đừng giữ hình thức.

**Quyết định:** ✅ ÁP DỤNG phương án trên. `grandfathered` là field mới trên shot record, set 1 lần lúc migration, có trong history.

---

## C-7. Thiếu mô hình năng lực: 1155 lượt việc, và luật "không tự chấm" làm mất toàn bộ dự phòng

**Nguồn:** §4.1 (2 review từ 2 agent khác nhau, khác người gen, mỗi người 1 lần/version), §9.4 (bảng agent), toàn spec: không có mục nào nói cần bao nhiêu agent

**Vấn đề:** 385 shot × (1 gen + 2 review) ≈ **1155 lượt việc**. Với N agent, mỗi shot cần `min(2, N−1)` người chấm khác người gen. Suy ra:

| Số agent | Người đủ điều kiện chấm/shot | Dư phòng | Kết cục |
|---|---|---|---|
| 2 | 1 | 0 | **Không bao giờ xanh được** (cần 2, chỉ có 1) |
| 3 | 2 | 0 | Xanh được, nhưng **1 agent nghỉ 20 phút là board đứng** ở "chờ chấm" |
| 4 | 3 | 1 | Chạy được |
| 5 | 4 | 2 | Êm |

Spec không nói con số này ở đâu cả. Với 3 agent (kịch bản dễ xảy ra nhất: manager + 2 worker), hệ thống **đúng biên** — không có sai số.

**Đề xuất:** 2 việc, làm cùng nhau:
1. **Điều kiện tiên quyết ghi vào §13:** bật board chỉ khi có **≥ 4 agent active** (1 manager + 3 worker). Nếu < 4, hạ luật xanh xuống 1 pass cho tier C (dưới đây).
2. **Review phân tầng theo rủi ro** — server tự phân loại từ dữ liệu đã có (`visual_prompt`, `camera_move` suy ra từ `index mod 5`, duration, có on-screen text):

| Tier | Điều kiện | Review cần | Ước tính |
|---|---|---|---|
| A | có mặt người/kính phản chiếu, `camera_move ≠ STATIC`, hoặc prompt có chữ/số | 2 | ~35% |
| B | STATIC, không mặt người, có chi tiết máy móc | 1 | ~45% |
| C | STATIC, vật thể đơn, không chữ, < 3s | 0 + audit ngẫu nhiên 20% | ~20% |

Tải review giảm khoảng 45–55%, và giảm đúng vào chỗ ít rủi ro — vì theo chính bảng 4.2, lỗi nặng (mặt phản chiếu, sai hướng camera, subject ra khung) đều tập trung ở tier A.

**Quyết định:** ✅ ÁP DỤNG cả hai. Tier tính ở server, trả trong `shot.tier`, hiện badge nhỏ trên thẻ. Tier là **read-only**, không cho sửa tay (nếu cho sửa, agent sẽ hạ tier để đỡ bị chấm).

---

## C-8. Owner là nút thắt cứng: takeover + claim treo vĩnh viễn

**Nguồn:** §5.1 (hết hạn → `suspect`, **không auto-nhả, không ai claim đè**), §11 (takeover **owner-only**), §12 #3 (tối đa 5 claim/agent)

**Vấn đề:** agent chết giữa lúc gen (rất thường: hết quota, mất mạng, phiên bị cắt) → claim `suspect` → shot kẹt `claimed` **vĩnh viễn** cho tới khi owner takeover. Với 385 shot, nhiều agent, TTL 1200s, đây sẽ là hàng đợi việc thường xuyên nhất — và là loại việc chỉ một người làm được, bằng tay, qua Telegram. Cộng thêm trần 5 claim/agent: 1 agent chết có thể khoá 5 shot.

**Đề xuất:**
1. **Manager được takeover** (không cần owner). Owner chỉ giữ: override, rút review xanh, `allow_red_shots`, thêm/bớt agent. Đây là phân quyền đúng bản chất — takeover là việc vận hành, không phải việc quyết định.
2. **`suspect` quá `VIDEO_SUSPECT_GRACE_SEC` (mặc định 7200s = 2h) → tự về `empty`** kèm event `claim_expired_released`, **chỉ khi shot chưa có video**. Lý do an toàn: chưa có video thì không mất gì ngoài công gen dở; giữ claim vĩnh viễn thì mất cả shot. Đây là ngoại lệ có chủ ý với luật "không auto-steal" — luật đó đúng cho **shot đã có video** (không được để người khác gen đè), sai cho shot trống.
3. Nút **"Giải phóng toàn bộ claim suspect"** cho owner/manager (1 click, có log).
4. Header job hiện thêm **"Claim nghi ngờ n"** — hiện tại con số này bị trộn vào "Đang gen n" nên không ai thấy nó tăng.

**Quyết định:** ✅ ÁP DỤNG cả 4. Ghi rõ vào §5.1 là **sửa luật đã chốt 2026-11-23**, kèm lý do — không để nó thành "trôi" ngoài changelog.

---

## C-9. Duration: cắt ở server thay vì từ chối, và chốt lại hash

**Nguồn:** §12 #5 (> ±10% → 422 `duration_mismatch`), §4.1 (`reviewed_video_hash` khớp hash hiện tại), §7 (audio khoá cứng)

**Vấn đề:** shot 1.861s thì ±10% = ±0.186s ≈ **±4.5 frame ở 24fps**. Agent trim sai nửa frame là mất cả lần upload (upload 385 clip, mỗi clip vài chục MB). Trong khi server đã biết `expected_duration_sec`, biết 24fps, và đã có ffmpeg ở bước assemble — việc cắt đuôi cho đúng số frame là **deterministic**, không cần agent làm.

Vấn đề thứ hai, quan trọng hơn: nếu server có xử lý file (trim, re-encode, chuẩn hoá) thì `video_hash` là hash của **file upload** hay **artifact lưu trữ**? Spec không nói. Nếu là file upload, review bind vào một hash mà bản thật không còn khớp → mọi review thành stale ngay sau khi lưu.

**Đề xuất:**
- Server **tự trim** về đúng `round(expected_duration_sec × 24)` frame khi nhận file. Trả `trimmed: true`, `frames_expected`, `frames_received`, `delta_frames`. Chỉ 422 khi `|delta| > 10%` (giữ nguyên ngưỡng) — tức là chỉ chặn khi agent gen sai hẳn độ dài, không chặn vì lệch frame.
- **`video_hash` = SHA-256 của artifact sau xử lý**, ghi rõ trong §2.1. Hash file upload (nếu cần chống trùng) là field riêng `source_hash`.
- Thêm **sổ độ dài ở mức job**: header hiện `Σ expected vs Σ actual`, chặn `confirm-assemble` khi lệch luỹ kế > 0.5s. Hiện chỉ có lệch từng shot, không có luỹ kế — mà 385 shot × +1% là lệch 4s so với audio khoá cứng, đủ để trôi tiếng.

**Quyết định:** ✅ ÁP DỤNG. Đây là thay đổi rẻ nhất trong toàn bộ danh sách và xoá được cả một lớp lỗi.

---

## C-10. Assemble phải tạo build bất biến

**Nguồn:** §3.2 (`confirm-assemble`), §4.1 (review muộn lật xanh → đỏ, không khoá review khi đã xanh)

**Vấn đề:** `confirm-assemble` đọc trạng thái **sống**. Assemble lúc 10h và 11h có thể ra 2 kết quả khác nhau, vì một review muộn có thể lật xanh → đỏ ở giữa. Nghĩa là không tồn tại khái niệm "bản đã giao đạt chuẩn" — mà đó chính là lý do tồn tại của cả board.

**Đề xuất:** mỗi lần assemble sinh 1 **build record bất biến**: `build_id`, `created_at`, manifest `[{shot_index, video_hash, video_version}]`, `assembled_by`, `red_overrides[]`. Review muộn sau đó:
- Nếu `video_hash` trong manifest vẫn là bản hiện tại → cập nhật trạng thái shot bình thường (đỏ/xanh), **không đổi build**.
- Nếu khác → ghi `regression` trên shot, và build cũ được đánh dấu `superseded_by: <build_id mới>` khi assemble lại.

`confirm-assemble` kiểm tra **trên manifest** (đã chốt), không phải trên trạng thái sống. Kết quả: cùng input → cùng output, và luôn trả lời được "bản đang chạy trên kênh là build nào, gồm hash nào, ai duyệt".

**Quyết định:** ✅ ÁP DỤNG. P1 (không đợi P3) — vì job ISS sắp assemble.

---

## C-11. `shots/next` nên claim luôn, và ưu tiên theo vùng

**Nguồn:** §8.1 (`GET /shots/next` → agent tự gọi claim)

**Vấn đề:** GET rồi claim riêng = 5 agent cùng nhận 1 shot, 4 thua `409 shot_claimed`, quay lại gọi next. Với hook 60s và poll 10–15s, đây là vòng lặp lãng phí liên tục. Thứ hai: ưu tiên thuần theo trạng thái nghĩa là agent đang làm chương 3 bị ném sang chương 9, mất toàn bộ context palette/style đang giữ.

**Đề xuất:**
- `POST /shots/next {claim: true, near_index?: 190}` → trả shot **đã claim**, kèm `claim.id` + `claim.lease_until`. Hết việc → `{shot: null, reason: "no_work"}` như cũ (200).
- Điểm ưu tiên = `base_priority + locality_bonus`, trong đó `locality_bonus` cộng cho shot trong ±10 index so với shot cuối caller claim/chấm. Trả `priority_breakdown` để agent/người đọc hiểu vì sao.
- Giữ `GET /shots/next` (read-only) cho UI "Shot tiếp theo cần tôi" — hai endpoint, cùng một hàm tính.

**Quyết định:** ✅ ÁP DỤNG. `GET` cũ giữ nguyên để không phá client đang có.

---

## C-12. Thống kê lỗi theo loại shot — chỗ board "học"

**Nguồn:** §9.4 (bảng agent: số gen/xanh/đỏ/review), toàn spec: không có thống kê theo **loại lỗi**

**Vấn đề:** board đang đối xử 385 shot như 385 sự kiện độc lập. Nhưng chúng tương quan rất cao: cùng chapter → cùng palette; `camera_move` xoay vòng `index mod 5`; cùng subject. Nếu pull-out fail 3 lần ở chương 3 thì 40 shot pull-out còn lại của chương đó cũng sắp fail. Spec có đủ dữ liệu để biết điều này (`defect_codes`, `camera_move`, `chapter`) nhưng không có mặt phẳng nào tổng hợp.

**Đề xuất:** `GET /api/v1/todos/{id}/board/defect-stats`:
```
{ by_camera_move: [{move:"PULL_OUT", shots:28, fails:11, fail_rate:0.39, top_codes:[...]}],
  by_chapter: [...], by_tier: [...], by_code: [{code:"CAMERA_WRONG_DIRECTION", count:9}] }
```
Ba cách dùng, tất cả đều rẻ:
1. **Header board** hiện 1 dòng cảnh báo: "PULL_OUT: 39% fail (11/28) — cân nhắc đổi rotation".
2. **Thẻ shot** hiện gợi ý tiền nghiệm: shot 201 (PULL_OUT, chương 3) → "họ này fail 39%, ghi rõ 'camera pulls back' ở đầu prompt".
3. **Đổ ngược vào MASTER-GUIDE**: đây là vòng phản hồi mà spec đang thiếu hoàn toàn — board ghi sổ rất kỹ nhưng không nói lại cho hệ prompt biết cái gì đang hỏng.

**Quyết định:** ✅ ÁP DỤNG ở **P2** (không chặn P1). Endpoint read-only, không đụng state.

---

## C-13. Idempotency-Key cho mọi mutation

**Nguồn:** §8.0 (envelope lỗi), §12 #1 (`already_reviewed`), §17.6 (`duplicate_body`)

**Vấn đề:** agent là LLM client — timeout rồi retry là **mặc định**, không phải ngoại lệ. Retry `POST /reviews` hiện nhận `409 already_reviewed`, không phân biệt được với "người khác đã chấm". Retry `POST /videos` có thể tạo 2 video version. Spec có chống trùng nội dung (`duplicate_video`, `duplicate_body`) nhưng không có chống trùng **ý định**.

**Đề xuất:** header `Idempotency-Key` (client sinh UUID) trên mọi POST/PATCH mới. Server lưu `(key, endpoint, caller)` → response gốc trong 24h; replay trả **nguyên response cũ kèm 200** (không phải 409), thêm `idempotent_replay: true`. Bắt buộc với: reviews, videos, claim, comments, block/unblock/override.

**Quyết định:** ✅ ÁP DỤNG. Đây là loại lỗi sẽ làm board "trông như có bug" mà thực ra là retry — rất khó truy khi đang chạy.

---

## C-14. Endpoint preview để review không phải tải MP4

**Nguồn:** §4.3 bước 2 ("first/mid/last frame tách sẵn" — nhưng không có endpoint nào trả chúng)

**Vấn đề:** 385 shot × 2 review × 1 file MP4 = ~770 lần tải video. Reviewer (người hoặc agent) chỉ cần 3 frame + 1 bản xem nhanh để quyết. UI đang nói "tách sẵn" nhưng API không có.

**Đề xuất:**
- `GET /shots/{index}/preview.jpg` → 3 frame (first/mid/last) ghép ngang, có nhãn frame + số frame. Cache theo `video_hash`.
- `GET /shots/{index}/preview.mp4` → 0.5× scale, CRF cao, không audio, ~200KB. Cache theo hash.
- Màn chấm mặc định load preview; nút "Tải bản gốc" giữ nguyên cho khi cần soi chi tiết.

**Quyết định:** ✅ ÁP DỤNG ở P1 — nó là điều kiện để C-7 (review phân tầng) có lãi thật.

---

## C-15. Vặt — sửa luôn, không cần bàn

| # | Vấn đề | Sửa |
|---|---|---|
| a | `video_version` về 0 khi xoá video (changelog #20) → "v1" xuất hiện 2 lần với 2 hash khác nhau, history đọc không hiểu | Monotonic, không reset. Thêm `video_attempt` nếu cần đếm riêng |
| b | Claim trên shot `blocked` không được định nghĩa ở đâu (`shot_not_empty` chỉ liệt kê generated/green/claimed) | Thêm `409 invalid_state` cho claim trên `blocked` |
| c | Header không hiện `blocked` — shot kẹt staging biến mất khỏi tầm nhìn | Thêm "Kẹt staging n" vào header |
| d | 1 env flag cho 4 pha → rollback all-or-nothing | Tách `BOARD_UI`, `CLAIM_ENFORCE`, `ASSEMBLE_GATE` |
| e | `max_rounds_reached` là dead code (round luôn = 1) | Xoá khỏi bảng mã lỗi |
| f | Tên `blocked` nghe nặng hơn `red` nhưng không chặn assemble | Giữ tên trong DB, đổi nhãn UI thành "⛔ Kẹt staging" |
| g | `scores_summary.count` đếm review còn hiệu lực → không biết "thực ra có bao nhiêu người đã nhìn" | Thêm `distinct_reviewers` (đếm cả review đã stale/retract) |

---

## Chốt các câu hỏi mở §15

| # | Câu hỏi | Quyết định | Lý do |
|---|---|---|---|
| Q1 | Ngưỡng xanh: 2 pass + 0 fail, hay 2 pass bất kể fail? | **Giữ 2 pass + 0 fail** | Đỏ = lệnh gen lại ngay (theo đúng lời owner). Không thể vừa "cần gen lại" vừa "đạt chuẩn". Bỏ điều kiện 0 fail sẽ làm cờ đỏ thành trang trí. Ngưỡng 9.5 giữ nguyên, nhưng định nghĩa lại theo C-1: "≤ 2 lỗi nhẹ, 0 lỗi nặng" |
| Q2 | Shot đỏ chặn assemble? | **Giữ mặc định**, cho `allow_red_shots: true` + reason | Đúng: đỏ do lỗi nhỏ không đáng chặn cả job. Nhưng phải ghi vào **build record** (C-10), không chỉ vào log |
| Q3 | `blocked` có tính là đỏ? | **Không, giữ riêng** | Đỏ = "gen rồi nhưng dở"; blocked = "chưa gen được". Trộn vào nhau sẽ làm mất khả năng phân biệt lỗi sản xuất với lỗi staging. Nhưng phải hiện trên header (C-15c) |
| Q4 | Key cho `muse-manager` + worker | **[OWNER]** vẫn phải cấp trước P1. Trong lúc chờ: `config/agent_keys.yaml` tĩnh (key → tên, role), 10 dòng, đủ để không chặn P1 | §5.3 đã có fallback này, chỉ cần nói rõ nó là **đường chính thức của P0**, không phải tạm bợ |
| Q5 | `?light=1` có auth riêng? | **Dùng Bearer key như thường** | Không có lý do tạo đường auth thứ hai cho một endpoint chỉ trả counts. Đường auth thứ hai = lỗ hổng thứ hai |

---

## Ba điểm tôi đã cân nhắc và quyết định **giữ nguyên**

Ghi lại để không ai sửa lại lần nữa:

1. **Bỏ vòng 2 critic** (§4.1) — đúng. Thêm vòng 2 chỉ nhân đôi chi phí để giải quyết một bất đồng mà `defect_codes` (C-1) giải quyết rẻ hơn: hai người nhìn cùng frame, hoặc thấy lỗi hoặc không.
2. **`@tag` / `mentions[]` để sau** — đúng. Nó phụ thuộc registry, mà registry chưa có. Đưa vào P0 chỉ tạo nợ.
3. **Không có `in_review` session** (§4.3 bước 1) — đúng. Bỏ session là bỏ được một máy trạng thái thứ hai. Việc "quên đóng form" không gây hại vì review chỉ ghi lúc submit.

---

## Thứ tự thực hiện đề xuất

| Bước | Nội dung | Vì sao trước |
|---|---|---|
| 0 | Chốt Q1–Q5 + C-1 (schema review) | C-1 đổi shape của `reviews` — làm sau khi đã code API là tốn gấp đôi |
| 1 | C-2, C-3, C-5, C-13 (vòng đỏ + dispatch + chống lách + idempotency) | Đây là phần **điều phối**, sai ở đây thì agent chạy sai hàng loạt |
| 2 | C-9, C-14, C-11 (trim, preview, next+claim) | Hạ tầng upload/review — làm trước khi agent chạy thật |
| 3 | C-6, C-7, C-4 (grandfather, tier, blind) | Cần cho đúng job ISS |
| 4 | C-10 (build snapshot) | Trước lần assemble thật đầu tiên |
| 5 | C-12 (defect stats) | Sau khi có ≥ 50 review thật, nếu không thì số liệu rỗng |

---

## Ghi chú về cách review này ra đời

Tôi đã đọc toàn bộ 801 dòng, đối chiếu với 3 tài liệu được dẫn (`agent_comms_decision.md` chưa đọc trực tiếp — các kết luận về nó dựa trên phần trích trong spec và changelog §16). Các con số ước lượng ở C-6/C-7 (60–80 shot tier A, giảm 45–55% tải review) là **suy luận từ phân bố trong bảng 4.2 và rotation `index mod 5`**, chưa đếm trên dữ liệu thật của job ISS — cần kiểm lại bằng 1 query trước khi dùng để lập kế hoạch.
