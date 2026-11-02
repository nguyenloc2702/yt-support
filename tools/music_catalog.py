"""Catalog the background-music beds found in reference videos.

    python -m tools.music_catalog scan --refs analysis/refs --out analysis/music_beds

Downloads each reference's audio to a temp dir, cancels the centred narration
with a mid/side split, segments the remaining music and tags every segment with
a story role (position in the episode) plus a measured character (tempo, key,
brightness, onset density). Selected segments per role are exported as 15 s
previews: music-only and the original mix.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

SR = 24000
HOP_S = 0.25
MIN_REGION_S = 8.0
MAX_GAP_S = 2.0
PREVIEW_S = 15.0

ROLE_POSITION = (
    (0.08, "hook"),
    (0.25, "setup"),
    (0.60, "rising"),
    (0.80, "crisis"),
    (0.94, "breakthrough"),
    (1.01, "payoff"),
)

ROLE_ORDER = ["hook", "setup", "rising", "crisis", "breakthrough", "payoff", "moral"]

ROLE_MOOD = {
    "hook": "mysterious_pizzicato",
    "setup": "nostalgic_acoustic",
    "rising": "energetic_bassline",
    "crisis": "ominous_cello",
    "breakthrough": "triumphant_synth",
    "payoff": "celebratory_groove",
    "moral": "reflective_guitar",
}

MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _run(cmd: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {result.stderr[-500:]}")
    return result


def download_audio(url: str, workdir: Path) -> Path:
    exe = shutil.which("yt-dlp")
    if not exe:
        raise RuntimeError("yt-dlp not found on PATH")
    _run([exe, "--no-playlist", "-f", "bestaudio[acodec^=opus]/bestaudio",
          "-P", str(workdir), "-o", "%(id)s.%(ext)s", url], timeout=1800)
    files = [p for p in workdir.iterdir() if p.is_file()]
    if not files:
        raise RuntimeError("yt-dlp produced no audio")
    return max(files, key=lambda p: p.stat().st_size)


def load_stereo(path: Path) -> Tuple[np.ndarray, np.ndarray]:
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "2",
         "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True,
    )
    a = np.frombuffer(p.stdout, dtype=np.float32).reshape(-1, 2).astype(np.float64)
    return a[:, 0], a[:, 1]


def rms_db(x: np.ndarray, hop_s: float = HOP_S) -> np.ndarray:
    n = int(SR * hop_s)
    m = len(x) // n
    if m < 1:
        return np.zeros(0)
    seg = x[: m * n].reshape(m, n)
    return 20 * np.log10(np.sqrt(np.mean(seg ** 2, axis=1)) + 1e-12)


def find_regions(side_db: np.ndarray) -> List[Tuple[float, float]]:
    if len(side_db) < 4:
        return []
    noise = float(np.percentile(side_db, 5))
    thr = max(-58.0, noise + 6.0)
    active = side_db > thr
    regions: List[Tuple[float, float]] = []
    start: Optional[int] = None
    last_active = -1
    for i, flag in enumerate(active):
        if flag:
            if start is None:
                start = i
            last_active = i
        elif start is not None and (i - last_active) * HOP_S > MAX_GAP_S:
            regions.append((start * HOP_S, last_active * HOP_S))
            start = None
    if start is not None:
        regions.append((start * HOP_S, last_active * HOP_S))
    out = []
    for a, b in regions:
        if b - a < MIN_REGION_S:
            continue
        if float(np.median(side_db[int(a / HOP_S): int(b / HOP_S) + 1])) < -55.0:
            continue
        out.append((round(a, 2), round(b, 2)))
    return out


def spectral_features(x: np.ndarray) -> Optional[Dict[str, Any]]:
    nfft, hop = 4096, 2048
    k = (len(x) - nfft) // hop + 1
    if k < 4:
        return None
    idx = np.arange(nfft)[None, :] + hop * np.arange(k)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(nfft), axis=1))
    freqs = np.fft.rfftfreq(nfft, 1 / SR)
    mean_spec = spec.mean(axis=0)

    band = (freqs >= 65) & (freqs <= 2000)
    midi = np.round(69 + 12 * np.log2(freqs[band] / 440.0)).astype(int)
    pc = midi % 12
    sub = spec[:, band]
    chroma = np.stack([sub[:, pc == i].sum(axis=1) for i in range(12)], axis=1).mean(axis=0)
    chroma = chroma / (np.linalg.norm(chroma) + 1e-9)

    centroid = float((mean_spec * freqs).sum() / (mean_spec.sum() + 1e-9))
    low = float(mean_spec[freqs < 200].sum() / (mean_spec.sum() + 1e-9))

    frame_db = rms_db(x, 0.05)
    flux = np.maximum(0, np.diff(frame_db))
    onset_per_s = float((flux > max(1.0, 2 * flux.mean())).sum() / (len(x) / SR))

    key_idx, mode = 0, "major"
    best = -np.inf
    for root in range(12):
        for name, tmpl in (("major", MAJOR), ("minor", MINOR)):
            score = float(np.dot(np.roll(tmpl / np.linalg.norm(tmpl), root), chroma))
            if score > best:
                best, key_idx, mode = score, root, name

    return {
        "centroid_hz": round(centroid, 1),
        "low_ratio": round(low, 3),
        "onsets_per_s": round(onset_per_s, 2),
        "key": f"{KEYS[key_idx]} {mode}",
    }


def tempo_bpm(x: np.ndarray) -> Dict[str, Any]:
    decim, hop, nfft = 4, 128, 256
    xd = x[::decim]
    k = (len(xd) - nfft) // hop + 1
    if k < 8:
        return {"bpm": None, "confidence": 0.0}
    idx = np.arange(nfft)[None, :] + hop * np.arange(k)[:, None]
    freqs = np.fft.rfftfreq(nfft, decim / SR)
    band = (freqs >= 30) & (freqs <= 250)
    spec = np.abs(np.fft.rfft(xd[idx] * np.hanning(nfft), axis=1))[:, band]
    flux = np.maximum(0, np.diff(spec, axis=0)).sum(axis=1)
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    ac /= ac[0] + 1e-9
    lag_s = np.arange(len(ac)) * hop * decim / SR
    with np.errstate(divide="ignore"):
        bpm = 60.0 / lag_s
    mask = (bpm >= 55) & (bpm <= 190) & (np.arange(len(ac)) > 2)
    if not mask.any():
        return {"bpm": None, "confidence": 0.0}
    best = np.argmax(ac[mask])
    return {"bpm": round(float(bpm[mask][best]), 1), "confidence": round(float(ac[mask][best]), 3)}


def role_for_position(pos: float) -> str:
    for limit, role in ROLE_POSITION:
        if pos < limit:
            return role
    return "payoff"


def character_tag(feat: Dict[str, Any], bpm: Optional[float]) -> str:
    bpm = bpm or 100.0
    bright = feat["centroid_hz"] > 1800
    dark = feat["centroid_hz"] < 1100
    busy = feat["onsets_per_s"] > 2.5
    sparse = feat["onsets_per_s"] < 0.8
    bassy = feat["low_ratio"] > 0.45 or "minor" in feat["key"]
    if busy and bright and bpm >= 110:
        return "uptempo / percussive"
    if dark and sparse and bassy:
        return "dark / drone / tense"
    if sparse and not bright:
        return "calm / reflective"
    if feat["onsets_per_s"] > 1.5 and bright:
        return "quirky / plucky"
    if bassy and busy:
        return "driving / serious"
    return "neutral / bed"


def scan_clip(report: Dict[str, Any], workdir: Path) -> Dict[str, Any]:
    src = report.get("source") or {}
    url = src.get("url")
    rid = src.get("id") or report.get("id")
    duration = float(report.get("video", {}).get("duration_s") or 0)
    clip_dir = workdir / str(rid)
    clip_dir.mkdir(parents=True, exist_ok=True)
    audio = download_audio(url, clip_dir)
    left, right = load_stereo(audio)
    side = 0.5 * (left - right)
    mid = 0.5 * (left + right)
    side_db = rms_db(side)
    mid_db = rms_db(mid)
    regions = []
    for start, end in find_regions(side_db):
        i0, i1 = int(start / HOP_S), int(end / HOP_S) + 1
        seg = side[i0 * int(SR * HOP_S): i1 * int(SR * HOP_S)]
        feat = spectral_features(seg)
        if feat is None:
            continue
        tempo = tempo_bpm(seg)
        side_med = float(np.median(side_db[i0:i1]))
        mid_med = float(np.median(mid_db[i0:i1]))
        pos = round(start / duration, 3) if duration else 0.0
        regions.append({
            "clip_id": rid,
            "title": src.get("title", ""),
            "start_s": start,
            "end_s": end,
            "duration_s": round(end - start, 2),
            "position": pos,
            "role": role_for_position(pos),
            "mood": ROLE_MOOD[role_for_position(pos)],
            "character": character_tag(feat, tempo.get("bpm")),
            "bpm": tempo.get("bpm"),
            "bpm_conf": tempo.get("confidence"),
            "key": feat["key"],
            "centroid_hz": feat["centroid_hz"],
            "low_ratio": feat["low_ratio"],
            "onsets_per_s": feat["onsets_per_s"],
            "side_db": round(side_med, 1),
            "side_minus_mid_db": round(side_med - mid_med, 1),
        })
    return {
        "id": rid,
        "title": src.get("title", ""),
        "channel": src.get("channel", ""),
        "duration_s": duration,
        "audio": str(audio),
        "regions": regions,
        "bed_share": round(
            sum(r["duration_s"] for r in regions) / duration, 3
        ) if duration else 0.0,
    }


def select_previews(regions: List[Dict[str, Any]], per_role: int) -> List[Dict[str, Any]]:
    chosen: List[Dict[str, Any]] = []
    for role in ROLE_ORDER:
        pool = [r for r in regions if r["role"] == role]
        pool.sort(key=lambda r: (-r["side_minus_mid_db"], -r["duration_s"]))
        picked: List[Dict[str, Any]] = []
        episodes = set()
        for region in pool:
            if len(picked) >= per_role:
                break
            if region["clip_id"] in episodes:
                continue
            picked.append(region)
            episodes.add(region["clip_id"])
        for region in pool:
            if len(picked) >= per_role:
                break
            if region not in picked:
                picked.append(region)
        chosen.extend(picked)
    return chosen


def export_previews(clips: List[Dict[str, Any]], picks: List[Dict[str, Any]], out_dir: Path) -> None:
    audio_by_clip = {c["id"]: Path(c["audio"]) for c in clips}
    preview_dir = out_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for i, region in enumerate(picks, 1):
        preview_id = f"{i:02d}_{region['role']}_{region['clip_id']}"
        start = min(region["start_s"] + 3.0, max(region["start_s"], region["end_s"] - PREVIEW_S))
        length = min(PREVIEW_S, region["end_s"] - start)
        audio = audio_by_clip[region["clip_id"]]
        music_path = preview_dir / f"{preview_id}_music.mp3"
        context_path = preview_dir / f"{preview_id}_context.mp3"
        _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}",
              "-i", str(audio),
              "-af", "pan=mono|c0=0.5*c0-0.5*c1,loudnorm=I=-15:TP=-1.5:LRA=11",
              "-ar", "44100", "-b:a", "192k", str(music_path)], timeout=120)
        _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}",
              "-i", str(audio),
              "-af", "loudnorm=I=-15:TP=-1.5:LRA=11",
              "-ar", "44100", "-b:a", "192k", str(context_path)], timeout=120)
        region["preview_id"] = preview_id
        region["preview_music"] = f"previews/{music_path.name}"
        region["preview_context"] = f"previews/{context_path.name}"


def write_index(clips: List[Dict[str, Any]], picks: List[Dict[str, Any]], out_dir: Path) -> Path:
    index = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "clips": [{
            "id": c["id"], "title": c["title"], "channel": c["channel"],
            "duration_s": c["duration_s"], "bed_share": c["bed_share"],
            "regions": len(c["regions"]),
        } for c in clips],
        "previews": picks,
        "region_count": sum(len(c["regions"]) for c in clips),
    }
    path = out_dir / "index.json"
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_csv(picks: List[Dict[str, Any]], out_dir: Path) -> Path:
    path = out_dir / "beds.csv"
    cols = ["preview_id", "role", "mood", "character", "bpm", "key", "centroid_hz",
            "onsets_per_s", "side_minus_mid_db", "clip_id", "start_s", "duration_s",
            "preview_music", "preview_context"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols)
        for p in picks:
            writer.writerow([p.get(c, "") for c in cols])
    return path


def write_listen_page(picks: List[Dict[str, Any]], out_dir: Path) -> Path:
    sections = []
    for role in ROLE_ORDER:
        items = [p for p in picks if p["role"] == role]
        if not items:
            continue
        cards = []
        for p in items:
            cards.append(f"""
      <div class="card">
        <div class="meta"><b>{html.escape(p['clip_id'])}</b> · {p['start_s']:.0f}s ·
          {p['duration_s']:.0f}s · {p['side_minus_mid_db']} dB</div>
        <div class="meta">{html.escape(p['character'])} · {html.escape(p['key'])} ·
          {p['bpm'] or '?'} BPM · onset {p['onsets_per_s']}/s</div>
        <label>Nhạc (tách giọng)</label>
        <audio controls preload="none" src="{p['preview_music']}"></audio>
        <label>Ngữ cảnh gốc</label>
        <audio controls preload="none" src="{p['preview_context']}"></audio>
        <div class="src">{html.escape(p['title'])}</div>
      </div>""")
        sections.append(f"""
    <h2>{role} <span class="mood">{html.escape(items[0]['mood'])}</span>
      <span class="count">{len(items)} đoạn</span></h2>
    <div class="grid">{''.join(cards)}</div>""")
    page = f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>Music beds</title>
<style>
 body{{background:#111;color:#eee;font:14px/1.4 system-ui,sans-serif;margin:24px}}
 h1{{font-size:18px;font-weight:600}}
 h2{{font-size:15px;margin:26px 0 10px;text-transform:uppercase;letter-spacing:.04em}}
 .mood{{color:#9cc7ff;font-weight:400;text-transform:none;margin-left:8px}}
 .count{{color:#8e8e93;font-weight:400;font-size:12px;margin-left:6px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}}
 .card{{background:#1c1c1e;border:1px solid #2c2c2e;border-radius:10px;padding:12px}}
 .meta{{color:#9a9a9e;font-size:12px;margin:2px 0}}
 label{{display:block;color:#8e8e93;font-size:11px;margin:8px 0 2px}}
 audio{{width:100%;height:32px}}
 .src{{color:#636366;font-size:11px;margin-top:6px}}
</style></head><body>
<h1>Music beds — {len(picks)} đoạn · tách nhạc bằng mid/side</h1>
{''.join(sections)}
</body></html>"""
    path = out_dir / "listen.html"
    path.write_text(page, encoding="utf-8")
    return path


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Catalog background-music beds in reference videos")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("scan", help="scan reports in --refs and write the bed catalog")
    cmd.add_argument("--refs", default="analysis/refs")
    cmd.add_argument("--out", default="analysis/music_beds")
    cmd.add_argument("--limit", type=int, default=None, help="only scan the first N reports")
    cmd.add_argument("--per-role", type=int, default=6)
    cmd.add_argument("--keep-audio", action="store_true")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    refs = sorted(Path(args.refs).glob("*/report.json"))
    if args.limit:
        refs = refs[: args.limit]

    workdir = Path(tempfile.mkdtemp(prefix="musiccat_"))
    clips: List[Dict[str, Any]] = []
    try:
        for i, ref in enumerate(refs, 1):
            report = json.loads(ref.read_text(encoding="utf-8"))
            if not (report.get("source") or {}).get("url"):
                continue
            print(f"[{i}/{len(refs)}] scanning {ref.parent.name}", flush=True)
            try:
                clip = scan_clip(report, workdir)
            except Exception as exc:
                print(f"  FAILED {ref.parent.name}: {type(exc).__name__}: {exc}", flush=True)
                continue
            clips.append(clip)
            print(f"  {len(clip['regions'])} regions, bed {clip['bed_share']:.0%}", flush=True)
        picks = select_previews([r for c in clips for r in c["regions"]], args.per_role)
        export_previews(clips, picks, out_dir)
        index_path = write_index(clips, picks, out_dir)
        csv_path = write_csv(picks, out_dir)
        page_path = write_listen_page(picks, out_dir)
    finally:
        if not args.keep_audio:
            shutil.rmtree(workdir, ignore_errors=True)

    print(f"previews={len(picks)} -> {index_path}")
    print(f"csv -> {csv_path}")
    print(f"listen -> {page_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
