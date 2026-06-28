"""Tests for Douyin publisher orchestration."""

from pathlib import Path

import pytest

from yt2bili.config import Config, DouyinConfig
from yt2bili.db import Database
from yt2bili.douyin_publisher import (
    PAUSE_KEY,
    STREAK_KEY,
    resume_douyin_publishing,
    run_douyin_publisher,
    status_key,
)
from yt2bili.states import State


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.sqlite")
    d.init()
    return d


@pytest.fixture
def warehouse(tmp_path):
    w = tmp_path / "warehouse"
    w.mkdir()
    return w


def _config(**kw):
    return Config(douyin=DouyinConfig(enabled=True, **kw))


def _insert_published(db, vid="v1"):
    db.insert_video(vid, "CH1", "https://yt/" + vid, "Title " + vid, False)
    db.mark_published(vid)


def test_douyin_publisher_skips_when_disabled(db, warehouse):
    _insert_published(db)
    called = {"n": 0}

    def fake(*args):
        called["n"] += 1
        return True

    run_douyin_publisher(db, Config(douyin=DouyinConfig(enabled=False)), warehouse, publish_fn=fake)
    assert called["n"] == 0


def test_douyin_publisher_marks_done(db, warehouse):
    _insert_published(db)
    run_douyin_publisher(db, _config(), warehouse, publish_fn=lambda *args: True)
    assert db.get_meta(status_key("v1")) == "done"
    assert db.get_meta(STREAK_KEY) == "0"


def test_douyin_publisher_skips_done(db, warehouse):
    _insert_published(db)
    db.set_meta(status_key("v1"), "done")
    called = {"n": 0}

    def fake(*args):
        called["n"] += 1
        return True

    run_douyin_publisher(db, _config(), warehouse, publish_fn=fake)
    assert called["n"] == 0


def test_douyin_publisher_respects_daily_limit(db, warehouse):
    _insert_published(db, "v1")
    _insert_published(db, "v2")
    published = []

    def fake(wd, title, cfg):
        published.append(wd.name)
        return True

    run_douyin_publisher(db, _config(daily_publish_limit=1), warehouse, publish_fn=fake)
    assert published == ["v1"]
    assert db.get_meta(status_key("v1")) == "done"
    assert db.get_meta(status_key("v2")) is None


def test_douyin_publisher_pauses_after_failures(db, warehouse):
    _insert_published(db, "v1")
    _insert_published(db, "v2")
    run_douyin_publisher(
        db,
        _config(publish_fail_threshold=2, daily_publish_limit=10),
        warehouse,
        publish_fn=lambda *args: False,
    )
    assert db.get_meta(PAUSE_KEY) == "1"
    assert db.get_meta(STREAK_KEY) == "2"


def test_resume_douyin_publishing(db):
    db.set_meta(PAUSE_KEY, "1")
    db.set_meta(STREAK_KEY, "3")
    resume_douyin_publishing(db)
    assert db.get_meta(PAUSE_KEY) == "0"
    assert db.get_meta(STREAK_KEY) == "0"
