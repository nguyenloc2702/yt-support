"""Unit tests for Phase 1 zero-fake + dynamic duration in ImageKenBurnsProvider."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.image_kenburns_provider import (
    CRAYON_STYLE_PREFIX,
    POSES_PER_SHOT,
    POSE_BLEND_SECONDS,
    ImageKenBurnsProvider,
)
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
        lambda prompt, out_path, **kw: (_ for _ in ()).throw(RuntimeError("xKiro image down")),
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
    sizes = []

    def fake_xkiro(prompt, out_path, **kw):
        prompts.append(prompt)
        sizes.append(kw.get("size"))
        Path(out_path).write_bytes(b"jpg" * 600)
        return "openai/gpt-image-2.5"

    monkeypatch.setattr("services.xkiro_image_provider.generate_xkiro_image", fake_xkiro)
    monkeypatch.setattr(
        "services.xkiro_image_provider.edit_xkiro_image",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("edit unavailable")),
    )
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
    # Wide images keep the composition; square ones get vertically cropped.
    assert sizes == ["1792x1024"]


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

    graph = captured_cmds[0][captured_cmds[0].index("-filter_complex") + 1]
    assert "zoompan=" in graph
    assert "s=1920x1080" in graph
    assert "scale=2304:1296" in graph
    assert "crop=2304:1296" in graph
    # 7s -> 175 frames split around the mid-clip reframe cut
    assert "d=87" in graph and "d=88" in graph
    assert f"{duration:.2f}" in captured_cmds[0]


def test_camera_moves_vary_between_shots():
    from services.image_kenburns_provider import build_kenburns_filter

    short = [build_kenburns_filter(seed, 4.0) for seed in range(6)]
    assert len(set(short)) == len(short)
    long = [build_kenburns_filter(seed, 8.0) for seed in range(6)]
    assert len(set(long)) == len(long)
    # Long shots cut to a second framing mid-clip; short ones do not.
    assert all("concat=n=2" in g for g in long)
    assert all("concat=n=2" not in g for g in short)


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
    # 60s clamped to 15s -> 375 frames, split 187/188 around the reframe cut
    graph = captured_cmds[0][captured_cmds[0].index("-filter_complex") + 1]
    assert "d=187" in graph and "d=188" in graph


def test_ink_boil_wobbles_on_a_stepped_hold():
    """Flat explainer art only reads as animated when the ink line moves, so
    every camera move carries a stepped wobble clamped inside the canvas."""
    from services.image_kenburns_provider import BOIL_HOLD_FRAMES, build_kenburns_filter

    graph = build_kenburns_filter(3, 4.0)
    assert f"floor(on/{BOIL_HOLD_FRAMES})" in graph
    assert "min(max(" in graph
    # The wobble differs per shot, otherwise every clip boils in lockstep.
    assert build_kenburns_filter(3, 4.0) != build_kenburns_filter(4, 4.0)


def test_pose_keyframes_become_a_multi_input_blend(kb_provider, tmp_path, monkeypatch):
    """The point of the pose pass: the render must consume several drawings of
    the same character, not one still with a zoom."""
    out = tmp_path / "shots" / "shot_09.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    def fake_xkiro(prompt, out_path, **kw):
        Path(out_path).write_bytes(b"jpg" * 600)
        return "openai/gpt-image-2.5"

    cues = []

    def fake_edit(prompt, source_image, out_path, **kw):
        cues.append(prompt)
        Path(out_path).write_bytes(b"jpg" * 600)
        return "openai/gpt-image-2.5"

    captured = []

    def fake_run(cmd, check, capture_output, timeout):
        captured.append(cmd)
        out.write_bytes(b"x" * 10000)
        return MagicMock(returncode=0)

    monkeypatch.setattr("services.xkiro_image_provider.generate_xkiro_image", fake_xkiro)
    monkeypatch.setattr("services.xkiro_image_provider.edit_xkiro_image", fake_edit)
    monkeypatch.setattr("services.image_kenburns_provider.subprocess.run", fake_run)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    res = kb_provider.generate(
        prompt="Werner pointing at a keyboard",
        out_path=out,
        session_file=None,
        ref_image=None,
        duration=6.0,
    )

    assert res.ok is True
    assert len(cues) == POSES_PER_SHOT - 1
    # Every drawing is fed to ffmpeg as its own input
    assert captured[0].count("-loop") == POSES_PER_SHOT
    graph = captured[0][captured[0].index("-filter_complex") + 1]
    assert f"xfade=transition=fade:duration={POSE_BLEND_SECONDS:.2f}" in graph
    assert "[2:v]" in graph


def test_pose_blend_is_skipped_when_edits_fail(kb_provider, tmp_path, monkeypatch):
    out = tmp_path / "shots" / "shot_10.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    captured = []

    def fake_run(cmd, check, capture_output, timeout):
        captured.append(cmd)
        out.write_bytes(b"x" * 10000)
        return MagicMock(returncode=0)

    monkeypatch.setattr(
        "services.xkiro_image_provider.generate_xkiro_image",
        lambda prompt, out_path, **kw: (Path(out_path).write_bytes(b"jpg" * 600), "m")[1],
    )
    monkeypatch.setattr(
        "services.xkiro_image_provider.edit_xkiro_image",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rate limited")),
    )
    monkeypatch.setattr("services.image_kenburns_provider.subprocess.run", fake_run)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    res = kb_provider.generate(prompt="scene", out_path=out, session_file=None, ref_image=None)

    assert res.ok is True  # one drawing still renders, no fake failure
    assert captured[0].count("-loop") == 1


def test_crayon_style_prefix_blocks_photorealism():
    low = CRAYON_STYLE_PREFIX.lower()
    assert "crayon" in low
    assert "stick figure" in low
    assert "no photorealism" in low
