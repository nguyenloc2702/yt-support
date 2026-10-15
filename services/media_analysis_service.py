"""Media analysis service for human-in-the-loop video analysis pipeline.

Orchestrates the pipeline stages (media probe, shot detection, audio analysis,
content block creation) into a single AnalysisDocument suitable for UI review.

This is distinct from services/analysis_service.py which handles the legacy
heuristic timeline pipeline. This service produces schema-typed output using
the Pydantic models defined in core/analysis_schemas.py.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.analysis_schemas import (
    AudioEvent,
    ContentBlock,
    MediaInfo,
    ReviewInfo,
    ShotAnalysis,
    TranscriptSegment,
    Speaker,
    AnalysisDocument,
)
from core.exceptions import AnalysisError, InvalidMediaError, JobCancelledError

logger = logging.getLogger(__name__)


class MediaAnalysisService:
    """High-level service that runs the full block-based analysis pipeline.

    Stages:
        1. Probe media metadata          (pipeline.media_probe.probe_media)
        2. Detect shots / scenes         (pipeline.shot_analysis.detect_shots)
        3. Transcribe & analyze audio    (pipeline.audio_analysis.analyze_audio)
        4. Build content blocks          (pipeline.content_analysis.create_content_blocks)
        5. Assemble AnalysisDocument     (this class)

    Each stage writes its intermediate artefact to disk so re-runs can skip
    completed work (checkpoint/resume pattern).
    """

    def __init__(self, projects_dir: str | Path):
        self.projects_dir = Path(projects_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_full_analysis(
        self,
        project_id: str,
        source_path: Optional[Path] = None,
        *,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancellation_token=None,
        options: Optional[Dict[str, Any]] = None,
    ) -> AnalysisDocument:
        """Execute the complete analysis pipeline and return an AnalysisDocument.

        Args:
            project_id: Unique project identifier.
            source_path: Override path to source video. Defaults to
                         ``<projects_dir>/<project_id>/source/input.mp4``.
            progress_callback: Called with (percent_int, message_str).
            cancellation_token: threading.Event checked between stages.
            options: Pipeline tuning knobs – see individual keys below.

        Options keys:
            whisper_model (str): Model size for transcription ("small", "medium").
            language (str): Language hint ("vi", "en") or None for auto.
            min_scene_length (float): Minimum scene length in seconds (default 0.5).
            scene_threshold (float): Content detector threshold (default 30.0).
            silence_threshold (float): Silence gap threshold in seconds (default 0.5).
            min_block_duration_ms (int): Minimum block duration in ms (default 1000).
            use_cached (bool): Re-use on-disk artefacts when valid (default True).

        Returns:
            Fully populated AnalysisDocument ready for UI display.

        Raises:
            InvalidMediaError: Source file missing or unreadable.
            AnalysisError: Any pipeline stage failure.
            JobCancelledError: Cancellation token set mid-run.
        """
        options = options or {}
        use_cached = options.get("use_cached", True)
        proj_dir = self._get_project_dir(project_id)
        if source_path is None:
            source_path = proj_dir / "source" / "input.mp4"
        if not source_path.exists():
            raise InvalidMediaError(f"Source file not found: {source_path}")

        analysis_dir = proj_dir / "block_analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)

        def report(pct: int, msg: str) -> None:
            if progress_callback:
                progress_callback(pct, msg)

        def check_cancel() -> None:
            if cancellation_token is not None and cancellation_token.is_set():
                raise JobCancelledError("Media analysis cancelled by user")

        try:
            # ---- Stage 1: Probe media ------------------------------------
            report(5, "Probing media metadata...")
            check_cancel()
            cached_media = self._load_json_safe(analysis_dir / "media_info.json")
            if use_cached and cached_media:
                media_info = MediaInfo(**cached_media)
                report(8, "Using cached media info.")
            else:
                media_info = self._stage_probe(source_path)
                self._write_json(analysis_dir / "media_info.json", media_info.model_dump())

            # ---- Stage 2: Detect shots -----------------------------------
            report(12, "Detecting shots/scenes...")
            check_cancel()
            cached_shots = self._load_json_safe(analysis_dir / "shots.json")
            if use_cached and cached_shots:
                shots = [ShotAnalysis(**s) for s in cached_shots]
                report(25, f"Loaded {len(shots)} cached shots.")
            else:
                shots = self._stage_detect_shots(
                    source_path,
                    min_scene_length=options.get("min_scene_length", 0.5),
                    threshold=options.get("scene_threshold", 30.0),
                )
                self._write_json(
                    analysis_dir / "shots.json",
                    [s.model_dump() for s in shots],
                )
                report(25, f"Detected {len(shots)} shots.")

            # ---- Stage 3: Audio analysis ---------------------------------
            report(30, "Analyzing audio track...")
            check_cancel()
            cached_segs = self._load_json_safe(analysis_dir / "transcript_segments.json")
            cached_events = self._load_json_safe(analysis_dir / "audio_events.json")
            cached_speakers = self._load_json_safe(analysis_dir / "speakers.json")

            if use_cached and cached_segs and cached_events:
                transcript_segments = [TranscriptSegment(**s) for s in cached_segs]
                audio_events = [AudioEvent(**e) for e in cached_events]
                speakers = [Speaker(**sp) for sp in (cached_speakers or [])]
                report(55, f"Loaded {len(transcript_segments)} cached segments.")
            else:
                transcript_segments, audio_events, speakers = self._stage_analyze_audio(
                    source_path,
                    model_size=options.get("whisper_model"),
                    language=options.get("language"),
                    silence_threshold=options.get("silence_threshold", 0.5),
                    media_duration_ms=media_info.duration_ms,
                )
                self._write_json(
                    analysis_dir / "transcript_segments.json",
                    [s.model_dump() for s in transcript_segments],
                )
                self._write_json(
                    analysis_dir / "audio_events.json",
                    [e.model_dump() for e in audio_events],
                )
                self._write_json(
                    analysis_dir / "speakers.json",
                    [sp.model_dump() for sp in speakers],
                )
                report(55, f"Analyzed audio: {len(transcript_segments)} segments.")

            # ---- Stage 4: Build content blocks ---------------------------
            report(60, "Building content blocks...")
            check_cancel()
            cached_blocks = self._load_json_safe(analysis_dir / "content_blocks.json")
            if use_cached and cached_blocks:
                content_blocks = [ContentBlock(**b) for b in cached_blocks]
                report(75, f"Loaded {len(content_blocks)} cached content blocks.")
            else:
                content_blocks = self._stage_create_blocks(
                    shots=shots,
                    transcript_segments=transcript_segments,
                    audio_events=audio_events,
                    media_duration_ms=media_info.duration_ms,
                    min_block_duration_ms=options.get("min_block_duration_ms", 1000),
                )
                self._write_json(
                    analysis_dir / "content_blocks.json",
                    [b.model_dump() for b in content_blocks],
                )
                report(75, f"Created {len(content_blocks)} content blocks.")

            # ---- Stage 5: Assemble document ------------------------------
            report(80, "Assembling analysis document...")
            check_cancel()
            doc = self._assemble_document(
                project_id=project_id,
                media_info=media_info,
                shots=shots,
                transcript_segments=transcript_segments,
                audio_events=audio_events,
                speakers=speakers,
                content_blocks=content_blocks,
            )

            # Persist the full document
            self._write_json(analysis_dir / "document.json", doc.model_dump(mode="json"))
            report(100, "Analysis complete.")
            return doc

        except JobCancelledError:
            logger.warning("Media analysis cancelled for project %s", project_id)
            raise
        except (InvalidMediaError, AnalysisError):
            raise
        except Exception as exc:
            logger.exception("Unexpected error during media analysis for %s", project_id)
            raise AnalysisError(f"Media analysis failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Individual stage helpers (each wraps one pipeline module call)
    # ------------------------------------------------------------------

    @staticmethod
    def _stage_probe(source_path: Path) -> MediaInfo:
        from pipeline.media_probe import probe_media
        return probe_media(source_path)

    @staticmethod
    def _stage_detect_shots(
        video_path: Path,
        min_scene_length: float = 0.5,
        threshold: float = 30.0,
    ) -> List[ShotAnalysis]:
        from pipeline.shot_analysis import detect_shots
        return detect_shots(video_path, min_scene_length=min_scene_length, threshold=threshold)

    @staticmethod
    def _stage_analyze_audio(
        video_or_audio_path: Path,
        model_size: Optional[str] = None,
        language: Optional[str] = None,
        silence_threshold: float = 0.5,
        media_duration_ms: int = 0,
    ):
        """Run transcription + speaker/event detection.

        Returns (segments, events, speakers).
        """
        from pipeline.audio_analysis import (
            transcribe_audio,
            detect_speaker_turns,
            detect_audio_events,
        )

        segments, mi = transcribe_audio(
            video_or_audio_path,
            model_size=model_size,
            language=language,
        )
        speakers = detect_speaker_turns(segments)
        duration = media_duration_ms or mi.duration_ms
        events = detect_audio_events(
            segments,
            duration,
            silence_threshold=silence_threshold,
        )
        # Assign speaker IDs round-robin onto segments
        if speakers and segments:
            for i, seg in enumerate(segments):
                seg.speaker_id = speakers[i % len(speakers)].speaker_id
        return segments, events, speakers

    @staticmethod
    def _stage_create_blocks(
        shots: List[ShotAnalysis],
        transcript_segments: List[TranscriptSegment],
        audio_events: List[AudioEvent],
        media_duration_ms: int,
        min_block_duration_ms: int = 1000,
    ) -> List[ContentBlock]:
        from pipeline.content_analysis import create_content_blocks
        result = create_content_blocks(
            shots=shots,
            transcript_segments=transcript_segments,
            audio_events=audio_events,
            media_duration_ms=media_duration_ms,
            min_block_duration_ms=min_block_duration_ms,
        )
        return result.content_blocks

    # ------------------------------------------------------------------
    # Document assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _assemble_document(
        project_id: str,
        media_info: MediaInfo,
        shots: List[ShotAnalysis],
        transcript_segments: List[TranscriptSegment],
        audio_events: List[AudioEvent],
        speakers: List[Speaker],
        content_blocks: List[ContentBlock],
    ) -> AnalysisDocument:
        now = datetime.now(timezone.utc)
        return AnalysisDocument(
            schema_version=1,
            analysis_id=str(uuid.uuid4()),
            project_id=project_id,
            media=media_info,
            speakers=speakers,
            shots=shots,
            transcript_segments=transcript_segments,
            audio_events=audio_events,
            content_blocks=content_blocks,
            review=ReviewInfo(),
            metadata={
                "generated_at": now.isoformat(),
                "service_version": "1.0.0",
            },
            created_at=now,
            updated_at=now,
        )

    # ------------------------------------------------------------------
    # Loading / saving partial results (for resume support)
    # ------------------------------------------------------------------

    def load_existing_document(self, project_id: str) -> Optional[AnalysisDocument]:
        """Attempt to load a previously persisted AnalysisDocument from disk."""
        proj_dir = self._get_project_dir(project_id)
        doc_path = proj_dir / "block_analysis" / "document.json"
        data = self._load_json_safe(doc_path)
        if data is None:
            return None
        try:
            return AnalysisDocument(**data)
        except Exception as exc:
            logger.warning("Failed to deserialize existing document for %s: %s", project_id, exc)
            return None

    def save_document(self, doc: AnalysisDocument, keep_version: bool = True) -> None:
        """Persist an AnalysisDocument to disk, snapshotting the previous state.

        Phase 12 (document versioning): before overwriting document.json, the
        current file is copied into block_analysis/versions/ with a timestamp
        name (capped at MAX_VERSIONS entries). Pass keep_version=False to skip.
        Writes are atomic (temp file + os.replace) so a crash never leaves a
        half-written document.
        """
        proj_dir = self._get_project_dir(doc.project_id)
        analysis_dir = proj_dir / "block_analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        doc_path = analysis_dir / "document.json"

        if keep_version and doc_path.exists():
            self._snapshot_document(analysis_dir, doc_path)

        doc.updated_at = datetime.now(timezone.utc)
        self._write_json(doc_path, doc.model_dump(mode="json"))
        self._write_current_pointer(proj_dir, doc)

    # ------------------------------------------------------------------
    # Plan API (Phase A / Phase 6 of enhance_1.md)
    # ------------------------------------------------------------------

    def create_analysis(self, project_id: str) -> AnalysisDocument:
        """Create a new empty AnalysisDocument skeleton for *project_id*."""
        from core.analysis_schemas import AudioEvent, Speaker
        now = datetime.now(timezone.utc)
        doc = AnalysisDocument(
            schema_version=1,
            analysis_id=str(uuid.uuid4()),
            project_id=project_id,
            media=MediaInfo(
                duration_ms=1, fps_num=30, fps_den=1, width=0, height=0,
            ),
            speakers=[],
            shots=[],
            transcript_segments=[],
            audio_events=[],
            content_blocks=[],
            review=ReviewInfo(),
            metadata={"generated_at": now.isoformat(), "service_version": "1.0.0"},
            created_at=now,
            updated_at=now,
        )
        return doc

    def run_analysis(
        self,
        project_id: str,
        options: Optional[Dict[str, Any]] = None,
        *,
        source_path: Optional[Path] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancellation_token=None,
    ) -> AnalysisDocument:
        """Plan-named alias for :meth:`run_full_analysis`."""
        return self.run_full_analysis(
            project_id,
            source_path=source_path,
            progress_callback=progress_callback,
            cancellation_token=cancellation_token,
            options=options,
        )

    def get_current_analysis(self, project_id: str) -> AnalysisDocument:
        """Load the current AnalysisDocument or raise AnalysisError."""
        doc = self.load_existing_document(project_id)
        if doc is None:
            raise AnalysisError(f"No saved analysis document for project {project_id}")
        return doc

    def get_analysis_version(self, project_id: str, version: int) -> AnalysisDocument:
        """Load a specific document *version* (snapshot) or raise.

        Versions are the timestamped snapshots under block_analysis/versions/.
        A version number maps to the (version - 1)-th newest snapshot.
        """
        versions = self.list_document_versions(project_id)  # newest first
        idx = version - 2  # version 1 = current document; older ones are snapshots
        if idx < 0 or idx >= len(versions):
            raise AnalysisError(
                f"Version {version} not found for project {project_id}"
            )
        data = self._load_json_safe(Path(versions[idx]["path"]))
        if data is None:
            raise AnalysisError(f"Version {version} snapshot is unreadable")
        try:
            doc = AnalysisDocument(**data)
        except Exception as exc:
            raise AnalysisError(f"Version {version} failed schema validation: {exc}") from exc
        doc.project_id = project_id
        return doc

    def save_reviewed_analysis(self, doc: AnalysisDocument) -> AnalysisDocument:
        """Persist a reviewed document as a new sequential version.

        Increments ``doc.version`` by 1 relative to the stored current version,
        snapshots the previous state and updates the current pointer.
        """
        current = self.load_existing_document(doc.project_id)
        if current is not None and doc.version <= current.version:
            doc.version = current.version + 1
        self.save_document(doc)
        return doc

    def export_json(self, project_id: str, version: Optional[int] = None) -> str:
        """Serialize the current (or a specific) AnalysisDocument to JSON."""
        if version is None:
            doc = self.get_current_analysis(project_id)
        else:
            doc = self.get_analysis_version(project_id, version)
        return json.dumps(doc.model_dump(mode="json"), indent=2, ensure_ascii=False)

    def import_json(self, project_id: str, json_str: str) -> AnalysisDocument:
        """Plan-named alias for :meth:`import_document_json`."""
        return self.import_document_json(project_id, json_str)

    @staticmethod
    def validate_document(doc: AnalysisDocument) -> List[str]:
        """Run structural validations and return a list of problems (empty = valid)."""
        problems: List[str] = []
        media_duration = doc.media.duration_ms
        seen: set = set()
        for block in doc.content_blocks:
            if block.block_id in seen:
                problems.append(f"Duplicate block_id: {block.block_id}")
            seen.add(block.block_id)
            if block.end_ms > media_duration:
                problems.append(
                    f"Block {block.block_id} exceeds media duration "
                    f"({block.end_ms}ms > {media_duration}ms)"
                )
        narration = [b for b in doc.content_blocks if b.script_final and b.script_final.strip()]
        narration.sort(key=lambda b: b.start_ms)
        for a, b in zip(narration, narration[1:]):
            if a.end_ms > b.start_ms:
                problems.append(
                    f"Narration overlap: {a.block_id} ({a.start_ms}-{a.end_ms}ms) "
                    f"overlaps {b.block_id} ({b.start_ms}-{b.end_ms}ms)"
                )
        return problems

    def _write_current_pointer(self, proj_dir: Path, doc: AnalysisDocument) -> None:
        """Write projects/<id>/block_analysis/current.json pointer."""
        pointer = {
            "analysis_id": doc.analysis_id,
            "version": doc.version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_json(proj_dir / "block_analysis" / "current.json", pointer)

    MAX_VERSIONS = 20

    def _snapshot_document(self, analysis_dir: Path, doc_path: Path) -> None:
        """Copy the current document.json into versions/ (timestamped, capped)."""
        versions_dir = analysis_dir / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        dest = versions_dir / f"document_{stamp}.json"
        try:
            shutil.copyfile(doc_path, dest)
        except OSError as exc:
            logger.warning("Failed to snapshot document version: %s", exc)
            return
        # Cap the number of retained versions (oldest removed first)
        versions = sorted(versions_dir.glob("document_*.json"))
        for old in versions[: max(0, len(versions) - self.MAX_VERSIONS)]:
            try:
                old.unlink()
            except OSError:
                pass

    def list_document_versions(self, project_id: str) -> List[Dict[str, Any]]:
        """List retained document snapshots, newest first.

        Returns dicts with: name, path, modified_at (ISO), size_bytes.
        """
        versions_dir = self._get_project_dir(project_id) / "block_analysis" / "versions"
        if not versions_dir.exists():
            return []
        out = []
        for p in sorted(versions_dir.glob("document_*.json"), reverse=True):
            stat = p.stat()
            out.append({
                "name": p.name,
                "path": str(p),
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "size_bytes": stat.st_size,
            })
        return out

    def restore_document_version(self, project_id: str, version_name: str) -> AnalysisDocument:
        """Restore a snapshot as the current document (snapshotting the present state first)."""
        versions_dir = self._get_project_dir(project_id) / "block_analysis" / "versions"
        snap_path = versions_dir / version_name
        if not snap_path.exists():
            raise AnalysisError(f"Không tìm thấy phiên bản: {version_name}")
        data = self._load_json_safe(snap_path)
        if data is None:
            raise AnalysisError(f"Phiên bản hỏng hoặc không đọc được: {version_name}")
        try:
            doc = AnalysisDocument(**data)
        except Exception as exc:
            raise AnalysisError(f"Schema validation failed trên phiên bản: {exc}") from exc
        doc.project_id = project_id
        self.save_document(doc)  # snapshots the pre-restore state
        logger.info("Restored document version %s for %s", version_name, project_id)
        return doc

    # ------------------------------------------------------------------
    # Block-level operations (used by Phase 7 UI)
    # ------------------------------------------------------------------

    def update_block_script(
        self,
        project_id: str,
        block_id: str,
        script_final: str,
        review_note: str = "",
    ) -> AnalysisDocument:
        """Update a single block's final script and persist."""
        doc = self.load_existing_document(project_id)
        if doc is None:
            raise AnalysisError(f"No saved analysis document for project {project_id}")
        block = doc.get_block(block_id)
        if block is None:
            raise AnalysisError(f"Block {block_id} not found in project {project_id}")
        block.script_final = script_final.strip() or None
        block.review_note = review_note
        block.human_verified = True
        # Sync verified list in review info
        if block_id not in doc.review.verified_block_ids:
            doc.review.verified_block_ids.append(block_id)
        self.save_document(doc)
        return doc

    def delete_block(self, project_id: str, block_id: str) -> AnalysisDocument:
        """Remove a content block entirely."""
        doc = self.load_existing_document(project_id)
        if doc is None:
            raise AnalysisError(f"No saved analysis document for project {project_id}")
        original_len = len(doc.content_blocks)
        doc.content_blocks = [b for b in doc.content_blocks if b.block_id != block_id]
        if len(doc.content_blocks) == original_len:
            raise AnalysisError(f"Block {block_id} not found")
        doc.review.verified_block_ids = [bid for bid in doc.review.verified_block_ids if bid != block_id]
        self.save_document(doc)
        return doc

    def reorder_blocks(self, project_id: str, ordered_block_ids: List[str]) -> AnalysisDocument:
        """Reorder content blocks according to the supplied ID sequence."""
        doc = self.load_existing_document(project_id)
        if doc is None:
            raise AnalysisError(f"No saved analysis document for project {project_id}")
        id_map = {b.block_id: b for b in doc.content_blocks}
        reordered = []
        for bid in ordered_block_ids:
            if bid not in id_map:
                raise AnalysisError(f"Unknown block_id in reorder request: {bid}")
            reordered.append(id_map.pop(bid))
        # Append any remaining blocks not listed
        reordered.extend(id_map.values())
        doc.content_blocks = reordered
        self.save_document(doc)
        return doc

    # ------------------------------------------------------------------
    # Export / Import (Phase 11 foundation)
    # ------------------------------------------------------------------

    def export_document_json(self, project_id: str) -> str:
        """Serialize the full AnalysisDocument to a JSON string."""
        doc = self.load_existing_document(project_id)
        if doc is None:
            raise AnalysisError(f"No saved analysis document for project {project_id}")
        return json.dumps(doc.model_dump(mode="json"), indent=2, ensure_ascii=False)

    def import_document_json(
        self, project_id: str, json_str: str, *, allow_foreign_project: bool = False
    ) -> AnalysisDocument:
        """Deserialize an AnalysisDocument from JSON and persist it.

        By default, importing a document whose ``project_id`` differs from the
        target project is rejected (per Phase 6 test plan). Pass
        ``allow_foreign_project=True`` to adopt the document into the target
        project (explicit re-parenting).
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise AnalysisError(f"Invalid JSON input: {exc}") from exc
        try:
            doc = AnalysisDocument(**data)
        except Exception as exc:
            raise AnalysisError(f"Schema validation failed on import: {exc}") from exc
        if not allow_foreign_project and doc.project_id != project_id:
            raise AnalysisError(
                f"Import rejected: document belongs to project "
                f"'{doc.project_id}', not '{project_id}'. "
                f"Pass allow_foreign_project=True to re-parent explicitly."
            )
        doc.project_id = project_id
        self.save_document(doc)
        return doc

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    def _get_project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    @staticmethod
    def _load_json_safe(path: Path) -> Optional[Any]:
        """Load JSON from *path*, returning None on any failure."""
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        """Atomic JSON write: temp file in same dir, then os.replace."""
        import os
        import tempfile
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=path.name
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise