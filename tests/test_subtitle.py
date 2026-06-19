"""Tests for the subtitle stage (YouTube CC + Whisper ASR fallback)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt2bili.stages.subtitle import (
    clean_srt,
    get_english_subtitle,
    vtt_to_srt,
)

# ---------------------------------------------------------------------------
# clean_srt
# ---------------------------------------------------------------------------

def test_clean_srt_strips_html_tags() -> None:
    """clean_srt must remove HTML tags while preserving the surrounding text."""
    raw = (
        "1\n"
        "00:00:01,000 --> 00:00:03,000\n"
        "<font color=white>Hello</font> <i>world</i></i>\n\n"
        "2\n"
        "00:00:04,000 --> 00:00:06,000\n"
        "<b>Bold text</b> and plain text\n"
    )
    result = clean_srt(raw)

    # Tags must be gone
    assert "<font" not in result
    assert "<i>" not in result
    assert "</i>" not in result
    assert "<b>" not in result
    assert "</b>" not in result
    assert "</font>" not in result

    # Text must survive
    assert "Hello" in result
    assert "world" in result
    assert "Bold text" in result
    assert "plain text" in result


# ---------------------------------------------------------------------------
# vtt_to_srt
# ---------------------------------------------------------------------------

def test_vtt_to_srt_basic_conversion() -> None:
    """vtt_to_srt must produce a valid SRT block from a minimal VTT input."""
    vtt = (
        "WEBVTT\n"
        "Kind: captions\n"
        "Language: en\n"
        "\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "Hello, world.\n"
        "\n"
        "00:00:04.000 --> 00:00:06.500\n"
        "Second line.\n"
    )
    result = vtt_to_srt(vtt)

    # Sequence numbers
    assert "1\n" in result
    assert "2\n" in result

    # SRT uses commas, not dots
    assert "00:00:01,000 --> 00:00:03,000" in result
    assert "00:00:04,000 --> 00:00:06,500" in result

    # Text preserved
    assert "Hello, world." in result
    assert "Second line." in result


# ---------------------------------------------------------------------------
# get_english_subtitle — prefer CC (prefer_asr=False, CC returns content)
# ---------------------------------------------------------------------------

def test_prefer_cc_when_available(tmp_path: Path) -> None:
    """When prefer_asr=False and YouTube CC returns valid SRT, source must be 'youtube'."""
    good_srt = "1\n00:00:01,000 --> 00:00:03,000\nHello from CC.\n\n"

    with patch(
        "yt2bili.stages.subtitle.fetch_youtube_cc",
        return_value=good_srt,
    ) as mock_cc, patch(
        "yt2bili.stages.subtitle.run_asr",
    ) as mock_asr:
        content, source = get_english_subtitle(
            "https://youtube.com/watch?v=test",
            tmp_path,
            prefer_asr=False,
        )

    assert source == "youtube"
    assert content == good_srt
    mock_cc.assert_called_once()
    mock_asr.assert_not_called()

    # en.srt must be written
    srt_file = tmp_path / "en.srt"
    assert srt_file.exists()
    assert srt_file.read_text(encoding="utf-8") == good_srt


# ---------------------------------------------------------------------------
# get_english_subtitle — CC empty → fallback to ASR
# ---------------------------------------------------------------------------

def test_fallback_to_asr_when_no_cc(tmp_path: Path) -> None:
    """When prefer_asr=False and CC returns empty string, source must be 'asr'."""
    asr_srt = "1\n00:00:01,000 --> 00:00:04,000\nTranscribed speech.\n\n"

    with patch(
        "yt2bili.stages.subtitle.fetch_youtube_cc",
        return_value="",
    ), patch(
        "yt2bili.stages.subtitle.run_asr",
        return_value=asr_srt,
    ) as mock_asr:
        content, source = get_english_subtitle(
            "https://youtube.com/watch?v=test",
            tmp_path,
            prefer_asr=False,
        )

    assert source == "asr"
    assert content == asr_srt
    mock_asr.assert_called_once_with(tmp_path)

    srt_file = tmp_path / "en.srt"
    assert srt_file.exists()
    assert srt_file.read_text(encoding="utf-8") == asr_srt


# ---------------------------------------------------------------------------
# get_english_subtitle — prefer_asr=True skips CC entirely
# ---------------------------------------------------------------------------

def test_prefer_asr_skips_cc(tmp_path: Path) -> None:
    """When prefer_asr=True, fetch_youtube_cc must never be called."""
    asr_srt = "1\n00:00:00,000 --> 00:00:02,000\nASR only.\n\n"

    with patch(
        "yt2bili.stages.subtitle.fetch_youtube_cc",
    ) as mock_cc, patch(
        "yt2bili.stages.subtitle.run_asr",
        return_value=asr_srt,
    ):
        content, source = get_english_subtitle(
            "https://youtube.com/watch?v=test",
            tmp_path,
            prefer_asr=True,
        )

    assert source == "asr"
    assert content == asr_srt
    mock_cc.assert_not_called()

    srt_file = tmp_path / "en.srt"
    assert srt_file.exists()
