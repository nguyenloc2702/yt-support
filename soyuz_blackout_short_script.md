# Soyuz Reentry Blackout & Final Meter — Short Video Script (< 3 Minutes)

- **Source Reference:** [Facebook Reel (`1000839135879359` — Xe Ôm Vlog, 4:45)](https://www.facebook.com/reel/1000839135879359)
- **System Standard:** `9:16 Vertical (1080x1920 @ 30fps)` — mode `cinematic`,
  overlay preset `clean_karaoke` (chỉ karaoke, **không** badge "TIN NÓNG", không headline).
  Đây **không** phải format Card-in-Frame của `daily_digest`.
- **Target Duration:** `130 – 150s` (`< 180s`) — xem ghi chú tốc độ đọc bên dưới
- **Hook Rule:** Enters the core paradox within the **first 5 seconds**

---

## 1. English Audio Script (TTS / Voiceover Ready)

> 390 words · **Đo thật trên prod: 2:16 (136.4s)** = 2.86 từ/giây (`en-US-BrianMultilingualNeural`,
> `tts_en.rate: "+6%"`). Bản trước 385 từ ra 141.9s = 2.71 từ/giây — **cùng cấu hình mà lệch 5%**,
> nên chỉ nên ước lượng khoảng **2.7–2.9 từ/giây**. Muốn dài hơn thì **giảm `rate`**, đừng nhồi chữ.

```text
Four minutes falling through fire. Zero radio contact with Earth. No one can save them.

That is the reality every time three astronauts leave the International Space Station and climb into a capsule the size of a small car. Their seats are custom-molded to their exact bodies — shoulders almost touching, not an inch of wasted space. Yet the blackout isn't even the wildest part. Their survival also depends on the final one meter before they hit the ground.

After undocking, Soyuz fires its main engine to drop out of orbit. Moments later, explosive bolts detonate, splitting the spacecraft into three modules. The living compartment and the propulsion module are left behind to burn up. Only the bell-shaped descent module — carrying all three crew — plunges toward Earth.

Hundreds of kilometers up, the capsule flips, pointing its thick heat shield straight into the atmosphere. Slamming into the air at hypersonic speed, friction superheats the gas around it until it ionizes into a blinding layer of glowing plasma. Inside, the crew is pressed into their seats by four to five times their own body weight.

From the outside, the capsule looks like a blazing fireball. That plasma shell acts like a wall of steel, swallowing every radio signal. For four agonizing minutes, Mission Control is blind and deaf — no telemetry, no voice, no way to know if the crew is still alive. Flight surgeons sit watching screens that show nothing. Only when the capsule punches through the thickest air does contact snap back.

Escaping the fire doesn't mean they're safe. Lower in the sky, a small drogue chute snaps out to stabilize the fall, then the massive main canopy inflates with a hard jolt. Seconds later, the capsule jettisons its charred heat shield.

That reveals six solid-fuel engines hidden under the base. Even under a giant parachute, hitting the steppe can still injure a crew. So at roughly one meter above the ground, all six fire at once — an upward blast that cushions the touchdown.

Even after landing, it isn't over. After months in zero gravity, an astronaut's body cannot stand or walk on its own. Recovery crews lift each one out and set them in chairs. Four minutes lost in fire. One final meter that saves their lives. Would you dare take that seat back to Earth?
```

### Shot Breakdown (EN — `cinematic` / `clean_karaoke`)

Thời gian thật do TTS quyết, không theo bảng ước lượng. Bảng dưới là **kết quả đo sau khi
tạo job trên prod** — cập nhật lại sau mỗi lần đổi script hoặc đổi `tts_en.rate`.

| Shot | Real Time | Category | Headline (nội bộ) | Audio Cue |
| :--- | :--- | :--- | :--- | :--- |
| **01** | `0:00.0 – 0:06.6` | `HOOK` | 4 Minutes Trapped In Plasma Fire | *"Four minutes falling through fire... No one can save them."* |
| **02** | `0:06.6 – 0:27.5` | `CREW` | Capsule The Size Of A Small Car | *"That is the reality... before they hit the ground."* |
| **03** | `0:27.5 – 0:46.5` | `UNDOCKING` | Explosive Bolts Split Soyuz In Three | *"After undocking... plunges toward Earth."* |
| **04** | `0:46.5 – 1:05.0` | `REENTRY` | 4–5g Through The Porthole | *"Hundreds of kilometers up... their own body weight."* |
| **05** | `1:05.0 – 1:28.7` | `BLACKOUT` | Mission Control Blind For 4 Minutes | *"From the outside... contact snap back."* |
| **06** | `1:28.7 – 1:42.5` | `DESCENT` | Parachutes Deploy & Heat Shield Drops | *"Escaping the fire... charred heat shield."* |
| **07** | `1:42.5 – 1:56.9` | `LANDING` | Six Engines Fire At The Final Meter | *"That reveals six solid-fuel engines... cushions the touchdown."* |
| **08** | `1:56.9 – 2:16.4` | `RECOVERY` | Zero-G Aftermath & Steppe Rescue | *"Even after landing... seat back to Earth?"* |

**Tổng: 136.38s (2:16)** — short prod `sho_296b62342df5`, status `queued`.

---

## 2. Những gì đã sửa so với bản đầu

| # | Vấn đề | Sửa |
| :--- | :--- | :--- |
| 1 | **Sai kỹ thuật:** "two clusters of braking rockets" | Soyuz có **6** động cơ nhiên liệu rắn (D-2.5) dưới đáy, không phải 2 cụm. Sửa thành "six solid-fuel engines", "all six fire at once". |
| 2 | **Sai địa hình:** "the desert" | Soyuz hạ cánh trên **thảo nguyên Kazakhstan** (steppe), không phải sa mạc. |
| 3 | **Shot 02 gánh 2 ý** (ghế + teaser "final meter") trong 22s | Bỏ chi tiết "không một inch lãng phí" rườm, thêm quy mô cabin ("capsule the size of a small car") để khán giả *hình dung* được sự chật chội thay vì nghe kể. Teaser "final meter" vẫn giữ nhưng xuống cuối đoạn, làm bàn đạp. |
| 4 | **Thiếu stake con người** từ shot 02→08 | Thêm 2 mốc: **4–5g** đè phi hành đoàn vào ghế (shot 04), và **flight surgeons nhìn màn hình trống** trong blackout (shot 05). Đây là thứ biến "cỗ máy gặp sự cố" thành "ba người có thể chết". |
| 5 | **Doc ghi sai format** | "Card-in-Frame / `INSTRUCTION_DAILY_WORLD_BRIEF.md`" là format `daily_digest`. Mode `cinematic` dùng `clean_karaoke`, không badge, không headline. |
| 6 | **Target duration sai** | 155–175s dựa trên 155–160 WPM, nhưng TTS chạy `+6%` = **2.71 từ/giây** (đo thật: 385 từ → 141.9s). Target đổi thành 140–160s. |

## 3. Cách nạp lên hệ thống

```bash
# 1. Tạo short từ kịch bản có sẵn (không gọi LLM, không cắt beat)
POST /api/v1/shorts/from-script
  { title, mode: "cinematic", lang: "en", hook, beats[], closing,
    description, source_note, search_keywords_en[] }

# 2. Phát hành job cho agent video
POST /api/v1/shorts/{id}/publish-job     # -> status queued, TTS sinh audio + timing

# 3. Gắn brief hình ảnh viết tay cho từng shot (dùng timing thật ở bước 2)
PUT /api/v1/shorts/{id}/scene-prompts
  { prompts: [{ seq: 1, prompt: "..." }, ...] }
```

Agent video poll `GET /api/v1/short-jobs?status=queued` → `GET /api/v1/shorts/{id}/job`.
