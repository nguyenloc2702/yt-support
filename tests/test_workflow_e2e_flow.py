"""End-to-end integration test for the 5-step video workflow.

Validates the full pipeline flow:
1. Project creation
2. Target duration synthesis & timeline report building
3. Detailed timestamp report inspection
4. Script matching with audio missing detection
5. Render configuration with background music
6. Page execution with full state
"""

from __future__ import annotations

import json
from pathlib import Path

from core.database import SessionLocal, init_db
from core.enums import ProjectStatus
from core.models import Project
from pipeline.script_matcher import match_script_to_report
from pipeline.timeline_report import build_timeline_report
from services.render_config_service import RenderConfigService


def test_full_5_step_workflow_flow(tmp_path: Path, monkeypatch):
    """Executes the complete 5-step workflow end-to-end."""
    # Point settings projects_dir and data_dir to tmp_path
    monkeypatch.setattr("config.settings.settings.projects_dir", str(tmp_path / "projects"))
    monkeypatch.setattr("config.settings.settings.data_dir", str(tmp_path / "data"))

    init_db()

    # Step 1: Create project
    import uuid
    project_id = f"e2e_{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        p = Project(
            id=project_id,
            name="Workflow E2E Test",
            description="Testing full 5 steps",
            source_filename="input.mp4",
            duration_seconds=120.0,
            status=ProjectStatus.SOURCE_READY.value,
        )
        db.add(p)
        db.commit()

    proj_dir = tmp_path / "projects" / project_id
    (proj_dir / "analysis").mkdir(parents=True, exist_ok=True)
    (proj_dir / "timelines").mkdir(parents=True, exist_ok=True)
    (proj_dir / "render_configs").mkdir(parents=True, exist_ok=True)

    # Step 2: Target duration synthesis data
    timeline = {
        "version": 1,
        "actual_duration": 40.0,
        "mode": "summary",
        "clips": [
            {
                "clip_id": "c1",
                "source_start": 0.0,
                "source_end": 20.0,
                "output_start": 0.0,
                "output_end": 20.0,
                "duration": 20.0,
                "enabled": True,
            },
            {
                "clip_id": "c2",
                "source_start": 40.0,
                "source_end": 60.0,
                "output_start": 20.0,
                "output_end": 40.0,
                "duration": 20.0,
                "enabled": True,
            },
        ],
    }
    (proj_dir / "timelines" / "current.json").write_text(json.dumps(timeline), encoding="utf-8")

    transcript = [
        {"start": 2.0, "end": 8.0, "text": "Chào mừng các bạn đến với video trải nghiệm thiên nhiên."},
        {"start": 42.0, "end": 50.0, "text": "Chúng ta cùng ngắm hoàng hôn buông xuống trên đồi."},
    ]
    scenes = [
        {"scene_id": "scene_0", "start": 0.0, "end": 20.0},
        {"scene_id": "scene_1", "start": 40.0, "end": 60.0},
    ]
    silences = [
        {"start": 10.0, "end": 18.0},
    ]

    # Step 3: Build & verify timestamp report
    report = build_timeline_report(
        timeline=timeline,
        transcript=transcript,
        scenes=scenes,
        silences=silences,
    )
    assert report["output_duration_seconds"] == 40.0
    assert len(report["rows"]) >= 4

    # Verify rows have required properties
    row0 = report["rows"][0]
    assert "row_id" in row0
    assert "visual_description" in row0
    assert "audio_state" in row0
    assert "confidence" in row0

    # Save report
    (proj_dir / "analysis" / "timeline_report.json").write_text(json.dumps(report), encoding="utf-8")

    # Step 4: Script matching with audio missing detection
    script_content = (
        "Chào mừng các bạn đến với video trải nghiệm thiên nhiên.\n"
        "MC nói lời cảm nghĩ giữa không gian tĩnh lặng của rừng cây.\n"
        "Chúng ta cùng ngắm hoàng hôn buông xuống trên đồi."
    )
    match_result = match_script_to_report(script_content, report)

    assert match_result["summary"]["total"] == 3
    assert match_result["summary"]["matched"] >= 1

    # Sentence 2 requires speech ("nói") but aligns with silence/no speech region -> must be miss
    silence_issues = [
        i for i in match_result["issues"]
        if "MC nói" in i["script_text"]
    ]
    assert len(silence_issues) == 1
    assert silence_issues[0]["status"] == "miss"
    assert silence_issues[0]["severity"] == "high"
    assert "từ " in silence_issues[0]["message"]

    (proj_dir / "analysis" / "script_match_report.json").write_text(json.dumps(match_result), encoding="utf-8")

    # Step 5: Render configuration with background music
    cfg_svc = RenderConfigService()
    cfg = cfg_svc.get_current(project_id)
    assert cfg is not None
    assert cfg.music.enabled is False

    # Toggle background music with standard low volume (-16.5 dB)
    cfg.music.enabled = True
    cfg.music.gain_db = -16.5
    cfg.music.loop = True
    cfg_svc.save_new_version(project_id, cfg)

    updated_cfg = cfg_svc.get_current(project_id)
    assert updated_cfg.music.enabled is True
    assert updated_cfg.music.gain_db == -16.5
    assert updated_cfg.music.loop is True
