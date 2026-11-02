import pytest
from pathlib import Path

from pipeline.bgm_manager import BGMManager, BEAT_ROLE_TO_MOOD, mood_family


def _touch(d: Path, name: str) -> None:
    (d / name).write_bytes(b"not really audio")


def test_mood_family_collapses_legacy_tokens():
    assert mood_family("mysterious") == "mysterious"
    assert mood_family("mysterious_pizzicato") == "mysterious"
    assert mood_family("reflective_guitar") == "reflective"
    assert mood_family("") == ""


def test_legacy_role_tokens_resolve_to_pack_files(tmp_path):
    _touch(tmp_path, "mysterious__a.mp3")
    _touch(tmp_path, "mysterious__b.mp3")
    _touch(tmp_path, "ominous__c.mp3")
    mgr = BGMManager(bgm_dir=tmp_path)

    assert mgr._find_bgm_file("mysterious").name.startswith("mysterious__")
    picked = mgr._find_bgm_file("mysterious_pizzicato", key="beat_hook")
    assert picked is not None and picked.name.startswith("mysterious__")
    assert mgr._find_bgm_file("ominous_cello", key="beat_crisis").name == "ominous__c.mp3"


def test_rotation_is_stable_per_seed_and_key(tmp_path):
    for i in range(5):
        _touch(tmp_path, f"energetic__{i}.mp3")
    a = BGMManager(bgm_dir=tmp_path, rotation_seed="job-42")
    b = BGMManager(bgm_dir=tmp_path, rotation_seed="job-42")

    first = a._find_bgm_file("energetic_bassline", key="beat_rising")
    assert first == b._find_bgm_file("energetic_bassline", key="beat_rising")
    assert first.parent == tmp_path

    picks = {a._find_bgm_file("energetic_bassline", key=f"beat_{i}").name for i in range(5)}
    assert len(picks) > 1


def test_every_beat_role_resolves_when_pack_is_present(tmp_path):
    families = {mood_family(mood) for mood in BEAT_ROLE_TO_MOOD.values()}
    for family in families:
        for i in range(3):
            _touch(tmp_path, f"{family}__{i}.mp3")

    mgr = BGMManager(bgm_dir=tmp_path)
    for role, mood in BEAT_ROLE_TO_MOOD.items():
        picked = mgr._find_bgm_file(mood, key=f"beat_{role}")
        assert picked is not None, f"{role}/{mood} did not resolve"
        assert picked.name.startswith(mood_family(mood) + "__")


def test_shot_duration_plan_follows_the_story_arc(tmp_path):
    for family in {mood_family(mood) for mood in BEAT_ROLE_TO_MOOD.values()}:
        _touch(tmp_path, f"{family}__x.mp3")

    mgr = BGMManager(bgm_dir=tmp_path)
    ids = mgr.create_from_shot_durations([10.0] * 40, 400.0)
    tracks = [mgr.tracks[i] for i in ids]

    assert len(tracks) >= 5
    assert tracks[0].beat_role == "hook"
    assert tracks[-1].beat_role == "payoff"
    assert tracks[0].start_time == 0.0
    assert tracks[-1].end_time == 400.0
    for before, after in zip(tracks, tracks[1:]):
        assert before.end_time <= after.start_time + 1e-6


def test_shot_duration_plan_skips_families_without_files(tmp_path):
    _touch(tmp_path, "mysterious__x.mp3")
    mgr = BGMManager(bgm_dir=tmp_path)
    ids = mgr.create_from_shot_durations([10.0] * 10, 100.0)
    assert len(ids) == 1
    assert mgr.tracks[ids[0]].beat_role == "hook"


def test_beat_roles_can_reach_every_pack_family():
    from pipeline.bgm_manager import BEAT_ROLE_MOODS
    used = {mood for moods in BEAT_ROLE_MOODS.values() for mood in moods}
    assert used == {"mysterious", "nostalgic", "gentle", "energetic", "quirky",
                    "ominous", "triumphant", "celebratory", "reflective"}


def test_bed_drops_silent_before_the_closing_cue(tmp_path):
    from pipeline.bgm_manager import MUSIC_BREAK_S
    for family in {mood_family(mood) for mood in BEAT_ROLE_TO_MOOD.values()}:
        _touch(tmp_path, f"{family}__x.mp3")
    mgr = BGMManager(bgm_dir=tmp_path)
    ids = mgr.create_from_shot_durations([10.0] * 40, 400.0)
    tracks = [mgr.tracks[i] for i in ids]
    gap = tracks[-1].start_time - tracks[-2].end_time
    assert gap == pytest.approx(MUSIC_BREAK_S)


def test_bed_is_levelled_from_measured_loudness(tmp_path):
    import json
    from pipeline.bgm_manager import BED_TARGET_LUFS, DEFAULT_BGM_VOLUME
    families = {mood_family(mood) for mood in BEAT_ROLE_TO_MOOD.values()}
    for family in families:
        _touch(tmp_path, f"{family}__x.mp3")
    (tmp_path / "LOUDNESS.json").write_text(json.dumps(
        {"tracks": {f"{family}__x.mp3": {"lufs": -12.0, "tp": -0.3} for family in families}}
    ), encoding="utf-8")

    mgr = BGMManager(bgm_dir=tmp_path)
    tracks = [mgr.tracks[i] for i in mgr.create_from_shot_durations([10.0] * 40, 400.0)]
    assert tracks
    for track in tracks:
        assert track.volume_db == pytest.approx(BED_TARGET_LUFS - (-12.0))

    plain = tmp_path / "plain"
    plain.mkdir()
    for family in families:
        _touch(plain, f"{family}__x.mp3")
    unlevelled = BGMManager(bgm_dir=plain)
    ids = unlevelled.create_from_shot_durations([10.0] * 40, 400.0)
    assert ids
    assert all(unlevelled.tracks[i].volume_db == DEFAULT_BGM_VOLUME for i in ids)
