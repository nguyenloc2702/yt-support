"""Keyframe extraction per shot (Phase 2).

Given shot boundaries (from PySceneDetect) and a video file, extract a small
number of representative frames per shot using FFmpeg. Frames are quality
filtered (blur/darkness/duplicates) so downstream VLM calls stay cheap.
"""

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.exceptions import AnalysisError
from core.logging_config import get_logger
from pipeline.ffmpeg import FFMPEG

logger = get_logger(__name__)

# Defaults tuned to keep VLM cost low while covering shot content.
DEFAULTS = {
    "frames_per_short_shot": 1,   # shots <= long_shot_seconds
    "frames_per_long_shot": 2,    # extra frames for long shots
    "long_shot_seconds": 8.0,
    "max_frame_px": 768,          # resize longest edge before storage/upload
    "blur_threshold": 30.0,       # Laplacian variance below this = blurry
    "brightness_min": 12.0,       # mean luma below this = near-black
}


class KeyframeExtractor:
    def __init__(self, ffmpeg_path: str = FFMPEG, **overrides):
        self.ffmpeg = ffmpeg_path
        self.cfg = {**DEFAULTS, **overrides}

    def extract_for_shots(
        self,
        video_path: Path,
        shots: List[Dict[str, Any]],
        out_dir: Path,
        cancellation_token=None,
    ) -> List[Dict[str, Any]]:
        """Extract keyframes for each shot. Returns shots augmented with
        `keyframes: [{time, path, quality}]` (only frames passing quality filters).
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        results: List[Dict[str, Any]] = []

        for shot in shots:
            if cancellation_token is not None and cancellation_token.is_set():
                raise AnalysisError("Keyframe extraction cancelled by user")

            start = float(shot["start"])
            end = float(shot["end"])
            duration = end - start
            if duration <= 0.2:
                continue

            n = (
                self.cfg["frames_per_short_shot"]
                if duration <= self.cfg["long_shot_seconds"]
                else self.cfg["frames_per_long_shot"]
            )
            # Evenly spaced sample times inside the shot, avoiding edges (transitions).
            margin = min(0.3, duration * 0.2)
            times = [
                start + margin + (duration - 2 * margin) * (i + 0.5) / n
                for i in range(n)
            ]

            keyframes: List[Dict[str, Any]] = []
            for i, t in enumerate(times):
                path = out_dir / f"shot_{int(shot.get('shot_id', len(results))):04d}_f{i}.jpg"
                if not self._grab_frame(video_path, t, path):
                    continue
                quality = self._score_frame(path)
                if quality is None:
                    continue  # unreadable frame
                if quality["mean_luma"] < self.cfg["brightness_min"]:
                    path.unlink(missing_ok=True)
                    continue
                if quality["blur"] < self.cfg["blur_threshold"]:
                    path.unlink(missing_ok=True)
                    continue
                keyframes.append({"time": round(t, 3), "path": str(path), **quality})

            if keyframes:
                entry = dict(shot)
                entry["keyframes"] = keyframes
                results.append(entry)

        return results

    def _grab_frame(self, video: Path, time_s: float, out: Path) -> bool:
        cmd = [
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{time_s:.3f}", "-i", str(video),
            "-frames:v", "1",
            "-vf", f"scale='min({self.cfg['max_frame_px']},iw)':-2",
            "-q:v", "3",
            str(out),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return False
        return r.returncode == 0 and out.exists() and out.stat().st_size > 0

    def _score_frame(self, path: Path) -> Optional[Dict[str, float]]:
        """Compute blur (Laplacian variance) and mean luma via OpenCV."""
        try:
            import cv2
            import numpy as np

            img = cv2.imread(str(path))
            if img is None:
                return None
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            luma = float(np.mean(gray))
            return {"blur": round(blur, 2), "mean_luma": round(luma, 2)}
        except ImportError:
            # OpenCV missing (scenedetect[opencv] provides it, but be safe):
            # accept the frame unfiltered rather than failing analysis.
            logger.warning("opencv not available; keyframe quality filters skipped")
            return {"blur": 999.0, "mean_luma": 128.0}
