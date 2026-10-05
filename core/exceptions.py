class AppError(Exception):
    pass

class DependencyNotFoundError(AppError):
    pass

class InvalidMediaError(AppError):
    pass

class ProjectNotFoundError(AppError):
    pass

class AnalysisError(AppError):
    pass

class TimelineValidationError(AppError):
    pass

class RenderError(AppError):
    pass

class JobCancelledError(AppError):
    pass


class InsufficientDiskSpaceError(AppError):
    pass

class ScriptNotApprovedError(AppError):
    """Raised when timeline/render is attempted with an unapproved script."""
    pass

class LLMNotConfiguredError(AppError):
    pass