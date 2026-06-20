"""Download stage: fetch video+audio via yt-dlp and write info JSON."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


def download_video(url: str, warehouse_dir: Path) -> bool:
    """Download video and audio via yt-dlp, merge to mp4, write info JSON.

    Args:
        url: YouTube (or other) video URL.
        warehouse_dir: Directory to write source.mp4 and source.info.json into.

    Returns:
        True on success (returncode 0), False otherwise.
    """
    warehouse_dir = Path(warehouse_dir)
    warehouse_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(warehouse_dir / "source.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        "--write-info-json",
        url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.warning("yt-dlp failed (returncode %d): %s", result.returncode, result.stderr)
        return False

    # yt-dlp with --merge-output-format mp4 should produce source.mp4,
    # but handle the edge case where another extension was written.
    source_mp4 = warehouse_dir / "source.mp4"
    if not source_mp4.exists():
        # Search for any source.* file that isn't the info json and rename it.
        candidates = sorted(
            p for p in warehouse_dir.glob("source.*")
            if not p.name.endswith(".info.json") and p.suffix != ".json"
        )
        if candidates:
            candidates[0].rename(source_mp4)

    return True


def load_meta(warehouse_dir: Path) -> Dict:
    """Read the yt-dlp info JSON from warehouse_dir.

    Searches for any ``*.info.json`` file written by yt-dlp (naming pattern:
    ``source.<video_id>.info.json``).

    Args:
        warehouse_dir: Directory containing the downloaded files.

    Returns:
        Parsed dict from the info JSON, or an empty dict if not found.
    """
    warehouse_dir = Path(warehouse_dir)
    matches = list(warehouse_dir.glob("*.info.json"))
    if not matches:
        return {}

    info_path = sorted(matches)[0]
    try:
        with info_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
