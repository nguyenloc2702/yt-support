from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8501

    data_dir: Path = Path("./data")
    projects_dir: Path = Path("./projects")
    downloads_dir: Path = Path("G:/My Drive/Video_Projects/downloads")
    log_dir: Path = Path("./logs")

    max_upload_gb: int = 10
    max_concurrent_jobs: int = 1

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ytdlp_path: str = "yt-dlp"
    max_download_height: int = 2160

    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    whisper_beam_size: int = 5
    whisper_vad: bool = True
    whisper_vad_min_silence_ms: int = 500

    default_scene_threshold: float = 27.0
    default_silence_db: float = -35.0
    default_silence_duration: float = 0.6

    preview_crf: int = 28
    final_crf: int = 20

    max_music_upload_mb: int = 500
    default_voice_gain_db: float = 0.0
    default_music_gain_db: float = -16.5
    default_target_lufs: float = -16.0
    default_loudness_range: float = 11.0
    default_true_peak_db: float = -1.5
    default_music_fade_in_seconds: float = 2.0
    default_music_fade_out_seconds: float = 3.0
    min_test_preview_seconds: int = 10
    max_test_preview_seconds: int = 30
    default_test_preview_seconds: int = 20
    render_duration_tolerance_seconds: float = 0.75

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def _load_env_into_os_environ() -> None:
    """Expose .env entries to os.environ (pydantic only reads them into Settings).

    The settings DB lives on Google Drive and its rows can be reverted by a
    stale cloud sync; callers fall back to os.environ (e.g. XKIRO_API_KEY), so
    the .env values must actually be present there at runtime.
    """
    import os

    env_file = Path(".env")
    if not env_file.is_file():
        return
    try:
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
    except OSError:
        pass


_load_env_into_os_environ()

settings = Settings()