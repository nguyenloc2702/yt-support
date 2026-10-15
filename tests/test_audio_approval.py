# -*- coding: utf-8 -*-
"""Tests for the audio-approval gate and script .txt export."""
import json
from pathlib import Path

import pytest

from core.exceptions import AnalysisError
from services.script_service import ScriptService


@pytest.fixture
def project_with_script(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.settings.projects_dir", str(tmp_path))
    proj = tmp_path / "p1"
    (proj / "scripts").mkdir(parents=True)
    script = {
        "title": "Test script",
        "status": "approved",
        "target_duration_seconds": 60.0,
        "beats": [
            {"id": 1, "text": "Xin chào mọi người.", "emotion": "excited"},
            {"id": 2, "text": "Hẹn gặp lại.", "emotion": "calm"},
        ],
    }
    (proj / "scripts" / "current.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    return proj


def test_export_script_txt(project_with_script):
    path = ScriptService().export_script_txt("p1")
    text = path.read_text(encoding="utf-8")
    assert "Test script" in text
    assert "Xin chào mọi người." in text


def test_export_script_txt_no_script(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.settings.projects_dir", str(tmp_path))
    (tmp_path / "p2" / "scripts").mkdir(parents=True)
    with pytest.raises(AnalysisError):
        ScriptService().export_script_txt("p2")


def test_audio_approval_flow(project_with_script):
    svc = ScriptService()
    assert not svc.is_audio_approved("p1")
    svc.approve_audio("p1")
    assert svc.is_audio_approved("p1")
    data = json.loads((project_with_script / "scripts" / "current.json").read_text(encoding="utf-8"))
    assert data["audio_approved"] is True
    assert data["status"] == "approved"  # status untouched


def test_approve_audio_no_script(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.settings.projects_dir", str(tmp_path))
    (tmp_path / "p3" / "scripts").mkdir(parents=True)
    with pytest.raises(AnalysisError):
        ScriptService().approve_audio("p3")
