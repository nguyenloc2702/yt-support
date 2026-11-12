"""Gen 1 short EN quốc tế chạy END-TO-END qua code path thật.

Do chưa có VIDEO_XKIRO_API_KEY (LLM adapter fail-closed), script EN viết tay
được trả qua FakeLlm — mọi bước khác (validate → TTS edge-tts EN → Pixabay
b-roll → karaoke → ffmpeg 1080x1920 → QC) là thật 100%.

Chạy: .venv/Scripts/python.exe -I tools/gen_test_short_en.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import providers
from backend.app.service_shorts import ShortService
from backend.app.store import SQLiteStore
from backend.app.config import db_path

SCRIPT_EN = {
    "title": "Three billion dong gone overnight",
    "hook": "Overnight, three billion dong vanished from dozens of bank accounts",
    "beats": [
        "By morning, customers kept calling the hotline about strange transactions they never made",
        "The bank later confirmed a gang posing as support staff had been stealing OTP codes",
        "Average losses hit fifty million dong each, with some victims losing two hundred million dong",
    ],
    "closing": "So who bears the final responsibility — the bank or the customer?",
    "hashtags": {
        "tiktok": ["#breakingnews", "#bankfraud", "#scamalert", "#money", "#news"],
        "facebook": ["#bankfraud", "#news"],
        "youtube": ["#shorts", "#news"],
    },
    "description": "Bank OTP scam drained customer accounts overnight. Stay alert.",
    "source_note": "Source: BBC Business",
}


class FakeLlm:
    """Trả script viết tay — chỉ thay bước LLM, còn lại pipeline thật."""

    def chat_json(self, system, user, schema=None):
        print("[gen] LLM called (fake) — system prompt:", system[:60], "...")
        return SCRIPT_EN


def main() -> None:
    store = SQLiteStore(db_path())
    shorts = ShortService(store)
    saved = providers.registry.llm
    providers.registry.llm = FakeLlm()
    try:
        short = shorts.create(
            mode="news", lang="en",
            source_text=("Brief: A bank OTP scam drained customer accounts "
                         "overnight; dozens of victims lost 50-200 million dong "
                         "each after callers posing as support staff stole OTP codes."),
            source_note="Source: BBC Business",
            actor="test-seed",
        )
    finally:
        providers.registry.llm = saved
    print("[gen] created:", short["id"], "| lang:", short["lang"],
          "| words:", short["word_count"], "| status:", short["status"])

    done = shorts.produce(short["id"], actor="test-seed")
    print("[gen] produced:", done["id"], "| status:", done["status"],
          "| duration:", round(done["duration_s"], 1), "s")
    for sc in done["scenes"]:
        print(f"   scene {sc['seq_num']}: kind={sc['asset_kind']} "
              f"attr={sc['attribution'] or '-'} | {sc['text'][:50]}")
    print("[gen] MP4:", shorts.video_path(short["id"]))


if __name__ == "__main__":
    main()
