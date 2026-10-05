from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from core.enums import ProjectStatus, RightsType

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    rights_type: RightsType
    confirmed: bool
    source_url: Optional[str] = None
    notes: Optional[str] = None

class ProjectOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    status: ProjectStatus
    source_filename: Optional[str]
    duration_seconds: Optional[float]
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    created_at: datetime
    updated_at: Optional[datetime]