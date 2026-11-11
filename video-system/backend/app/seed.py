"""Seed GENERIC TEST DATA — clearly labelled, never real project data.

Run: VIDEO_DATA_DIR=data python -m backend.app.seed
"""
from __future__ import annotations

import os

from .config import db_path
from .main import batches, importer, items, shots, todos
from .service_import import sha256_file


def make_wav(path, seconds: float) -> None:
    import math
    import struct
    import wave

    rate = 8000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for _ in range(int(rate * seconds)):
            frames += struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * 0.01)))
        w.writeframes(bytes(frames))


def main() -> None:
    item = items.create("SEED — Demo Item (TEST DATA, not a real project)",
                        "prebuilt_import", "en", "v3")
    audio_path = db_path().parent / "objects" / item["id"] / "seed_audio.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    make_wav(audio_path, 32.0)
    shots_payload = {
        "script_version": 1,
        "language": "en",
        "text": "Seed script",
        "audio": {"file_path": str(audio_path), "duration_sec": 32.0},
        "shots": [
            {"index": i, "shot_type": "scene" if i != 3 else "quote",
             "narration": f"Seed narration line {i}.",
             "audio_start_sec": (i - 1) * 8.0,
             "audio_end_sec": i * 8.0,
             "visual_prompt_clean": f"seed visual prompt {i}",
             **({"on_screen_text": "Seed quote.",
                 "on_screen_type": "thought_bubble"} if i == 3 else {})}
            for i in range(1, 5)
        ],
    }
    result = importer.import_shots(item["id"], shots_payload, audio_path, None)
    rows = shots.list(item["id"])
    # vary statuses so every UI state is visible
    for r in rows[:2]:
        shots.transition(r["id"], "keyframe_pending", actor="seed")
    shots.approve_keyframe(rows[0]["id"], actor="seed")
    b = batches.create(item["id"], size=2)
    print("seeded item:", item["id"])
    print("import:", result)
    print("batch:", b["id"])

    os.environ.setdefault("VIDEO_OFFLINE_TTS", "1")
    todo_pkg = todos.create_from_script(
        title="SEED EN TODO — Global Semiconductor Supply Chain",
        script_text=(
            "Modern microprocessors require over a thousand precision manufacturing steps. "
            "Extreme ultraviolet lithography prints features smaller than a human virus. "
            "That is why a single fabrication plant costs twenty billion dollars to build."
        ),
        bgm_enabled=False,
    )
    print("seeded EN todo:", todo_pkg["item_id"], "audio_duration_sec:", todo_pkg["audio_duration_sec"])


if __name__ == "__main__":
    main()
