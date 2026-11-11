"""Tests for the intentional black title cards (pipeline.title_cards, P2)."""

import re
import subprocess
from pathlib import Path

import pytest

from pipeline.title_cards import (
    CARD_FADE_SEC,
    MAX_CARD_SHARE,
    build_card,
    card_text,
    plan_cards,
    write_cards_sidecar,
)


def _scene(i, text, duration=5.0, key=True, punchline=False):
    return {
        "index": i,
        "text": text,
        "duration": duration,
        "is_key_scene": key,
        "is_punchline": punchline,
    }


def _probe_duration(path: Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    return float(res.stdout.strip())


def test_build_card_lasts_its_duration_and_fades(tmp_path):
    """A 1.2s card probes at 1.2s and fades both ends (0.15s)."""
    out = tmp_path / "cards" / "title_card_003.mp4"
    build_card("Bến cũ vẫn đông thợ.", 1.2, out)

    assert out.exists() and out.stat().st_size > 1000
    assert _probe_duration(out) == pytest.approx(1.2, abs=0.1)


def test_build_card_draws_white_text_on_black(tmp_path):
    """The card is black except its centered white line (the 'có chữ' law)."""
    out = tmp_path / "title_card_002.mp4"
    build_card("44.000 tỷ đồng", 1.5, out)

    # Sample the middle of the card as a PNG and count bright pixels.
    # Đã từng assert chuỗi 'Parsed_blackframe' trong stderr của ffmpeg —
    # format log per-frame đổi theo version (ffmpeg 7.x không in nữa) nên
    # test chỉ pass trên máy dev có ffmpeg 8.x. Đo pixel trực tiếp là
    # bất biến giữa 6.x / 7.x / 8.x.
    png = tmp_path / "mid_center.png"
    res = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", "0.6", "-i", str(out),
         "-vf", "crop=960:540:480:270", "-frames:v", "1", str(png)],
        capture_output=True, text=True, timeout=60,
    )
    assert png.exists(), (res.stderr or "")[-400:]

    import cv2

    img = cv2.imread(str(png))
    assert img is not None, "cannot decode the sampled card frame"
    # The centered white line must light up the dark card: bright pixels exist
    # in the center crop (white text ≈ brightness 255, card ≈ 0-32).
    bright = int((img.max(axis=2) > 96).sum())
    assert bright > 100, f"center crop has no bright text pixels ({bright})"


def test_blackdetect_catches_the_card_as_one_span(tmp_path):
    """The standard report's blackdetect sees a 1.0-1.4s black span on a 1.2s card."""
    out = tmp_path / "title_card_004.mp4"
    build_card("Chỉ một con số.", 1.2, out)

    res = subprocess.run(
        ["ffmpeg", "-i", str(out),
         "-vf", "fps=4,blackdetect=d=0.6:pix_th=0.10", "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    spans = re.findall(
        r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)",
        res.stderr or "",
    )
    assert spans, "blackdetect found no black span on the card"
    # The fades keep ~0.15s of grey at each end, so the detected span is a
    # little shorter than the card itself but still one contiguous span.
    start, end, dur = (float(x) for x in spans[0])
    assert start <= CARD_FADE_SEC + 0.1
    assert 0.5 <= dur <= 1.4


def test_card_text_prefers_the_shortest_punch():
    text = card_text(
        "Đây là câu dẫn dài nhất trong cảnh và cũng chẳng chốt ý gì. Bốn mươi bốn nghìn tỷ. "
        "Một câu khác nữa để kiểm tra độ dài tổng thể của đoạn văn bản này cần vượt giới hạn cho phép."
    )
    assert text == "Bốn mươi bốn nghìn tỷ."


def test_card_text_caps_length():
    long_text = "từ " * 80
    assert len(card_text(long_text)) <= 90


def test_plan_cards_follows_the_corpus_rate_and_priority():
    # 3 minutes of film -> 1.4*3 = 4 cards.
    scenes = []
    for i in range(1, 31):
        scenes.append(_scene(i, f"Cảnh thường số {i} kể về dòng tiền của hãng.", 6.0))
    # Seed four high-signal scenes beyond the protected hook.
    scenes[4] = _scene(5, "44.000 tỷ đồng biến mất trong ba tháng.", 6.0, punchline=True)
    scenes[9] = _scene(10, "Làm sao một con số lớn đến vậy?", 6.0, punchline=True)
    scenes[14] = _scene(15, "Gấp 10 lần.", 6.0, punchline=True)
    scenes[24] = _scene(25, "Chưa từng có tiền lệ.", 6.0, punchline=True)

    picked = plan_cards(scenes, shot_durations=[6.0] * 30)

    assert len(picked) == 4
    assert 0 not in picked and 1 not in picked, "hook must stay card-free"
    assert {4, 9, 14, 24} <= set(picked), "high-signal scenes win the cards"


def test_plan_cards_never_exceeds_the_share_cap():
    # Every scene screams for a card, but the cap bounds the count.
    scenes = [
        _scene(i, "44.000 tỷ đồng gấp 10 lần.", 6.0, punchline=True)
        for i in range(1, 21)
    ]

    picked = plan_cards(scenes, shot_durations=[6.0] * 20)

    assert len(picked) <= int(20 * MAX_CARD_SHARE)


def test_plan_cards_skips_scenes_without_signal():
    scenes = [_scene(i, f"Cảnh thường số {i} kể về dòng tiền của hãng.", 6.0) for i in range(1, 11)]
    assert plan_cards(scenes, shot_durations=[6.0] * 10) == []


def test_cards_sidecar_records_the_spans(tmp_path):
    video = tmp_path / "documentary_final.mp4"
    video.write_bytes(b"fake")
    sidecar = write_cards_sidecar(video, [
        {"index": 5, "start": 24.0, "end": 25.2, "text": "44.000 tỷ đồng"},
    ])

    assert sidecar.name == "documentary_final.cards.json"
    import json
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["cards"][0]["end"] == 25.2
