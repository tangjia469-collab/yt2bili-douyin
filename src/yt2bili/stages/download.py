"""Download stage: fetch video+audio via yt-dlp and write info JSON."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


def _has_video_stream(path: Path) -> bool:
    ffprobe = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    binary = str(ffprobe) if ffprobe.exists() else "ffprobe"
    result = subprocess.run(
        [
            binary,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    try:
        data = json.loads(result.stdout if isinstance(result.stdout, str) else "{}")
    except json.JSONDecodeError:
        return False
    return bool(data.get("streams"))


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
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        "--write-info-json",
        url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.warning("yt-dlp failed (returncode %d): %s", result.returncode, result.stderr)
        return False

    # yt-dlp with --merge-output-format mp4 should produce source.mp4.
    # If separate source.* files remain, choose one that actually has video.
    source_mp4 = warehouse_dir / "source.mp4"
    if not source_mp4.exists() or not _has_video_stream(source_mp4):
        candidates = sorted(
            p for p in warehouse_dir.glob("source.*")
            if not p.name.endswith(".info.json") and p.suffix != ".json"
        )
        video_candidates = [p for p in candidates if _has_video_stream(p)]
        if video_candidates:
            video_candidates[0].replace(source_mp4)

    if not source_mp4.exists() or not _has_video_stream(source_mp4):
        logger.warning("yt-dlp did not produce a playable source.mp4 with video in %s", warehouse_dir)
        return False

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
