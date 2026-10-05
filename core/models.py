from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text
from sqlalchemy.sql import func
from core.database import Base
import uuid

class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="created")
    source_filename = Column(String, nullable=True)
    source_path = Column(String, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    fps = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

class RightsRecord(Base):
    __tablename__ = "rights_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, nullable=False)
    rights_type = Column(String, nullable=False)
    confirmed = Column(Boolean, nullable=False, default=False)
    source_url = Column(String, nullable=True)
    license_file_path = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    project_id = Column(String, nullable=False)
    job_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    progress = Column(Integer, default=0)
    current_step = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    # Encrypted with Fernet (master key lives in DATA_DIR/.secret_key).
    # Never store API keys in plain text.
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Render(Base):
    __tablename__ = "renders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    project_id = Column(String, nullable=False)
    timeline_version = Column(Integer, nullable=False)
    render_config_version = Column(Integer, nullable=True)
    render_type = Column(String, nullable=False)
    preset_name = Column(String, nullable=False)
    output_path = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)