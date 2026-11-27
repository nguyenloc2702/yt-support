# Shot Board v1.0 — báo cáo triển khai

Ngày: 2026-11-28 · Người viết: WorkBuddy (Reviewer C) · Repo: `yt-support`

Tài liệu này mô tả **cái đã làm**, **cái đã kiểm**, và **cái còn thiếu** của Shot Board v1.0 — bảng điều phối 385 shot cho nhiều agent gen video, chạy trên `vidfac.gacdero.vn`.

---

## 1. Trạng thái trong một khối

| hạng mục | trạng thái |
|---|---|
| 15 finding của Review C | **7 xong** · 1 đang làm · 7 chưa nhận |
| Endpoint board mới | 7 |
| Test board (gated) | 20 — tất cả xanh |
| Test toàn repo | 396 passed, 20 skipped |
| Prod | đã deploy `d3bac84`, 4 danh tính muse đã bật |
| Chặn việc dùng thật | **C-11** (không claim shot) |

---

## 2. Bối cảnh

Spec `shot-board-spec-v1.0-2026-11-27.md` (801 dòng) mô tả board điều phối 385 shot. Spec đã qua 3 vòng review; vòng thứ ba (`plans/team/review_c_shot_board_v1.md`) ra 15 finding, mỗi finding kèm quyết định.

Nguyên tắc làm việc xuyên suốt: **nghiệm thu bằng test độc lập và bằng việc chạy thử**, không review diff từng dòng. Mọi báo cáo đi qua kênh file markdown trong `plans/team/handoff/`, không dùng kênh khác.

---

## 3. Cái gì đã xây

### Endpoint

| method | path | việc |
|---|---|---|
| `GET` | `/api/v1/todos/{id}/shots/next` | dispatch: `red_regen` → `empty_ready` → `waiting_review` → `missing_info` |
| `GET` | `/api/v1/todos/{id}/shots/{index}` | chi tiết shot, `reviews[]` redact theo caller |
| `POST` | `/api/v1/todos/{id}/shots/{index}/reviews` | chấm, server tự tính `score`/`verdict` |
| `GET` | `/api/v1/agent/inbox` | tách `red_regen_shots[]` (của tôi) và `red_unclaimed[]` |
| `GET` | `/api/v1/todos/{id}/board/summary` | header `Xanh n · Cũ (chưa chấm) n · Đỏ n` |
| `POST` | `/api/v1/board/migrate-grandfathered` | migration C-6, idempotent |
| `POST` | `/api/v1/todos/{id}/confirm-assemble` | + cổng assemble (C-6) |

### Auth

| method | path | việc |
|---|---|---|
| `POST` | `/api/v1/auth/login` | `{pin}` owner hoặc `{code}` agent → token |
| `GET` | `/api/v1/auth/whoami` | agent tự kiểm identity + scope |

---

## 4. Bảng 15 finding

| # | Nội dung | Trạng thái |
|---|---|---|
| C-1 | Server tính `score`/`verdict` từ `defect_codes` | ✅ xong, nghiệm thu |
| C-2 | Vòng đỏ → `regen_brief` → gen lại có ràng buộc | ✅ xong, nghiệm thu |
| C-3 | Dispatch bỏ `red_unreviewed`, thêm `red_regen` scoped theo caller | ✅ xong, nghiệm thu |
| C-4 | Chấm mù chặn ở tầng API (redact theo caller) | ✅ xong, nghiệm thu |
| C-5 | Chống lách "upload hộ" bằng `declared_generator` | ✅ xong, nghiệm thu |
| C-6 | `grandfathered` cho video trước ngày bật board | ✅ xong, nghiệm thu |
| C-7 | Tier review | 🔄 đang làm — thu hẹp còn "thứ tự ưu tiên" |
| C-8 | Owner là nút thắt: takeover + claim treo | ❌ chưa nhận |
| C-9 | Duration: cắt ở server thay vì từ chối | ❌ chưa nhận |
| C-10 | Assemble tạo build bất biến | ❌ chưa nhận |
| C-11 | `shots/next` phải claim luôn | ❌ **chưa nhận — đang chặn việc chạy nhiều agent** |
| C-12 | Thống kê lỗi theo loại shot | ❌ chưa nhận |
| C-13 | `Idempotency-Key` cho mọi mutation | ✅ xong, nghiệm thu |
| C-14 | Endpoint preview để review không phải tải MP4 | ❌ chưa nhận |
| C-15 | Vặt — sửa luôn | ❌ chưa nhận |

---

## 5. Chi tiết những phần đã làm

### C-1 — điểm do server tính

