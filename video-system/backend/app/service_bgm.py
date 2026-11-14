"""BGM & Audio Collection Management Service (syncs files, LOUDNESS.json, and DB metadata)."""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config import data_dir
from .pipeline_en.audio_generator_en import BEAT_ROLE_MOODS, _get_bgm_dir
from .service_import import fetch_url
from .store import Store, new_id, now

CANONICAL_MOODS: dict[str, str] = {
    mood: role
    for role, moods in BEAT_ROLE_MOODS.items()
    for mood in moods
}
ALLOWED_TRACK_TYPES = {"bgm", "sfx", "ambient", "stinger"}
ALLOWED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".webm"}
MAX_BGM_BYTES = 80 * 1024 * 1024


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return s[:64] or "track"


def _resolve_ffmpeg() -> str | None:
    return shutil.which("ffmpeg") or (
        r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"
        if Path(r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe").exists()
        else None
    )


def _resolve_ffprobe() -> str | None:
    return shutil.which("ffprobe") or (
        r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffprobe.exe"
        if Path(r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffprobe.exe").exists()
        else None
    )


def _probe_audio_duration(path: Path) -> float:
    ffprobe = _resolve_ffprobe()
    if not ffprobe:
        return 0.0
    res = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if res.returncode == 0:
        try:
            return round(float((res.stdout or "0").strip()), 3)
        except ValueError:
            pass
    return 0.0


def _measure_loudness(path: Path) -> tuple[float | None, float | None]:
    """Measure integrated LUFS and true peak (dBTP) using ffmpeg ebur128."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        return None, None
    res = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    text = (res.stderr or "") + "\n" + (res.stdout or "")
    lufs_matches = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", text)
    tp_matches = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", text)
    lufs = round(float(lufs_matches[-1]), 2) if lufs_matches else None
    tp = round(float(tp_matches[-1]), 2) if tp_matches else None
    return lufs, tp


class BgmService:
    """Manages BGM collection files, LOUDNESS.json, and rich metadata in SQLite."""

    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def bgm_dir() -> Path:
        d = _get_bgm_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _update_loudness_json(bgm_dir: Path, filename: str, lufs: float | None, tp: float | None) -> None:
        lf = bgm_dir / "LOUDNESS.json"
        data: dict[str, Any] = {"tracks": {}}
        if lf.exists():
            try:
                loaded = json.loads(lf.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                pass
        tracks = data.setdefault("tracks", {})
        entry: dict[str, Any] = {}
        if lufs is not None:
            entry["lufs"] = round(float(lufs), 2)
        if tp is not None:
            entry["tp"] = round(float(tp), 2)
        if entry:
            tracks[filename] = entry
            lf.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _remove_from_loudness_json(bgm_dir: Path, filename: str) -> None:
        lf = bgm_dir / "LOUDNESS.json"
        if not lf.exists():
            return
        try:
            data = json.loads(lf.read_text(encoding="utf-8"))
            tracks = data.get("tracks") or {}
            if filename in tracks:
                tracks.pop(filename, None)
                lf.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _format_row(row: dict[str, Any]) -> dict[str, Any]:
        tags = []
        if row.get("tags_json"):
            try:
                tags = json.loads(row["tags_json"])
            except Exception:
                tags = []
        return {
            "track_id": row["id"],
            "filename": row["filename"],
            "title": row["title"],
            "mood": row["mood"],
            "track_type": row["track_type"],
            "beat_role": row["beat_role"],
            "genre": row["genre"],
            "bpm": row["bpm"],
            "energy": row["energy"],
            "tags": tags,
            "artist": row["artist"],
            "license": row["license"],
            "source_url": row["source_url"],
            "note": row["note"],
            "duration_sec": float(row.get("duration_sec") or 0.0),
            "lufs": row.get("lufs"),
            "true_peak_dbtp": row.get("true_peak_dbtp"),
            "active": bool(row.get("active", 1)),
            "file_url": f"/api/v1/bgm/{row['id']}/file",
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def add_track(
        self,
        *,
        title: str,
        mood: str,
        track_type: str = "bgm",
        beat_role: str | None = None,
        genre: str | None = None,
        bpm: int | None = None,
        energy: str | None = None,
        tags: list[str] | None = None,
        artist: str | None = None,
        license: str | None = None,
        source_url: str | None = None,
        note: str | None = None,
        lufs: float | None = None,
        true_peak_dbtp: float | None = None,
        overwrite: bool = False,
        allow_custom_mood: bool = False,
        file_bytes: bytes | None = None,
        filename: str | None = None,
        base64_data: str | None = None,
        file_path: Path | None = None,
        file_url: str | None = None,
    ) -> dict[str, Any]:
        norm_mood = _slugify(mood)
        if not allow_custom_mood and norm_mood not in CANONICAL_MOODS:
            allowed = ", ".join(sorted(CANONICAL_MOODS))
            raise ValueError(f"mood '{mood}' không hợp lệ. Các mood chuẩn: {allowed} (hoặc bật allow_custom_mood=true)")

        norm_type = (track_type or "bgm").strip().lower()
        if norm_type not in ALLOWED_TRACK_TYPES:
            raise ValueError(f"track_type '{track_type}' không hợp lệ. Hỗ trợ: {', '.join(sorted(ALLOWED_TRACK_TYPES))}")

        resolved_role = (beat_role or "").strip().lower() or CANONICAL_MOODS.get(norm_mood)
        slug = _slugify(Path(filename).stem if filename else title)
        target_name = f"{norm_mood}__{slug}.mp3" if norm_type == "bgm" else f"{norm_type}__{norm_mood}__{slug}.mp3"

        bdir = self.bgm_dir()
        dest_mp3 = bdir / target_name
        existing_row = self.store.query_one("SELECT * FROM bgm_tracks WHERE filename=?", (target_name,))
        if (dest_mp3.exists() or existing_row) and not overwrite:
            raise FileExistsError(f"Track '{target_name}' đã tồn tại (truyền overwrite=true để ghi đè).")

        raw_bytes = file_bytes
        if raw_bytes is None and base64_data:
            b64 = base64_data.split(",", 1)[1] if "," in base64_data and base64_data.startswith("data:") else base64_data
            raw_bytes = base64.b64decode(b64)

        with tempfile.TemporaryDirectory(prefix="bgm_in_") as tmpdir:
            tmp_p = Path(tmpdir)
            ext = Path(filename or "audio.mp3").suffix.lower()
            if ext not in ALLOWED_AUDIO_EXTS:
                ext = ".mp3"
            src_tmp = tmp_p / f"input{ext}"

            if raw_bytes is not None:
                if len(raw_bytes) > MAX_BGM_BYTES:
                    raise ValueError("File nhạc vượt quá giới hạn 80MB.")
                src_tmp.write_bytes(raw_bytes)
            elif file_path is not None:
                resolved = Path(file_path).resolve()
                sandbox = data_dir().resolve()
                if sandbox not in resolved.parents and resolved != sandbox:
                    raise ValueError("file_path phải nằm trong VIDEO_DATA_DIR")
                if not resolved.exists():
                    raise ValueError(f"file_path không tồn tại: {file_path}")
                ext = resolved.suffix.lower() if resolved.suffix.lower() in ALLOWED_AUDIO_EXTS else ".mp3"
                src_tmp = tmp_p / f"input{ext}"
                shutil.copy2(resolved, src_tmp)
            elif file_url:
                fetch_url(file_url, src_tmp, MAX_BGM_BYTES)
            else:
                raise ValueError("Phải gửi file audio (multipart file, base64_data, file_url, hoặc file_path).")

            if ext != ".mp3":
                ffmpeg = _resolve_ffmpeg()
                if not ffmpeg:
                    raise RuntimeError("Cần ffmpeg để chuyển đổi audio không phải .mp3")
                conv = subprocess.run(
                    [ffmpeg, "-y", "-i", str(src_tmp), "-vn", "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2", str(dest_mp3)],
                    capture_output=True, text=True, timeout=120, check=False,
                )
                if conv.returncode != 0:
                    raise RuntimeError(f"Chuyển đổi sang MP3 thất bại: {conv.stderr[:300]}")
            else:
                shutil.copy2(src_tmp, dest_mp3)

        duration_sec = _probe_audio_duration(dest_mp3)
        if lufs is None or true_peak_dbtp is None:
            m_lufs, m_tp = _measure_loudness(dest_mp3)
            if lufs is None:
                lufs = m_lufs
            if true_peak_dbtp is None:
                true_peak_dbtp = m_tp

        self._update_loudness_json(bdir, target_name, lufs, true_peak_dbtp)

        t = now()
        track_id = existing_row["id"] if existing_row else new_id("bgm")
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        if existing_row:
            self.store.execute(
                "UPDATE bgm_tracks SET title=?, mood=?, track_type=?, beat_role=?, genre=?, "
                "bpm=?, energy=?, tags_json=?, artist=?, license=?, source_url=?, note=?, "
                "duration_sec=?, lufs=?, true_peak_dbtp=?, active=1, updated_at=? WHERE id=?",
                (
                    title, norm_mood, norm_type, resolved_role, genre,
                    bpm, str(energy) if energy is not None else None, tags_json,
                    artist, license, source_url, note,
                    duration_sec, lufs, true_peak_dbtp, t, track_id,
                ),
            )
        else:
            self.store.execute(
                "INSERT INTO bgm_tracks "
                "(id,filename,title,mood,track_type,beat_role,genre,bpm,energy,tags_json,"
                "artist,license,source_url,note,duration_sec,lufs,true_peak_dbtp,active,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    track_id, target_name, title, norm_mood, norm_type, resolved_role, genre,
                    bpm, str(energy) if energy is not None else None, tags_json,
                    artist, license, source_url, note,
                    duration_sec, lufs, true_peak_dbtp, 1, t, t,
                ),
            )
        row = self.store.query_one("SELECT * FROM bgm_tracks WHERE id=?", (track_id,))
        return self._format_row(row)

    def list_tracks(
        self,
        *,
        mood: str | None = None,
        track_type: str | None = None,
        beat_role: str | None = None,
        genre: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        sql = "SELECT * FROM bgm_tracks WHERE active=1"
        params: list[Any] = []
        if mood:
            sql += " AND mood=?"
            params.append(mood.strip().lower())
        if track_type:
            sql += " AND track_type=?"
            params.append(track_type.strip().lower())
        if beat_role:
            sql += " AND beat_role=?"
            params.append(beat_role.strip().lower())
        if genre:
            sql += " AND LOWER(genre)=?"
            params.append(genre.strip().lower())
        if q:
            sql += " AND (LOWER(title) LIKE ? OR LOWER(filename) LIKE ? OR LOWER(tags_json) LIKE ?)"
            pat = f"%{q.strip().lower()}%"
            params.extend([pat, pat, pat])
        sql += " ORDER BY created_at DESC"
        rows = self.store.query(sql, tuple(params))
        tracks = [self._format_row(r) for r in rows]

        # Also surface pre-existing disk files not yet registered in bgm_tracks
        bdir = self.bgm_dir()
        db_names = {r["filename"] for r in self.store.query("SELECT filename FROM bgm_tracks")}
        disk_files: list[dict[str, Any]] = []
        for mp3 in sorted(bdir.glob("*.mp3")):
            if mp3.name in db_names:
                continue
            m_prefix = mp3.name.split("__", 1)[0].lower() if "__" in mp3.name else "custom"
            if mood and m_prefix != mood.strip().lower():
                continue
            if track_type and track_type.strip().lower() != "bgm":
                continue
            if q and q.strip().lower() not in mp3.name.lower():
                continue
            disk_files.append({"filename": mp3.name, "mood": m_prefix, "track_type": "bgm", "source": "disk"})

        return {
            "bgm_dir": str(bdir),
            "canonical_moods": CANONICAL_MOODS,
            "allowed_track_types": sorted(ALLOWED_TRACK_TYPES),
            "tracks": tracks,
            "unregistered_disk_tracks": disk_files,
            "total": len(tracks) + len(disk_files),
        }

    def get_track_file(self, track_id: str) -> Path:
        row = self.store.query_one("SELECT filename FROM bgm_tracks WHERE id=? AND active=1", (track_id,))
        if not row:
            raise KeyError(track_id)
        p = self.bgm_dir() / row["filename"]
        if not p.exists():
            raise FileNotFoundError(str(p))
        return p

    def delete_track(self, track_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM bgm_tracks WHERE id=?", (track_id,))
        if not row:
            raise KeyError(track_id)
        bdir = self.bgm_dir()
        p = bdir / row["filename"]
        if p.exists():
            p.unlink()
        self._remove_from_loudness_json(bdir, row["filename"])
        self.store.execute("DELETE FROM bgm_tracks WHERE id=?", (track_id,))
        return {"deleted": True, "track_id": track_id, "filename": row["filename"]}
