import pytest
from pathlib import Path
from pipeline.bgm_manager import BGMManager
from pipeline.sfx_manager import SFXManager
from pipeline.character_manager import CharacterManager


def test_bgm_manager_add_and_create():
    mgr = BGMManager()
    sentences = [
        {"text": "Hook paradox", "start": 0.0, "end": 15.0, "beat_role": "hook"},
        {"text": "Setup story", "start": 15.0, "end": 60.0, "beat_role": "setup"},
        {"text": "Rising tension", "start": 60.0, "end": 120.0, "beat_role": "rising"},
    ]
    track_ids = mgr.create_from_algorithm_result(sentences, total_duration=120.0)
    assert len(track_ids) == 3
    assert "beat_hook" in mgr.tracks
    assert mgr.tracks["beat_hook"].start_time == 0.0
    assert mgr.tracks["beat_hook"].end_time == 15.0
    # Ensure path is not double-nested
    path_str = str(mgr.tracks["beat_hook"].path)
    assert path_str.count("assets\\music\\bgm") <= 1 or path_str.count("assets/music/bgm") <= 1


def test_sfx_manager_text_and_shot_triggers():
    mgr = SFXManager()
    events_created = mgr.create_from_algorithm_result(
        sentences=[
            {"text": "He made a billion dollars from this venture.", "start": 12.0},
            {"text": "Then a dramatic crisis occurred.", "start": 35.0},
        ],
        shots=[
            {"start": 0.0, "scene_description": "Animated chart with money and rocket launch"}
        ],
        metaphors=[
            {"text": "It was like throwing cash into a furnace", "timestamp": 50.0}
        ],
        humor_lines=[
            {"text": "And nobody noticed the huge mistake.", "timestamp": 70.0}
        ]
    )
    assert events_created > 0
    assert len(mgr.events) > 0
    # Verify events have valid timestamps and types
    for evt in mgr.events.values():
        assert evt.timestamp >= 0.0
        assert evt.sfx_type != ""


def test_character_manager_registration():
    mgr = CharacterManager()
    cid = mgr.create_character(
        name="Jensen Huang",
        description="CEO in black leather jacket"
    )
    assert cid is not None
    char = mgr.get_character(cid)
    assert char is not None
    assert char.name == "Jensen Huang"
