"""Owner-submitted images cho Daily Brief submission flow.

Owner chốt list tin qua POST /api/v1/digest/submit kèm thumbnail_url/image_urls.
Module này tải ảnh về `objects/digest_submissions/{date}/s{seq:02d}/` — seq
khớp convention story trong produce(): story k (0-based) -> scene seq = k + 2
(seq 1 = Hook, produce tự borrow ảnh tin đầu cho hook/outro).

Fail-tolerant: ảnh chết/kích thước lỗi -> skip story đó, produce rơi về
collect_story_visuals (scrape link + Wikipedia) như bình thường.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import story_media
from ..config import data_dir

logger = logging.getLogger("video-system.digest.owner_images")


def download_owner_images(
    date_label: str,
    stories: list[dict],
    *,
    max_per_story: int = 4,
) -> dict[int, list[str]]:
    """Tải ảnh owner submit về đĩa.

    Returns: {seq: [đường dẫn file ảnh]} với seq = story_index + 2 (scene seq).
    Không raise — lỗi mạng/URL chỉ được log và skip.
    """
    result: dict[int, list[str]] = {}
    base = Path(data_dir()) / "objects" / "digest_submissions" / date_label
    for idx, story in enumerate(stories):
        seq = idx + 2  # seq 1 = Hook; story đầu tiên là seq 2
        urls: list[str] = []
        thumb = str((story or {}).get("thumbnail_url") or "").strip()
        if thumb:
            urls.append(thumb)
        for u in (story or {}).get("image_urls") or []:
            su = str(u or "").strip()
            if su and su not in urls:
                urls.append(su)
        urls = urls[:max(1, int(max_per_story))]
        if not urls:
            continue
        seen: set[str] = set()
        saved: list[str] = []
        story_dir = base / f"s{seq:02d}"
        for i, url in enumerate(urls):
            out = story_dir / f"owner_{i:02d}.jpg"
            try:
                if story_media._download_valid_image(url, out, seen):
                    saved.append(str(out))
            except Exception as exc:  # noqa: BLE001 — fail-tolerant theo hợp đồng module
                logger.warning("owner image download failed (story %d): %s", idx + 1, exc)
        if saved:
            result[seq] = saved
    logger.info(
        "owner images downloaded: %d/%d story có ảnh", len(result), len(stories)
    )
    return result
