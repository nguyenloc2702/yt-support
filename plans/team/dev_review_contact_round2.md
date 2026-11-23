# Senior Dev — chốt contact architecture

| R | Kết luận | Điều kiện kỹ thuật |
|---|---|---|
| R1 | CHẤP NHẬN | Đếm `agent_usage.questions`, chặn ở service trước khi ghi tin. |
| R2 | CHẤP NHẬN | Gỡ trong 10′ ghi `approvals` + event; quá hạn chỉ Owner. |
| R3 | CHẤP NHẬN | Sửa cam kết A1 §5.1 60s → 180s, nếu không test đỏ. |
| R4 | CHẤP NHẬN | `escalation_id` trỏ row `escalations` đang mở; trần 1/giờ theo `(sender,item)`. |
| R5 | CHẤP NHẬN | `attempts` reset khi ack; dedupe khoá `(agent, watermark_seq)`. |
| R6 | CHẤP NHẬN | Limit theo identity, không theo IP sau proxy; 429 là silent. |
| R7 | CHẤP NHẬN | Shadow ghi `events registry.miss`; enforce bằng 2 env riêng claim/review. |
| R8 | CHẤP NHẬN | Check `item_id IS NOT NULL` + role ở service, không sửa auth middleware. |
| R9 | CHẤP NHẬN | Snapshot 1 transaction + hash; seed `chat_read_state` manager mới cùng request. |

## Schema chốt

- **R4:** `chat_mentions(agent, thread_id, seq, acked_at)` PK(agent,seq), idx(agent,acked_at); `chat_messages.mentions_json`; `escalations(id, item_id, opened_by, status, opened_at)` + idx(item_id,status).
- **R5:** `agent_wake_state(agent PK, watermark_seq, last_wake_at, attempts)`.
- **R7:** `agent_registry(agent PK, role, manager, display_name, status)` + idx(role,status), idx(manager).
- **R9:** `manager_handovers(id, from_agent, to_agent, snapshot_json, sha256, acked_at, approved_by, created_at)` + idx(to_agent,created_at).
- **R1:** `agent_usage.questions INTEGER DEFAULT 0`.

## Rủi ro

**Wake trùng → đốt 2 lượt AI.** Hai hook song song hoặc restart giữa intent/ack wake lại cùng mention. Phát hiện: `attempts` >1 cho cùng `(agent, watermark_seq)`; tỷ lệ wake:mention >3:1/ngày/agent.
