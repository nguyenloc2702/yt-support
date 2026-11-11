"""Appendix A + §2.3 transition law + canonical hash contract (§3.2.5)."""
import pytest

from backend.app import domain


def test_no_jump_over_qc():
    # generated → appended is ILLEGAL: must pass qc_passed first
    with pytest.raises(domain.TransitionError):
        domain.assert_shot_transition("generated", "appended")
    # keyframe_approved → generated (skipping the animate step bookkeeping) illegal
    with pytest.raises(domain.TransitionError):
        domain.assert_shot_transition("keyframe_approved", "appended")


def test_appended_and_superseded_terminal():
    assert domain.SHOT_TRANSITIONS[domain.ShotStatus.APPENDED] == set()
    assert domain.SHOT_TRANSITIONS[domain.ShotStatus.SUPERSEDED] == set()
    with pytest.raises(domain.TransitionError):
        domain.assert_shot_transition("appended", "held")


def test_held_retry_groups():
    # keyframe-group held → only back to keyframe_pending (A12)
    # video-group held → only to generating (A13)
    held_targets = domain.SHOT_TRANSITIONS[domain.ShotStatus.HELD]
    assert domain.ShotStatus.KEYFRAME_PENDING in held_targets
    assert domain.ShotStatus.GENERATING in held_targets
    # held never auto-escapes to qc_passed or appended
    assert domain.ShotStatus.QC_PASSED not in held_targets
    assert domain.ShotStatus.APPENDED not in held_targets


def test_item_transitions_no_status_mixture():
    # awaiting_review is the single gate-pending value; item cannot be
    # script_ready AND audio_ready (plan §2.1 review note)
    assert "script_ready" not in [s.value for s in domain.ItemStatus]
    assert "audio_ready" not in [s.value for s in domain.ItemStatus]
    with pytest.raises(domain.TransitionError):
        domain.assert_item_transition("awaiting_review", "exported")
    with pytest.raises(domain.TransitionError):
        domain.assert_item_transition("exported", "generating")


def test_text_only_is_not_a_done_engine():
    assert "text_only" not in domain.ALLOWED_DONE_ENGINES


def test_held_reason_groups():
    assert domain.HeldReason("REF_IMAGE_MISSING").group == "keyframe"
    assert domain.HeldReason("DURATION_MISMATCH").group == "video"
    assert domain.HeldReason("CHARACTER_COLOR_SUSPECT").group == "human"
    assert domain.HeldReason("TIMECODE_SHIFTED").group == "workflow"
    # every reason has a Vietnamese label (DESIGN.md requirement)
    for r in domain.HeldReason:
        assert r.value in domain.HELD_LABELS_VI


def test_canonical_hash_stable_under_key_order_and_rounding():
    a = [{"index": 1, "narration": "x", "audio_start_sec": 0.0,
          "audio_end_sec": 7.2, "visual_prompt_clean": "p",
          "on_screen_text": None, "on_screen_type": None,
          "keyframe_sha256": None}]
    # same content, different insertion order + extra float noise
    b = [{"visual_prompt_clean": "p", "on_screen_type": None,
          "audio_end_sec": 7.20000001, "narration": "x",
          "keyframe_sha256": None, "index": 1,
          "audio_start_sec": 0.00000001, "on_screen_text": None}]
    assert domain.canonical_shots_hash(a) == domain.canonical_shots_hash(b)
    # different content → different hash
    c = [dict(a[0], narration="y")]
    assert domain.canonical_shots_hash(a) != domain.canonical_shots_hash(c)
