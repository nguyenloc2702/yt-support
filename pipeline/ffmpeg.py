import json
import subprocess
import threading
from typing import Callable, List, Optional

from config.settings import settings
from core.exceptions import JobCancelledError

FFMPEG = settings.ffmpeg_path
FFPROBE = settings.ffprobe_path


def check_binary(name: str) -> bool:
    try:
        subprocess.run([name, "-version"], capture_output=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def run_ffmpeg(args: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    """
    Run ffmpeg for short operations. Output is bounded (-nostats/-loglevel warning)
    so long encodes do not buffer megabytes of progress text in memory.
    No timeout by default so multi-hour proxy/audio jobs are not killed.
    """
    cmd = [FFMPEG, "-y", "-hide_banner", "-nostats", "-loglevel", "warning"] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)


def run_ffprobe(args: List[str], timeout: Optional[int] = 120) -> dict:
    cmd = [FFPROBE, "-v", "quiet", "-print_format", "json"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
    return json.loads(result.stdout)


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


def run_ffmpeg_with_progress(
    args: List[str],
    total_duration: float,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancellation_token=None,
    base_percent: int = 0,
    span_percent: int = 100,
    step_label: str = "",
) -> None:
    """
    Run ffmpeg while parsing `-progress pipe:1` output. Reports progress mapped into
    [base_percent, base_percent + span_percent]. Supports cooperative cancellation via
    a threading.Event exposed as `cancellation_token`. stderr is drained on a background
    thread to avoid pipe-buffer deadlocks on long jobs.
    """
    cmd = [FFMPEG, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1"] + args
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stderr_lines: List[str] = []

    def _drain(pipe) -> None:
        try:
            for line in pipe:
                stderr_lines.append(line)
        except Exception:
            pass

    drain_thread = threading.Thread(target=_drain, args=(proc.stderr,), daemon=True)
    drain_thread.start()

    last_percent = base_percent
    try:
        for raw in proc.stdout:
            if cancellation_token is not None and cancellation_token.is_set():
                _terminate(proc)
                raise JobCancelledError("Job cancelled by user")
            line = raw.strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    value = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                if value < 0:
                    continue
                seconds = value / 1_000_000.0
                if total_duration > 0 and progress_callback:
                    ratio = min(seconds / total_duration, 1.0)
                    pct = base_percent + int(ratio * span_percent)
                    if pct > last_percent:
                        last_percent = pct
                        progress_callback(pct, step_label)
            elif line == "progress=end":
                break
    finally:
        if proc.poll() is None:
            # `progress=end` is emitted before ffmpeg has finished writing the
            # container trailer. Wait for a clean exit instead of killing the
            # process, otherwise a successful encode is turned into a non-zero
            # return code and reported as a failure.
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                _terminate(proc)
        drain_thread.join(timeout=2)

    if proc.returncode != 0:
        tail = "".join(stderr_lines).splitlines()[-80:]
        raise subprocess.CalledProcessError(proc.returncode, cmd, stderr="\n".join(tail))


def available_encoders() -> set:
    """Return the set of encoder names reported by `ffmpeg -encoders`."""
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return set()
    encoders = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] and parts[0][0] in ("V", "A"):
            encoders.add(parts[1])
    return encoders


def pick_video_encoder(preferred: str = "libx264") -> str:
    """Return `preferred` if present, otherwise fall back to libx264."""
    encoders = available_encoders()
    if not encoders:
        return "libx264"
    if preferred in encoders:
        return preferred
    return "libx264"