"""Tests for the Streamlit pages: importability smoke test (FE).

Each page is imported in an isolated subprocess. The subprocess runs with
DATA_DIR/PROJECTS_DIR pointed at a temp directory and initializes a fresh
SQLite DB there, so pages can safely query the DB at import time without
touching real user data.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PAGES_DIR = Path(__file__).resolve().parent.parent / "pages"

PAGE_FILES = [
    "00_settings.py",
    "01_projects.py",
    "02_analysis.py",
    "03_transcript.py",
    "03b_script.py",
    "04_timeline.py",
    "05_export.py",
]


def _import_page(page: str, tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "import streamlit as st\n"
        "st.set_page_config = lambda **kw: None\n"
        # In bare mode st.stop() is a no-op; make it halt like it does in a
        # real Streamlit run so early-exit guards behave correctly.
        "def _stop(): raise SystemExit(0)\n"
        "st.stop = _stop\n"
        "st.session_state.setdefault('project_id', None)\n"
        "from core.database import init_db\n"
        "init_db()\n"
        f"exec(compile(open(r'{PAGES_DIR / page}', encoding='utf-8').read(), r'{page}', 'exec'))"
    )
    env = os.environ.copy()
    env["DATA_DIR"] = str(tmp_path / "data")
    env["PROJECTS_DIR"] = str(tmp_path / "projects")
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(PAGES_DIR.parent),
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, f"Page {page} failed to import:\n{result.stderr[-3000:]}"


@pytest.mark.parametrize("page", PAGE_FILES)
def test_page_imports(page, tmp_path):
    """Each page must be importable without crashing (smoke test for FE)."""
    _import_page(page, tmp_path)
