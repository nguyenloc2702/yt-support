import pytest
from services.prompt_sanitizer import clean_narration_for_tts, sanitize_veo_prompt


def test_clean_narration_for_tts():
    raw = "[music] In 1830, they introduced (applause) the company's new policy ♪♪"
    cleaned = clean_narration_for_tts(raw)
    assert "[music]" not in cleaned
    assert "(applause)" not in cleaned
    assert "♪" not in cleaned
    assert cleaned == "In 1830, they introduced the company's new policy"


def test_sanitize_veo_prompt_conflict_and_hate():
    raw = "Spain absolutely hated the Dutch because they were in the middle of the Eighty Years' War"
    prompt = sanitize_veo_prompt(raw)
    assert "hated" not in prompt.lower()
    assert "war" not in prompt.lower()
    assert "Cinematic 4K" in prompt
    assert "35mm master lens" in prompt


def test_sanitize_veo_prompt_political_speech():
    raw = "The Dutch government watched this and said, we must act immediately"
    prompt = sanitize_veo_prompt(raw)
    assert "government watched and said" not in prompt.lower()
    assert "leadership council observed" in prompt.lower()


def test_sanitize_veo_prompt_minors():
    raw = "Young children and minors were observing the harbor fleet"
    prompt = sanitize_veo_prompt(raw)
    assert "children" not in prompt.lower()
    assert "minors" not in prompt.lower()


def test_sanitize_veo_prompt_slang_and_violence():
    raw = "decided to put them out of their misery. They blockaded Dutch ports and hunted"
    prompt = sanitize_veo_prompt(raw)
    assert "put them out of their misery" not in prompt.lower()
    assert "hunted" not in prompt.lower()
    assert "concluded the prolonged standoff" in prompt.lower()
