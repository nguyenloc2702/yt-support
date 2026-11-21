# Đối chiếu nâng cấp khuôn mẫu hình ảnh: v2 (Crayon Capital Flat 2D) → v3 (Stick-Hero in Epic World)

> **Ngày nâng cấp:** `2026-11-18`  
> **Thư mục lưu bản gốc v2 để đối chiếu:** [`video-system/docs/archive_v2_to_v3/`](./)  
> - `v2_skill_bundle.py.bak` (bản gốc `backend/app/pipeline_en/skill_bundle.py` từ git `HEAD`)
> - `v2_script_generator_en.py.bak` (bản gốc `backend/app/pipeline_en/script_generator_en.py` từ git `HEAD`)
> - `v2_AGENT_API_GUIDE.md` (bản gốc `AGENT_API_GUIDE.md` v2.3)
> - `v2_shot_generation_SKILL.md` (bản gốc `skills/shot_generation/SKILL.md` v2.0.0)
> - `v2_video_production_SKILL.md` (bản gốc `skills/video_production/SKILL.md` v1.0.0)

---

## Bảng so sánh chi tiết v2 vs v3

| Hạng mục | **v2 (Cũ — Crayon Capital Flat 2D)** | **v3 (Mới — Stick-Hero in Epic World)** |
| :--- | :--- | :--- |
| **Phong cách nền & cảnh vật** | `Simple flat 2D cartoon`, nền phẳng sáng màu đơn giản, **cấm** `photorealism`, **cấm** `3D render`, **cấm** `detailed shading / gradient`. | Đại cảnh 3D/tả thực điện ảnh chi tiết cao (`volumetric lighting`, vật liệu thép/nước/không gian thật, `shallow depth of field`). |
| **Nhân vật** | Người que 2D phẳng, chỉ 1 mảng bóng xám phẳng bên cạnh, không nhận ánh sáng môi trường. | Vẫn giữ người que đặc trưng kênh StickSto (đầu tròn da đào, mắt chấm, chỉ giữ 2–3 nét nhận diện: tóc, áo khoác/vest, kính) + **bắt buộc nhận rim-light và đổ bóng xuống sàn** để hòa nhập cảnh thật. |
| **Độ dài & số lượng shot** | Chia nhỏ 4–7s/shot (~75–80+ shot cho video 8–10 phút), khó gen và dễ đứt nhịp. | **Macro-Shot 12–18 giây/cảnh** (~35–45 từ/shot, khoảng 30–38 Macro-Shots cho video 8–10 phút), gom 2–4 câu chung 1 bối cảnh lớn. |
| **Chuyển động Camera** | `Static camera` (máy đứng yên hoàn toàn). | **Slow continuous cinematic camera movement** (`slow push-in`, `gentle orbital pan`, `tilt`) kết hợp nhịp 30-40-30. |
| **Sơ đồ / Diagram** | Chia 2 dải màu phẳng (pale blue / beige) với nét mực đen và số đỏ. | Mô hình 3D editorial trực quan kết hợp đường vector phát sáng & 1 con số/nhãn lớn nổi bật. |
| **API Agent nhận được** | `skill_bundle.version = "2.1.0"`, `AGENT_API_GUIDE` v2.3. | `skill_bundle.version = "3.0.0"`, `AGENT_API_GUIDE` v3.0, và `get_full_skill_bundle()` loại bỏ 2 file mold v2 cũ (`CRAYON_EXPLAINER_MOLD.md`, `UNIVERSAL_SHOT_FRAMEWORK.md`) khỏi payload để tránh mâu thuẫn. |
