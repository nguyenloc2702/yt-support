"""Reference Image Collector for English Video Production Pipeline.

Automatically scans the complete script, extracts 10-20 visual anchors distributed
across the entire timeline, harvests authentic reference photos from Wikipedia /
Wikimedia Commons, downloads them to disk, and packages them onto the TODO item.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
    HAS_PIL = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    HAS_PIL = False

logger = logging.getLogger("reference_image_collector")

USER_AGENT = "VideoSystemBot/1.0 (+https://talk.gacdero.vn; contact@gacdero.vn)"


def _pure_python_png(width: int = 1280, height: int = 720, color: tuple[int, int, int] = (26, 32, 44)) -> bytes:
    """Generate a valid 16:9 solid PNG file purely with standard library struct and zlib."""
    import struct
    import zlib
    r, g, b = color
    row = b"\x00" + bytes([r, g, b]) * width
    raw_data = row * height
    compressed = zlib.compress(raw_data)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")


def _detect_image_format_and_ext(data: bytes) -> tuple[str, str]:
    """Detect image format and appropriate file extension from magic bytes."""
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG", ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG", ".png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP", ".webp"
    return "PNG", ".png"

STOP_WORDS = {
    "the", "this", "that", "these", "those", "and", "but", "or", "because",
    "as", "until", "while", "of", "at", "by", "for", "with", "about", "against",
    "between", "into", "through", "during", "before", "after", "above", "below",
    "to", "from", "up", "down", "in", "out", "on", "off", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "any", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "can",
    "will", "just", "should", "now", "clean", "flat", "vector", "documentary",
    "scene", "illustration", "shot", "editorial", "background", "foreground",
    "midground", "depicting", "composition", "sharp", "lines", "clutter",
}


def _clean_title_context(title: str) -> str:
    """Extract core subject from item title (e.g. 'BYD', 'BlackRock')."""
    t = re.sub(r"[—–\-_()].*", "", title).strip()
    words = [w for w in t.split() if w.lower() not in STOP_WORDS]
    return " ".join(words[:2]) if words else ""


def extract_reference_anchors(
    shots: list[dict[str, Any]],
    target_count: int = 15,
    item_title: str = "",
) -> list[dict[str, Any]]:
    """Select 10-20 evenly distributed key visual anchors across the script."""
    if not shots:
        return []

    target_count = max(5, min(25, target_count))
    total_shots = len(shots)

    if total_shots <= target_count:
        selected_indexes = list(range(total_shots))
    else:
        # Distribute evenly across timeline (beginning to ending)
        selected_indexes = [
            int(round(i * (total_shots - 1) / (target_count - 1)))
            for i in range(target_count)
        ]
        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for idx in selected_indexes:
            if idx not in seen:
                seen.add(idx)
                deduped.append(idx)
        selected_indexes = deduped

    title_ctx = _clean_title_context(item_title)
    anchors: list[dict[str, Any]] = []

    for idx in selected_indexes:
        s = shots[idx]
        shot_idx = int(s.get("index") or (idx + 1))
        narr = str(s.get("narration") or "").strip()
        vprompt = str(s.get("visual_prompt") or "").strip()
        combined = f"{narr} {vprompt}"

        # 1. Look for capitalized entity words / phrases
        capital_matches = re.findall(r"\b[A-Z][a-zA-Z0-9-]+\b", combined)
        clean_entities = [
            w for w in capital_matches
            if w.lower() not in STOP_WORDS and len(w) > 2
        ]

        # 2. Look for concrete topic nouns across diverse documentary domains
        nouns = re.findall(
            r"\b(?:battery|batteries|factory|supercar|car|bus|electric|wall\s+street|stock|"
            r"investor|aladdin|fund|market|system|computer|founder|billionaire|crisis|debt|"
            r"headquarters|chip|chips|semiconductor|lithography|wafer|solar|automaker|"
            r"rocket|satellite|space|engine|orbit|spacex|nasa|telescope|launch|"
            r"ai|gpu|model|robot|neural|algorithm|hardware|cloud|data\s+center|"
            r"bank|inflation|economy|treasury|currency|dollar|trade|tariff|fed|"
            r"virus|vaccine|dna|biotech|cell|medicine|drug|hospital|laboratory|"
            r"oil|energy|grid|pipeline|nuclear|reactor|refinery|turbine|"
            r"treaty|empire|war|president|minister|parliament|court|constitution)\b",
            combined,
            flags=re.IGNORECASE,
        )

        query_terms: list[str] = []
        # Include title context if relevant
        if title_ctx and not any(title_ctx.lower() in e.lower() for e in clean_entities):
            query_terms.append(title_ctx)

        for ent in clean_entities:
            if ent not in query_terms:
                query_terms.append(ent)
            if len(query_terms) >= 3:
                break

        for n in nouns:
            clean_n = n.strip().capitalize()
            if clean_n not in query_terms:
                query_terms.append(clean_n)
            if len(query_terms) >= 4:
                break

        final_query = " ".join(query_terms).strip()
        if not final_query:
            final_query = f"{title_ctx} documentary".strip() if title_ctx else f"Shot {shot_idx}"

        anchors.append({
            "seq_num": len(anchors) + 1,
            "shot_index": shot_idx,
            "query": final_query,
            "narration_snippet": narr[:90] + ("..." if len(narr) > 90 else ""),
            "shot_type": s.get("shot_type", "scene"),
        })

    return anchors


def search_reference_image(query: str, timeout: float = 8.0) -> dict[str, Any] | None:
    """Search Wikipedia & Wikimedia Commons for a real high-res reference photo."""
    if not query or os.environ.get("VIDEO_OFFLINE_SEARCH") in {"1", "true", "yes"}:
        return None

    # Step 1: Query English Wikipedia Page Thumbnail (accurate hero images)
    try:
        wiki_url = (
            "https://en.wikipedia.org/w/api.php?action=query&format=json&generator=search"
            f"&gsrsearch={urllib.parse.quote(query)}&gsrlimit=3"
            "&prop=pageimages|extracts&piprop=original|thumbnail&pithumbsize=1000"
            "&exintro=1&explaintext=1"
        )
        req = urllib.request.Request(wiki_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            pages = list(data.get("query", {}).get("pages", {}).values())
            for page in pages:
                orig = page.get("original")
                thumb = page.get("thumbnail")
                chosen = orig or thumb
                if chosen and chosen.get("source"):
                    w = int(chosen.get("width") or 800)
                    h = int(chosen.get("height") or 600)
                    return {
                        "source_title": page.get("title") or query,
                        "source_url": chosen["source"],
                        "source_engine": "wikipedia",
                        "width": w,
                        "height": h,
                    }
    except Exception as exc:
        logger.debug("Wikipedia query failed for '%s': %s", query, exc)

    # Step 2: Query Wikimedia Commons directly for rich media
    try:
        commons_url = (
            "https://commons.wikimedia.org/w/api.php?action=query&generator=search"
            f"&gsrnamespace=6&gsrsearch={urllib.parse.quote(query)}&gsrlimit=5"
            "&prop=imageinfo&iiprop=url|size|mime&format=json"
        )
        req = urllib.request.Request(commons_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            pages = list(data.get("query", {}).get("pages", {}).values())
            for p in pages:
                for info in p.get("imageinfo", []):
                    mime = str(info.get("mime") or "").lower()
                    if mime.startswith("image/") and not mime.endswith(("djvu", "pdf", "svg")):
                        url = info.get("url")
                        if url:
                            return {
                                "source_title": p.get("title") or query,
                                "source_url": url,
                                "source_engine": "wikimedia_commons",
                                "width": int(info.get("width") or 800),
                                "height": int(info.get("height") or 600),
                            }
    except Exception as exc:
        logger.debug("Wikimedia Commons query failed for '%s': %s", query, exc)

    return None


def _synthesize_reference_card(
    dest_path: Path,
    shot_index: int,
    query: str,
    narration_snippet: str,
    width: int = 1280,
    height: int = 720,
) -> tuple[int, int]:
    """Generate a clean 16:9 reference concept card when offline or no web photo found."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if HAS_PIL and Image is not None and ImageDraw is not None:
        try:
            img = Image.new("RGB", (width, height), color=(26, 32, 44))  # Dark slate
            draw = ImageDraw.Draw(img)

            # Draw border
            draw.rectangle([(20, 20), (width - 20, height - 20)], outline=(66, 153, 225), width=3)
            # Accent badge
            draw.rectangle([(50, 50), (450, 110)], fill=(229, 62, 62))  # Muted red accent

            # Texts
            draw.text((70, 68), f"REFERENCE ANCHOR · SHOT #{shot_index:03d}", fill=(255, 255, 255))
            draw.text((70, 150), f"Topic Query: {query}", fill=(237, 242, 247))
            draw.text((70, 200), f"Script: {narration_snippet}", fill=(203, 213, 225))

            # Center schematic box
            draw.rectangle([(70, 260), (width - 70, height - 80)], outline=(113, 128, 150), width=2, fill=(45, 55, 72))
            draw.text((100, 300), "VISUAL IDEA ANCHOR FOR VIDEO GENERATION AGENT:", fill=(254, 215, 215))
            draw.text(
                (100, 350),
                "Use this reference context to ground the scene's keyframe and motion.\n"
                "Lock to Crayon Capital Vector Mold (Peach skin protagonist / White faces crowd,\n"
                "1 muted red accent, bold vector outlines, 30-40-30 motion timeline).",
                fill=(226, 232, 240),
            )

            img.save(str(dest_path), format="PNG")
            return width, height
        except Exception as exc:
            logger.debug("PIL card synthesis failed: %s; falling back to pure-Python PNG", exc)

    # Pure-Python fallback (no external dependencies required)
    png_data = _pure_python_png(width, height)
    dest_path.write_bytes(png_data)
    return width, height


