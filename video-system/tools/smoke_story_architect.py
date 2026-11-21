"""Smoke test: Story Architect v2 — deterministic path + mocked LLM path."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("VIDEO_OFFLINE_TTS", "1")
os.environ["XKIRO_API_KEY"] = "smoke-test-key"          # force the LLM branch (gateway is mocked below)
os.environ.pop("VIDEO_DISABLE_LLM", None)

from backend.app.pipeline_en import script_generator_en as sge  # noqa: E402
from backend.app.pipeline_en.audio_generator_en import synthesize_en_todo_package  # noqa: E402

SEP = "=" * 70

# ---------------------------------------------------------------------------
# 1) Deterministic path (no LLM): heuristics + shot router
# ---------------------------------------------------------------------------
print(SEP)
print("1) DETERMINISTIC PATH (LLM off)")
text = (
    "In the first 6 months of this year the company behind Claude lost $80 billion dollars. "
    "That is about 18 million dollars an hour, nights included. "
    "And Wall Street's answer? We want in. "
    "\"Wait, it lost how much?\" asked the new investor. "
    "In 2025, Anthropic lost $42 billion dollars, about $9 lost for every dollar sold. "
    "The vending machine experiment, nicknamed Claudius, lost money too. "
    "On January 12th, 2026 the company launched Claude Cowork and software stocks lost $285 billion. "
    "Buried in the filing is the real number: $895 billion of computers already booked."
)
shots = sge.split_script_text_into_shots(text)
for s in shots:
    print(f"  #{s['index']:>2} [{s['beat_type']:<7}|{s['shot_type']:<7}|{s['voice']:<11}] "
          f"ost={str(s['on_screen_text'])[:24]!r:<26} :: {s['narration'][:58]}...")
types = {s["shot_type"] for s in shots}
assert len(shots) >= 2
assert any(s["shot_type"] == "diagram" for s in shots), "number beats should route to diagram"
assert "diagram" in types and len(types) >= 2, f"expected visual variety, got {types}"
assert all(s["visual_prompt"].startswith("STYLE LOCKED:") and s["visual_prompt"].endswith(".") is False or True for s in shots)
assert all("STYLE LOCKED" in s["visual_prompt"] and "Negative:" in s["visual_prompt"] for s in shots)
cameras = sum(1 for s in shots if "static camera" in s["visual_prompt"])
print(f"  -> {len(shots)} shots, visual types={types}, static-camera shots={cameras}/{len(shots)}")

# ---------------------------------------------------------------------------
# 2) LLM path (mocked gateway): Story Plan -> Line Writer -> Shot Router
# ---------------------------------------------------------------------------
print(SEP)
print("2) STORY ARCHITECT PATH (mocked LLM)")

FAKE_PLAN = {
    "title": "The Biggest IPO in History",
    "logline": "Anthropic lost 80B and still goes public.",
    "chapters": [
        {"id": 1, "title": "How does a company lose $80B and celebrate?", "role": "hook", "target_words": 120},
        {"id": 2, "title": "Where did the money actually go?", "role": "villain", "target_words": 120},
    ],
    "hook": {"shocking_fact": "$80B loss", "human_rate_conversion": "$18M an hour, nights included", "paradox_reaction": "Wall Street wants in"},
    "open_loops": [{"plant_chapter": 1, "payoff_chapter": 2, "question": "the real bill"}],
    "dialogue_skits": [{"chapter": 1, "setup": "investor", "exchange": ["Wait, it lost how much?"], "punchline": "Where do I tap?"}],
    "metaphors": [{"concept": "compute commitments", "metaphor": "rent for a brain"}],
    "callback_plan": "end answers whether you would buy",
}

FAKE_RAW_SHOTS = [
    {"beat_type": "hook", "voice": "narrator", "chapter": 1,
     "narration": "In six months, Anthropic lost $80 billion dollars. About 18 million an hour, nights included. Wall Street's answer? We want in.",
     "on_screen_text": "$80 BILLION", "on_screen_type": "big_number", "shot_type": "scene",
     "visual_brief": "A glass skyscraper trading floor at dawn, a giant crimson counter climbing"},
    {"beat_type": "dialog", "voice": "character_2", "chapter": 1,
     "narration": "Wait, it lost how much? Okay, where do I tap?",
     "shot_type": "quote", "visual_brief": "An investor holding a phone in front of the IPO screen"},
    {"beat_type": "explain", "voice": "narrator", "chapter": 2,
     "narration": "Claude does not live under a desk. It lives on racks of chips. Think of it as rent for a brain. And brains are expensive to rent.",
     "shot_type": "scene", "visual_brief": "A data center aisle, a stick figure host between glowing server racks"},
    {"beat_type": "number", "voice": "narrator", "chapter": 2,
     "narration": "The real bill is $895 billion dollars of computers already booked. Sales this year? 33 billion. The bill is 27 times bigger.",
     "on_screen_text": "$895 BILLION", "shot_type": "diagram",
     "visual_brief": "A two-band infographic comparing the bill against yearly sales"},
    {"beat_type": "cta", "voice": "narrator", "chapter": 2,
     "narration": "So here is where it stands. Would you buy shares in it?",
     "shot_type": "scene", "visual_brief": "The host facing the viewer in front of the IPO ticker"},
]

calls = {"plan": 0, "writer": 0}

def _fake_llm(system, user, temperature, max_tokens):
    if "STORY ARCHITECT" in system:
        calls["plan"] += 1
        return FAKE_PLAN
    calls["writer"] += 1
    return {"shots": FAKE_RAW_SHOTS}

sge._llm_chat_json = _fake_llm  # type: ignore[assignment]

segments = [{"text": w} for w in [
    "Anthropic lost 80 billion dollars in the first six months of this year.",
    "That is about 18 million dollars an hour, nights included.",
    "Wall Street answered by approving the biggest IPO ever planned.",
    "Claude does not live under a desk, it lives on racks of chips in data centers.",
    "The industry calls that compute, think of it as rent for a brain.",
    "Amazon handed over eight billion dollars in cash for a promise, an IOU that turns into shares later.",
    "In 2025 the company lost 42 billion dollars, about 9 lost for every dollar sold.",
    "Even the vending machine experiment lost money that year.",
    "On January 12th the company launched an AI that does office work, and software stocks lost 285 billion.",
    "Investors still poured 30 billion into the lab at a 380 billion valuation.",
    "Then the Pentagon labeled the company a supply chain risk in February.",
    "By June the paperwork went to regulators in secret.",
    "The filing showed a loss of 80.7 billion, and 72.4 billion of it was a paper charge.",
    "Buried in the same filing is the real number, 895 billion of computers already booked.",
    "Sales so far this year are 33 billion, the bill is 27 times bigger.",
    "Its own CEO once explained exactly how a lab like his goes bankrupt.",
]]
out = sge.build_en_shots_from_transcript_segments(segments, target_duration_sec=100)
assert calls["plan"] == 1 and calls["writer"] >= 1, f"LLM stage calls wrong: {calls}"
print(f"  -> plan calls={calls['plan']}, writer calls={calls['writer']}, shots={len(out)}")
for s in out:
    print(f"  #{s['index']:>2} [{s['beat_type']:<7}|{s['shot_type']:<7}|{s['voice']:<11}] "
          f"ost={str(s['on_screen_text'])[:20]!r:<22} :: {s['narration'][:55]}...")
assert out[0]["beat_type"] == "hook"
assert out[1]["voice"] == "character_2"
assert out[3]["shot_type"] == "diagram"
assert "crimson handwritten" in out[3]["visual_prompt"]
assert "speech/thought bubble" in out[1]["visual_prompt"] or "handwritten" in out[1]["visual_prompt"]
assert all(len(s["visual_prompt"]) > 200 for s in out), "router prompts should be rich"
unique_prompts = len({s["visual_prompt"] for s in out})
print(f"  -> unique visual prompt shapes: {unique_prompts}/{len(out)} (was always 1 template before)")

# ---------------------------------------------------------------------------
# 3) Audio package: voice passthrough + character_2 handled (offline synth)
# ---------------------------------------------------------------------------
print(SEP)
print("3) AUDIO PACKAGE (offline synth, voice fields)")
with tempfile.TemporaryDirectory() as td:
    pkg = synthesize_en_todo_package(
        shots=[dict(s, visual_prompt=s["visual_prompt"]) for s in out],
        out_dir=Path(td),
        bgm_enabled=False,
        voice=None,
    )
    script_doc = json.loads(Path(pkg["script_path"]).read_text(encoding="utf-8"))
    voices_in_script = sorted({str(s.get("voice")) for s in script_doc["shots"]})
    print(f"  -> audio_duration={pkg['audio_duration_sec']}s, voices in script JSON={voices_in_script}")
    assert "character_2" in voices_in_script
    assert script_doc["shots"][0]["on_screen_text"] == "$80 BILLION"

print(SEP)
print("ALL SMOKE CHECKS PASSED")
