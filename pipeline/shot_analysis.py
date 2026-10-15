"""Shot/scene detection for video analysis pipeline.

This module provides functions to detect shots (scenes) in a video and return
schema-compatible ShotAnalysis objects.
"""

from pathlib import Path
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from pipeline.media_probe import probe_media
from core.analysis_schemas import ShotAnalysis, MediaInfo
from core.exceptions import AnalysisError


def detect_shots(
    video_path: Path | str,
    min_scene_length: float = 0.5,
    threshold: float = 30.0,
) -> List[ShotAnalysis]:
    """Detect shots in a video file using PySceneDetect.
    
    Args:
        video_path: Path to the video file
        min_scene_length: Minimum scene length in seconds (default: 0.5)
        threshold: Content detection threshold (default: 30.0)
        
    Returns:
        List of ShotAnalysis objects with detected shots
        
    Raises:
        AnalysisError: If scene detection fails
    """
    video_path = Path(video_path)
    
    try:
        from scenedetect import VideoManager, SceneManager
        from scenedetect.detectors import ContentDetector
    except ImportError as e:
        raise AnalysisError(f"PySceneDetect not available: {e}")
    
    video_manager = None
    try:
        # Get media info for frame rate
        media_info = probe_media(video_path)
        fps = media_info.fps
        
        video_manager = VideoManager([str(video_path)])
        scene_manager = SceneManager()
        scene_manager.add_detector(
            ContentDetector(threshold=threshold, min_scene_len=min_scene_length)
        )
        video_manager.set_downscale_factor()
        video_manager.start()
        scene_manager.detect_scenes(frame_source=video_manager)
        
        scenes = scene_manager.get_scene_list()
        shots = []
        
        for i, scene in enumerate(scenes):
            start_seconds = scene[0].get_seconds()
            end_seconds = scene[1].get_seconds()
            
            # Convert to milliseconds
            start_ms = int(start_seconds * 1000)
            end_ms = int(end_seconds * 1000)
            
            # Calculate frame index
            frame_index = int(start_seconds * fps)
            
            # Generate shot ID
            shot_id = f"shot_{i:04d}"
            
            shot = ShotAnalysis(
                shot_id=shot_id,
                start_ms=start_ms,
                end_ms=end_ms,
                frame_index=frame_index,
                confidence=0.95,  # Default confidence for scene detection
                description="",  # Will be filled later by VLM
            )
            shots.append(shot)
        
        return shots
        
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


def detect_shots_with_frames(
    video_path: Path | str,
    output_dir: Path | str,
    min_scene_length: float = 0.5,
    threshold: float = 30.0,
    frames_per_shot: int = 1,
) -> List[ShotAnalysis]:
    """Detect shots and extract representative frames.
    
    Args:
        video_path: Path to the video file
        output_dir: Directory to save extracted frames
        min_scene_length: Minimum scene length in seconds
        threshold: Content detection threshold
        frames_per_shot: Number of frames to extract per shot
        
    Returns:
        List of ShotAnalysis objects with frame paths
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    shots = detect_shots(video_path, min_scene_length, threshold)
    
    # Extract frames for each shot
    media_info = probe_media(video_path)
    fps = media_info.fps
    
    for shot in shots:
        # Extract middle frame
        shot_start_sec = shot.start_ms / 1000.0
        shot_end_sec = shot.end_ms / 1000.0
        shot_duration = shot_end_sec - shot_start_sec
        
        # Get middle time
        middle_sec = shot_start_sec + (shot_duration / 2)
        
        # Generate frame filename
        frame_filename = f"shot_{shot.shot_id}_frame.jpg"
        frame_path = output_dir / frame_filename
        
        # Extract frame using ffmpeg
        try:
            from pipeline.ffmpeg import run_ffmpeg
            from config.settings import settings
            
            cmd = [
                settings.ffmpeg_path,
                "-y",
                "-hide_banner",
                "-nostats",
                "-loglevel", "warning",
                "-i", str(video_path),
                "-ss", str(middle_sec),
                "-frames:v", "1",
                "-q:v", "2",
                str(frame_path),
            ]
            run_ffmpeg(cmd, timeout=30)
            
            if frame_path.exists():
                shot.frame_path = str(frame_path.relative_to(output_dir))
        except Exception:
            # Frame extraction failed, continue without frame
            pass
    
    return shots


def shots_to_dict_list(shots: List[ShotAnalysis]) -> List[dict]:
    """Convert ShotAnalysis objects to dictionary list for JSON serialization.
    
    Args:
        shots: List of ShotAnalysis objects
        
    Returns:
        List of dictionaries
    """
    return [
        {
            "shot_id": shot.shot_id,
            "start_ms": shot.start_ms,
            "end_ms": shot.end_ms,
            "duration_ms": shot.duration_ms,
            "frame_path": shot.frame_path,
            "frame_index": shot.frame_index,
            "confidence": shot.confidence,
            "description": shot.description,
        }
        for shot in shots
    ]


def dict_list_to_shots(data: List[dict]) -> List[ShotAnalysis]:
    """Convert dictionary list to ShotAnalysis objects.
    
    Args:
        data: List of shot dictionaries
        
    Returns:
        List of ShotAnalysis objects
    """
    return [
        ShotAnalysis(
            shot_id=d["shot_id"],
            start_ms=d["start_ms"],
            end_ms=d["end_ms"],
            frame_path=d.get("frame_path"),
            frame_index=d.get("frame_index"),
            confidence=d.get("confidence", 0.95),
            description=d.get("description", ""),
        )
        for d in data
    ]
