"""Unit tests for Phase 1 zero-fake + dynamic duration in ImageKenBurnsProvider."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.image_kenburns_provider import CRAYON_STYLE_PREFIX, ImageKenBurnsProvider
from services.video_provider import ErrorKind


@pytest.fixture
def kb_provider():
    # Avoid constructing a real GeminiWebApiProvider; it is unused when no
    # session file is supplied.
    return ImageKenBurnsProvider(fallback_provider=MagicMock())


def test_no_provider_fails_loudly_without_placeholder(kb_provider, tmp_path, monkeypatch):
    """Zero-fake policy: with no Imagen session, no ref image and no OPENAI key,
    generation must FAIL with FATAL — never render a placeholder caption card."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "services.xkiro_image_provider.generate_xkiro_image",
        lambda prompt, out_path: (_ for _ in ()).throw(RuntimeError("xKiro image down")),
    )

    out = tmp_path / "shots" / "shot_01.mp4"
    res = kb_provider.generate(
        prompt="A banker counting coins",
        out_path=out,
        session_file=None,
        ref_image=None,
    )

    assert res.ok is False
    assert res.error_kind == ErrorKind.FATAL
    assert "No image provider" in res.error_message
    # No placeholder artifacts on disk
    assert not out.exists()
    assert not out.with_suffix(".jpg").exists()


def test_xkiro_image_is_the_primary_provider(kb_provider, tmp_path, monkeypatch):
    """xKiro must carry shots when Gemini cookies are stale and no DALL-E key
    exists (the exact production failure on job remake_1792831087_55d0ea)."""
    out = tmp_path / "shots" / "shot_04.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    prompts = []

    def fake_xkiro(prompt, out_path):
        prompts.append(prompt)
        Path(out_path).write_bytes(b"jpg" * 600)
        return "openai/gpt-image-2.5"

    monkeypatch.setattr("services.xkiro_image_provider.generate_xkiro_image", fake_xkiro)
    monkeypatch.setattr(
        "services.image_kenburns_provider.subprocess.run",
        lambda cmd, check, capture_output, timeout: (
            out.write_bytes(b"x" * 10000) or MagicMock(returncode=0)
        ),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    res = kb_provider.generate(
        prompt="A banker counting coins",
        out_path=out,
        session_file=None,
        ref_image=None,
        duration=6.0,
    )

    assert res.ok is True
    assert len(prompts) == 1
    assert prompts[0].lower().startswith(CRAYON_STYLE_PREFIX.lower()[:20])


def test_ref_image_copy_and_dynamic_kenburns_duration(kb_provider, tmp_path, monkeypatch):
    """When a character reference image exists the Ken Burns render must be
    sized to the shot's planned duration (no hardcoded 10s)."""
    ref = tmp_path / "char_ref.jpg"
    ref.write_bytes(b"fake-jpg-bytes")

    out = tmp_path / "shots" / "shot_02.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    captured_cmds = []

    def fake_run(cmd, check, capture_output, timeout):
        captured_cmds.append(cmd)
        # Simulate ffmpeg producing a valid output file
        out.write_bytes(b"x" * 10000)
        return MagicMock(returncode=0)

    monkeypatch.setattr("services.image_kenburns_provider.subprocess.run", fake_run)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    duration = 7.0
    res = kb_provider.generate(
        prompt="A banker counting coins",
        out_path=out,
        session_file=None,
        ref_image=ref,
        duration=duration,
    )

    assert res.ok is True
    assert res.video_path == out
    assert len(captured_cmds) == 1

    frames = int(duration * 25)  # 175
    vf = next(c for c in captured_cmds[0] if c.startswith("zoompan="))
    assert f"d={frames}" in vf
    assert f"zoom+{0.15 / frames:.6f}" in vf
    assert f"{duration:.2f}" in captured_cmds[0]


def test_duration_clamped_between_2_and_15(kb_provider, tmp_path, monkeypatch):
    ref = tmp_path / "char_ref.jpg"
    ref.write_bytes(b"fake-jpg-bytes")
    out = tmp_path / "shots" / "shot_03.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    captured_cmds = []

    def fake_run(cmd, check, capture_output, timeout):
        captured_cmds.append(cmd)
        out.write_bytes(b"x" * 10000)
        return MagicMock(returncode=0)

    monkeypatch.setattr("services.image_kenburns_provider.subprocess.run", fake_run)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    res = kb_provider.generate(
        prompt="scene", out_path=out, session_file=None, ref_image=ref, duration=60.0
    )
    assert res.ok is True
    # 60s clamped to 15s -> 375 frames
    assert "d=375" in next(c for c in captured_cmds[0] if c.startswith("zoompan="))


def test_crayon_style_prefix_blocks_photorealism():
    low = CRAYON_STYLE_PREFIX.lower()
    assert "crayon" in low
    assert "no photorealism" in low
