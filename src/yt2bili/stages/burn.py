"""Burn stage: hard-burn Chinese subtitles into video via ffmpeg."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_ffmpeg() -> str:
    ffmpeg_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    if ffmpeg_full.exists():
        return str(ffmpeg_full)
    return "ffmpeg"


def _resolve_ffprobe() -> str:
    ffprobe_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    if ffprobe_full.exists():
        return str(ffprobe_full)
    return "ffprobe"


def _has_video_stream(path: Path) -> bool:
    result = subprocess.run(
        [
            _resolve_ffprobe(),
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
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return False
    return bool(data.get("streams"))


def burn_subtitles(
    warehouse_dir: Path,
    font: str = "PingFang SC",
    font_size: int = 22,
    outline: int = 1,
    margin_v: int = 30,
) -> bool:
    """Hard-burn zh.srt into source.mp4, producing final.mp4.

    Uses ffmpeg ``subtitles`` video filter with ``force_style`` for font
    control.  Audio stream is copied without re-encoding.

    Args:
        warehouse_dir: Directory containing ``source.mp4`` and ``zh.srt``.
        font: Font name for subtitle rendering.
        font_size: Font size in points.
        outline: Outline (border) thickness in pixels.
        margin_v: Vertical margin from the bottom edge in pixels.

    Returns:
        True on success (``final.mp4`` written), False on any failure.
    """
    warehouse_dir = Path(warehouse_dir)
    source_mp4 = warehouse_dir / "source.mp4"
    zh_srt = warehouse_dir / "zh.srt"
    final_mp4 = warehouse_dir / "final.mp4"

    if not source_mp4.exists():
        logger.warning("burn_subtitles: source.mp4 not found in %s", warehouse_dir)
        return False

    if not zh_srt.exists():
        logger.warning("burn_subtitles: zh.srt not found in %s", warehouse_dir)
        return False

    if not _has_video_stream(source_mp4):
        logger.warning("burn_subtitles: source.mp4 has no video stream in %s", warehouse_dir)
        return False

    # Build the ASS override style string for ffmpeg subtitles filter.
    # Commas separate force_style key=value pairs, but a comma is also the
    # ffmpeg filtergraph separator. Since we pass -vf as a single argv element
    # (no shell), the commas inside force_style MUST be backslash-escaped or
    # ffmpeg parses them as new filters ("Error parsing filterchain"). Do NOT
    # wrap the value in single quotes here — without a shell those quotes are
    # literal characters and break parsing.
    force_style = (
        f"Fontname={font}\\,"
        f"Fontsize={font_size}\\,"
        f"Outline={outline}\\,"
        f"MarginV={margin_v}"
    )

    # ffmpeg filter graph uses ':' as option separator and '\:' as a literal
    # colon inside option values (e.g. Windows drive letters).  On macOS paths
    # never contain colons so only backslashes need escaping.
    srt_path_str = str(zh_srt.resolve()).replace("\\", "\\\\").replace(":", "\\:")

    vf = f"subtitles={srt_path_str}:force_style={force_style}"

    cmd = [
        _resolve_ffmpeg(),
        "-y",
        "-i", str(source_mp4),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
        str(final_mp4),
    ]

    logger.info("Burning subtitles into %s", final_mp4)
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.warning(
            "ffmpeg burn failed (rc=%d): %s",
            result.returncode,
            result.stderr[-500:],
        )
        return False

    if not final_mp4.exists():
        logger.warning("ffmpeg exited 0 but final.mp4 not found in %s", warehouse_dir)
        return False

    if not _has_video_stream(final_mp4):
        logger.warning("ffmpeg exited 0 but final.mp4 has no video stream in %s", warehouse_dir)
        return False

    logger.info("Subtitles burned → %s (%d bytes)", final_mp4, final_mp4.stat().st_size)
    return True