Reviewer chỉ gửi `defect_codes[]`. Server tra bảng §4.2 và tính:

```
score = max(0, 10 − Σ deduction(code))
verdict = fail nếu có code mức fail, hoặc score <= 9.5
```

Gửi `score` trong body → `422 client_score_rejected`. Lý do phải chặn: bảng §4.2 tự mâu thuẫn — lỗi mặt phản chiếu ghi "−0.4, mức FAIL" nhưng 10−0.4=9.6 vẫn trên ngưỡng 9.5, nên nếu để client tính thì cùng một lỗi lúc pass lúc fail tuỳ người chấm.

### C-2 — vòng đỏ

- `verdict=fail` bắt buộc có `regen_brief` (`422 regen_brief_required`), kể cả fail nhẹ. Không có ngoại lệ.
- Upload lên shot đỏ bắt buộc `addresses_review_id` (`422 brief_required`).
- `addresses_review_id` phải là fail review còn hiệu lực trên version hiện tại (`422 stale_brief`).
- Upload lần ≥3 mà `staging_changed=false` và lỗi trùng lần trước → `409 must_change_staging`.
- `recurring_defect` bật khi review mới tick lại đúng code đã fail ở version trước.

**Thứ tự validate: board fields → media → probe.** Nếu không, mỗi lần agent vi phạm lại tốn một lần upload vài chục MB. Đây không phải tối ưu vặt — nó là điều kiện để `must_change_staging` đếm đúng, vì nếu version được ghi trước khi board từ chối thì upload bị từ chối cũng bị tính là một lần vi phạm.

### C-3 — dispatch

`shots/next` không bao giờ trả shot đỏ chưa ai chấm. Thay vào đó:

- `red_regen` — shot đỏ **do chính caller gen**, kèm brief để gen lại;
- `red_unclaimed` — shot đỏ chưa có ai nhận, để manager điều phối.

Scope theo caller là điểm mấu chốt: nếu `red_regen` trả mọi shot đỏ thì nó chỉ là `red_unreviewed` đổi tên. Có test riêng khoá điều này.

### C-4 — chấm mù ở tầng API

`reviews[].score` và `defect_codes` trả `null` cho tới khi caller đã chấm version hiện tại. `scores_summary` ẩn cùng điều kiện. `manager`/`admin` thấy tất cả.

Hai thứ **cố ý không** redact:

- `verdict` — ẩn không cứu được gì (shot đỏ ai cũng thấy) mà bắt UI thêm trạng thái "không biết".
- `regen_brief` — generator cần nó để sửa shot đỏ, mà generator không phải reviewer. Redact là bẻ gãy vòng C-2.

Redact nằm trong `_format_shot(shot, caller, privileged)` chứ không ở tầng route, để endpoint thêm sau này không vô tình bỏ qua.

### C-6 — `grandfathered`

201 shot cũ vào board ở trạng thái `generated`, mà `generated` không chặn assemble ⇒ với job ISS board không gác gì. Không bắt review lại 201 shot (402 lượt).

- `grandfathered=true` → không chặn assemble, **không tính là xanh**.
- Migration dựa trên `created_at < cutoff`, idempotent, chạy lại trả `migrated: 0`.
- Upload video mới thì `grandfathered` reset về 0 — shot sửa lại không còn là "cũ".

### C-13 — idempotency

Mọi mutation đi qua `_idempotent_call`, scope theo `identity:key`. Replay trả **200** + response gốc, không trả 409.

Điểm dễ sai: `preflight` (validate board) phải chạy **sau khi claim key và sau nhánh replay**, nhưng **trước khi đọc media**. Nếu chạy trước cả idempotency thì retry một upload đã thành công sẽ bị đánh giá lại trên state mà chính nó vừa thay đổi và nhận 409 sai.

---

## 6. Mã lỗi mới

| HTTP | code | khi nào |
|---|---|---|
| 422 | `client_score_rejected` | body review còn field `score` |
| 422 | `regen_brief_required` | `verdict=fail` thiếu `regen_brief` |
| 422 | `brief_required` | upload shot đỏ thiếu `addresses_review_id` |
| 422 | `stale_brief` | `addresses_review_id` không còn hiệu lực |
| 422 | `invalid_defect_code` | code không có trong bảng §4.2 |
| 422 | `idempotency_key_reused` | cùng key, khác method/path/body |
| 409 | `must_change_staging` | upload lần ≥3, staging không đổi, lỗi trùng |
| 409 | `idempotency_key_conflict` | cùng key, khác caller |
| 409 | `idempotency_in_progress` | request cùng key đang chạy |
| 409 | `already_reviewed` | đã chấm version này rồi |
| 409 | `stale_video_hash` | `reviewed_video_hash` không khớp |
| 403 | `self_review` | người chấm là người gen hoặc người upload |
| 404 | `shot_not_found` | shot không tồn tại (không phải `todo item not found`) |
| 409 | `assemble_blocked_red` | còn shot đỏ |
| 409 | `assemble_blocked_unreviewed` | video mới chưa xanh |

