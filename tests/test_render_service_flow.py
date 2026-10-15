"""Service-level mock tests for RenderService render flows.

Verifies the contract between service orchestration and the render pipeline:
- Test preview slices the timeline by output range and passes the derived
  timeline plus output offset to render_video.
- Preview and final resolve the same (requested or current) configuration
  version and pass it through.
- DB render rows are created and transitioned to COMPLETED.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.exceptions import RenderError
from core.render_schemas import TestPreviewOptions
from services.render_service import RenderService

# Prevent pytest from trying to collect the imported pydantic model as a
# test class ("cannot collect test class 'TestPreviewOptions'").
TestPreviewOptions.__test__ = False


def _script_led_timeline():
    return {
        "version": 2,
        "output_aspect_ratio": "16:9",
        "clips": [
            {
                "id": "b0",
                "source_start": 10.0,
                "source_end": 40.0,
                "duration": 30.0,
                "output_start": 0.0,
                "output_end": 30.0,
                "beat_id": 0,
                "narration_text": "Beat một",
                "order": 1,
                "enabled": True,
            },
            {
                "id": "b1",
                "source_start": 200.0,
                "source_end": 230.0,
                "duration": 30.0,
                "output_start": 30.0,
                "output_end": 60.0,
                "beat_id": 1,
                "narration_text": "Beat hai",
                "order": 2,
                "enabled": True,
            },
        ],
    }


def _make_service(isolated_db, monkeypatch, tmp_path):
    """RenderService against an isolated DB + temp project dir with a fake source."""
    svc = RenderService()
    proj_id = "p_test"
    proj_dir = Path(svc.projects_dir) / proj_id
    (proj_dir / "source").mkdir(parents=True, exist_ok=True)
    source = proj_dir / "source" / "input.mp4"
    source.write_bytes(b"0" * 1024)
    return svc, proj_id, proj_dir


def _stub_config(svc, monkeypatch):
    config = SimpleNamespace(version=3)
    monkeypatch.setattr(
        svc.config_service, "get_current", lambda pid: config, raising=True
    )
    monkeypatch.setattr(
        svc.config_service, "get_version", lambda pid, v: config if v == 3 else None,
        raising=True,
    )
    return config


def _stub_render_video(monkeypatch, calls):
    def fake_render_video(**kwargs):
        calls.append(kwargs)
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"0" * 256)
        return out

    monkeypatch.setattr("services.render_service.render_video", fake_render_video)


def _stub_cleanup(monkeypatch):
    monkeypatch.setattr(
        "services.render_service.cleanup_render_temp", lambda *_: None, raising=True
    )
    monkeypatch.setattr(
        "services.render_service.cleanup_stale_previews", lambda *_: None, raising=True
    )


def _stub_disk_guard(monkeypatch):
    monkeypatch.setattr(
        "services.render_service.ensure_free_space", lambda *_: None, raising=True
    )


class TestRenderTestPreview:
    def test_slices_by_output_range_and_passes_offset(self, isolated_db, monkeypatch):
        svc, project_id, _ = _make_service(isolated_db, monkeypatch, isolated_db)
        _stub_config(svc, monkeypatch)
        _stub_disk_guard(monkeypatch)
        _stub_cleanup(monkeypatch)
        calls: list = []
        _stub_render_video(monkeypatch, calls)

        out = svc.render_test_preview(
            project_id=project_id,
            timeline=_script_led_timeline(),
            test_options=TestPreviewOptions(output_start_seconds=25.0, duration_seconds=15.0),
        )

        assert out.exists()
        assert len(calls) == 1
        kwargs = calls[0]
        derived = kwargs["timeline"]
        # Derived preview: slice 25-40s of the 60s output timeline.
        assert derived["is_derived_preview"] is True
        assert derived["actual_duration"] == pytest.approx(15.0)
        assert kwargs["output_timeline_offset_seconds"] == 25.0
        # Rebasing: every slice clip output window starts from 0.
        assert derived["clips"][0]["output_start"] == pytest.approx(0.0)
        # Source mapping must not bridge the 40s->200s source gap.
        assert derived["clips"][0]["source_end"] == pytest.approx(40.0)
        assert derived["clips"][1]["source_start"] == pytest.approx(200.0)

    def test_renders_with_requested_config_version(self, isolated_db, monkeypatch):
        svc, project_id, _ = _make_service(isolated_db, monkeypatch, isolated_db)
        _stub_config(svc, monkeypatch)
        _stub_disk_guard(monkeypatch)
        _stub_cleanup(monkeypatch)
        calls: list = []
        _stub_render_video(monkeypatch, calls)

        svc.render_test_preview(
            project_id=project_id,
            timeline=_script_led_timeline(),
            test_options=TestPreviewOptions(output_start_seconds=0.0, duration_seconds=10.0),
            render_config_version=3,
        )
        assert calls[0]["render_configuration"].version == 3

    def test_marks_render_completed_in_db(self, isolated_db, monkeypatch):
        from core.database import SessionLocal
        from core.models import Render

        svc, project_id, _ = _make_service(isolated_db, monkeypatch, isolated_db)
        _stub_config(svc, monkeypatch)
        _stub_disk_guard(monkeypatch)
        _stub_cleanup(monkeypatch)
        _stub_render_video(monkeypatch, [])

        svc.render_test_preview(
            project_id=project_id,
            timeline=_script_led_timeline(),
            test_options=TestPreviewOptions(output_start_seconds=0.0, duration_seconds=10.0),
        )

        with SessionLocal() as db:
            row = db.query(Render).filter(Render.project_id == project_id).one()
            assert row.status == "completed"


class TestRenderPreviewAndFinal:
    def test_preview_resolves_current_config(self, isolated_db, monkeypatch):
        svc, project_id, _ = _make_service(isolated_db, monkeypatch, isolated_db)
        _stub_config(svc, monkeypatch)
        _stub_disk_guard(monkeypatch)
        calls: list = []
        _stub_render_video(monkeypatch, calls)

        svc.render_preview(project_id=project_id, timeline=_script_led_timeline())
        assert calls, "render_preview must call render_video"
        assert calls[0]["render_configuration"].version == 3
        # Full preview renders the whole timeline (not a derived slice).
        assert "is_derived_preview" not in calls[0]["timeline"]
        assert not calls[0].get("output_timeline_offset_seconds")

    def test_final_resolves_requested_config_version(self, isolated_db, monkeypatch):
        svc, project_id, _ = _make_service(isolated_db, monkeypatch, isolated_db)
        _stub_config(svc, monkeypatch)
        _stub_disk_guard(monkeypatch)
        calls: list = []
        _stub_render_video(monkeypatch, calls)

        svc.render_final(
            project_id=project_id,
            timeline=_script_led_timeline(),
            render_config_version=3,
        )
        assert calls, "render_final must call render_video"
        assert calls[0]["render_configuration"].version == 3

    def test_missing_source_raises(self, isolated_db, monkeypatch):
        svc = RenderService()
        project_id = "p_novideo"
        # No source dir created on purpose.
        monkeypatch.setattr(
            "services.render_service.ensure_free_space", lambda *_: None, raising=True
        )
        with pytest.raises(RenderError, match="Source file not found"):
            svc.render_preview(project_id=project_id, timeline=_script_led_timeline())
