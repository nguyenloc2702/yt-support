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
from typing import Any, Dict

from core.exceptions import (
    AnalysisError,
    ScriptNotApprovedError,
)
from core.logging_config import get_logger
from pipeline.beat_mapper import beats_to_clips, estimate_script_duration
from pipeline.script_rewrite import load_script, rewrite_script, save_script
from utils.json_io import read_json

logger = get_logger(__name__)

VALID_STATUSES = {"draft", "edited", "approved", "rejected"}
FINAL_STATUSES = {"approved", "rejected"}


class ScriptService:
    """State + gate enforcement for the AI-rewritten review script."""

    def get_script(self, project_id: str) -> Dict[str, Any] | None:
        return load_script(self._project_dir(project_id))

    def get_status(self, project_id: str) -> str | None:
        script = self.get_script(project_id)
        return script.get("status") if script else None

    # ---- generation ----

    def generate(self, project_id: str, tone: str = "bựa bựa, hài hước, bắt trend") -> Dict[str, Any]:
        """Call the LLM to (re)write the script. Result starts as 'draft'."""
        proj_dir = self._project_dir(project_id)
        transcript = self._load_transcript(proj_dir)
        script = rewrite_script(transcript, tone=tone)
        save_script(proj_dir, script)
        logger.info("Script generated for project %s (%d beats)", project_id, len(script["beats"]))
        return script

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
        transcript = self._load_transcript(proj_dir)
        clips = beats_to_clips(script, transcript)
        if not clips:
            raise AnalysisError("Không map được beat nào sang clip. Kiểm tra script/transcript.")

        # Attach beat narration text to each clip for TTS export.
        beats = script.get("beats", [])
        for i, clip in enumerate(clips):
            if i < len(beats):
                clip["narration_text"] = str(beats[i].get("text") or beats[i].get("new_text") or "").strip()

        timeline = {
            "mode": "script_review",
            "name": f"Script review: {script.get('title', '')}",
            "clips": [
                {
                    "id": f"clip_{i:03d}",
                    "source_start": c["source_start"],
                    "source_end": c["source_end"],
                    "order": i,
                    "enabled": True,
                    "label": "script_beat",
                    "reason": f"beat: {str(c.get('text', ''))[:80]}",
                    "narration_text": c.get("narration_text", ""),
                    "score": 1.0,
                    "transition": "cut",
                }
                for i, c in enumerate(clips, start=1)
            ],
            "actual_duration": sum(c["source_end"] - c["source_start"] for c in clips),
            "target_duration": estimate_script_duration(script, transcript),
            "script_status": status,
        }
        logger.info(
            "Script timeline built for %s: %d clips, %.1fs",
            project_id, len(timeline["clips"]), timeline["actual_duration"],
        )
        return timeline

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
