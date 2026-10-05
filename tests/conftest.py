"""Shared pytest fixtures: isolated SQLite database per test.

core.database builds its engine at import time, so tests redirect persistence
by rebuilding the engine on a temp file and patching every module that holds
a direct SessionLocal reference.
"""

import pytest

MODULES_WITH_SESSIONLOCAL = [
    "core.database",
    "services.project_service",
    "services.job_service",
    "services.analysis_service",
    "services.render_service",
    "services.settings_service",
    "services.render_config_service",
    "services.asset_service",
]


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point the app at a fresh SQLite DB + temp data/projects dirs."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import core.database as db_mod
    import core.models  # noqa: F401  (register tables)

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    db_mod.Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    monkeypatch.setattr(db_mod, "engine", engine, raising=False)
    monkeypatch.setattr(db_mod, "SessionLocal", TestSession, raising=False)
    for mod_name in MODULES_WITH_SESSIONLOCAL:
        try:
            mod = __import__(mod_name, fromlist=["SessionLocal"])
        except ImportError:
            continue
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", TestSession, raising=False)

    # Temp data/projects dirs so nothing touches real user data
    monkeypatch.setattr(
        "config.settings.settings.data_dir", tmp_path / "data", raising=False
    )
    monkeypatch.setattr(
        "config.settings.settings.projects_dir", tmp_path / "projects", raising=False
    )
    return tmp_path
