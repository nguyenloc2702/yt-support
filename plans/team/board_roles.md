# Phân cấp muse — cấp key và phân quyền board

Cập nhật: 2026-11-27 · Áp dụng cho `VIDEO_API_KEYS` của video-system.

## 1. Bốn mức quyền (scope)

| scope | làm được | không làm được |
|---|---|---|
| `read` | xem item, shot, tiến độ | upload, chấm, assemble |
| `ingest` | **upload video, chấm review, nhận việc** (`shots/next`) | assemble, đổi cấu hình |
| `manager` | như `ingest` + **thấy điểm review của người khác**, `?reveal=1`, xem board summary | việc admin (migration, xoá, cấu hình) |
| `admin` | tất cả | — |

Thứ tự: `read < ingest < manager < admin`. Mỗi endpoint khai mức tối thiểu; thiếu thì 403.

**Quan trọng:** `manager` **không** phải admin thu nhỏ. Nó chỉ mở đúng phần "nhìn thấy điểm" — thứ mà worker không được thấy, vì thấy điểm trước khi chấm là mất chấm mù (C-4). Đừng cấp `admin` cho muse chỉ để nó xem được điểm.

## 2. Cấu hình

Hai đường, dùng cho hai mục đích khác nhau.

### 2a. Key dài cho máy (khuyến nghị cho agent chạy hàng loạt)

```bash
VIDEO_API_KEYS=owner:admin,muse-boss:manager,muse-gen-1:ingest,muse-gen-2:ingest,muse-gen-3:ingest
```

- Định dạng `tên:scope`, phân tách bằng dấu phẩy. **Chuỗi dùng làm Bearer token chính là `tên:scope`** — ví dụ `Authorization: Bearer muse-gen-1:ingest`.
- Phần **tên** trở thành identity, và là thứ được ghi vào `uploaded_by`, `declared_generator`, `reviewer`. Đổi tên key = đổi danh tính trong toàn bộ lịch sử board.

### 2b. Mã 6 số cho người (đăng nhập đổi token)

```bash
VIDEO_AGENT_CODES=997220:owner:admin,997221:muse-boss:manager,997222:muse-gen-1:ingest,997223:muse-gen-2:ingest,997224:muse-gen-3:ingest
```

Đăng nhập:

```bash
curl -s -X POST https://vidfac.gacdero.vn/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"code":"997222"}'
```

Trả về:

```json
{"authenticated": true, "identity": "muse-gen-1", "scope": "ingest",
 "token": "<exp>.<identity>.<scope>.<mac>", "expires_in_sec": 2592000}
```

Token dùng làm Bearer y như key dài, sống 30 ngày:

```bash
curl -s https://vidfac.gacdero.vn/api/v1/auth/whoami -H "Authorization: Bearer <token>"
# {"identity":"muse-gen-1","scope":"ingest"}
```

**Vì sao không cho dùng thẳng mã 6 số làm key:** mã 6 số chỉ có 10^6 tổ hợp. Endpoint này nằm trên domain public, không rate limit ở tầng Bearer — một script dò hết bảng mã trong vài phút, và mỗi mã dò được là một danh tính có quyền upload + chấm + kí review. Mã chỉ đi qua `/auth/login`, nơi có **khoá 60 giây sau 5 lần sai** (đếm theo IP). Token sinh ra dài và có chữ ký HMAC nên không dò được.

**Giới hạn phải biết:** đổi mã không thu hồi token đã phát — token sống tới hạn 30 ngày. Muốn đá toàn bộ người ra ngay thì đổi `VIDEO_SESSION_SECRET` (hoặc xoá `data/session_secret.key`) rồi restart; mọi token cũ mất hiệu lực tức thì.

`owner` là danh tính của phiên PIN trên web UI, luôn scope `admin`. PIN owner đặt bằng `VIDEO_WEB_PIN` (mặc định `997220`), đi qua cùng endpoint với trường `pin` thay vì `code`.

Sửa env xong phải **restart process** — key và mã được đọc lúc khởi động (riêng `agent_codes()` đọc lại mỗi lần gọi, nhưng đừng dựa vào chi tiết đó).

## 3. Vì sao mỗi muse phải một key riêng

Luật xanh là **2 agent khác nhau** chấm đạt và kí, cả hai đều không phải người gen/upload. Nếu hai muse dùng chung key:

- cả hai review ghi cùng một `reviewer` → server thấy "cùng người chấm 2 lần" và chặn bằng `409 already_reviewed`;
- hoặc tệ hơn, nếu chặn không chặt thì một người tự kí 2 lần và shot lên xanh mà chỉ có một người xem.

Nên: **1 muse = 1 key = 1 identity**. Không có ngoại lệ.

## 4. Cần bao nhiêu muse

Mỗi shot cần 2 người chấm khác người gen, nên với N agent thì mỗi shot có `N − 1` người đủ điều kiện.

| Số agent | Người chấm được/shot | Dư phòng | Kết cục |
|---|---|---|---|
| 2 | 1 | 0 | **không bao giờ xanh được** |
| 3 | 2 | 0 | xanh được, nhưng 1 agent nghỉ là board đứng |
| 4 | 3 | 1 | chạy được |
| 5 | 4 | 2 | êm |

**Tối thiểu 4 agent active** (1 manager + 3 worker) trước khi bật board cho job thật. Với 3 agent, hệ thống đúng biên — không có sai số nào.

Manager cũng chấm được (scope `manager` bao gồm quyền của `ingest`), nên khi thiếu người thì manager là người chấm thứ ba. Nhưng đừng thiết kế dựa vào điều đó: manager còn phải xử lý shot đỏ và assemble.

## 5. Đặt tên

Gợi ý theo vai, không theo số thứ tự:

```
muse-boss:manager     # điều phối, xử lý shot đỏ, assemble
muse-gen-1:ingest     # gen + upload
muse-gen-2:ingest
muse-gen-3:ingest
```

Tên theo vai thì đọc log hiểu ngay ai làm gì. Tên theo số (`muse-1`, `muse-2`) thì sau ba tuần không ai nhớ ai là ai — và board ghi tên đó vào lịch sử vĩnh viễn.

## 6. Thứ tự bật board cho job ISS

1. Cấp đủ key, restart, kiểm `GET /api/v1/health`.
2. Chạy migration `POST /api/v1/board/migrate-grandfathered` với `board_enabled_at` = thời điểm bật. **Chạy lại lần hai phải trả `migrated: 0`** — nếu không, migration không idempotent, dừng lại.
3. Kiểm `GET /api/v1/todos/{id}/board/summary` — header phải hiện `Xanh 0 · Cũ (chưa chấm) N · Đỏ 0` với N ≈ số shot đã có video.
4. Từ đây video mới bắt buộc qua 2 review mới assemble được. Video cũ (`grandfathered`) không chặn.
