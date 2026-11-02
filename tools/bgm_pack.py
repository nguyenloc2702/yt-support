"""Build a tagged, legally embeddable BGM pack from the Incompetech catalog.

    python -m tools.bgm_pack build [--per-role 3] [--out assets/music/bgm]

Every track is CC BY 4.0 (Kevin MacLeod); attribution text is written to
CREDITS.txt next to the audio. Files are named "<mood>__<slug>.mp3" so
BGMManager._find_bgm_file(mood) picks them up.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = "https://incompetech.com/music/royalty-free"
CATALOG = BASE + "/pieces.json"
GENRES = BASE + "/genre.json"
MP3 = BASE + "/mp3-royaltyfree/"
UA = {"User-Agent": "Mozilla/5.0 (music-catalog; contact: local)"}

ROLES: Dict[str, Dict[str, Any]] = {
    "mysterious": {
        "tokens": ["Mysterious", "Mystical", "Eerie", "Suspenseful"],
        "desc": ["mystery", "enigma", "secret", "curious", "investigat"],
        "bpm": (85, 125),
        "instr": ["Pizzicato", "Xylophone", "Marimba", "Celesta", "Harpsichord", "Plucked", "Bassoon", "Harp"],
    },
    "nostalgic": {
        "tokens": ["Calming", "Relaxed", "Somber"],
        "desc": ["memory", "remember", "gentle", "warm", "homesick", "past"],
        "bpm": (60, 100),
        "instr": ["Guitar", "Piano", "Acoustic", "Cello"],
    },
    "energetic": {
        "tokens": ["Driving", "Grooving", "Intense", "Action"],
        "desc": ["racing", "chase", "momentum", "pulse", "attack"],
        "bpm": (105, 142),
        "instr": ["Bass", "Drums", "Synth", "Percussion", "Electric"],
    },
    "ominous": {
        "tokens": ["Dark", "Eerie", "Unnerving", "Suspenseful", "Aggressive"],
        "desc": ["dread", "tension", "danger", "threat", "horror", "menacing"],
        "bpm": (55, 100),
        "instr": ["Cello", "Strings", "Bass", "Organ", "Choir"],
    },
    "triumphant": {
        "tokens": ["Epic", "Uplifting", "Bright"],
        "desc": ["victory", "triumph", "glory", "heroic", "rise", "soar"],
        "bpm": (95, 135),
        "instr": ["Strings", "Brass", "Synth", "Drums", "Timpani"],
    },
    "celebratory": {
        "tokens": ["Bouncy", "Bright", "Humorous", "Grooving"],
        "desc": ["fun", "happy", "party", "celebration", "cheerful", "upbeat"],
        "bpm": (105, 145),
        "instr": ["Ukulele", "Brass", "Drums", "Guitar", "Claps"],
    },
    "reflective": {
        "tokens": ["Calming", "Somber", "Relaxed"],
        "desc": ["sad", "melancholy", "thoughtful", "reflective", "lonely", "loss"],
        "bpm": (55, 85),
        "instr": ["Piano", "Guitar", "Strings", "Cello"],
    },
    "gentle": {
        "tokens": ["Calming", "Relaxed", "Bright"],
        "desc": ["gentle", "soft", "peaceful", "serene", "ambient", "floating", "dream"],
        "bpm": (50, 82),
        "instr": ["Piano", "Harp", "Strings", "Pad", "Flute", "Ambient"],
    },
    "quirky": {
        "tokens": ["Humorous", "Bouncy", "Bright", "Grooving"],
        "desc": ["quirky", "playful", "silly", "whimsical", "comedy", "cartoon", "bouncy"],
        "bpm": (100, 138),
        "instr": ["Pizzicato", "Marimba", "Xylophone", "Celesta", "Clarinet", "Bassoon", "Tuba", "Trombone"],
    },
}

EXCLUDE_GENRES = {"Holiday", "Polka"}


ATTRIBUTION = "Kevin MacLeod (incompetech.com), licensed under Creative Commons: By Attribution 4.0"


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _duration_s(text: str) -> Optional[float]:
    try:
        h, m, s = (int(x) for x in text.split(":"))
        return h * 3600 + m * 60 + s
    except (AttributeError, ValueError):
        return None


def _bpm(piece: Dict[str, Any]) -> Optional[float]:
    try:
        return float(piece.get("bpm"))
    except (TypeError, ValueError):
        return None


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60]


def score_piece(piece: Dict[str, Any], role: Dict[str, Any]) -> float:
    feel = (piece.get("feel") or "").lower()
    instr = (piece.get("instruments") or "").lower()
    title = (piece.get("title") or "").lower()
    desc = (piece.get("description") or "").lower()
    bpm = _bpm(piece)
    dur = _duration_s(piece.get("length") or "")
    if bpm is None or dur is None or dur < 60 or dur > 300:
        return -1.0
    if any(word in title for word in ("christmas", "merry", "carol", "holiday")):
        return -1.0
    score = 0.0
    score += 3.0 * sum(1 for t in role["tokens"] if t.lower() in feel)
    score += 1.5 * sum(1 for t in role.get("desc", []) if t.lower() in desc)
    score += 1.0 * sum(1 for t in role["instr"] if t.lower() in instr)
    low, high = role["bpm"]
    if low <= bpm <= high:
        score += 2.0
    else:
        score -= min(4.0, abs(bpm - (low if bpm < low else high)) / 15.0)
    if 90 <= dur <= 240:
        score += 1.0
    if piece.get("video"):
        score -= 1.0
    return score


def select(pieces: List[Dict[str, Any]], per_role: int,
           genres: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    if genres:
        pieces = [p for p in pieces
                  if genres.get(str(p.get("genre"))) not in EXCLUDE_GENRES]
    chosen: List[Dict[str, Any]] = []
    used = set()
    for mood, role in ROLES.items():
        ranked = sorted(pieces, key=lambda p: score_piece(p, role), reverse=True)
        picked = 0
        for piece in ranked:
            if picked >= per_role:
                break
            key = piece.get("title")
            if key in used or score_piece(piece, role) <= 2.5:
                continue
            used.add(key)
            chosen.append({"mood": mood, "piece": piece, "score": round(score_piece(piece, role), 2)})
            picked += 1
    return chosen


def download(url: str, dest: Path, retries: int = 3) -> int:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = resp.read()
            if len(data) < 200_000:
                raise RuntimeError(f"suspiciously small download ({len(data)} bytes)")
            dest.write_bytes(data)
            return len(data)
        except Exception as exc:  # noqa: BLE001 - retried below
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to download {url}: {last}")


def build(out_dir: Path, per_role: int, catalog: Optional[Path], genres_path: Optional[Path]) -> Dict[str, Any]:
    pieces = json.loads(catalog.read_text(encoding="utf-8")) if catalog else _get_json(CATALOG)
    genres_raw = json.loads(genres_path.read_text(encoding="utf-8")) if genres_path else _get_json(GENRES)
    genres = {str(g["id"]): g["genre"] for g in genres_raw}
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    pending = []
    for item in select(pieces, per_role, genres):
        piece = item["piece"]
        mood = item["mood"]
        filename = f"{mood}__{_slug(piece['title'])}.mp3"
        dest = out_dir / filename
        url = MP3 + urllib.parse.quote(piece["filename"])
        if not dest.exists():
            pending.append((url, dest))
        entries.append({
            "mood": mood,
            "file": filename,
            "title": piece["title"],
            "feel": piece.get("feel"),
            "bpm": _bpm(piece),
            "length": piece.get("length"),
            "genre": genres.get(str(piece.get("genre")), piece.get("genre")),
            "instruments": piece.get("instruments"),
            "isrc": piece.get("isrc"),
            "source": url,
            "license": "CC BY 4.0",
            "attribution": ATTRIBUTION,
            "score": item["score"],
            "bytes": dest.stat().st_size if dest.exists() else 0,
        })

    if pending:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(download, url, dest): dest for url, dest in pending}
            for fut in as_completed(futures):
                fut.result()
    for e in entries:
        e["bytes"] = (out_dir / e["file"]).stat().st_size

    manifest = {"generated_by": "tools.bgm_pack", "license": "CC BY 4.0",
                "attribution": ATTRIBUTION, "tracks": entries}
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    credits = ["Background music credits (CC BY 4.0)", ""]
    for e in entries:
        credits.append(f"- \"{e['title']}\" by {ATTRIBUTION} — {e['source']}")
    (out_dir / "CREDITS.txt").write_text("\n".join(credits) + "\n", encoding="utf-8")
    write_listen_page(entries, out_dir)
    return manifest


def write_listen_page(entries: List[Dict[str, Any]], out_dir: Path) -> Path:
    """Full-length local player; relative mp3 paths, open it in a browser."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for e in entries:
        groups.setdefault(e["mood"], []).append(e)
    sections = []
    for mood, items in groups.items():
        cards = []
        for e in sorted(items, key=lambda x: x["title"].lower()):
            cards.append(
                f'<div class="card"><b>{e["title"]}</b>'
                f'<span class="m">{e.get("bpm") or "?"} BPM · {e.get("length")} · {e.get("feel") or ""}</span>'
                f'<audio controls preload="none" src="{e["file"]}"></audio>'
                f'<code>{e["file"]}</code></div>'
            )
        sections.append(f'<h2>{mood} <span>{len(items)}</span></h2><div class="grid">{"".join(cards)}</div>')
    html = f"""<!doctype html><meta charset="utf-8"><title>BGM pack — nghe thử</title>
<style>
body {{ background:#0e1116; color:#e6e9ef; font-family:-apple-system,"Segoe UI",Roboto,sans-serif; padding:24px; }}
h1 {{ font-size:20px; }} h2 {{ font-size:15px; margin:26px 0 10px; text-transform:capitalize; }}
h2 span {{ color:#7c8798; font-weight:400; font-size:12px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:12px; }}
.card {{ background:#151b23; border:1px solid #232b36; border-radius:10px; padding:12px; }}
.card b {{ font-size:13.5px; }} .m {{ display:block; color:#8b95a5; font-size:11.5px; margin:4px 0 7px; }}
audio {{ width:100%; height:30px; }} code {{ display:block; margin-top:6px; font-size:10.5px; color:#6b7686; }}
p.note {{ color:#9aa4b2; font-size:12px; }}
</style>
<h1>Gói nhạc nền — {len(entries)} track (CC BY 4.0)</h1>
<p class="note">Phát cả bài. Ghi công bắt buộc: xem CREDITS.txt. Mở file này trực tiếp bằng trình duyệt.</p>
{"".join(sections)}"""
    path = out_dir / "listen.html"
    path.write_text(html, encoding="utf-8")
    return path


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a tagged CC BY BGM pack")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("build")
    cmd.add_argument("--per-role", type=int, default=3)
    cmd.add_argument("--out", default="assets/music/bgm")
    cmd.add_argument("--catalog", default=None)
    cmd.add_argument("--genres", default=None)
    args = parser.parse_args(argv)

    manifest = build(Path(args.out), args.per_role,
                     Path(args.catalog) if args.catalog else None,
                     Path(args.genres) if args.genres else None)
    for e in manifest["tracks"]:
        print(f"{e['mood']:<12} {e['bpm'] or '?':>5} BPM  {e['length']}  {e['feel']}  — {e['title']}")
    print(f"tracks={len(manifest['tracks'])} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
