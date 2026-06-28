import json
from unittest.mock import MagicMock, patch

import pytest

from yt2bili.config import Config, Channel, Defaults
from yt2bili.db import Database
from yt2bili.discover import Discoverer
from yt2bili.states import State


def _make_discoverer(max_duration_min: int = 60) -> Discoverer:
    """Build a Discoverer with a minimal in-memory config and mock DB."""
    defaults = Defaults(max_duration_min=max_duration_min)
    config = Config(channels=[], defaults=defaults)
    db = MagicMock(spec=Database)
    db.list_all.return_value = []
    return Discoverer(config=config, db=db)


def _make_yt_json(entries) -> str:
    """Wrap a list of entry dicts in the yt-dlp flat-playlist envelope."""
    return json.dumps({
        "id": "UCfake",
        "title": "Fake Channel",
        "entries": entries,
    })


# ---------------------------------------------------------------------------
# test_parse_entries_duration_filter
# ---------------------------------------------------------------------------

def test_parse_entries_duration_filter():
    """Short entry -> skip=False; long entry (>60 min) -> skip=True."""
    discoverer = _make_discoverer(max_duration_min=60)

    raw_json = _make_yt_json([
        {
            "id": "shortVid",
            "title": "Short Video",
            "url": "https://youtu.be/shortVid",
            "duration": 1800,   # 30 min — under limit
        },
        {
            "id": "longVid",
            "title": "Long Video",
            "url": "https://youtu.be/longVid",
            "duration": 4200,   # 70 min — over limit
        },
    ])

    entries = discoverer._parse_entries(raw_json)

    assert len(entries) == 2

    short = next(e for e in entries if e["video_id"] == "shortVid")
    long_ = next(e for e in entries if e["video_id"] == "longVid")

    assert short["skip"] is False, "30-min video should not be flagged as skip"
    assert long_["skip"] is True,  "70-min video should be flagged as skip"


def test_parse_entries_exact_boundary():
    """Entry exactly at max_duration_min * 60 seconds should NOT be skipped."""
    discoverer = _make_discoverer(max_duration_min=60)

    raw_json = _make_yt_json([
        {
            "id": "exactVid",
            "title": "Exact Boundary",
            "url": "https://youtu.be/exactVid",
            "duration": 3600,  # exactly 60 min
        },
    ])

    entries = discoverer._parse_entries(raw_json)
    assert entries[0]["skip"] is False


def test_parse_entries_missing_duration():
    """Entry with no duration field should default to 0 and not be skipped."""
    discoverer = _make_discoverer(max_duration_min=60)

    raw_json = _make_yt_json([
        {
            "id": "noDur",
            "title": "No Duration",
            "url": "https://youtu.be/noDur",
            # no "duration" key
        },
    ])

    entries = discoverer._parse_entries(raw_json)
    assert len(entries) == 1
    assert entries[0]["skip"] is False


# ---------------------------------------------------------------------------
# test_filter_new_excludes_known
# ---------------------------------------------------------------------------

def test_filter_new_excludes_known():
    """Only entries whose video_id is not in known_ids should be returned."""
    discoverer = _make_discoverer()

    entries = [
        {"video_id": "aaa", "title": "A", "url": "u1", "duration": 100, "skip": False},
        {"video_id": "bbb", "title": "B", "url": "u2", "duration": 200, "skip": False},
    ]

    known_ids = {"aaa"}  # "aaa" already in DB
    result = discoverer._filter_new(entries, known_ids)

    assert len(result) == 1
    assert result[0]["video_id"] == "bbb"


def test_filter_new_all_known():
    """All entries already known -> empty list."""
    discoverer = _make_discoverer()

    entries = [
        {"video_id": "x", "title": "X", "url": "u", "duration": 10, "skip": False},
    ]
    result = discoverer._filter_new(entries, {"x"})
    assert result == []


def test_filter_new_none_known():
    """No known ids -> all entries returned."""
    discoverer = _make_discoverer()

    entries = [
        {"video_id": "p", "title": "P", "url": "u", "duration": 10, "skip": False},
        {"video_id": "q", "title": "Q", "url": "u", "duration": 10, "skip": False},
    ]
    result = discoverer._filter_new(entries, set())
    assert len(result) == 2


