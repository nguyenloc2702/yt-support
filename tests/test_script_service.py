"""Unit tests for ScriptService status machine transitions."""

import json

import pytest

from core.exceptions import AnalysisError, ScriptNotApprovedError
from services.script_service import ScriptService

TRANSCRIPT = {
    "segments": [
        {"id": 0, "start": 0.0, "end": 3.0, "text": "hello"},
        {"id": 1, "start": 3.5, "end": 8.0, "text": "world"},
    ]
}


@pytest.fixture
def project_with_script(tmp_path, monkeypatch, isolated_db):
    monkeypatch.setattr(
        "config.settings.settings.projects_dir", str(tmp_path), raising=False
    )
    proj = tmp_path / "p1"
    (proj / "analysis").mkdir(parents=True)
    (proj / "analysis" / "transcription.json").write_text(
        json.dumps(TRANSCRIPT), encoding="utf-8"
    )
    (proj / "scripts").mkdir()
    (proj / "scripts" / "current.json").write_text(
        json.dumps(
            {
                "title": "T",
                "tone": "bựa",
                "status": "draft",
                "beats": [
                    {"id": 0, "text": "A", "source_segment_ids": [0]},
                    {"id": 1, "text": "B", "source_segment_ids": [1]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return proj


def test_full_flow_edited_approved(tmp_path, monkeypatch, project_with_script):
    monkeypatch.setattr(
        "config.settings.settings.projects_dir", str(tmp_path), raising=False
    )
    svc = ScriptService()

    # draft → gate blocks
    with pytest.raises(ScriptNotApprovedError):
        svc.build_script_timeline("p1")

    # edit → status 'edited', still blocked
    beats = svc.get_script("p1")["beats"]
    beats[0]["text"] = "A edited"
    script = svc.save_edits("p1", beats, title="T2")
    assert script["status"] == "edited"
    assert script["title"] == "T2"
    with pytest.raises(ScriptNotApprovedError):
        svc.build_script_timeline("p1")

    # reject → blocked
    svc.reject("p1")
    assert svc.get_status("p1") == "rejected"
    with pytest.raises(ScriptNotApprovedError):
        svc.build_script_timeline("p1")

    # approve → gate passes
    svc.approve("p1")
    timeline = svc.build_script_timeline("p1")
    assert timeline["mode"] == "script_review"
    assert len(timeline["clips"]) == 2
    assert timeline["clips"][0]["label"] == "script_beat"


def test_missing_script_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "config.settings.settings.projects_dir", str(tmp_path), raising=False
    )
    svc = ScriptService()
    assert svc.get_script("nothing") is None
    assert svc.get_status("nothing") is None
    with pytest.raises(AnalysisError):
        svc.approve("nothing")
    with pytest.raises(ScriptNotApprovedError):
        svc.build_script_timeline("nothing")


def test_missing_transcript_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "config.settings.settings.projects_dir", str(tmp_path), raising=False
    )
    (tmp_path / "p2" / "scripts").mkdir(parents=True)
    (tmp_path / "p2" / "scripts" / "current.json").write_text(
        json.dumps({"status": "approved", "beats": []}), encoding="utf-8"
    )
    svc = ScriptService()
    with pytest.raises(AnalysisError):
        svc.build_script_timeline("p2")
