"""Tests for the publish stage (biliup upload)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt2bili.config import BiliupConfig
from yt2bili.stages.publish import publish_video


@pytest.fixture()
def warehouse(tmp_path: Path) -> Path:
    return tmp_path / "vid001"


@pytest.fixture()
def cfg() -> BiliupConfig:
    return BiliupConfig(binary="biliup", tid=122, tags=["搬运", "中文字幕"])


def _make_final(warehouse: Path) -> Path:
    warehouse.mkdir(parents=True, exist_ok=True)
    f = warehouse / "final.mp4"
    f.write_bytes(b"fake")
    return f


# ---------------------------------------------------------------------------
# Guard: missing file
# ---------------------------------------------------------------------------

def test_publish_returns_false_if_final_mp4_missing(warehouse: Path, cfg: BiliupConfig):
    warehouse.mkdir(parents=True, exist_ok=True)
    assert publish_video(warehouse, "Test Title", cfg) is False


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_publish_returns_true_on_success(warehouse: Path, cfg: BiliupConfig):
    _make_final(warehouse)
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = publish_video(warehouse, "My Video", cfg)

    assert result is True
    mock_run.assert_called_once()


def test_publish_calls_biliup_with_correct_args(warehouse: Path, cfg: BiliupConfig):
    _make_final(warehouse)
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        publish_video(warehouse, "Test Title", cfg)

    call_args = mock_run.call_args[0][0]  # positional list
    assert call_args[0] == "biliup"
    assert call_args[1] == "upload"
    assert str(warehouse / "final.mp4") in call_args
    assert "--title" in call_args
    assert "Test Title" in call_args
    assert "--tid" in call_args
    assert "122" in call_args
    assert "--tag" in call_args
    assert "搬运,中文字幕" in call_args


# ---------------------------------------------------------------------------
# Failure cases
# ---------------------------------------------------------------------------

def test_publish_returns_false_on_biliup_error(warehouse: Path, cfg: BiliupConfig):
    _make_final(warehouse)
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "network error"

    with patch("subprocess.run", return_value=mock_result):
        assert publish_video(warehouse, "Title", cfg) is False


def test_publish_detects_auth_error(warehouse: Path, cfg: BiliupConfig, caplog):
    _make_final(warehouse)
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "Error: login cookie expired"

    import logging
    with patch("subprocess.run", return_value=mock_result):
        with caplog.at_level(logging.ERROR, logger="yt2bili.stages.publish"):
            result = publish_video(warehouse, "Title", cfg)

    assert result is False
    assert "biliup login" in caplog.text.lower() or "re-authenticate" in caplog.text.lower()
