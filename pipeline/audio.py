from pathlib import Path
from pipeline.ffmpeg import run_ffmpeg
from core.exceptions import RenderError

def extract_audio(input_path: Path, output_path: Path) -> Path:
    """
    Extract audio to mono 16kHz PCM WAV.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-i", str(input_path),
        "-vn",           # no video
        "-ac", "1",      # mono
        "-ar", "16000",  # 16kHz
        "-c:a", "pcm_s16le",
        str(output_path)
    ]
    try:
        run_ffmpeg(args)
    except Exception as e:
        raise RenderError(f"Audio extraction failed: {e}")
    return output_path