from pathlib import Path
from pipeline.ffmpeg import run_ffmpeg
from core.exceptions import RenderError

def create_proxy(input_path: Path, output_path: Path, max_width: int = 1280, max_height: int = 720, max_fps: float = 30.0) -> Path:
    """
    Create a proxy video (H.264, AAC, max 1280x720, max 30 fps).
    Returns output_path on success.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Build scale filter: scale to fit within max_width x max_height, keep aspect ratio
    scale_filter = f"scale='min({max_width},iw)':'min({max_height},ih)':force_original_aspect_ratio=decrease"
    # FPS filter if needed (max_fps)
    fps_filter = f"fps={max_fps}" if max_fps > 0 else None
    vf = [scale_filter]
    if fps_filter:
        vf.append(fps_filter)
    vf_str = ",".join(vf)

    args = [
        "-i", str(input_path),
        "-vf", vf_str,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path)
    ]
    try:
        run_ffmpeg(args)
    except Exception as e:
        raise RenderError(f"Proxy creation failed: {e}")
    return output_path