---

## 7. Phân quyền

### Bốn scope

| scope | quyền |
|---|---|
| `read` | xem |
| `ingest` | upload, chấm, nhận việc |
| `manager` | như ingest + **thấy điểm của người khác** |
| `admin` | tất cả |

`manager` không phải admin thu nhỏ — nó chỉ mở đúng phần "nhìn thấy điểm", thứ worker không được thấy vì thấy trước khi chấm là mất chấm mù.

### Hai đường đăng nhập

**Key dài** (`VIDEO_API_KEYS`) cho máy nội bộ:

```bash
VIDEO_API_KEYS=owner:admin,muse-gen-1:ingest
# Bearer token CHÍNH LÀ PHẦN TÊN:
#   Authorization: Bearer muse-gen-1        -> 200
#   Authorization: Bearer muse-gen-1:ingest -> 401
```

**Mã 6 số** (`VIDEO_AGENT_CODES`) cho người:

```bash
VIDEO_AGENT_CODES=997220:owner:admin,997221:muse-boss:manager,997222:muse-gen-1:ingest
```

```bash
curl -X POST https://vidfac.gacdero.vn/api/v1/auth/login \
  -H "Content-Type: application/json" -d '{"code":"997222"}'
# -> {"identity":"muse-gen-1","scope":"ingest","token":"<exp>.<identity>.<scope>.<mac>", ...}
```

**Vì sao mã 6 số không được dùng trực tiếp làm key:** 10^6 tổ hợp, endpoint public, không rate limit ở tầng Bearer ⇒ dò sạch bảng mã trong vài phút. Mỗi mã dò được là một danh tính có quyền upload + chấm + kí review. Mã chỉ đi qua `/auth/login`, nơi có khoá 60 giây sau 5 lần sai theo IP; token sinh ra dài và ký HMAC.

**Giới hạn:** đổi mã không thu hồi token đã phát (sống 30 ngày). Muốn đá tất cả ngay thì đổi `VIDEO_SESSION_SECRET` rồi restart.

**Cảnh báo còn treo:** vì Bearer token chính là tên, "key dài" ở hệ thống này **không phải bí mật** — đoán được tên là vào được. `AGENT_API_GUIDE.md` đang công khai `sk-video-agent-prod`, tức ai đọc guide cũng có quyền của agent đó. Nên chuyển mọi agent sang đường mã 6 số.

---

## 8. Bộ test

```bash
cd video-system
VIDEO_BOARD_ACCEPTANCE=1 .venv/Scripts/python.exe -m pytest \
  tests/test_board_acceptance.py tests/test_board_tranche3.py -q     # 20 passed
.venv/Scripts/python.exe -m pytest tests/ -q                          # 396 passed, 20 skipped
```

Bộ board **gated bằng env** để CI không đỏ khi tính năng chưa có; bật gate thì chạy thật.

Nguyên tắc của bộ test:

- Chỉ gọi HTTP qua `client` — không import service nội bộ, để refactor bên trong không làm đỏ test.
- Bảng trừ điểm §4.2 được **copy độc lập** trong test, không import từ backend. Backend đổi số thì test phải đỏ — đó là điểm của nó.
- Assert lên `code` trong envelope lỗi, không assert lên `message`.

Hai cái bẫy đã vấp:

1. **Không `from tests.conftest import ...`** trong test — import đó chạy lại module và ghi đè `VIDEO_API_KEYS`, làm mất key của fixture.
2. **`TestClient` giữ cookie.** Test "token giả bị từ chối" sẽ pass giả nếu client còn cookie hợp lệ từ lần login trước. Phải `client.cookies.clear()`.

---

## 9. Deploy

Push `main` là deploy: `.github/workflows/deploy.yml` chạy ruff + pytest, rồi SSH vào VPS rsync `backend/ frontend/ config/ read_me/` sang `/opt/video-system` và `systemctl restart video-system` (port 8090).

Biến môi trường nằm ở **`/opt/video-system/.env`** (unit dùng `EnvironmentFile`). Không đưa secret vào git.

Thứ tự bật board cho job thật:

