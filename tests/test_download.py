"""Tests for the download stage (yt-dlp integration)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt2bili.stages.download import download_video, load_meta


def test_download_calls_ytdlp_with_correct_args(tmp_path: Path) -> None:
    """yt-dlp must be invoked with the url and warehouse_dir output template."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    # Create the expected output file so download_video doesn't fail post-run
    (tmp_path / "source.mp4").touch()

    with patch("yt2bili.stages.download._has_video_stream", return_value=True):
        with patch("yt2bili.stages.download.subprocess.run", return_value=mock_result) as mock_run:
            download_video("https://youtube.com/watch?v=abc123", tmp_path)

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]  # first positional arg is the command list

    assert "yt-dlp" in cmd[0] or any("yt-dlp" in part for part in cmd)
    assert "https://youtube.com/watch?v=abc123" in cmd
    assert any(str(tmp_path) in part for part in cmd), "warehouse_dir should appear in output template"


def test_download_returns_true_on_success(tmp_path: Path) -> None:
    """Returns True when yt-dlp exits with returncode 0 and mp4 exists."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    # Simulate yt-dlp writing source.mp4
    (tmp_path / "source.mp4").touch()

    with patch("yt2bili.stages.download._has_video_stream", return_value=True):
        with patch("yt2bili.stages.download.subprocess.run", return_value=mock_result):
            result = download_video("https://youtube.com/watch?v=abc123", tmp_path)

    assert result is True


def test_download_returns_false_on_failure(tmp_path: Path) -> None:
    """Returns False when yt-dlp exits with a non-zero returncode."""
    mock_result = MagicMock()
    mock_result.returncode = 1

    with patch("yt2bili.stages.download.subprocess.run", return_value=mock_result):
        result = download_video("https://youtube.com/watch?v=abc123", tmp_path)

    assert result is False


def test_load_meta_returns_dict(tmp_path: Path) -> None:
    """load_meta reads the *.info.json yt-dlp writes and returns its contents."""
    meta = {"id": "abc123", "title": "Test Video", "duration": 120}
    info_file = tmp_path / "source.abc123.info.json"
    info_file.write_text(json.dumps(meta), encoding="utf-8")

    result = load_meta(tmp_path)

    assert result == meta


def test_load_meta_returns_empty_when_missing(tmp_path: Path) -> None:
    """load_meta returns an empty dict when no .info.json file is present."""
    result = load_meta(tmp_path)

    assert result == {}
