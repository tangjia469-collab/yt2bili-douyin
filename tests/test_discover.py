import json
import unittest.mock as mock
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

    entries = discoverer._parse_entries(raw_json, channel_id="UCfake", is_priority=False)

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

    entries = discoverer._parse_entries(raw_json, channel_id="UCfake", is_priority=False)
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

    entries = discoverer._parse_entries(raw_json, channel_id="UCfake", is_priority=False)
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


# ---------------------------------------------------------------------------
# test_discover_channel integration (mocked subprocess)
# ---------------------------------------------------------------------------

def test_discover_channel_inserts_and_skips_long(tmp_path):
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
