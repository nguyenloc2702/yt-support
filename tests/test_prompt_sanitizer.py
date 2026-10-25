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
    assert "eighty years war" not in prompt.lower()
    assert "historic era" in prompt.lower()
    assert "vector cartoon" in prompt
    assert "no photorealism" in prompt
    assert "photorealistic" not in prompt.lower()


def test_sanitize_veo_prompt_replaces_documentary_wrapper_with_vector_cartoon():
    raw = (
        "Visual representation of: Werner enters the bank. "
        "35mm master lens, photorealistic atmospheric depth, dramatic cinematic lighting, silent, no audio."
    )
    prompt = sanitize_veo_prompt(raw)
    assert "Cinematic 4K" not in prompt
    assert "35mm master lens" not in prompt
    assert "Clean flat vector cartoon animation" in prompt
    assert "Scene: Werner enters the bank" in prompt
    assert "Bright, even lighting" in prompt


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


def test_sanitize_veo_prompt_flattens_black_background_and_close_up():
    raw = (
        "A close-up of Werner's face, lit harshly from a single studio light casting sharp shadows "
        "across his features. The background is a deep, uniform black, making his face the sole "
        "point of attention. The style is hyper-expressive, with exaggerated facial features and "
        "dramatic lighting."
    )
    prompt = sanitize_veo_prompt(raw, for_video=False)
    lowered = prompt.lower()
    assert "close-up" not in lowered
    assert "medium shot of werner" in lowered
    assert "uniform black" not in lowered
    assert "plain warm beige paper" in lowered
    assert "sharp shadows" not in lowered
    assert "lit flat and evenly" in lowered
    assert "hyper-expressive" not in lowered
    assert "exaggerated facial features" not in lowered


def test_sanitize_veo_prompt_empty_uses_vector_cartoon_frame():
    prompt = sanitize_veo_prompt("")
    assert "Clean flat vector cartoon animation" in prompt
    assert "no photorealism" in prompt
    assert "Cinematic 4K" not in prompt


def test_sanitize_veo_prompt_still_mode_drops_motion_and_audio():
    prompt = sanitize_veo_prompt("Werner reads a ledger at the oak counter", for_video=False)
    assert "Clean flat vector cartoon illustration" in prompt
    assert "Scene: Werner reads a ledger at the oak counter" in prompt
    assert "Motion:" not in prompt
    assert "no audio" not in prompt.lower()
    assert "animation" not in prompt.lower()


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


def test_ensure_style_prefixes_prompts_that_lost_the_style():
    from services.algorithm_qa import PHOTO_KEYWORDS, STYLE_KEYWORDS
    from services.prompt_sanitizer import ensure_style_prefix

    styled = ensure_style_prefix("Extreme close-up of a chalkboard with numbers")

    assert any(k in styled.lower() for k in STYLE_KEYWORDS)
    assert not any(k in styled.lower() for k in PHOTO_KEYWORDS)


def test_ensure_style_strips_a_negated_photoreal_phrase():
    from services.algorithm_qa import PHOTO_KEYWORDS
    from services.prompt_sanitizer import ensure_style_prefix

    styled = ensure_style_prefix(
        "A whimsical hand-drawn crayon cartoon: dream house, flat 2D, no photorealistic details"
    )

    assert "no photorealistic" not in styled.lower()
    assert not any(k in styled.lower() for k in PHOTO_KEYWORDS)
    assert styled.startswith("A whimsical hand-drawn crayon cartoon")
