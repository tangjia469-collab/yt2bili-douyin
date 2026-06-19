"""Subtitle stage: fetch English subtitles from YouTube CC or Whisper ASR."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

# Path to the whisper model
_WHISPER_MODEL = Path.home() / ".whisper" / "models" / "ggml-small.en.bin"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_srt(raw: str) -> str:
    """Strip HTML tags (font, i, b, etc.) from SRT text, preserving content.

    Args:
        raw: SRT text that may contain inline HTML tags.

    Returns:
        SRT text with all HTML tags removed.
    """
    return re.sub(r"<[^>]+>", "", raw)


def vtt_to_srt(vtt: str) -> str:
    """Convert WebVTT subtitle text to SRT format.

    Handles the WEBVTT header/metadata lines and converts dot-separated
    timestamps to comma-separated ones as required by SRT.

    Args:
        vtt: Raw WebVTT text.

    Returns:
        SRT-formatted subtitle text.
    """
    lines = vtt.splitlines()
    srt_blocks = []
    seq = 1
    i = 0

    # Skip WEBVTT header and any metadata lines until first blank line
    while i < len(lines) and lines[i].strip() != "":
        i += 1
    # Skip the blank line after the header
    while i < len(lines) and lines[i].strip() == "":
        i += 1

    while i < len(lines):
        line = lines[i].strip()

        # Skip cue identifiers (non-timestamp, non-blank lines before timing)
        if line == "":
            i += 1
            continue

        # Check if this is a timing line (may be preceded by an optional cue id)
        timing_match = re.match(
            r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[.,]\d{3})",
            line,
        )
        if not timing_match:
            # Might be a cue identifier — peek at next line for timing
            i += 1
            continue

        start = timing_match.group(1).replace(".", ",")
        end = timing_match.group(2).replace(".", ",")
        i += 1

        # Collect text lines until blank or EOF
        text_lines = []
        while i < len(lines) and lines[i].strip() != "":
            text_lines.append(lines[i])
            i += 1

        if text_lines:
            srt_blocks.append(
                f"{seq}\n{start} --> {end}\n" + "\n".join(text_lines) + "\n"
            )
            seq += 1

    return "\n".join(srt_blocks) + "\n" if srt_blocks else ""


# ---------------------------------------------------------------------------
# Internal stage helpers
# ---------------------------------------------------------------------------

def fetch_youtube_cc(url: str, warehouse_dir: Path) -> str:
    """Download YouTube auto-generated captions via yt-dlp.

    Runs yt-dlp with ``--write-auto-sub --sub-lang en --skip-download
    --sub-format vtt``, then reads the resulting VTT file and converts it
    to SRT.

    Args:
        url: YouTube video URL.
        warehouse_dir: Directory to write subtitle files into.

    Returns:
        Cleaned SRT text, or an empty string if no CC is available.
    """
    warehouse_dir = Path(warehouse_dir)
    warehouse_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "--write-auto-sub",
        "--sub-lang", "en",
        "--skip-download",
        "--sub-format", "vtt",
        "-o", str(warehouse_dir / "cc.%(ext)s"),
        url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.debug("yt-dlp CC fetch failed (rc=%d): %s", result.returncode, result.stderr)
        return ""

    # yt-dlp writes <base>.en.vtt
    vtt_files = list(warehouse_dir.glob("*.vtt"))
    if not vtt_files:
        logger.debug("yt-dlp finished but no .vtt file found in %s", warehouse_dir)
        return ""

    vtt_path = sorted(vtt_files)[0]
    try:
        vtt_text = vtt_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read VTT file %s: %s", vtt_path, exc)
        return ""

    srt = vtt_to_srt(vtt_text)
    return clean_srt(srt)


def run_asr(warehouse_dir: Path) -> str:
    """Transcribe the video audio with whisper-cli and return SRT text.

    Steps:
    1. Extract a 16-kHz mono WAV with ffmpeg from ``source.mp4``.
    2. Run ``whisper-cli`` with the small English model to produce an SRT.
    3. Read and return the resulting ``.srt`` file.

    Args:
        warehouse_dir: Directory containing ``source.mp4``; output files are
            also written here.

    Returns:
        SRT text produced by Whisper, or an empty string on any failure.
    """
    warehouse_dir = Path(warehouse_dir)
    source_mp4 = warehouse_dir / "source.mp4"
    audio_wav = warehouse_dir / "audio.wav"
    srt_prefix = str(warehouse_dir / "whisper_out")

    if not source_mp4.exists():
        logger.warning("run_asr: source.mp4 not found in %s", warehouse_dir)
        return ""

    # 1. Extract audio
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-i", str(source_mp4),
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(audio_wav),
    ]
    ff_result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
    if ff_result.returncode != 0:
        logger.warning("ffmpeg audio extraction failed: %s", ff_result.stderr)
        return ""

    # 2. Run whisper-cli
    whisper_cmd = [
        "whisper-cli",
        "-m", str(_WHISPER_MODEL),
        "-f", str(audio_wav),
        "-osrt",
        "-of", srt_prefix,
    ]
    wh_result = subprocess.run(whisper_cmd, capture_output=True, text=True)
    if wh_result.returncode != 0:
        logger.warning("whisper-cli failed: %s", wh_result.stderr)
        return ""

    # 3. Read the SRT whisper-cli produced (<prefix>.srt)
    srt_path = Path(srt_prefix + ".srt")
    if not srt_path.exists():
        logger.warning("whisper-cli ran but %s not found", srt_path)
        return ""

    try:
        return srt_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read ASR SRT %s: %s", srt_path, exc)
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_english_subtitle(
    url: str,
    warehouse_dir: Path,
    prefer_asr: bool,
) -> Tuple[str, str]:
    """Retrieve English subtitles, writing ``en.srt`` to warehouse_dir.

    Source priority:
    - ``prefer_asr=False``: try YouTube CC first; fall back to ASR if CC is
      empty or unavailable.
    - ``prefer_asr=True``: skip YouTube CC entirely and go straight to ASR.

    Args:
        url: YouTube video URL.
        warehouse_dir: Directory to write ``en.srt`` and intermediate files.
        prefer_asr: When True, bypass YouTube CC and use Whisper ASR.

    Returns:
        A tuple of ``(srt_content, source)`` where *source* is ``"youtube"``
        or ``"asr"``.
    """
    warehouse_dir = Path(warehouse_dir)
    warehouse_dir.mkdir(parents=True, exist_ok=True)

    srt_content = ""
    source = ""

    if not prefer_asr:
        srt_content = fetch_youtube_cc(url, warehouse_dir)
        if srt_content:
            source = "youtube"

    if not srt_content:
        srt_content = run_asr(warehouse_dir)
        source = "asr"

    # Write en.srt regardless of source
    srt_path = warehouse_dir / "en.srt"
    srt_path.write_text(srt_content, encoding="utf-8")
    logger.info("Wrote %s (source=%s, %d chars)", srt_path, source, len(srt_content))

    return srt_content, source
