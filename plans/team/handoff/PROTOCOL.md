# PROTOCOL — kênh bàn giao WorkBuddy ⇄ Antigravity

Hai agent không gọi được API của nhau. Nên kênh liên lạc là **file markdown trong repo**, và mỗi bên tự có trigger riêng để phát hiện file mới/đổi.

```
plans/team/handoff/
├── PROTOCOL.md          ← luật chơi (file này). Đọc 1 lần, không sửa nếu không thống nhất.
├── BOARD.md             ← bảng theo dõi thread. Mỗi bên chỉ sửa PHẦN CỦA MÌNH.
├── to-antigravity/      ← WorkBuddy viết cho Antigravity đọc
├── to-workbuddy/        ← Antigravity viết cho WorkBuddy đọc
└── archive/             ← thread đã đóng, chuyển vào đây (không xoá)
```

## 1. Nguyên tắc gốc

1. **1 file = 1 thread.** Không tạo file mới để trả lời. Trả lời bằng cách **append** vào file cũ.
2. **Không viết 2 lượt liên tiếp.** Bên nào vừa viết thì phải chờ bên kia. Đây là guard chống ping-pong.
3. **Không xoá, không ghi đè nội dung của bên kia.** Chỉ append. Muốn sửa ý mình đã viết → viết lượt mới, nói rõ "đính chính lượt N".
4. **Trần 8 lượt/thread.** Chạm trần → `status: needs-human`, ghi vào BOARD.md, dừng. Việc này để tránh hai LLM nói chuyện vô hạn.
5. **Báo cáo cũng là thư.** Không có kênh riêng cho report — report là một lượt có `type: report`.

## 2. Định dạng file

Tên file: `YYYY-MM-DD-NNN-<slug>.md` (NNN = số thứ tự trong ngày, bắt đầu 001).

Mở đầu file bằng front-matter **dạng dòng `key: value`** (không cần YAML parser):

```
thread: shot-board-v1
opened_by: workbuddy
to: antigravity
turn: 1
status: open
needs: ack
```

| Field | Giá trị | Nghĩa |
|---|---|---|
| `thread` | slug | ID thread, trùng tên file |
| `opened_by` | `workbuddy` \| `antigravity` | ai mở |
| `to` | bên kia | **đến lượt ai trả lời** |
| `turn` | số | lượt hiện tại, tăng 1 mỗi lần append |
| `status` | `open` \| `answered` \| `closed` \| `needs-human` | trạng thái |
| `needs` | `ack` \| `answer` \| `review` \| `action` \| `none` | bên kia cần làm gì |
| `type` | `task` \| `question` \| `report` \| `review` | tuỳ chọn, mặc định `task` |

Nội dung: mỗi lượt là một section, **không sửa section cũ**:

```md
## Turn 1 — workbuddy · 2026-11-27 21:55

<nội dung>

## Turn 2 — antigravity · 2026-11-27 22:30

<nội dung>
```

Khi append lượt mới, cập nhật front-matter: `turn: 2`, `to: workbuddy`, `status: answered`.

## 3. Ai làm gì khi thức dậy

Cả hai bên chạy cùng một routine:

```
1. Quét to-<mình>/ tìm file có mtime/hash khác lần trước
2. Với mỗi file: đọc front-matter
   - to != mình  → bỏ qua (không phải lượt mình)
   - status=closed → bỏ qua
   - turn >= 8  → set needs-human, ghi BOARD.md, dừng
3. Đọc toàn bộ thread (để không trả lời trùng ý đã nói)
4. Append Turn n+1, cập nhật front-matter, ghi BOARD.md phần của mình
5. Nếu cần bên kia làm gì tiếp → tạo/sửa file trong to-<bên kia>/
```

**Điều kiện duy nhất để trả lời:** `to` trỏ về mình **và** `status != closed`. Không có ngoại lệ "thấy hay thì trả lời" — đó là nguồn spam số một.

## 4. BOARD.md

Bảng theo dõi, mỗi bên **chỉ sửa section của mình**, không sửa section bên kia:

```md
## Từ WorkBuddy
| thread | turn | status | chờ gì | cập nhật |
|---|---|---|---|---|

## Từ Antigravity
| thread | turn | status | chờ gì | cập nhật |
|---|---|---|---|---|
```

Không sửa BOARD.md của nhau = không bao giờ conflict merge.

## 5. Trigger

| Bên | Trigger | Trạng thái |
|---|---|---|
| WorkBuddy | automation định kỳ, quét `to-workbuddy/` | đã đặt |
| Antigravity | agent của bạn phải tự quét — xem §6 | **bạn cần bật** |

WorkBuddy không đẩy được việc vào Antigravity (không có API/CLI). Nên trigger phía Antigravity phải do bạn đặt: hoặc một rule thường trực trong agent của nó, hoặc chạy `tools/handoff_watch.py`.

## 6. Câu để dán vào Antigravity

> Trong repo này có kênh bàn giao `plans/team/handoff/`. Đọc `PROTOCOL.md` và tuân thủ.
> Mỗi lần bắt đầu phiên: quét `to-antigravity/`, tìm file có `to: antigravity` và `status != closed`, đọc cả thread, rồi append lượt tiếp theo đúng định dạng. Trả lời xong thì cập nhật phần "Từ Antigravity" trong `BOARD.md`.
> Không tạo file mới để trả lời. Không sửa lượt của bên kia. Không viết 2 lượt liên tiếp.

Kiểm tra nhanh file nào đã đổi:

```bash
python tools/handoff_watch.py --side antigravity
```

## 7. Vì sao thiết kế như vậy

Kênh này là bản thu nhỏ của chính shot board: **claim = turn**, **ký tên = `## Turn n — <side>`**, **chống loop = không viết 2 lượt liên tiếp + trần 8 lượt**, **báo cáo = file md**. Nếu bạn thấy protocol này chạy ổn, nó là bằng chứng rằng cơ chế board cũng sẽ ổn — và ngược lại, chỗ nào protocol này thấy vướng thì board cũng sẽ vướng y như vậy.
