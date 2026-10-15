"""Audio timeline builder for the block-based analysis pipeline.

Converts a human-approved AnalysisDocument into a timeline dict compatible
with pipeline.renderer.render_video(), assembling clips from verified content
blocks and preparing per-block audio delivery assets for overlay mixing.

Gate: blocks must be human_verified with an approved audio delivery
(or no audio needed) before they are eligible for render.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.analysis_schemas import AnalysisDocument, ContentBlock
from core.exceptions import AnalysisError

logger = logging.getLogger(__name__)


class AudioTimelineError(AnalysisError):
    """Raised when the document is not ready for timeline assembly."""


def build_audio_timeline(
    doc: AnalysisDocument,
    source_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Assemble a render-ready timeline from an approved AnalysisDocument.

    Contract:
        - Only human_verified blocks with script_final are included.
        - Blocks with an approved AudioDelivery contribute an audio overlay
          entry; blocks without one fall back to source audio.
        - Output windows are contiguous in block order (start of block N+1 =
          end of block N), so gaps in the source do not stretch the output.

    Args:
        doc: AnalysisDocument that passed the review/approval gates.
        source_path: Override path to source video. Defaults to the project's
                     source/input.mp4 (resolved by the caller if needed).

    Returns:
        Timeline dict with keys: clips (list), audio_overlays (list),
        total_duration_seconds, block_count.

    Raises:
        AudioTimelineError: No eligible blocks, or audio not approved.
    """
    eligible = [
        b for b in doc.content_blocks
        if b.human_verified and b.script_final
    ]
    if not eligible:
        raise AudioTimelineError(
            "Không có khối nào đủ điều kiện render "
            "(cần human_verified=True và script_final)."
        )

    # Blocks with delivered audio must have it approved
    unapproved = [
        b.block_id for b in eligible
        if b.audio_delivery is not None and not b.audio_delivery.approved
    ]
    if unapproved:
        raise AudioTimelineError(
            f"Các khối có audio chưa được duyệt: {', '.join(unapproved)}"
        )

    clips: List[Dict[str, Any]] = []
    audio_overlays: List[Dict[str, Any]] = []
    output_cursor = 0.0  # seconds, contiguous output timeline

    for block in eligible:
        out_start = output_cursor
        duration_s = block.duration_ms / 1000.0
        out_end = out_start + duration_s

        clips.append({
            "block_id": block.block_id,
            "source_start": block.source_start_ms / 1000.0,
            "source_end": block.source_end_ms / 1000.0,
            "output_start": round(out_start, 3),
            "output_end": round(out_end, 3),
            "duration": round(duration_s, 3),
            "script": block.script_final,
            "enabled": True,
        })

        ad = block.audio_delivery
        if ad is not None:
            audio_overlays.append({
                "block_id": block.block_id,
                "file_path": ad.file_path,
                "output_start": round(out_start, 3),
                "output_end": round(out_end, 3),
                "fit_mode": ad.fit_mode,
                "actual_duration_ms": ad.actual_duration_ms,
                "expected_duration_ms": ad.expected_duration_ms,
                "delta_ms": ad.actual_duration_ms - ad.expected_duration_ms,
            })

        output_cursor = out_end

    timeline: Dict[str, Any] = {
        "clips": clips,
        "audio_overlays": audio_overlays,
        "total_duration_seconds": round(output_cursor, 3),
        "block_count": len(clips),
        "source": str(source_path) if source_path else None,
        "analysis_id": doc.analysis_id,
        "review_status": getattr(doc.review.status, "value", str(doc.review.status)),
    }

    logger.info(
        "Audio timeline built: %d clips, %d overlays, %.1fs total",
        len(clips), len(audio_overlays), output_cursor,
    )
    return timeline


def check_render_readiness(doc: AnalysisDocument) -> Dict[str, Any]:
    """Pre-flight check without raising. Returns a diagnostics dict."""
    reasons: List[str] = []
    blocks = doc.content_blocks
    if not blocks:
        reasons.append("Tài liệu không có khối nào.")
    unverified = [b.block_id for b in blocks if not b.human_verified]
    if unverified:
        reasons.append(f"{len(unverified)} khối chưa duyệt nội dung.")
    no_script = [
        b.block_id for b in blocks
        if b.human_verified and not b.script_final
    ]
    if no_script:
        reasons.append(f"{len(no_script)} khối đã duyệt nhưng thiếu script_final.")
    unapproved_audio = [
        b.block_id for b in blocks
        if b.audio_delivery is not None and not b.audio_delivery.approved
    ]
    if unapproved_audio:
        reasons.append(f"{len(unapproved_audio)} khối có audio chưa duyệt.")
    if doc.has_overlap():
        reasons.append("Các khối narration chồng lấn nhau.")

    return {
        "ready": not reasons,
        "reasons": reasons,
        "eligible_blocks": sum(
            1 for b in blocks if b.human_verified and b.script_final
        ),
    }