def download_or_generate_reference(
    anchor: dict[str, Any],
    dest_path: Path,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Attempt web search & download; fallback cleanly to synthetic reference card."""
    query = anchor["query"]
    shot_idx = anchor["shot_index"]
    snippet = anchor["narration_snippet"]

    found = search_reference_image(query, timeout=timeout)
    if found and found.get("source_url"):
        try:
            req = urllib.request.Request(
                found["source_url"],
                headers={"User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if len(data) >= 1024:  # At least 1KB
                    _, ext = _detect_image_format_and_ext(data)
                    actual_dest = dest_path.with_suffix(ext) if dest_path.suffix.lower() != ext else dest_path
                    actual_dest.parent.mkdir(parents=True, exist_ok=True)
                    actual_dest.write_bytes(data)

                    # Probe image dimensions
                    w, h = int(found.get("width") or 1280), int(found.get("height") or 720)
                    if HAS_PIL and Image is not None:
                        try:
                            with Image.open(str(actual_dest)) as im:
                                w, h = im.size
                        except Exception:
                            pass
                    return {
                        **anchor,
                        "file_path": actual_dest,
                        "source_url": found["source_url"],
                        "source_engine": found["source_engine"],
                        "source_title": found.get("source_title") or query,
                        "width": w,
                        "height": h,
                        "is_synthetic": False,
                    }
        except Exception as exc:
            logger.warning("Failed to download reference image for '%s': %s", query, exc)

    # Fallback: create reference concept card (guaranteed PNG)
    fallback_dest = dest_path.with_suffix(".png")
    w, h = _synthesize_reference_card(fallback_dest, shot_idx, query, snippet)
    return {
        **anchor,
        "file_path": fallback_dest,
        "source_url": f"synthetic://reference_anchor/{shot_idx}",
        "source_engine": "system_concept_card",
        "source_title": f"Reference Concept: {query}",
        "width": w,
        "height": h,
        "is_synthetic": True,
    }


def collect_references_for_shots(
    shots: list[dict[str, Any]],
    out_dir: Path,
    target_count: int = 15,
    item_title: str = "",
) -> list[dict[str, Any]]:
    """Harvest 10-20 reference images throughout the script and save them to out_dir."""
    anchors = extract_reference_anchors(shots, target_count=target_count, item_title=item_title)
    results: list[dict[str, Any]] = []

    out_dir.mkdir(parents=True, exist_ok=True)
    for a in anchors:
        seq = a["seq_num"]
        dest_path = out_dir / f"reference_{seq:04d}.png"
        ref_obj = download_or_generate_reference(a, dest_path)
        results.append(ref_obj)

    return results
