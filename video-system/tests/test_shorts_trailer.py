# ruff: noqa: E501
"""Mode "trailer" — teaser 30-45s cho video dài EN explainer.

Thiết kế 3 bên PO-BA-Dev 2026-11-22 (xem plans/README.md):
- Formula: hook định vị mạnh nhất (số nguyên văn) → 2-3 beat tease KHÔNG spoil
  → closing CTA bắt buộc ("full video" + "on the channel"/"link in bio").
- Config: 38s, 70-105 từ EN, hero_images 2, voice Brian.
- Validate fail-closed: thiếu CTA hoặc >3 beats là từ chối.
"""
from __future__ import annotations

import pytest

from backend.app import providers
from backend.app.pipeline_short import prompts


def trailer_script_en() -> dict:
    """~88 từ EN, đúng ngân sách 70-105, CTA đủ keyword, ≤3 beats."""
    hook = "Apple's services business just crossed one hundred billion dollars a year"
    beats = [
        "But buried in the earnings call was a warning about where that growth actually comes from",
        "And the man who built the App Store quietly changed one rule nobody noticed",
        "The next five minutes explain why developers call it the silent tax",
    ]
    closing = "The full video on the channel breaks down every number — watch it before Apple's next earnings call."
    texts = [hook] + beats + [closing]
    wc = sum(len(t.split()) for t in texts)
    return {
        "title": "Trailer: Apple services — the silent tax",
        "hook": hook,
        "beats": beats,
        "closing": closing,
        "search_keywords_en": ["apple headquarters", "tim cook keynote", "app store phone"],
        "hashtags": {"tiktok": ["#apple", "#tech"], "facebook": ["#apple"], "youtube": ["#shorts"]},
        "description": "The full Apple services breakdown is on the channel.",
        "source_note": "",
        "word_count": wc,
    }


# --- prompt + config -----------------------------------------------------------

def test_system_prompt_trailer_en_has_formula_and_cta_rules():
    sys_txt = prompts.system_prompt("trailer", "en")
    assert "trailer" in sys_txt.lower()
    assert "full video" in sys_txt.lower()          # CTA bắt buộc trong template
    assert "NEVER reveal the ending" in sys_txt     # luật anti-spoil
    assert "EXACTLY from the script" in sys_txt     # luật số nguyên văn


def test_system_prompt_trailer_vi_fails_closed():
    with pytest.raises(ValueError, match="trailer"):
        prompts.system_prompt("trailer", "vi")


def test_trailer_budget_from_config():
    assert prompts._budget_en("trailer") == (70, 105)
    cfg = prompts.SHORT_CONFIG["modes"]["trailer"]
    assert cfg["target_seconds"] == 38
    assert cfg["hero_images"] == 2


# --- validate_script -----------------------------------------------------------

def _base_script() -> dict:
    s = trailer_script_en()
    s.pop("word_count", None)
    return s


def test_validate_trailer_ok_and_trims_beats():
    s = _base_script()
    s["beats"] = s["beats"] + ["This extra beat must be cut"]  # 4 beats → cắt 3
    clean = prompts.validate_script("trailer", s, lang="en")
    assert len(clean["beats"]) == 3
    assert clean["word_count"] <= 105


def test_validate_trailer_missing_cta_rejected():
    s = _base_script()
    s["closing"] = "And that's why services are Apple's engine of growth."
    with pytest.raises(ValueError, match="CTA"):
        prompts.validate_script("trailer", s, lang="en")


def test_validate_trailer_cta_bio_variant_accepted():
    s = _base_script()
    # closing thay thế phải đủ dài để tổng từ vẫn trong ngân sách 70-105
    s["closing"] = ("The full video on Apple's silent tax services business "
                    "breaks down every single hidden number in the filing — link in bio right now.")
    clean = prompts.validate_script("trailer", s, lang="en")
    assert "full video" in clean["closing"].lower()
    assert "bio" in clean["closing"].lower()


# --- API flow ------------------------------------------------------------------

def test_create_trailer_short_via_api(client, ingest, short_adapters):
    short_adapters["llm"].script = trailer_script_en()
    r = client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "trailer",
        "lang": "en",
        "source_text": "Long video script: Apple services crossed $100B/year... (dán script video dài)",
        "source_link": "https://www.youtube.com/watch?v=abc123",
        "source_note": "",
    })
    assert r.status_code == 201, r.text
    short = r.json()
    assert short["mode"] == "trailer"
    assert short["lang"] == "en"
    assert short["script"]["lang"] == "en"
    # LLM nhận đúng system prompt trailer (không phải news/decode)
    sys_txt = short_adapters["llm"].calls[0]["system"]
    assert "trailer" in sys_txt.lower()
    assert "vertical short-form news" not in sys_txt
    # CTA sống sót qua validate vào script lưu DB
    assert "full video" in short["script"]["closing"].lower()


def test_create_trailer_unknown_mode_rejected(client, ingest):
    r = client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "teaser",
        "lang": "en",
        "source_text": "x",
    })
    assert r.status_code == 422  # schema regex chặn trước khi tới service


def test_create_trailer_budget_violation_rejected(client, ingest, short_adapters):
    bad = trailer_script_en()
    bad["hook"] = "Too short"  # tổng từ rơi dưới 70
    bad["beats"] = ["One tiny beat."]
    bad["closing"] = "Watch the full video on the channel."
    short_adapters["llm"].script = bad
    r = client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "trailer",
        "lang": "en",
        "source_text": "Long video script paste...",
    })
    assert r.status_code == 422
    assert "ngân sách" in r.text  # fail-closed message tiếng người trả về client
