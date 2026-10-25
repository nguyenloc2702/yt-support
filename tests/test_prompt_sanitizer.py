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
    assert "storybook cartoon" in prompt
    assert "no photorealism" in prompt
    assert "photorealistic" not in prompt.lower()


def test_sanitize_veo_prompt_replaces_documentary_wrapper_with_storybook():
    raw = (
        "Visual representation of: Werner enters the bank. "
        "35mm master lens, photorealistic atmospheric depth, dramatic cinematic lighting, silent, no audio."
    )
    prompt = sanitize_veo_prompt(raw)
    assert "Cinematic 4K" not in prompt
    assert "35mm master lens" not in prompt
    assert "Flat 2D storybook cartoon animation" in prompt
    assert "Scene: Werner enters the bank" in prompt
    assert "Bright, flat, even lighting" in prompt


def test_sanitize_veo_prompt_flattens_mood_cues():
    raw = (
        "A middle-aged man enters a tiny bank branch. The scene is dimly lit, with old oak counters. "
        "CRT monitors glow with greenish-blue light, cold blue glow, sharp contrast, dramatic lighting."
    )
    prompt = sanitize_veo_prompt(raw)
    lowered = prompt.lower()
    assert "dimly lit" not in lowered
    assert "greenish-blue light" not in lowered
    assert "cold blue glow" not in lowered
    assert "sharp contrast" not in lowered
    assert "dramatic lighting" not in lowered
    assert "old oak counters" in prompt


def test_sanitize_veo_prompt_empty_uses_storybook_frame():
    prompt = sanitize_veo_prompt("")
    assert "Flat 2D storybook cartoon animation" in prompt
    assert "no photorealism" in prompt
    assert "Cinematic 4K" not in prompt


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


def test_ensure_crayon_style_prefixes_prompts_that_lost_the_style():
    from services.algorithm_qa import CRAYON_KEYWORDS, PHOTO_KEYWORDS
    from services.prompt_sanitizer import ensure_crayon_style

    styled = ensure_crayon_style("Extreme close-up of a chalkboard with numbers")

    assert any(k in styled.lower() for k in CRAYON_KEYWORDS)
    assert not any(k in styled.lower() for k in PHOTO_KEYWORDS)


def test_ensure_crayon_style_strips_a_negated_photoreal_phrase():
    from services.algorithm_qa import PHOTO_KEYWORDS
    from services.prompt_sanitizer import ensure_crayon_style

    styled = ensure_crayon_style(
        "A whimsical hand-drawn crayon cartoon: dream house, flat 2D, no photorealistic details"
    )

    assert "no photorealistic" not in styled.lower()
    assert not any(k in styled.lower() for k in PHOTO_KEYWORDS)
    assert styled.startswith("A whimsical hand-drawn crayon cartoon")
