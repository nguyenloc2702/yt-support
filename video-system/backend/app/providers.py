"""Provider/adapter PROTOCOLS only (plan: adapters plugged in later).

A provider may never silently downgrade (plan §1.4). Missing adapters are
fail-closed: callers get AdapterNotConfigured and the workflow holds.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class AdapterNotConfigured(Exception):
    """Raised when an adapter required by a workflow step is not plugged in."""


@runtime_checkable
class ProbeAdapter(Protocol):
    def probe_video(self, path: str) -> dict:
        """-> {width,height,fps,codec,pix_fmt,duration,has_audio,audio{rate,channels,codec}}"""

    def probe_audio(self, path: str) -> dict:
        """-> {duration, ...}"""

    def probe_image(self, path: str) -> dict:
        """-> {width,height}"""


@runtime_checkable
class OcrAdapter(Protocol):
    def ocr(self, image_or_video_path: str, at_seconds: float = 0.0) -> str:
        """Return all visible text in the frame."""


@runtime_checkable
class GenerationAdapter(Protocol):
    def generate_keyframe(self, prompt: str, ref_images: list[str]) -> str:
        """-> asset path of generated keyframe image."""

    def animate(self, keyframe_path: str, prompt: str, duration: float) -> str:
        """-> asset path of generated shot video. Never substitutes another engine."""


@runtime_checkable
class LlmScriptAdapter(Protocol):
    """LLM JSON-mode adapter (Phụ lục G — Short Studio)."""

    def chat_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        """Return a parsed JSON dict. Implementations may repair fences/blobs."""


@runtime_checkable
class ImageGenAdapter(Protocol):
    """Image generation adapter (Phụ lục G — Short Studio)."""

    def generate_image(self, prompt: str, out_path: str,
                       size: str = "1080x1920") -> dict:
        """-> {path, model, size_used}. If the model rejects `size`, retry a
        supported one and report it in size_used (caller center-crops)."""


class Registry:
    """Adapter registry. Everything starts unconfigured (fail-closed)."""

    def __init__(self) -> None:
        self.probe: ProbeAdapter | None = None
        self.ocr: OcrAdapter | None = None
        self.generation: GenerationAdapter | None = None
        self.llm: LlmScriptAdapter | None = None
        self.image_gen: ImageGenAdapter | None = None

    def require_probe(self) -> ProbeAdapter:
        if self.probe is None:
            raise AdapterNotConfigured("probe adapter not configured")
        return self.probe

    def require_ocr(self) -> OcrAdapter:
        if self.ocr is None:
            raise AdapterNotConfigured("ocr adapter not configured")
        return self.ocr

    def require_generation(self) -> GenerationAdapter:
        if self.generation is None:
            raise AdapterNotConfigured("generation adapter not configured")
        return self.generation

    def require_llm(self) -> LlmScriptAdapter:
        if self.llm is None:
            raise AdapterNotConfigured("llm adapter not configured (set VIDEO_XKIRO_API_KEY)")
        return self.llm

    def require_image_gen(self) -> ImageGenAdapter:
        if self.image_gen is None:
            raise AdapterNotConfigured("image_gen adapter not configured (set VIDEO_XKIRO_API_KEY)")
        return self.image_gen


registry = Registry()
