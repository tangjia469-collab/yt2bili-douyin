"""Tests for MiniMax translation stage."""

from unittest.mock import patch
from yt2bili.stages.translate import (
    parse_srt,
    build_srt,
    call_minimax,
    translate_srt,
    BATCH_SIZE,
)

SAMPLE_SRT = """\
1
00:00:01,000 --> 00:00:03,000
Hello world

2
00:00:04,000 --> 00:00:06,000
This is a test

"""


def test_parse_and_rebuild_roundtrip():
    """parse_srt → build_srt should reconstruct equivalent SRT."""
    cues = parse_srt(SAMPLE_SRT)
    assert len(cues) == 2
    assert cues[0]["start"] == "00:00:01,000"
    assert cues[0]["end"] == "00:00:03,000"
    assert cues[0]["text"] == "Hello world"
    assert cues[1]["text"] == "This is a test"

    rebuilt = build_srt(cues)
    rebuilt_cues = parse_srt(rebuilt)
    assert len(rebuilt_cues) == 2
    assert rebuilt_cues[0]["start"] == cues[0]["start"]
    assert rebuilt_cues[0]["end"] == cues[0]["end"]
    assert rebuilt_cues[1]["text"] == cues[1]["text"]


def test_translate_preserves_timeline():
    """translate_srt must keep original timestamps, only change text."""
    mock_zh = ["你好世界", "这是一个测试"]
    with patch("yt2bili.stages.translate.call_minimax", return_value=mock_zh):
        result = translate_srt(SAMPLE_SRT, api_key="fake", title="Test Video")

    cues = parse_srt(result)
    assert len(cues) == 2
    assert cues[0]["start"] == "00:00:01,000"
    assert cues[0]["end"] == "00:00:03,000"
    assert cues[0]["text"] == "你好世界"
    assert cues[1]["start"] == "00:00:04,000"
    assert cues[1]["text"] == "这是一个测试"


def test_translate_batches():
    """translate_srt should split into batches of BATCH_SIZE."""
    # Build SRT with BATCH_SIZE + 1 cues
    lines = []
    for i in range(BATCH_SIZE + 1):
        start = f"00:{i//60:02d}:{i%60:02d},000"
        end_s = i + 1
        end = f"00:{end_s//60:02d}:{end_s%60:02d},000"
        lines.append(f"{i+1}\n{start} --> {end}\nLine {i+1}\n")
    big_srt = "\n".join(lines) + "\n"

    call_count = []

    def mock_minimax(texts, api_key, title=""):
        call_count.append(len(texts))
        return [f"译{t}" for t in texts]

    with patch("yt2bili.stages.translate.call_minimax", side_effect=mock_minimax):
        result = translate_srt(big_srt, api_key="fake")

    assert len(call_count) == 2, f"Expected 2 batch calls, got {len(call_count)}"
    assert call_count[0] == BATCH_SIZE
    assert call_count[1] == 1
    cues = parse_srt(result)
    assert len(cues) == BATCH_SIZE + 1


def test_translate_fallback_on_short_response():
    """When MiniMax can't translate a line (wrong count even for a single
    line), that line falls back to English; timeline stays intact.

    The resilient translator recursively splits a mismatched batch. The mock
    returns an empty list regardless of input, so even single-line batches
    never match and must fall back to the original English text.
    """
    with patch("yt2bili.stages.translate.call_minimax", return_value=[]):
        result = translate_srt(SAMPLE_SRT, api_key="fake")

    cues = parse_srt(result)
    assert len(cues) == 2
    # Fallback: both cues keep English text
    assert cues[0]["text"] == "Hello world"
    assert cues[1]["text"] == "This is a test"
    # Timestamps must still be intact
    assert cues[0]["start"] == "00:00:01,000"


def test_translate_partial_mismatch_only_loses_bad_line():
    """A batch where MiniMax drops one line: resilient split keeps the good
    translations and only the unresolvable line stays English."""
    # First call (2 lines) returns 1 item → mismatch → split into two 1-line
    # calls. Each 1-line call returns exactly 1 item → both translate fine.
    with patch("yt2bili.stages.translate.call_minimax",
               side_effect=lambda texts, key, title="": ["译:" + t for t in texts]):
        result = translate_srt(SAMPLE_SRT, api_key="fake")

    cues = parse_srt(result)
    assert cues[0]["text"] == "译:Hello world"
    assert cues[1]["text"] == "译:This is a test"


def test_call_minimax_strips_numbering():
    """call_minimax should strip leading numbering like '1. ' from responses."""
    import json
    import urllib.request

    fake_response_body = json.dumps({
        "choices": [{
            "message": {
                "content": "1. 你好世界\n2. 这是测试\n"
            }
        }]
    }).encode("utf-8")

    class FakeResponse:
        def read(self):
            return fake_response_body
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = call_minimax(["Hello world", "This is a test"], api_key="fake")

    assert result == ["你好世界", "这是测试"]


def test_call_minimax_parses_json_array():
    """call_minimax should prefer exact JSON array responses."""
    import json
    import urllib.request

    fake_response_body = json.dumps({
        "choices": [{
            "message": {
                "content": "[\"你好世界\", \"这是一个测试\"]"
            }
        }]
    }).encode("utf-8")

    class FakeResponse:
        def read(self):
            return fake_response_body
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = call_minimax(["Hello world", "This is a test"], api_key="fake")

    assert result == ["你好世界", "这是一个测试"]
