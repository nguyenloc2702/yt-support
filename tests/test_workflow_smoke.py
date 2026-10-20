"""Smoke tests for the 5-step simplified workflow UI (pages/00_workflow.py).

Validates that the workflow page and report page import and execute safely
without unhandled exceptions in both empty and populated project states.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PAGES_DIR = Path(__file__).resolve().parent.parent / "pages"


def _run_page(page_name: str, tmp_path: Path, session_overrides: dict | None = None) -> subprocess.CompletedProcess:
    overrides = session_overrides or {}
    session_lines = "\n".join(f"st.session_state[{k!r}] = {v!r}" for k, v in overrides.items())
    code = f"""
import sys
import streamlit as st
st.set_page_config = lambda **kw: None
def _stop(): raise SystemExit(0)
st.stop = _stop
st.session_state.setdefault('project_id', None)
{session_lines}
from core.database import init_db
init_db()
exec(compile(open(r'{PAGES_DIR / page_name}', encoding='utf-8').read(), r'{page_name}', 'exec'))
"""
    env = os.environ.copy()
    env["DATA_DIR"] = str(tmp_path / "data")
    env["PROJECTS_DIR"] = str(tmp_path / "projects")
    return subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(PAGES_DIR.parent),
        timeout=120,
        env=env,
    )


def test_workflow_page_smoke_no_project(tmp_path: Path):
    """Workflow entry page executes cleanly when no project is selected."""
    result = _run_page("00_workflow.py", tmp_path, {})
    assert result.returncode == 0, f"Workflow page failed:\n{result.stderr[-3000:]}"


def test_workflow_page_smoke_with_project(tmp_path: Path):
    """Workflow entry page executes cleanly with a created project in DB."""
    # Pre-populate project in sqlite db
    env = os.environ.copy()
    env["DATA_DIR"] = str(tmp_path / "data")
    env["PROJECTS_DIR"] = str(tmp_path / "projects")

    setup_code = """
from core.database import init_db, SessionLocal
from core.models import Project
from core.enums import ProjectStatus, RightsType
init_db()
with SessionLocal() as db:
    p = Project(
        name="Test Project",
        description="test",
        source_filename="input.mp4",
        status=ProjectStatus.ANALYSIS_READY.value,
        duration_seconds=120.0,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    print("PROJECT_ID:" + p.id)
"""
    proc = subprocess.run([sys.executable, "-c", setup_code], check=False, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"Setup failed:\n{proc.stderr}"
    lines = [line for line in proc.stdout.splitlines() if line.startswith("PROJECT_ID:")]
    assert len(lines) == 1
    project_id = lines[0].split(":", 1)[1].strip()

    # Create dummy timeline
    proj_dir = tmp_path / "projects" / project_id
    (proj_dir / "timelines").mkdir(parents=True, exist_ok=True)
    (proj_dir / "timelines" / "current.json").write_text('{"actual_duration": 60.0, "clips": []}', encoding="utf-8")

    result = _run_page("00_workflow.py", tmp_path, {"project_id": project_id})
    assert result.returncode == 0, f"Workflow page with project failed:\n{result.stderr[-3000:]}"


def test_workflow_report_page_stops_safely_without_project(tmp_path: Path):
    """Report page safely calls st.stop() when no project is loaded."""
    result = _run_page("03_workflow_report.py", tmp_path, {})
    assert result.returncode == 0, f"Report page failed on stop:\n{result.stderr[-3000:]}"


def test_workflow_page_smoke_with_vlm_enabled(tmp_path: Path):
    """Workflow entry page renders smoothly when VLM is enabled in settings."""
    env = os.environ.copy()
    env["DATA_DIR"] = str(tmp_path / "data")
    env["PROJECTS_DIR"] = str(tmp_path / "projects")

    setup_code = """
from core.database import init_db, SessionLocal
from core.models import Project
from core.enums import ProjectStatus
from services.settings_service import set_vlm_enabled, set_vlm_model
init_db()
set_vlm_enabled(True)
set_vlm_model("gemini-2.0-flash")
with SessionLocal() as db:
    p = Project(
        name="VLM Project",
        description="vlm test",
        source_filename="input.mp4",
        status=ProjectStatus.ANALYSIS_READY.value,
        duration_seconds=90.0,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    print("PROJECT_ID:" + p.id)
"""
    proc = subprocess.run([sys.executable, "-c", setup_code], check=False, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"Setup failed:\n{proc.stderr}"
    lines = [line for line in proc.stdout.splitlines() if line.startswith("PROJECT_ID:")]
    assert len(lines) == 1
    project_id = lines[0].split(":", 1)[1].strip()

    proj_dir = tmp_path / "projects" / project_id
    (proj_dir / "timelines").mkdir(parents=True, exist_ok=True)
    (proj_dir / "timelines" / "current.json").write_text('{"actual_duration": 45.0, "clips": []}', encoding="utf-8")

    result = _run_page("00_workflow.py", tmp_path, {"project_id": project_id})
    assert result.returncode == 0, f"Workflow page with VLM enabled failed:\n{result.stderr[-3000:]}"
