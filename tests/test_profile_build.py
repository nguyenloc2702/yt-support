"""Tests for building reference_profile.json from measured reports."""

import json

import numpy as np
import pytest

from core.smoothness.metrics import SCHEMA_VERSION
from tools.profile_build import build_profile


def _report(rid, cuts_per_min, shot_median, strong_local, music, lufs):
    return {
        "schema_version": SCHEMA_VERSION,
        "id": rid,
        "video": {
            "fps": 30.0,
            "cuts_per_min": cuts_per_min,
            "motion": {
                "shot_dur_s": {"median": shot_median, "p90": shot_median + 1.5},
                "camera": {
                    "trans_px_s_median": 9.0,
                    "zoom_pct_s_median": 1.2,
                    "share_trans_gt2px_s": 0.72,
                    "share_zoom_gt0_5pct_s": 0.58,
                },
                "local_motion": {
                    "share_static_rawdiff_lt0_5": 0.15,
                    "share_rigid_residual_lt2": 0.33,
                    "share_local_residual_2to6": 0.49,
                    "share_strong_local_residual_ge6": strong_local,
                },
            },
        },
        "audio": {
            "looks_like_music_bed": music,
            "integrated_lufs": lufs,
            "lra_lu": 3.9,
            "side_minus_mid_mean_db": -15.5,
            "side_minus_mid_when_quiet_db": -4.8,
            "side_active_share": 0.73,
        },
    }


def test_build_profile_aggregates_percentiles(tmp_path):
    refs = tmp_path / "refs"
    for rid, cuts, dur, strong, music, lufs in [
        ("a", 10.0, 5.0, 0.18, True, -18.0),
        ("b", 12.0, 4.4, 0.12, False, -20.0),
    ]:
        (refs / rid).mkdir(parents=True)
        (refs / rid / "report.json").write_text(
            json.dumps(_report(rid, cuts, dur, strong, music, lufs)), encoding="utf-8")

    profile = build_profile(refs)

    assert profile["count"] == 2
    assert profile["refs"] == ["a", "b"]
    assert profile["music_bed_share"] == pytest.approx(0.5)
    assert profile["fps_mode"] == 30.0

    cuts = profile["metrics"]["cuts_per_min"]
    expected = np.percentile([10.0, 12.0], [10, 50, 90])
    assert cuts["p10"] == pytest.approx(expected[0], abs=1e-3)
    assert cuts["p50"] == pytest.approx(expected[1], abs=1e-3)
    assert cuts["p90"] == pytest.approx(expected[2], abs=1e-3)

    band = profile["bands"]["share_strong_local"]
    assert 0.0 <= band["low"] <= band["high"] <= 1.0


def test_build_profile_skips_foreign_schema(tmp_path):
    refs = tmp_path / "refs"
    (refs / "bad").mkdir(parents=True)
    (refs / "bad" / "report.json").write_text(json.dumps({"schema_version": 999}), encoding="utf-8")

    profile = build_profile(refs)

    assert profile["count"] == 0
    assert profile["metrics"] == {}
