"""
Script service: manages the review-script lifecycle with an explicit status
machine, enforcing the user-confirm gate.

Status machine:
    draft → edited → approved
      ↓         ↓
   rejected ←── (regenerate/edit resets)

The gate: build_script_timeline() raises ScriptNotApprovedError unless the
current script has status == "approved".
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.analysis_schemas import AnalysisDocument
from core.exceptions import (
    AnalysisError,
    ScriptNotApprovedError,
)
from core.logging_config import get_logger
from pipeline.script_timeline_planner import plan_script_timeline
from pipeline.script_rewrite import (
    duration_check,
    load_script,
    rewrite_script,
    save_script,
)
from services.media_analysis_service import MediaAnalysisService
from utils.json_io import read_json

logger = get_logger(__name__)

VALID_STATUSES = {"draft", "edited", "approved", "rejected"}
FINAL_STATUSES = {"approved", "rejected"}


class ScriptService:
    """State + gate enforcement for the AI-rewritten review script."""

    def __init__(self):
        self._media_service = MediaAnalysisService(projects_dir="projects")

    def get_script(self, project_id: str) -> Dict[str, Any] | None:
        return load_script(self._project_dir(project_id))
    
    def _load_block_document(self, project_id: str) -> Optional[AnalysisDocument]:
        """Load the AnalysisDocument from the new block-based pipeline if available."""
        try:
            return self._media_service.load_existing_document(project_id)
        except Exception:
            return None
            
    def _get_effective_transcript_data(self, project_id: str) -> Dict[str, Any]:
        """
        Return transcript data compatible with legacy format {'segments': [...]}.
        Prefers block_analysis document if present; falls back to analysis/transcription.json.
        """
        doc = self._load_block_document(project_id)
        if doc is not None and doc.transcript_segments:
            # Convert Pydantic models to dicts expected by downstream planners
            return {
                "segments": [seg.model_dump() for seg in doc.transcript_segments],
                "_source": "block_analysis"
            }
        
        # Fallback to legacy path
        proj_dir = self._project_dir(project_id)
        return self._load_transcript(proj_dir)

    def get_status(self, project_id: str) -> str | None:
        script = self.get_script(project_id)
        return script.get("status") if script else None

    # ------------------------------------------------------------------
    # Phase B: block-based script workflow (enhance_1.md)
    # ------------------------------------------------------------------

    # Rough narration pacing: ~14 chars/second is used as a conservative
    # estimate for validating script length against block duration.
    CHARS_PER_SECOND = 14

    def generate_block_draft(
        self,
        project_id: str,
        block_id: str,
        instructions: str = "",
        tone: str = "bựa bựa, hài hước, bắt trend",
    ) -> str:
        """Generate an AI draft for a single block's script.

        Only ``script_draft`` is updated. ``script_final``, timecodes and
        ``human_verified`` are never touched. The document is persisted as a
        new version.
        """
        from pipeline.llm_client import chat_text

        doc = self._media_service.get_current_analysis(project_id)
        block = doc.get_block(block_id)
        if block is None:
            raise AnalysisError(f"Block {block_id} not found in project {project_id}")

        limit_seconds = block.duration_ms / 1000.0
        limit_chars = max(20, int(limit_seconds * self.CHARS_PER_SECOND))

        system_prompt = (
            "Bạn là biên kịch video. Viết lời đọc ngắn gọn, tự nhiên, đúng giới hạn ký tự. "
            "Chỉ trả về nội dung lời đọc, không giải thích, không markdown."
        )
        user_prompt = (
            f"Mô tả hình ảnh: {block.description or '(không có)'}\n"
            f"Transcript gốc: {block.transcript or '(không có)'}\n"
            f"Giọng điệu: {tone}\n"
            f"Yêu cầu thêm: {instructions or '(không có)'}\n"
            f"Giới hạn TUYỆT ĐỐI: tối đa {limit_chars} ký tự "
            f"(khoảng {limit_seconds:.1f} giây)."
        )
        draft = chat_text(system_prompt, user_prompt)
        if not draft:
            raise AnalysisError(f"AI returned empty draft for block {block_id}")
        # Hard-trim over-length drafts (never extend timecodes)
        if len(draft) > limit_chars * 2:
            draft = draft[: limit_chars * 2]

        # Guard: AI must never touch final/verified/timecodes.
        block.script_draft = draft
        self._media_service.save_document(doc)
        return draft

    def save_block_script(
        self,
        project_id: str,
        block_id: str,
        script_final: str,
    ) -> AnalysisDocument:
        """Save the human-edited final script for one block.

        Validates non-empty text and rough duration fit; resets
        ``human_verified`` when content changes materially.
        """
        doc = self._media_service.get_current_analysis(project_id)
        block = doc.get_block(block_id)
        if block is None:
            raise AnalysisError(f"Block {block_id} not found in project {project_id}")

        text = (script_final or "").strip()
        if not text:
            raise AnalysisError(
                f"Block {block_id} requires narration; script_final cannot be empty"
            )

        limit_chars = max(20, int((block.duration_ms / 1000.0) * self.CHARS_PER_SECOND))
        if len(text) > limit_chars * 2:
            raise AnalysisError(
                f"Script for {block_id} is too long ({len(text)} chars > "
                f"{limit_chars * 2} allowed for {block.duration_ms}ms window)"
            )

        if block.script_final is not None and block.script_final != text:
            block.human_verified = False
            if block_id in doc.review.verified_block_ids:
                doc.review.verified_block_ids.remove(block_id)

        block.script_final = text
        self._media_service.save_document(doc)
        return doc

    def approve_reviewed_blocks(self, project_id: str) -> AnalysisDocument:
        """Approve the block-based script workflow.

        Returns the approved document. Raises AnalysisError with per-block
        diagnostics if any gate fails.
        """
        doc = self._media_service.get_current_analysis(project_id)
        problems: List[Dict[str, str]] = []

        narration_blocks = [
            b for b in doc.content_blocks if (b.script_draft or "").strip()
        ]
        if not doc.content_blocks:
            problems.append({
                "block_id": "",
                "issue": "Analysis document has no content blocks",
            })

        for block in doc.content_blocks:
            needs_narration = bool(
                (block.script_draft or "").strip() or (block.transcript or "").strip()
            )
            if needs_narration:
                if not (block.script_final or "").strip():
                    problems.append({
                        "block_id": block.block_id,
                        "issue": "Missing script_final for narration block",
                    })
                if not block.human_verified:
                    problems.append({
                        "block_id": block.block_id,
                        "issue": "Block not human_verified",
                    })
            # Script duration check (~ chars/second pacing)
            if (block.script_final or "").strip():
                limit_chars = max(
                    20,
                    int((block.duration_ms / 1000.0) * self.CHARS_PER_SECOND) * 2,
                )
                if len(block.script_final) > limit_chars:
                    problems.append({
                        "block_id": block.block_id,
                        "issue": (
                            f"Script too long for {block.duration_ms}ms window "
                            f"({len(block.script_final)} > {limit_chars} chars)"
                        ),
                    })

        # Narration overlap check (sorted sweep)
        final_blocks = sorted(
            [b for b in doc.content_blocks if (b.script_final or "").strip()],
            key=lambda b: b.start_ms,
        )
        for a, b in zip(final_blocks, final_blocks[1:]):
            if a.end_ms > b.start_ms:
                problems.append({
                    "block_id": b.block_id,
                    "issue": (
                        f"Narration overlap with {a.block_id} "
                        f"({a.start_ms}-{a.end_ms}ms vs {b.start_ms}-{b.end_ms}ms)"
                    ),
                })

        if problems:
            raise AnalysisError(
                "Approve rejected: " + json.dumps(problems, ensure_ascii=False)
            )

        from datetime import datetime, timezone as _tz
        from core.analysis_schemas import ReviewStatus
        doc.review.status = ReviewStatus.APPROVED
        doc.review.approved_at = datetime.now(_tz.utc)
        self._media_service.save_document(doc)
        logger.info("Block script approved for project %s", project_id)
        return doc

    def build_script_from_analysis(self, project_id: str) -> Dict[str, Any]:
        """Build a legacy-compatible timeline dict from approved analysis blocks.

        Every item keeps ``block_id``, ``start_ms`` and ``end_ms``. Blocks
        without narration are preserved as visual-only clips.
        """
        doc = self._media_service.get_current_analysis(project_id)
        if doc.review.status != "approved":
            raise ScriptNotApprovedError(
                f"Analysis for {project_id} is not approved "
                f"(status={doc.review.status})"
            )
        items: List[Dict[str, Any]] = []
        for block in doc.content_blocks:
            items.append({
                "id": block.block_id,
                "block_id": block.block_id,
                "start_ms": block.start_ms,
                "end_ms": block.end_ms,
                "text": block.script_final or "",
                "narration": bool((block.script_final or "").strip()),
                "description": block.description,
                "audio_delivery": (
                    block.audio_delivery.model_dump() if block.audio_delivery else None
                ),
            })
        return {
            "source": "block_analysis",
            "analysis_id": doc.analysis_id,
            "version": doc.version,
            "beats": items,
        }

    # ---- generation ----

    def generate(
        self,
        project_id: str,
        tone: str = "bựa bựa, hài hước, bắt trend",
        target_duration_seconds: float = 60.0,
        duration_tolerance_seconds: float = 2.0,
    ) -> Dict[str, Any]:
        """Call the LLM to (re)write the script fitting target duration. Result starts as 'draft'."""
        proj_dir = self._project_dir(project_id)
        # Use effective transcript which supports both legacy and block-based sources
        transcript = self._get_effective_transcript_data(project_id)
        script = rewrite_script(
            transcript,
            tone=tone,
            target_duration_seconds=target_duration_seconds,
            duration_tolerance_seconds=duration_tolerance_seconds,
        )
        save_script(proj_dir, script)
        logger.info(
            "Script generated for project %s (%d beats, target %.0fs)",
            project_id, len(script["beats"]), target_duration_seconds,
        )
        return script

    def duration_check(self, project_id: str) -> Dict[str, Any] | None:
        """Estimated narration duration vs target for the current script, or None."""
        script = self.get_script(project_id)
        if not script:
            return None
        return duration_check(script)

    # ---- review workflow ----

    def save_edits(self, project_id: str, beats: list[Dict[str, Any]], title: str | None = None) -> Dict[str, Any]:
        """Persist user-edited beats; resets status to 'edited'."""
        proj_dir = self._project_dir(project_id)
        script = load_script(proj_dir)
        if script is None:
            raise AnalysisError("Chưa có script để chỉnh sửa. Hãy generate trước.")
        script["beats"] = beats
        if title:
            script["title"] = title
        script["status"] = "edited"
        save_script(proj_dir, script)
        logger.info("Script edits saved for project %s", project_id)
        return script

    def approve(self, project_id: str) -> Dict[str, Any]:
        proj_dir = self._project_dir(project_id)
        script = load_script(proj_dir)
        if script is None:
            raise AnalysisError("Chưa có script để duyệt.")
        script["status"] = "approved"
        save_script(proj_dir, script)
        logger.info("Script approved for project %s", project_id)
        return script

    def reject(self, project_id: str) -> Dict[str, Any]:
        proj_dir = self._project_dir(project_id)
        script = load_script(proj_dir)
        if script is None:
            raise AnalysisError("Chưa có script để từ chối.")
        script["status"] = "rejected"
        save_script(proj_dir, script)
        logger.info("Script rejected for project %s", project_id)
        return script

    # ---- gate + timeline ----

    def build_script_timeline(self, project_id: str) -> Dict[str, Any]:
        """
        Convert the APPROVED script into a timeline. This is the confirm gate:
        raises ScriptNotApprovedError for draft/edited/rejected scripts.
        """
        status = self.get_status(project_id)
        if status != "approved":
            raise ScriptNotApprovedError(
                f"Script chưa được duyệt (trạng thái: {status or 'không có'}). "
                "Vui lòng review và approve script trước khi tạo timeline."
            )

        proj_dir = self._project_dir(project_id)
        script = load_script(proj_dir)
        # Use effective transcript which supports both legacy and block-based sources
        transcript = self._get_effective_transcript_data(project_id)

        # Script-led planner: output timeline sized by script target duration;
        # each beat gets its own output window (never stretched source gaps).
        try:
            timeline = plan_script_timeline(script, transcript)
            timeline["script_status"] = status
        except ValueError as exc:
            raise AnalysisError(f"Không dựng được timeline: {exc}")

        if not timeline.get("clips"):
            raise AnalysisError("Không map được beat nào sang clip. Kiểm tra script/transcript.")

        # Warn (not fail) when duration is out of tolerance — UI surfaces this.
        if timeline.get("duration_status") == "out_of_tolerance":
            logger.warning(
                "Script timeline out of tolerance: %.1fs vs target %.1fs",
                timeline["actual_duration"], timeline["target_duration"],
            )
        logger.info(
            "Script timeline built for %s: %d clips, %.1fs",
            project_id, len(timeline["clips"]), timeline["actual_duration"],
        )
        return timeline

    # ---- audio approval gate (listen before render) ----

    def export_script_txt(self, project_id: str) -> Path:
        """Write the full script (title + beats) as a .txt file under exports/ for human review."""
        script = self.get_script(project_id)
        if not script:
            raise AnalysisError("Chưa có script để xuất.")
        exports_dir = self._project_dir(project_id) / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"# {script.get('title') or 'Script'}"]
        for i, b in enumerate(script.get("beats") or [], 1):
            lines.append(f"\n[Beat {i}] ({b.get('emotion', '')})\n{b.get('text', '')}")
        txt_path = exports_dir / "script.txt"
        txt_path.write_text("\n".join(lines), encoding="utf-8")
        return txt_path

    def approve_audio(self, project_id: str) -> Dict[str, Any]:
        """Mark the current script's review-audio as approved (unlocks rendering)."""
        proj_dir = self._project_dir(project_id)
        script = load_script(proj_dir)
        if script is None:
            raise AnalysisError("Chưa có script để duyệt audio.")
        script["audio_approved"] = True
        save_script(proj_dir, script)
        logger.info("Script audio approved for project %s", project_id)
        return script

    def is_audio_approved(self, project_id: str) -> bool:
        script = self.get_script(project_id)
        return bool(script and script.get("audio_approved"))

    # ---- script audio export (listen before render) ----

    def export_script_audio(
        self,
        project_id: str,
        voice: str = "vi-VN-NamMinhNeural",
        with_karaoke: bool = True,
    ) -> Dict[str, Any]:
        """
        Render the current script to a TTS-only audio preview WITHOUT video:
        narration WAV/MP3 + SRT + (optional) karaoke ASS under
        projects/<id>/exports/. Uses beat word-count share as cue spacing so
        users can listen to the full script before approving a render.
        """
        import tempfile
        import subprocess

        from pipeline.subtitles import build_karaoke_ass, build_srt
        from pipeline.tts import _probe_duration, synthesize_narration

        script = self.get_script(project_id)
        if not script:
            raise AnalysisError("Chưa có script. Hãy generate script trước.")
        beats = [b for b in script.get("beats") or [] if str(b.get("text") or "").strip()]
        if not beats:
            raise AnalysisError("Script không có beat nào có nội dung.")

        target = float(script.get("target_duration_seconds") or 60.0)
        total_words = sum(max(1, len(str(b.get("text") or "").split())) for b in beats)

        items: list[Dict[str, Any]] = []
        cursor = 0.0
        for b in beats:
            text = str(b.get("text") or "").strip()
            share = max(1, len(text.split())) / total_words
            window = target * share
            # Split the beat text into sentence cues spread over its window.
            from pipeline.tts import _split_sentences

            import re as _re

            sentences = _split_sentences(text)
            # edge-tts rejects cues without actual letters (punctuation-only /
            # very short fragments raise NoAudioReceived). Drop them here and
            # redistribute the timing across the remaining sentences.
            sentences = [s for s in sentences if _re.search(r"\w", s, _re.UNICODE)]
            if not sentences:
                continue
            chars = sum(len(s) for s in sentences) or 1
            pos = 0.0
            for s in sentences:
                cue_dur = window * (len(s) / chars)
                items.append(
                    {
                        "output_start": round(cursor + pos, 3),
                        "text": s,
                        "clip_duration": round(cue_dur, 3),
                        "rate": "+0%",
                        "pitch": "+0Hz",
                    }
                )
                pos += cue_dur
            cursor += window

        exports_dir = self._project_dir(project_id) / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tts_export_") as td:
            temp_dir = Path(td)
            narrated = synthesize_narration(items, temp_dir, voice=voice)
            from pipeline.tts import build_narration_track

            track = build_narration_track(narrated, target, temp_dir / "narration.wav")
            wav_out = exports_dir / "script_audio.wav"
            mp3_out = exports_dir / "script_audio.mp3"
            import shutil

            shutil.copyfile(track, wav_out)
            # MP3 for easy playback/sharing.
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_out), "-b:a", "160k", str(mp3_out)],
                capture_output=True, check=True,
            )
            build_srt(narrated, exports_dir / "script_audio.srt")
            if with_karaoke:
                build_karaoke_ass(narrated, exports_dir / "script_audio_karaoke.ass")

        duration = _probe_duration(wav_out)
        logger.info("Script audio exported for %s: %.1fs", project_id, duration)
        return {
            "wav": str(wav_out),
            "mp3": str(mp3_out),
            "srt": str(exports_dir / "script_audio.srt"),
            "ass_karaoke": str(exports_dir / "script_audio_karaoke.ass") if with_karaoke else None,
            "duration_seconds": duration,
        }

    # ---- helpers ----

    @staticmethod
    def _project_dir(project_id: str) -> Path:
        from config.settings import settings

        return Path(settings.projects_dir) / project_id

    @staticmethod
    def _load_transcript(proj_dir: Path) -> Dict[str, Any]:
        path = proj_dir / "analysis" / "transcription.json"
        if not path.exists():
            raise AnalysisError(
                "Chưa có transcript. Chạy phân tích video trước khi viết script."
            )
        data = read_json(path)
        # transcription.json may be either a bare list of segments (current
        # AnalysisService format) or a dict with a "segments" key (legacy).
        if isinstance(data, list):
            data = {"segments": data}
        if not isinstance(data, dict) or not data.get("segments"):
            raise AnalysisError("Transcript trống hoặc hỏng.")
        return data
