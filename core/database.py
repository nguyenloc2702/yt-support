from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from pathlib import Path
from config.settings import settings

Base = declarative_base()

data_dir = Path(settings.data_dir)
data_dir.mkdir(parents=True, exist_ok=True)
db_path = data_dir / "app.sqlite3"
engine = create_engine(
    f"sqlite:///{db_path}",
    echo=False,
    # Pool of recurring Streamlit reruns + one background job thread.
    connect_args={"check_same_thread": False, "timeout": 30},
)

# WAL lets the background job thread write while the UI reads without
# "database is locked" errors. Set on every raw connection because these
# pragmas are per-connection, not persisted in the file.
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine)

def run_migrations():
    with engine.connect() as conn:
        # Check renders table columns
        try:
            res = conn.exec_driver_sql("PRAGMA table_info(renders)").fetchall()
            render_cols = {row[1] for row in res}
            if render_cols:
                if "render_config_version" not in render_cols:
                    conn.exec_driver_sql("ALTER TABLE renders ADD COLUMN render_config_version INTEGER")
                if "error_message" not in render_cols:
                    conn.exec_driver_sql("ALTER TABLE renders ADD COLUMN error_message TEXT")
                if "started_at" not in render_cols:
                    conn.exec_driver_sql("ALTER TABLE renders ADD COLUMN started_at DATETIME")
        except Exception:
            pass

        # Check jobs table columns
        try:
            res = conn.exec_driver_sql("PRAGMA table_info(jobs)").fetchall()
            job_cols = {row[1] for row in res}
            if job_cols:
                if "payload_json" not in job_cols:
                    conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN payload_json TEXT")
        except Exception:
            pass

def init_db():
    # Import models so their tables are registered on Base.metadata
    # before create_all runs. Without this, create_all is a no-op and
    # queries fail with "no such table: projects".
    import core.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    run_migrations()