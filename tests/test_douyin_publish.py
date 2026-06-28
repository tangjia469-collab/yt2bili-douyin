"""Tests for Douyin publish stage."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from yt2bili.config import DouyinConfig
from yt2bili.stages.douyin_publish import publish_to_douyin


def _make_final(warehouse: Path) -> Path:
    warehouse.mkdir(parents=True, exist_ok=True)
    f = warehouse / "final.mp4"
    f.write_bytes(b"fake")
    return f


def _result(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def test_douyin_publish_returns_false_if_final_missing(tmp_path):
    assert publish_to_douyin(tmp_path, "Title", DouyinConfig()) is False


def test_douyin_publish_rejects_invalid_media(tmp_path):
    _make_final(tmp_path)
    with patch("yt2bili.stages.douyin_publish.is_valid_upload_video", return_value=False):
        assert publish_to_douyin(tmp_path, "Title", DouyinConfig()) is False


def test_douyin_publish_returns_true_on_page_success(tmp_path):
    _make_final(tmp_path)
    stdout = '{"ok": true, "text": "审核中"}\n'
    with patch("yt2bili.stages.douyin_publish.is_valid_upload_video", return_value=True):
        with patch("yt2bili.stages.douyin_publish.subprocess.run", return_value=_result(stdout=stdout)) as run:
            assert publish_to_douyin(tmp_path, "Title", DouyinConfig(enabled=True)) is True
    cmd = run.call_args[0][0]
    assert cmd == ["ego-browser", "nodejs"]
    assert "final.mp4" in run.call_args.kwargs["input"]


def test_douyin_publish_returns_false_on_login_required(tmp_path):
    _make_final(tmp_path)
    stdout = '{"ok": false, "reason": "login_required"}\n'
    with patch("yt2bili.stages.douyin_publish.is_valid_upload_video", return_value=True):
        with patch("yt2bili.stages.douyin_publish.subprocess.run", return_value=_result(stdout=stdout)):
            assert publish_to_douyin(tmp_path, "Title", DouyinConfig(enabled=True)) is False


def test_douyin_publish_returns_false_on_browser_error(tmp_path):
    _make_final(tmp_path)
    with patch("yt2bili.stages.douyin_publish.is_valid_upload_video", return_value=True):
        with patch("yt2bili.stages.douyin_publish.subprocess.run", return_value=_result(returncode=1, stderr="boom")):
            assert publish_to_douyin(tmp_path, "Title", DouyinConfig(enabled=True)) is False
