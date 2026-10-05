import ipaddress
import re
import shutil
import socket
import subprocess
from urllib.parse import urlparse

from pathlib import Path
from typing import Callable, List, Optional

from config.settings import settings
from core.exceptions import DependencyNotFoundError, InvalidMediaError, JobCancelledError


def _ytdlp_path() -> str:
    """Return the configured yt-dlp path, tolerating an older settings object."""
    return getattr(settings, "ytdlp_path", "yt-dlp")


def _js_runtime_args() -> List[str]:
    """
    Build arguments for a JavaScript runtime.

    YouTube extraction requires a JS runtime to solve the player signature.
    Without one, yt-dlp falls back to clients whose formats frequently
    respond with HTTP 403. Prefer deno, then node; if neither exists, steer
    yt-dlp toward clients that do not need JS.
    """
    if shutil.which("deno"):
        return ["--js-runtimes", "deno"]
    if shutil.which("node"):
        return ["--js-runtimes", "node"]
    return ["--extractor-args", "youtube:player_client=android,ios"]


def check_ytdlp() -> bool:
    """Return True if the yt-dlp binary is available."""
    return shutil.which(_ytdlp_path()) is not None


def validate_public_url(url: str) -> None:
    """
    Reject URLs that resolve to loopback, private, or link-local addresses.

    Without this guard, a user of a public VPS-hosted instance could submit
    http://127.0.0.1:8501 or http://169.254.169.254/ and make the server
    fetch internal services (SSRF). Only called on user-supplied URLs; the
    localhost UI itself is unaffected.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidMediaError("URL must use http or https")
    host = parsed.hostname or ""
    if not host:
        raise InvalidMediaError("URL has no host")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise InvalidMediaError(f"Cannot resolve host '{host}'") from exc

    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (
            addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise InvalidMediaError(
                f"Refusing to download from non-public address '{host}'"
            )


def _terminate(proc: subprocess.Popen, timeout: int = 10) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def download_video(
    url: str,
    output_dir: Path,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancellation_token=None,
    max_height: int = 2160,
) -> Path:
    """
    Download a video with yt-dlp and merge to mp4.

    Equivalent CLI:
        yt-dlp -f "bestvideo[height<=2160]+bestaudio/best[height<=2160]"
               --merge-output-format mp4 "URL"

    Returns the path of the downloaded .mp4 file. Supports cooperative
    cancellation through a threading.Event passed as `cancellation_token`.
    """
    if not url or not re.match(r"^https?://", url.strip()):
        raise InvalidMediaError(f"Invalid URL: {url!r}")

    if not check_ytdlp():
        raise DependencyNotFoundError(
            f"yt-dlp not found at {_ytdlp_path()!r}. Install it with: pip install yt-dlp"
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(output_dir / "download.%(ext)s")

    fmt = (
        f"bestvideo[height<={max_height}]+bestaudio/"
        f"best[height<={max_height}]"
    )
    cmd = [
        _ytdlp_path(),
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--newline",
        "--no-color",
        "--no-update",
        *_js_runtime_args(),
        "--retries", "10",
        "--fragment-retries", "10",
        "--retry-sleep", "3",
        "--concurrent-fragments", "4",
        "--progress",
        "--progress-template",
        "download:%(progress._percent_str)s %(progress._speed_str)s %(progress._eta_str)s",
        "-o", out_template,
        url.strip(),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    last_percent = -1
    tail_lines: List[str] = []
    try:
        for raw in proc.stdout:
            if cancellation_token is not None and cancellation_token.is_set():
                _terminate(proc)
                raise JobCancelledError("Download cancelled by user")
            line = raw.rstrip("\n")
            if not line:
                continue
            # progress lines look like: "  12.3% 1.2MiB/s 00:30"
            m = re.match(r"\s*([0-9.]+)%", line)
            # Keep only non-progress lines so error tails stay readable.
            if not m:
                tail_lines.append(line)
                if len(tail_lines) > 200:
                    tail_lines.pop(0)
            if m and progress_callback:
                try:
                    pct = int(float(m.group(1)))
                except ValueError:
                    continue
                if pct > last_percent:
                    last_percent = pct
                    progress_callback(pct, f"Downloading {pct}%")
            elif "[download]" in line and "Destination" in line:
                if progress_callback:
                    progress_callback(max(last_percent, 0), "Downloading...")
    finally:
        if proc.poll() is None:
            _terminate(proc)

    if proc.returncode != 0:
        tail = "\n".join(tail_lines[-30:])
        raise InvalidMediaError(f"yt-dlp failed (exit {proc.returncode}):\n{tail}")

    candidates = sorted(
        output_dir.glob("download.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise InvalidMediaError("yt-dlp reported success but no output file was found")

    result = candidates[0]
    if result.suffix.lower() != ".mp4":
        # Force remux to mp4 if merge produced a different container.
        remux_target = result.with_suffix(".mp4")
        subprocess.run(
            [
                settings.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "warning",
                "-i", str(result), "-c", "copy", str(remux_target),
            ],
            check=True,
        )
        try:
            result.unlink()
        except OSError:
            pass
        result = remux_target

    return result