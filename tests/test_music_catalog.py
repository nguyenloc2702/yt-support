import numpy as np

from tools.music_catalog import (
    character_tag,
    find_regions,
    role_for_position,
    select_previews,
)
from tools.bgm_pack import score_piece, ROLES


def test_find_regions_keeps_long_beds_and_drops_blips():
    db = np.full(4000, -90.0)
    db[400:2000] = -45.0          # 100..500 s bed
    db[2200:2230] = -45.0         # 550..557 s blip, too short
    regions = find_regions(db)
    assert len(regions) == 1
    start, end = regions[0]
    assert 99.0 <= start <= 101.0
    assert 499.0 <= end <= 501.0


def test_find_regions_bridges_short_dropouts():
    db = np.full(1000, -90.0)
    db[100:300] = -45.0
    db[300:308] = -90.0           # 2 s gap inside the bed
    db[308:500] = -45.0
    regions = find_regions(db)
    assert len(regions) == 1
    assert regions[0][1] >= 124.0


def test_role_for_position_maps_story_arc():
    assert role_for_position(0.02) == "hook"
    assert role_for_position(0.15) == "setup"
    assert role_for_position(0.45) == "rising"
    assert role_for_position(0.70) == "crisis"
    assert role_for_position(0.88) == "breakthrough"
    assert role_for_position(0.99) == "payoff"


def _feat(centroid, low, onsets, key="A minor"):
    return {"centroid_hz": centroid, "low_ratio": low, "onsets_per_s": onsets, "key": key}


def test_character_tag_buckets():
    assert character_tag(_feat(2400, 0.2, 3.5), 125) == "uptempo / percussive"
    assert character_tag(_feat(800, 0.5, 0.4), 70) == "dark / drone / tense"
    assert character_tag(_feat(1000, 0.2, 0.5, "C major"), 80) == "calm / reflective"
    assert character_tag(_feat(2200, 0.1, 2.0), 105) == "quirky / plucky"


def test_select_previews_prefers_episode_diversity_and_prominence():
    def region(role, clip, prominence, dur=40):
        return {"role": role, "clip_id": clip, "side_minus_mid_db": prominence,
                "duration_s": dur, "start_s": 0.0, "end_s": dur}

    regions = [region("crisis", "a", -20), region("crisis", "a", -18),
               region("crisis", "b", -22), region("rising", "a", -19),
               region("rising", "b", -21), region("rising", "c", -23)]
    picks = select_previews(regions, per_role=2)
    crisis = [p for p in picks if p["role"] == "crisis"]
    rising = [p for p in picks if p["role"] == "rising"]
    assert len(crisis) == 2 and len(rising) == 2
    assert {p["clip_id"] for p in crisis} == {"a", "b"}
    assert {p["clip_id"] for p in rising} == {"a", "b"}


def test_bgm_pack_scoring_rejects_holiday_and_too_long():
    role = ROLES["triumphant"]
    piece = {"title": "We Wish you a Merry Christmas", "feel": "Bright, Uplifting",
             "bpm": "110", "length": "00:03:00", "instruments": "Strings"}
    assert score_piece(piece, role) == -1.0
    long_piece = {"title": "Epic Long", "feel": "Epic, Uplifting",
                  "bpm": "110", "length": "00:07:00", "instruments": "Strings"}
    assert score_piece(long_piece, role) == -1.0
    good = {"title": "At Launch", "feel": "Epic, Uplifting",
            "bpm": "110", "length": "00:03:00", "instruments": "Strings, Brass"}
    assert score_piece(good, role) > 5.0
