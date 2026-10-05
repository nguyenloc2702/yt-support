import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from pipeline.probe import probe_media
from pipeline.proxy import create_proxy
from pipeline.audio import extract_audio
from pipeline.transcribe import transcribe_audio
from pipeline.segments import build_candidate_segments
from pipeline.ranker import HeuristicContentRanker
from pipeline.timeline_builder import build_timeline
from pipeline.ffmpeg import FFMPEG
from utils.cleanup import cleanup_analysis_artifacts, cleanup_render_temp
from utils.disk import ensure_free_space

from core.database import SessionLocal
from core.models import Project
from core.enums import ProjectStatus
from core.exceptions import AnalysisError, InvalidMediaError, JobCancelledError
from config.settings import settings


class AnalysisService:
    def __init__(self):
        self.projects_dir = Path(settings.projects_dir)

    def _get_project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    def run_analysis(
        self,
        project_id: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancellation_token=None,
        options: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """
        Run the full analysis pipeline for a project.

        options (all optional):
            mode: "remove_silence" | "shorten" | "summary"
            target_duration: float seconds
            topic: str
            keywords: list[str]
            language: str  ("auto" | "vi" | "en" | ...)
            whisper_model: str
        """
        options = options or {}
        proj_dir = self._get_project_dir(project_id)
        source_path = proj_dir / "source" / "input.mp4"
        if not source_path.exists():
            raise InvalidMediaError(f"Source file not found for project {project_id}")

        analysis_dir = proj_dir / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)

        self._update_status(project_id, ProjectStatus.ANALYZING)

        def report(pct: int, msg: str) -> None:
            if progress_callback:
                progress_callback(pct, msg)

        def check_cancel() -> None:
            if cancellation_token is not None and cancellation_token.is_set():
                raise JobCancelledError("Analysis cancelled by user")

        try:
            source_size = source_path.stat().st_size
            ensure_free_space(proj_dir, source_size * 3)

            report(3, "Probing media...")
            check_cancel()
            media_info = probe_media(source_path)

            report(8, "Creating proxy...")
            check_cancel()
            proxy_path = proj_dir / "proxy" / "proxy_720p.mp4"
            if self._is_valid_video(proxy_path):
                report(12, "Proxy already exists, skipping.")
            else:
                if proxy_path.exists():
                    report(9, "Proxy cũ bị hỏng, đang tạo lại...")
                    try:
                        proxy_path.unlink()
                    except OSError:
                        pass
                create_proxy(source_path, proxy_path)
                if not self._is_valid_video(proxy_path):
                    raise AnalysisError(
                        f"Proxy tạo xong nhưng không giải mã được: {proxy_path}"
                    )

            report(15, "Extracting audio...")
            check_cancel()
            audio_path = proj_dir / "audio" / "speech.wav"
            if self._is_valid_file(audio_path):
                report(18, "Audio already exists, skipping.")
            else:
                extract_audio(source_path, audio_path)
            self._write_json(
                analysis_dir / "media_info.json",
                {
                    "duration_seconds": media_info.duration_seconds,
                    "width": media_info.width,
                    "height": media_info.height,
                    "fps": media_info.fps,
                    "video_codec": media_info.video_stream.get("codec_name")
                    if media_info.video_stream
                    else None,
                    "audio_codec": media_info.audio_stream.get("codec_name")
                    if media_info.audio_stream
                    else None,
                },
            )

            segments = self._load_json(analysis_dir / "transcription.json")
            if segments:
                report(58, f"Transcript already exists ({len(segments)} segments), skipping.")
                partial = analysis_dir / "transcription.partial.json"
                if partial.exists():
                    try:
                        partial.unlink()
                    except OSError:
                        pass
            else:
                partial = analysis_dir / "transcription.partial.json"
                if partial.exists():
                    report(20, "Tiếp tục phiên âm từ checkpoint...")
                else:
                    report(20, "Transcribing audio...")
                check_cancel()
                transcription = transcribe_audio(
                    audio_path,
                    model_size=options.get("whisper_model"),
                    language=options.get("language"),
                    progress_callback=lambda p, m: report(20 + int(p * 0.4), m),
                    cancellation_token=cancellation_token,
                    checkpoint_path=analysis_dir / "transcription.partial.json",
                )
                segments = transcription["segments"]
                self._write_json(analysis_dir / "transcription.json", segments)
                self._write_json(
                    analysis_dir / "transcription_meta.json",
                    {
                        "language": transcription.get("language"),
                        "language_probability": transcription.get("language_probability"),
                        "model": options.get("whisper_model") or settings.whisper_model,
                        "duration_seconds": transcription.get("duration"),
                    },
                )

            scenes = self._load_json(analysis_dir / "scenes.json")
            if scenes is None:
                report(62, "Detecting scenes...")
                check_cancel()
                scene_source = proxy_path if self._is_valid_video(proxy_path) else source_path
                try:
                    scenes = self._detect_scenes(scene_source)
                except AnalysisError:
                    if scene_source != source_path:
                        report(64, "Proxy lỗi khi phát hiện cảnh, thử lại với file gốc...")
                        check_cancel()
                        scenes = self._detect_scenes(source_path)
                    else:
                        raise
                self._write_json(analysis_dir / "scenes.json", scenes)
            else:
                report(68, f"Scenes already exist ({len(scenes)}), skipping.")

            silences = self._load_json(analysis_dir / "silences.json")
            if silences is None:
                report(72, "Detecting silences...")
                check_cancel()
                silences = self._detect_silence(audio_path)
                self._write_json(analysis_dir / "silences.json", silences)
            else:
                report(76, f"Silences already exist ({len(silences)}), skipping.")

            candidates = self._load_json(analysis_dir / "candidates.json")
            if candidates is None:
                report(80, "Building candidate segments...")
                check_cancel()
                candidates = build_candidate_segments(
                    transcript=segments,
                    scenes=scenes,
                    silences=silences,
                    source_duration=media_info.duration_seconds,
                )
                self._write_json(analysis_dir / "candidates.json", candidates)
            else:
                report(84, f"Candidates already exist ({len(candidates)}), skipping.")

            ranked = self._load_json(analysis_dir / "ranked.json")
            if ranked is None:
                report(88, "Ranking candidates...")
                ranker = HeuristicContentRanker(
                    topic=options.get("topic", "") or "",
                    keywords=options.get("keywords", []) or [],
                )
                ranked = ranker.rank(candidates)
                self._write_json(analysis_dir / "ranked.json", ranked)
            else:
                report(91, f"Ranked candidates already exist ({len(ranked)}), skipping.")

            report(94, "Building suggested timeline...")
            target_duration = options.get("target_duration")
            if not target_duration:
                target_duration = min(media_info.duration_seconds * 0.15, 300.0)
                if target_duration < 30:
                    target_duration = min(media_info.duration_seconds, 60.0)

            timeline = build_timeline(
                candidates=ranked,
                mode=options.get("mode", "shorten"),
                target_duration=target_duration,
                source_duration=media_info.duration_seconds,
                preserve_source_order=True,
                output_aspect_ratio=options.get("output_aspect_ratio", "16:9"),
            )
            timeline["project_id"] = project_id
            self._write_json(proj_dir / "timelines" / "current.json", timeline)

            with SessionLocal() as db:
                project = db.query(Project).filter(Project.id == project_id).first()
                if project:
                    project.duration_seconds = media_info.duration_seconds
                    project.width = media_info.width
                    project.height = media_info.height
                    project.fps = media_info.fps
                    project.status = ProjectStatus.ANALYSIS_READY.value
                    db.commit()

            # Free re-extractable intermediates (WAV) once the transcript
            # exists - on a small VPS this saves hundreds of MB per project.
            try:
                cleanup_analysis_artifacts(proj_dir)
                cleanup_render_temp(proj_dir)
            except Exception:  # noqa: BLE001 - cleanup must never fail analysis
                pass

            report(100, "Analysis complete.")
            return {
                "status": "success",
                "media_info": {
                    "duration": media_info.duration_seconds,
                    "width": media_info.width,
                    "height": media_info.height,
                    "fps": media_info.fps,
                },
            }

        except JobCancelledError:
            self._update_status(project_id, ProjectStatus.FAILED)
            raise
        except Exception as exc:
            self._update_status(project_id, ProjectStatus.FAILED)
            raise AnalysisError(f"Analysis failed: {exc}")

    def _update_status(self, project_id: str, status: ProjectStatus) -> None:
        with SessionLocal() as db:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                project.status = status.value
                db.commit()

    def _detect_scenes(self, video_path: Path) -> list:
        video_manager = None
        try:
            from scenedetect import VideoManager, SceneManager
            from scenedetect.detectors import ContentDetector

            video_manager = VideoManager([str(video_path)])
            scene_manager = SceneManager()
            scene_manager.add_detector(ContentDetector())
            video_manager.set_downscale_factor()
            video_manager.start()
            scene_manager.detect_scenes(frame_source=video_manager)
            scenes = []
            for i, scene in enumerate(scene_manager.get_scene_list()):
                scenes.append(
                    {
                        "scene_id": i,
                        "start": scene[0].get_seconds(),
                        "end": scene[1].get_seconds(),
                    }
                )
            return scenes
        except Exception as exc:
            raise AnalysisError(
                f"Scene detection failed for {video_path.name}: "
                f"{type(exc).__name__}: {exc}"
            )
        finally:
            if video_manager is not None:
                try:
                    video_manager.release()
                except Exception:
                    pass

    def _detect_silence(self, audio_path: Path) -> list:
        try:
            af = (
                f"silencedetect=noise={settings.default_silence_db}dB:"
                f"d={settings.default_silence_duration}"
            )
            cmd = [
                FFMPEG,
                "-hide_banner",
                "-nostats",
                "-i", str(audio_path),
                "-af", af,
                "-f", "null",
                "-",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            silences = []
            for line in result.stderr.split("\n"):
                if "silence_start" in line:
                    start = float(line.split("silence_start:")[1].strip())
                    silences.append({"start": start})
                elif "silence_end" in line:
                    end = float(line.split("silence_end:")[1].split("|")[0].strip())
                    if silences and "end" not in silences[-1]:
                        silences[-1]["end"] = end
            return [s for s in silences if "start" in s and "end" in s]
        except Exception as exc:
            raise AnalysisError(f"Silence detection failed: {exc}")

    @staticmethod
    def _is_valid_file(path: Path) -> bool:
        """True when a previous stage produced a non-empty artefact."""
        try:
            return path.exists() and path.stat().st_size > 0
        except OSError:
            return False

    @staticmethod
    def _is_valid_video(path: Path) -> bool:
        """True when the file exists and ffprobe can decode a video stream from it."""
        try:
            if not path.exists() or path.stat().st_size <= 0:
                return False
            probe_media(path)
            return True
        except (InvalidMediaError, OSError):
            return False
        except Exception:
            return False

    @staticmethod
    def _load_json(path: Path) -> Any:
        """Load a previously written stage artefact, or None if absent/corrupt."""
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)