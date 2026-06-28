"""Tests for the subtitle burn stage (ffmpeg hard-burn)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt2bili.stages.burn import burn_subtitles


def _make_success_result():
    m = MagicMock()
    m.returncode = 0
    m.stderr = ""
    return m


def _make_fail_result(stderr="ffmpeg error"):
    m = MagicMock()
    m.returncode = 1
    m.stderr = stderr
    return m


def test_burn_returns_false_if_source_mp4_missing(tmp_path):
    (tmp_path / "zh.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n\n")
    assert burn_subtitles(tmp_path) is False


def test_burn_returns_false_if_zh_srt_missing(tmp_path):
    (tmp_path / "source.mp4").write_bytes(b"\x00\x01")
    assert burn_subtitles(tmp_path) is False


def test_burn_returns_false_if_source_has_no_video(tmp_path):
    (tmp_path / "source.mp4").write_bytes(b"\x00\x01")
    (tmp_path / "zh.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n\n")
    with patch("yt2bili.stages.burn._has_video_stream", return_value=False):
        assert burn_subtitles(tmp_path) is False


def test_burn_calls_ffmpeg_with_correct_args(tmp_path):
    (tmp_path / "source.mp4").write_bytes(b"\x00\x01")
    (tmp_path / "zh.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n\n")
    captured_cmd = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        (tmp_path / "final.mp4").write_bytes(b"\x00\x02")
        return _make_success_result()

    with patch("yt2bili.stages.burn._has_video_stream", return_value=True):
        with patch("yt2bili.stages.burn.subprocess.run", side_effect=fake_run):
            result = burn_subtitles(tmp_path)

    assert result is True
    assert captured_cmd[0].endswith("ffmpeg")
    assert "-c:v" in captured_cmd
    assert "libx264" in captured_cmd
    assert "-c:a" in captured_cmd
    assert "aac" in captured_cmd
    assert "-pix_fmt" in captured_cmd
    assert "yuv420p" in captured_cmd
    assert "-vf" in captured_cmd
    vf_value = captured_cmd[captured_cmd.index("-vf") + 1]
    assert "subtitles=" in vf_value
    assert "force_style=" in vf_value


def test_burn_prefers_ffmpeg_full_when_available(tmp_path):
    ffmpeg_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    if not ffmpeg_full.exists():
        pytest.skip("ffmpeg-full is not installed on this machine")

    (tmp_path / "source.mp4").write_bytes(b"\x00\x01")
    (tmp_path / "zh.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n\n")
    captured_cmd = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        (tmp_path / "final.mp4").write_bytes(b"\x00\x02")
        return _make_success_result()

    with patch("yt2bili.stages.burn._has_video_stream", return_value=True):
        with patch("yt2bili.stages.burn.subprocess.run", side_effect=fake_run):
            result = burn_subtitles(tmp_path)

    assert result is True
    assert captured_cmd[0] == str(ffmpeg_full)


def test_burn_uses_custom_style(tmp_path):
    (tmp_path / "source.mp4").write_bytes(b"\x00\x01")
    (tmp_path / "zh.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n测试\n\n")
    captured_cmd = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        (tmp_path / "final.mp4").write_bytes(b"\x00\x02")
        return _make_success_result()

    with patch("yt2bili.stages.burn._has_video_stream", return_value=True):
        with patch("yt2bili.stages.burn.subprocess.run", side_effect=fake_run):
            result = burn_subtitles(
                tmp_path,
                font="微软雅黑",
                font_size=28,
                outline=2,
                margin_v=40,
            )

    assert result is True
    vf_value = captured_cmd[captured_cmd.index("-vf") + 1]
    assert "Fontname=微软雅黑" in vf_value
    assert "Fontsize=28" in vf_value
    assert "Outline=2" in vf_value
    assert "MarginV=40" in vf_value


def test_burn_returns_false_on_ffmpeg_error(tmp_path):
    (tmp_path / "source.mp4").write_bytes(b"\x00\x01")
    (tmp_path / "zh.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n错误\n\n")
    with patch("yt2bili.stages.burn._has_video_stream", return_value=True):
        with patch("yt2bili.stages.burn.subprocess.run", return_value=_make_fail_result("Invalid data")):
            result = burn_subtitles(tmp_path)
    assert result is False
    assert not (tmp_path / "final.mp4").exists()
