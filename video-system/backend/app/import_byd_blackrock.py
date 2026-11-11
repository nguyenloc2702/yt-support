"""Import the existing BYD and BlackRock English jobs + Skill Bundle into the TODO queue.

Run:
    cd C:\\app\\work\\video-system
    .\\.venv\\Scripts\\python.exe -m backend.app.import_byd_blackrock
"""
from __future__ import annotations

import json
from pathlib import Path

from .main import store, todos


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def import_all() -> list[dict]:
    results = []
    store.execute("DELETE FROM items WHERE status='failed' AND (title LIKE 'BYD%' OR title LIKE 'BlackRock%')")

    # 1. BYD (remake_1793345519_99f0b2 — 169 shots, 1147.53s)
    byd_dir = Path(r"C:\app\work\support-video-edit\data\automated_runs\remake_1793345519_99f0b2")
    if byd_dir.exists():
        existing = store.query_one(
            "SELECT id FROM items WHERE title LIKE 'BYD — Wang Chuanfu%' AND status = 'todo'"
        )
        if existing:
            print("BYD already in TODO:", existing["id"])
            results.append(todos.get_todo(existing["id"]))
        else:
            shots_raw = _load_json(byd_dir / "shots_plan_en.json")
            shots_list = shots_raw.get("shots", shots_raw) if isinstance(shots_raw, dict) else shots_raw
            clean_mp3 = byd_dir / "audio_en_full.mp3"
            audio_mp3 = byd_dir / "audio_en_with_bgm.mp3"
            if not audio_mp3.exists():
                audio_mp3 = clean_mp3
            pkg = todos.import_existing_package(
                title="BYD — Wang Chuanfu & How BYD Conquered Global EVs (169 Shots)",
                audio_src_path=audio_mp3,
                shots=shots_list,
                source_url="local://support-video-edit/data/automated_runs/remake_1793345519_99f0b2",
                bgm_enabled=True,
                actor="migration",
                clean_voice_src_path=clean_mp3 if clean_mp3.exists() else None,
            )
            item_id = pkg["item_id"]
            # Attach existing character reference sheets if present
            char_dir = byd_dir / "characters"
            if char_dir.exists():
                seq = 1
                for c_folder in sorted(char_dir.iterdir()):
                    ref_png = c_folder / "reference.png"
                    if ref_png.exists():
                        todos.submit_image(
                            item_id,
                            kind="character",
                            seq_num=seq,
                            name=c_folder.name,
                            raw_bytes=ref_png.read_bytes(),
                            filename=f"{c_folder.name}.png",
                            note=f"Character reference sheet for {c_folder.name}",
                            actor="migration",
                        )
                        seq += 1
            print(
                f"Imported BYD -> item_id={item_id}, shots={pkg['total_shots']}, "
                f"audio_duration_sec={pkg['audio_duration_sec']}s"
            )
            results.append(todos.get_todo(item_id))

    # 2. BlackRock 15min Master (products/BlackBrock/final_15min — 201 shots, 900.0s)
    br15_dir = Path(r"C:\app\work\support-video-edit\products\BlackBrock\final_15min")
    if br15_dir.exists():
        existing = store.query_one(
            "SELECT id FROM items WHERE title LIKE 'BlackRock (15-Min Master)%' AND status != 'deleted'"
        )
        if existing:
            print("BlackRock 15min already in TODO:", existing["id"])
            results.append(todos.get_todo(existing["id"]))
        else:
            shots_raw = _load_json(br15_dir / "shots_plan_15min.json")
            shots_list = shots_raw.get("shots", shots_raw) if isinstance(shots_raw, dict) else shots_raw
            clean_mp3 = br15_dir / "blackrock_15min_narration_only.mp3"
            audio_mp3 = br15_dir / "blackrock_15min_master_with_bgm.mp3"
            if not audio_mp3.exists():
                audio_mp3 = clean_mp3
            pkg = todos.import_existing_package(
                title="BlackRock (15-Min Master) — Larry Fink & The $14 Trillion Machine (201 Shots)",
                audio_src_path=audio_mp3,
                shots=shots_list,
                source_url="local://support-video-edit/products/BlackBrock/final_15min",
                bgm_enabled=True,
                actor="migration",
                clean_voice_src_path=clean_mp3 if clean_mp3.exists() else None,
            )
            print(
                f"Imported BlackRock 15min -> item_id={pkg['item_id']}, shots={pkg['total_shots']}, "
                f"audio_duration_sec={pkg['audio_duration_sec']}s"
            )
            results.append(pkg)

    # 3. BlackRock v0.3 (products/BlackBrock/audio_en_v0.3 & shot_prep_v0.3 — 130 shots, 674.9s)
    br03_audio_dir = Path(r"C:\app\work\support-video-edit\products\BlackBrock\audio_en_v0.3")
    br03_shot_dir = Path(r"C:\app\work\support-video-edit\products\BlackBrock\shot_prep_v0.3")
    if br03_audio_dir.exists() and br03_shot_dir.exists():
        existing = store.query_one(
            "SELECT id FROM items WHERE title LIKE 'BlackRock (v0.3)%' AND status != 'deleted'"
        )
        if existing:
            print("BlackRock v0.3 already in TODO:", existing["id"])
            results.append(todos.get_todo(existing["id"]))
        else:
            shots_raw = _load_json(br03_shot_dir / "shots_plan_en.json")
            shots_list = shots_raw.get("shots", shots_raw) if isinstance(shots_raw, dict) else shots_raw
            manifest = _load_json(br03_audio_dir / "audio_manifest.json")
            manifest_segs = manifest.get("segments", manifest.get("shots", [])) if isinstance(manifest, dict) else []
            seg_by_idx = {}
            for idx_m, seg in enumerate(manifest_segs, start=1):
                seg_by_idx[int(seg.get("index") or idx_m)] = seg
            merged_shots = []
            for idx_s, s in enumerate(shots_list, start=1):
                s_copy = dict(s)
                m_seg = seg_by_idx.get(int(s.get("index") or idx_s), {})
                for k in (
                    "audio_start_sec", "audio_end_sec", "output_start_sec", "output_end_sec",
                    "start_sec", "end_sec", "duration_sec", "total_shot_duration_sec",
                    "raw_duration_sec", "duration",
                ):
                    if k not in s_copy and k in m_seg:
                        s_copy[k] = m_seg[k]
                merged_shots.append(s_copy)
            clean_mp3 = br03_audio_dir / "blackrock_en_v0.3.mp3"
            audio_mp3 = br03_audio_dir / "blackrock_en_v0.3_with_bgm.mp3"
            if not audio_mp3.exists():
                audio_mp3 = clean_mp3
            pkg = todos.import_existing_package(
                title="BlackRock (v0.3) — How Aladdin & Larry Fink Built BlackRock (130 Shots)",
                audio_src_path=audio_mp3,
                shots=merged_shots,
                source_url="local://support-video-edit/products/BlackBrock/shot_prep_v0.3",
                bgm_enabled=True,
                actor="migration",
                clean_voice_src_path=clean_mp3 if clean_mp3.exists() else None,
            )
            print(
                f"Imported BlackRock v0.3 -> item_id={pkg['item_id']}, shots={pkg['total_shots']}, "
                f"audio_duration_sec={pkg['audio_duration_sec']}s"
            )
            results.append(pkg)

    return results


if __name__ == "__main__":
    import_all()
