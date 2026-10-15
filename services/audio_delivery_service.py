"""Audio delivery service for block-based video analysis pipeline.

Manages the lifecycle of uploaded audio files for content blocks:
- Upload & validation (duration, format)
- Fit mode calculation (preserve_speed vs stretch/compress)
- Overlap/gap detection between consecutive blocks
- Approval gate per block
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.analysis_schemas import (
    AnalysisDocument,
    AudioDelivery,
    AudioDeliveryStatus,
    AudioFitMode,
    ContentBlock,
)
from core.exceptions import AnalysisError
from services.media_analysis_service import MediaAnalysisService

ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
# ±200 ms tolerance before a fit warning is raised
FIT_TOLERANCE_MS = 200
# Extra silence trim headroom (ms) applied when trimming edge silence
SILENCE_TRIM_PADDING_MS = 50

logger = logging.getLogger(__name__)


class AudioDeliveryService:
    """Handles audio upload, validation, and approval for content blocks."""

    def __init__(self, projects_dir: str | Path):
        self.projects_dir = Path(projects_dir)
        self._media_service = MediaAnalysisService(projects_dir=self.projects_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_document(self, project_id: str) -> AnalysisDocument:
        """Load the persisted AnalysisDocument or raise if absent."""
        doc = self._media_service.load_existing_document(project_id)
        if doc is None:
            raise AnalysisError(f"No saved analysis document for project {project_id}")
        return doc

    def save_document(self, doc: AnalysisDocument) -> None:
        """Persist an updated AnalysisDocument to disk."""
        self._media_service.save_document(doc)

    def upload_audio(
        self,
        project_id: str,
        block_id: str,
        file_path: str,
        actual_duration_ms: int,
        fit_mode: str = "preserve_speed",
    ) -> AnalysisDocument:
        """Register an uploaded audio file for a specific content block.

        Args:
            project_id: Target project identifier.
            block_id: Block receiving the audio.
            file_path: Absolute or relative path to the audio asset.
            actual_duration_ms: Measured duration of the uploaded file in ms.
            fit_mode: How to reconcile actual vs expected duration
                      ("preserve_speed", "stretch", "compress").

        Returns:
            Updated AnalysisDocument with the new AudioDelivery attached.
        """
        doc = self.get_document(project_id)
        block = doc.get_block(block_id)
        if block is None:
            raise AnalysisError(f"Block {block_id} not found in project {project_id}")

        delivery = AudioDelivery(
            file_path=file_path,
            actual_duration_ms=actual_duration_ms,
            expected_start_ms=block.start_ms,
            expected_end_ms=block.end_ms,
            fit_mode=fit_mode,
            status=AudioDeliveryStatus.UPLOADED,
            approved=False,
        )
        block.audio_delivery = delivery
        self._recalculate_adjacent_gaps(doc, block_id)
        self.save_document(doc)
        logger.info("Audio delivered for block %s (%dms)", block_id, actual_duration_ms)
        return doc

    def validate_audio_fit(
        self, project_id: str, block_id: str
    ) -> dict[str, object]:
        """Check whether the uploaded audio fits its target time range.

        Returns a dict with keys:
            ok (bool), delta_ms (int), message (str)
        """
        doc = self.get_document(project_id)
        block = doc.get_block(block_id)
        if block is None:
            raise AnalysisError(f"Block {block_id} not found")
        if block.audio_delivery is None:
            return {"ok": False, "delta_ms": 0, "message": "Chưa có audio được tải lên."}

        expected = block.audio_delivery.expected_duration_ms
        actual = block.audio_delivery.actual_duration_ms
        delta = actual - expected
        ok = abs(delta) <= 200  # ±200 ms tolerance
        msg = (
            f"Âm thanh khớp ({expected}ms ≈ {actual}ms)."
            if ok
            else f"Lệch {delta:+d}ms so với khung mong đợi ({expected}ms)."
        )
        return {"ok": ok, "delta_ms": delta, "message": msg}

    def approve_audio(self, project_id: str, block_id: str) -> AnalysisDocument:
        """Mark a block's audio as human-approved."""
        doc = self.get_document(project_id)
        block = doc.get_block(block_id)
        if block is None:
            raise AnalysisError(f"Block {block_id} not found")
        if block.audio_delivery is None:
            raise AnalysisError(f"Block {block_id} chưa có audio để duyệt.")

        block.audio_delivery.status = AudioDeliveryStatus.APPROVED
        block.audio_delivery.approved = True
        self.save_document(doc)
        logger.info("Audio approved for block %s", block_id)
        return doc

    def revoke_approval(self, project_id: str, block_id: str) -> AnalysisDocument:
        """Unapprove a previously approved audio delivery."""
        doc = self.get_document(project_id)
        block = doc.get_block(block_id)
        if block is None:
            raise AnalysisError(f"Block {block_id} not found")
        if block.audio_delivery is None:
            raise AnalysisError(f"Block {block_id} chưa có audio.")

        block.audio_delivery.status = AudioDeliveryStatus.UPLOADED
        block.audio_delivery.approved = False
        self.save_document(doc)
        logger.info("Audio approval revoked for block %s", block_id)
        return doc

    def list_pending_blocks(self, project_id: str) -> list[ContentBlock]:
        """Return all blocks that still need audio delivery or approval."""
        doc = self.get_document(project_id)
        return [b for b in doc.content_blocks if b.audio_delivery is None or not b.audio_delivery.approved]

    def get_summary(self, project_id: str) -> dict[str, object]:
        """Aggregate statistics about audio delivery progress."""
        doc = self.get_document(project_id)
        total = len(doc.content_blocks)
        delivered = sum(1 for b in doc.content_blocks if b.audio_delivery is not None)
        approved = sum(1 for b in doc.content_blocks if b.audio_delivery and b.audio_delivery.approved)
        return {
            "total_blocks": total,
            "delivered": delivered,
            "approved": approved,
            "pending": total - approved,
            "all_ready": approved == total,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _recalculate_adjacent_gaps(doc: AnalysisDocument, changed_block_id: str) -> None:
        """After modifying one block's delivery, recompute gap/overlap fields
        on neighbouring blocks so the timeline stays consistent."""
        blocks = sorted(doc.content_blocks, key=lambda b: b.start_ms)
        ids = [b.block_id for b in blocks]
        try:
            idx = ids.index(changed_block_id)
        except ValueError:
            return

        targets = set()
        if idx > 0:
            targets.add(ids[idx - 1])
        targets.add(changed_block_id)
        if idx < len(ids) - 1:
            targets.add(ids[idx + 1])

        for i, blk in enumerate(blocks):
            if blk.block_id not in targets:
                continue
            prev_blk = blocks[i - 1] if i > 0 else None
            next_blk = blocks[i + 1] if i < len(blocks) - 1 else None

            if blk.audio_delivery is not None:
                if prev_blk and prev_blk.audio_delivery:
                    gap = max(0, blk.start_ms - prev_blk.end_ms)
                    overlap = max(0, prev_blk.end_ms - blk.start_ms)
                    blk.audio_delivery.gap_ms = gap
                    blk.audio_delivery.overlap_ms = overlap
                else:
                    blk.audio_delivery.gap_ms = 0
                    blk.audio_delivery.overlap_ms = 0

    # ------------------------------------------------------------------
    # Phase C API: block-based audio delivery
    # ------------------------------------------------------------------

    @staticmethod
    def probe_block_audio(path: str | Path) -> dict[str, object]:
        """Probe an audio file via FFprobe. Returns metadata dict.

        Raises AnalysisError if the file is missing, not audio, or unprobeable.
        """
        from pipeline.media_probe import probe_audio

        try:
            return probe_audio(path)
        except Exception as exc:
            raise AnalysisError(f"Audio probe failed for {path}: {exc}")

    @staticmethod
    def trim_edge_silence(path: str | Path, output_path: str | Path | None = None) -> Path:
        """Trim leading/trailing silence from an audio file (fit mode helper).

        Uses ffmpeg silenceremove; only runs when the user chose a mode that
        permits trimming. Returns the processed file path.
        """
        from pipeline.ffmpeg import run_ffmpeg

        src = Path(path)
        if not src.exists():
            raise AnalysisError(f"Audio file not found: {src}")
        dest = Path(output_path) if output_path else src.with_name(f"{src.stem}_trimmed{src.suffix}")
        result = run_ffmpeg([
            "-y", "-i", str(src),
            "-af", (
                "silenceremove=start_periods=1:start_threshold=-45dB,"
                "areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse"
            ),
            "-c:a", "pcm_s16le" if dest.suffix.lower() == ".wav" else "copy",
            str(dest),
        ])
        if result.returncode != 0:
            raise AnalysisError(f"Silence trim failed: {result.stderr[-400:]}")
        return dest

    @staticmethod
    def validate_block_audio(block: ContentBlock, audio_info: dict[str, object]) -> dict[str, object]:
        """Static validation of a probe result against a block's window.

        Returns {ok, delta_ms, overflow_ms, gap_ms, warnings[], errors[]}.
        Never mutates state and never trims/speeds audio on its own.
        """
        expected = block.end_ms - block.start_ms
        actual = int(audio_info.get("duration_ms", 0))
        delta = actual - expected
        warnings: list[str] = []
        errors: list[str] = []
        if actual <= 0:
            errors.append("Audio duration invalid (probe returned <= 0 ms)")
        if delta > FIT_TOLERANCE_MS:
            warnings.append(f"Audio dài hơn khung {delta}ms — cần chọn fit mode để xử lý")
        if delta < -FIT_TOLERANCE_MS:
            warnings.append(f"Audio ngắn hơn khung {-delta}ms — sẽ tạo gap khi render")
        return {
            "ok": not errors,
            "delta_ms": delta,
            "overflow_ms": max(0, delta),
            "gap_ms": max(0, -delta),
            "warnings": warnings,
            "errors": errors,
        }

    def upload_block_audio(
        self,
        project_id: str,
        block_id: str,
        uploaded_file,
    ) -> AnalysisDocument:
        """Persist an uploaded audio file for a block and register its delivery.

        The service copies the file into the project's analysis directory and
        probes it itself — client-supplied durations/paths are never trusted.
        Accepts a Streamlit UploadedFile (has .name/.getvalue) or a filesystem
        path (str/Path).
        """
        import shutil
        import uuid

        doc = self.get_document(project_id)
        block = doc.get_block(block_id)
        if block is None:
            raise AnalysisError(f"Block {block_id} not found in project {project_id}")

        # Resolve source to a path on disk
        if hasattr(uploaded_file, "name") and hasattr(uploaded_file, "getvalue"):
            src_name = Path(uploaded_file.name).name
            suffix = Path(src_name).suffix.lower()
            tmp_dir = self.projects_dir / "_uploads"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            src = tmp_dir / f"u{uuid.uuid4().hex[:8]}_{src_name}"
            src.write_bytes(uploaded_file.getvalue())
        else:
            src = Path(uploaded_file)
            src_name = src.name
            suffix = src.suffix.lower()

        if suffix not in ALLOWED_AUDIO_EXTENSIONS:
            raise AnalysisError(
                f"File '{src_name}' is not a supported audio file (extension '{suffix}'). "
                f"Allowed: {sorted(ALLOWED_AUDIO_EXTENSIONS)}"
            )

        audio_info = self.probe_block_audio(src)

        # Store inside the project's analysis directory; never trust client paths
        dest_dir = self.projects_dir / project_id / "block_analysis" / "audio"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{block_id}_{uuid.uuid4().hex[:8]}_{src_name}"
        shutil.copyfile(src, dest)

        validation = self.validate_block_audio(block, audio_info)
        delivery = AudioDelivery(
            file_path=str(dest),
            actual_duration_ms=int(audio_info["duration_ms"]),
            expected_start_ms=block.start_ms,
            expected_end_ms=block.end_ms,
            fit_mode=AudioFitMode.PRESERVE_SPEED.value,
            overflow_ms=validation["overflow_ms"],
            warnings=validation["warnings"],
            content_hash=audio_info.get("sha256"),
            sample_rate=int(audio_info.get("sample_rate", 0)),
            channels=int(audio_info.get("channels", 0)),
            codec=audio_info.get("codec"),
            status=AudioDeliveryStatus.UPLOADED,
            approved=False,  # replacing audio always invalidates prior approval
        )
        block.audio_delivery = delivery
        self._recalculate_adjacent_gaps(doc, block_id)
        self.save_document(doc)

        if hasattr(uploaded_file, "close"):
            try:
                uploaded_file.close()
            except Exception:
                pass
        logger.info("Audio uploaded for block %s (%dms)", block_id, delivery.actual_duration_ms)
        return doc

    def set_fit_mode(self, project_id: str, block_id: str, fit_mode: str) -> AnalysisDocument:
        """Set the fit mode for a block's audio delivery.

        Changing the fit mode invalidates a previous audio approval because the
        processed output may differ from what was approved.
        """
        allowed = {m.value for m in AudioFitMode}
        if fit_mode not in allowed:
            raise AnalysisError(f"Invalid fit_mode '{fit_mode}'. Allowed: {sorted(allowed)}")

        doc = self.get_document(project_id)
        block = doc.get_block(block_id)
        if block is None:
            raise AnalysisError(f"Block {block_id} not found")
        if block.audio_delivery is None:
            raise AnalysisError(f"Block {block_id} chưa có audio để đặt fit mode.")

        block.audio_delivery.fit_mode = fit_mode
        block.audio_delivery.approved = False
        block.audio_delivery.status = AudioDeliveryStatus.UPLOADED
        self.save_document(doc)
        logger.info("Fit mode '%s' set for block %s (approval invalidated)", fit_mode, block_id)
        return doc

    def approve_block_audio(self, project_id: str, block_id: str) -> AnalysisDocument:
        """Approve a block's audio with full gate checks (Phase C).

        Rejects when: file missing, probe failure, invalid duration,
        overlap with another approved audio block, or unresolved mandatory
        warnings under preserve_speed mode.
        """
        doc = self.get_document(project_id)
        block = doc.get_block(block_id)
        if block is None:
            raise AnalysisError(f"Block {block_id} not found")
        delivery = block.audio_delivery
        if delivery is None:
            raise AnalysisError(f"Block {block_id} chưa có audio để duyệt.")

        # File must exist and re-probe cleanly
        audio_path = Path(delivery.file_path)
        if not audio_path.exists():
            raise AnalysisError(f"Audio file missing for block {block_id}: {audio_path}")
        audio_info = self.probe_block_audio(audio_path)
        if int(audio_info.get("duration_ms", 0)) <= 0:
            raise AnalysisError(f"Invalid audio duration for block {block_id}")

        # Mandatory warnings must be resolved (a fit mode was chosen and applied)
        unresolved = [w for w in delivery.warnings if "fit mode" in w.lower()]
        if unresolved and delivery.fit_mode == AudioFitMode.PRESERVE_SPEED.value:
            raise AnalysisError(
                f"Approve rejected for block {block_id}: fit warnings unresolved — "
                "chọn fit mode phù hợp trước khi duyệt."
            )

        # Overlap gate against other approved blocks
        approved_sorted = sorted(
            [
                b for b in doc.content_blocks
                if b.block_id != block_id and b.audio_delivery and b.audio_delivery.approved
            ],
            key=lambda b: b.start_ms,
        )
        for other in approved_sorted:
            if block.start_ms < other.end_ms and other.start_ms < block.end_ms:
                raise AnalysisError(
                    f"Approve rejected for block {block_id}: audio window overlaps "
                    f"approved block {other.block_id} "
                    f"({other.start_ms}-{other.end_ms}ms vs {block.start_ms}-{block.end_ms}ms)"
                )

        delivery.status = AudioDeliveryStatus.APPROVED
        delivery.approved = True
        self.save_document(doc)
        logger.info("Audio approved for block %s", block_id)
        return doc

    def get_delivery_report(self, project_id: str) -> dict[str, object]:
        """Aggregated per-block delivery report (Phase C).

        Returns blocks with expected/actual/gap/overflow/overlap/status/approved,
        counts, errors/warnings lists and a render_ready boolean.
        """
        doc = self.get_document(project_id)
        blocks_report: list[dict[str, object]] = []
        errors: list[str] = []
        warnings: list[str] = []

        for block in sorted(doc.content_blocks, key=lambda b: b.start_ms):
            d = block.audio_delivery
            entry: dict[str, object] = {
                "block_id": block.block_id,
                "expected_start_ms": block.start_ms,
                "expected_end_ms": block.end_ms,
                "expected_duration_ms": block.end_ms - block.start_ms,
                "actual_duration_ms": d.actual_duration_ms if d else None,
                "gap_ms": d.gap_ms if d else None,
                "overflow_ms": d.overflow_ms if d else None,
                "overlap_ms": d.overlap_ms if d else None,
                "fit_mode": d.fit_mode if d else None,
                "status": d.status.value if d else "missing",
                "approved": bool(d and d.approved),
            }
            if d is None:
                warnings.append(f"Block {block.block_id}: chưa có audio")
            else:
                for w in d.warnings:
                    warnings.append(f"Block {block.block_id}: {w}")
                if d.overlap_ms > 0:
                    errors.append(
                        f"Block {block.block_id}: overlap {d.overlap_ms}ms với block trước"
                    )
            blocks_report.append(entry)

        total = len(doc.content_blocks)
        approved = sum(1 for e in blocks_report if e["approved"])
        render_ready = approved == total and not errors
        return {
            "blocks": blocks_report,
            "total_blocks": total,
            "approved": approved,
            "pending": total - approved,
            "errors": errors,
            "warnings": warnings,
            "render_ready": render_ready,
        }