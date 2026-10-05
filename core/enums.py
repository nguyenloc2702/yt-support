from enum import Enum

class ProjectStatus(str, Enum):
    CREATED = "created"
    SOURCE_READY = "source_ready"
    ANALYZING = "analyzing"
    ANALYSIS_READY = "analysis_ready"
    TIMELINE_READY = "timeline_ready"
    PREVIEW_RENDERING = "preview_rendering"
    PREVIEW_READY = "preview_ready"
    APPROVED = "approved"
    FINAL_RENDERING = "final_rendering"
    COMPLETED = "completed"
    FAILED = "failed"

class RightsType(str, Enum):
    OWNED = "owned"
    LICENSED = "licensed"
    PUBLIC_DOMAIN = "public_domain"
    CREATIVE_COMMONS = "creative_commons"

class JobType(str, Enum):
    ANALYSIS = "analysis"
    PREVIEW_RENDER = "preview_render"
    FINAL_RENDER = "final_render"
    TEST_PREVIEW_RENDER = "test_preview_render"

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class RenderType(str, Enum):
    PREVIEW = "preview"
    FINAL = "final"
    TEST_PREVIEW = "test_preview"

class RenderStatus(str, Enum):
    PENDING = "pending"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"