def test_fetch_video_metrics_parses_stdout_on_nonzero_exit():
    """yt-dlp often exits non-zero but still emits valid JSON on stdout.
    The fetcher must parse stdout regardless of return code, or quality
    gating starves for samples and skips every video."""
    discoverer = _make_discoverer()

    fake_result = MagicMock()
    fake_result.returncode = 1
    fake_result.stdout = json.dumps({"like_count": 42, "comment_count": 7})
    fake_result.stderr = "WARNING: some formats unavailable"

    with patch("subprocess.run", return_value=fake_result):
        metrics = discoverer._fetch_video_metrics("abc123")

    assert metrics == {"like_count": 42, "comment_count": 7}


def test_fetch_video_metrics_returns_none_on_empty_stdout():
    """When yt-dlp emits no stdout at all, return None metrics (not crash)."""
    discoverer = _make_discoverer()

    fake_result = MagicMock()
    fake_result.returncode = 1
    fake_result.stdout = ""
    fake_result.stderr = "network error"

    with patch("subprocess.run", return_value=fake_result):
        metrics = discoverer._fetch_video_metrics("abc123")

    assert metrics == {"like_count": None, "comment_count": None}


def test_channel_videos_url_supports_handle():
    """Configured YouTube @handles should be accepted directly."""
    discoverer = _make_discoverer()
    assert discoverer._channel_videos_url("@ASMR-Melle") == "https://www.youtube.com/@ASMR-Melle/videos"


def test_quality_gate_skips_below_80_percent_baseline():
    """A video below both like/comment baselines is marked skipped_quality."""
    defaults = Defaults(
        quality_gate_enabled=True,
        quality_gate_ratio=0.8,
        quality_gate_recent_count=3,
        quality_gate_min_samples=3,
    )
    config = Config(channels=[], defaults=defaults)
    db = MagicMock(spec=Database)
    db.list_all.return_value = []
    discoverer = Discoverer(config=config, db=db)

    entries = [
        {"video_id": "a", "title": "A", "url": "u", "duration": 10, "skip": False, "skip_reason": None, "like_count": 100, "comment_count": 100},
        {"video_id": "b", "title": "B", "url": "u", "duration": 10, "skip": False, "skip_reason": None, "like_count": 100, "comment_count": 100},
        {"video_id": "c", "title": "C", "url": "u", "duration": 10, "skip": False, "skip_reason": None, "like_count": 100, "comment_count": 100},
        {"video_id": "d", "title": "D", "url": "u", "duration": 10, "skip": False, "skip_reason": None, "like_count": 70, "comment_count": 70},
        {"video_id": "e", "title": "E", "url": "u", "duration": 10, "skip": False, "skip_reason": None, "like_count": 80, "comment_count": 1},
    ]

    gated = discoverer._apply_quality_gate(entries)
    assert gated[3]["skip_reason"] == "quality"
    assert gated[4]["skip_reason"] is None


# ---------------------------------------------------------------------------
# test_discover_channel integration (mocked subprocess)
# ---------------------------------------------------------------------------

def test_discover_channel_inserts_and_skips_long():
    """_discover_channel: short video inserted as DISCOVERED; long video gets SKIPPED_LONG."""
    defaults = Defaults(max_duration_min=60)
    channel = Channel(id="UCfake", name="Fake", priority=False)
    config = Config(channels=[channel], defaults=defaults)

    db = MagicMock(spec=Database)
    db.list_all.return_value = []

    discoverer = Discoverer(config=config, db=db)

    raw_json = _make_yt_json([
        {"id": "shortVid", "title": "Short", "url": "https://youtu.be/shortVid", "duration": 600},
        {"id": "longVid",  "title": "Long",  "url": "https://youtu.be/longVid",  "duration": 7200},
    ])

    with patch.object(discoverer, "_fetch_channel", return_value=raw_json):
        discoverer._discover_channel(channel)

    # Both videos should be inserted
    assert db.insert_video.call_count == 2

    # Only the long video should have update_stage called with SKIPPED_LONG
    skipped_calls = [
        c for c in db.update_stage.call_args_list
        if c.args[1] == State.SKIPPED_LONG
    ]
    assert len(skipped_calls) == 1
    assert skipped_calls[0].args[0] == "longVid"
