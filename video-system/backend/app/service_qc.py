"""Automatic QC — geometry + text ONLY (plan §4.B.5).

Character colour/expression is HUMAN QC at batch review; this module never
claims it. Fail-closed: if the OCR adapter is not configured, text checks
CANNOT pass — the shot is held with QC_TOOL_UNAVAILABLE, never silently
passed (the repo-old disease of placeholder validators returning True).
"""
from __future__ import annotations

from . import domain
from .providers import AdapterNotConfigured, registry
from .config import RENDER_SPEC


class QcResult:
    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.reason: domain.HeldReason | None = None
        self.detail: str | None = None

    def ok(self) -> bool:
        return self.reason is None

    def _fail(self, reason: domain.HeldReason, detail: str) -> None:
        if self.reason is None:
            self.reason = reason
            self.detail = detail


def run_video_qc(video_path: str, shot: dict, narration_keyframes: dict) -> QcResult:
    """QC a generated shot against render_spec. `shot` is a DB row dict."""
    res = QcResult()
    spec = RENDER_SPEC
    adapter = registry.require_probe()
    try:
        info = adapter.probe_video(video_path)
    except AdapterNotConfigured:
        res._fail(domain.HeldReason.QC_TOOL_UNAVAILABLE, "probe adapter not configured")
        return res

    v = spec["video"]
    if (info.get("width"), info.get("height")) != (v["width"], v["height"]):
        res._fail(domain.HeldReason.RESOLUTION_FAIL,
                  f"got {info.get('width')}x{info.get('height')}")
    if abs(info.get("fps", 0) - v["fps"]) > 0.01:
        res._fail(domain.HeldReason.SPEC_MISMATCH, f"fps {info.get('fps')} != {v['fps']}")
    if info.get("codec") != v["codec"] or info.get("pix_fmt") != v["pix_fmt"]:
        res._fail(domain.HeldReason.SPEC_MISMATCH,
                  f"codec/pix_fmt {info.get('codec')}/{info.get('pix_fmt')}")
    expected = float(shot["audio_end_sec"]) - float(shot["audio_start_sec"])
    tol = spec["qc"]["duration_tolerance_sec"]
    if abs(float(info.get("duration", 0)) - expected) > tol:
        res._fail(domain.HeldReason.DURATION_MISMATCH,
                  f"video {info.get('duration')}s vs audio {expected}s (±{tol}s)")

    # text allowlist: quote/diagram MUST show exactly on_screen_text;
    # everything else MUST show nothing. OCR unavailable => cannot pass.
    ocr = None
    try:
        ocr = registry.require_ocr()
    except AdapterNotConfigured:
        res._fail(domain.HeldReason.QC_TOOL_UNAVAILABLE,
                  "ocr adapter not configured — text allowlist unverifiable")
    if ocr is not None:
        try:
            seen = ocr.ocr(video_path, at_seconds=info.get("duration", 0) / 2)
        except AdapterNotConfigured:
            res._fail(domain.HeldReason.QC_TOOL_UNAVAILABLE, "ocr raised not-configured")
            return res
        allowed = (shot["on_screen_text"] or "").strip()
        if shot["shot_type"] in {"quote", "diagram"}:
            if seen.strip() != allowed:
                res._fail(domain.HeldReason.VIDEO_TEXT_LEAK,
                          f"ocr={seen.strip()!r} != on_screen_text={allowed!r}")
        elif seen.strip():
            res._fail(domain.HeldReason.VIDEO_TEXT_LEAK, f"unexpected text: {seen.strip()!r}")
    return res


def run_keyframe_qc(image_path: str, shot: dict) -> QcResult:
    """Keyframe QC: size + text only. Style check is a separate adapter gate."""
    res = QcResult()
    v = RENDER_SPEC["video"]
    try:
        adapter = registry.require_probe()
        dims = adapter.probe_image(image_path)
    except AdapterNotConfigured:
        res._fail(domain.HeldReason.QC_TOOL_UNAVAILABLE, "probe adapter not configured")
        return res
    if (dims.get("width"), dims.get("height")) != (v["width"], v["height"]):
        res._fail(domain.HeldReason.KEYFRAME_SIZE_FAIL,
                  f"got {dims.get('width')}x{dims.get('height')}")
    if shot.get("on_screen_text"):
        try:
            ocr = registry.require_ocr()
            seen = ocr.ocr(image_path).strip()
            if seen != shot["on_screen_text"].strip():
                res._fail(domain.HeldReason.KEYFRAME_TEXT_MISMATCH,
                          f"ocr={seen!r} != {shot['on_screen_text'].strip()!r}")
        except AdapterNotConfigured:
            res._fail(domain.HeldReason.QC_TOOL_UNAVAILABLE,
                      "ocr adapter not configured — on_screen_text unverifiable")
    return res