1. Cấp đủ key/mã trong `.env`, restart, kiểm `/api/v1/health`.
2. `POST /api/v1/board/migrate-grandfathered` với `board_enabled_at`. Chạy lại lần hai phải trả `migrated: 0`; nếu không thì migration không idempotent — dừng lại.
3. Kiểm `/board/summary`: header phải hiện `Xanh 0 · Cũ (chưa chấm) N · Đỏ 0`.
4. Từ đây video mới bắt buộc qua 2 review; video cũ không chặn.

**Tối thiểu 4 agent active** (1 manager + 3 worker) trước khi bật. Với 3 agent, mỗi shot chỉ có đúng 2 người đủ điều kiện chấm, dư phòng bằng 0 — một người nghỉ là board đứng.

---

## 10. Còn thiếu

| việc | vì sao quan trọng |
|---|---|
| **C-11 claim khi `shots/next`** | **Chặn việc chạy nhiều agent.** `next_shot` hiện chỉ đọc: 4 agent cùng gọi sẽ nhận cùng một shot và gen trùng. `claimed_by`/`claim_lease_until` có trong schema nhưng không chỗ nào ghi. |
| C-8 takeover + claim treo | Không có C-11 thì C-8 chưa có nghĩa |
| C-9 duration | Cắt ở server thay vì từ chối |
| C-10 build bất biến | Assemble hiện ghi đè |
| C-12 thống kê lỗi theo loại shot | Chỗ board "học" |
| C-14 endpoint preview | Review không phải tải MP4 |
| C-15 vặt | |
| C-7 tier | Đang làm, đã thu hẹp còn "thứ tự ưu tiên" — **không** hạ số review |

**C-7 đã chốt:** cờ xanh luôn cần **2 agent khác nhau** chấm đạt và kí, không ai là người gen/upload. Tier không được hạ số này, nên C-7 không còn là công cụ giảm tải — 1155 lượt review cho 385 shot là cố định. Cách duy nhất giảm là C-6 `grandfathered`.

---

## 11. Sự cố đã gặp và bài học

**Upload bị board từ chối vẫn ghi video version.** `submit_video` chạy trước `record_board_upload`, nên request 404/422 vẫn để lại file trên đĩa và tăng `uploaded_count`. Hệ quả nặng hơn vẻ ngoài: `must_change_staging` đếm cả upload bị từ chối ⇒ shot bị khoá oan sau 2 lần vi phạm. Sửa bằng `preflight_upload` chạy trước khi ghi media.

**Replay upload trả 409 thay vì 200.** Tôi đề xuất một shortcut tra key trước khi so body hash. Nó sửa được lỗi 409 nhưng mở lỗ nặng hơn: cùng key cho shot khác trả 200 + response cũ, shot mới **không được upload** mà không báo lỗi. Bài học: đừng thiết kế shortcut "tra key trước khi đọc body" cho endpoint mà body phân biệt đối tượng — nó phá luôn khả năng phát hiện key dùng lại sai chỗ. Cách đúng là hook `preflight` chạy sau khi claim key, và đọc media bên trong callback.

**Quyền suy từ tên agent.** `_is_board_privileged` so tên cứng `{owner, muse-manager}` ⇒ đổi tên là mất quyền, đặt tên trùng là được quyền. Đã thay bằng scope `manager`.

**Test xanh không chứng minh thứ tự gọi đúng.** Cả 20 test board xanh trong khi `preflight` chạy sai chỗ, vì test chỉ assert status code. Phải đọc code và dựng thêm test cho đúng cái sai đó.

**Một test của tôi sai, không phải code.** Test `recurring_defect` ban đầu dùng `DUPLICATE_OBJECT` — lỗi nhẹ nên review đó là **pass**, không tạo fail cũ để so. Phải dùng mã mức fail (`FACE_REFLECTION`). Ghi lại vì lần sau dễ vấp lại.

---

## 12. Lịch sử commit

| commit | nội dung |
|---|---|
| `a2653a3` | C-3 dispatch + C-1 điểm server tính |
| `21ff76c` | preflight upload, `shot_not_found`, C-2, C-13 |
| `b70d9bd` | `_idempotent_hit` (bị thay ở `30a381b`) |
| `30a381b` | sửa: preflight sau claim, đọc media trong callback |
| `03a931a` | C-4 redact + C-6 `grandfathered` |
| `a4c8147` | scope `manager` |
| `d3bac84` | mã 6 số → token, `/auth/whoami` |
| `104096e` | `tools/muse_agent.py` + sửa tài liệu Bearer |

Tài liệu liên quan: `plans/team/review_c_shot_board_v1.md` (15 finding), `plans/team/board_roles.md` (cấp key), `plans/team/tranche3_c4_c6_c7_design.md` (quyết định C-7), `plans/team/handoff/` (kênh bàn giao).
