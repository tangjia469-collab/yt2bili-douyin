"""Tests for the publisher: FIFO ready queue, daily limit, fail-pause gate."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yt2bili import publisher
from yt2bili.db import Database
from yt2bili.states import State
from yt2bili.config import Config, Channel, Defaults, BiliupConfig


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


def _config(threshold=3, limit=2, gap=30):
    return Config(
        defaults=Defaults(
            publish_fail_threshold=threshold,
            daily_publish_limit=limit,
            min_publish_gap_min=gap,
        ),
        biliup=BiliupConfig(binary="biliup", tid=122, tags=["搬运"]),
    )


def _ready(db, vid):
    db.insert_video(vid, "CH1", "https://yt/" + vid, "Title " + vid, False)
    db.update_stage(vid, State.READY)


_NO_SLEEP = lambda s: None


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_publisher_publishes_ready_fifo(db, warehouse):
    _ready(db, "a")
    _ready(db, "b")
    order = []
    def fake_publish(wd, title, cfg):
        order.append(Path(wd).name)
        return True
    publisher.run_publisher(db, _config(limit=10), warehouse,
                            publish_fn=fake_publish, sleep_fn=_NO_SLEEP)
    assert order == ["a", "b"]
    assert db.get_video("a").stage == State.PUBLISHED.value
    assert db.get_video("b").stage == State.PUBLISHED.value
    assert db.get_video("a").published_at is not None


def test_publisher_respects_daily_limit(db, warehouse):
    for v in ("a", "b", "c"):
        _ready(db, v)
    count = {"n": 0}
    def fake_publish(wd, title, cfg):
        count["n"] += 1
        return True
    publisher.run_publisher(db, _config(limit=2), warehouse,
                            publish_fn=fake_publish, sleep_fn=_NO_SLEEP)
    assert count["n"] == 2
    # third stays ready
    assert db.get_video("c").stage == State.READY.value


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------

def test_publisher_marks_failed_on_upload_failure(db, warehouse):
    _ready(db, "a")
    publisher.run_publisher(db, _config(), warehouse,
                            publish_fn=lambda wd, t, c: False, sleep_fn=_NO_SLEEP)
    assert db.get_video("a").stage == State.failed("publish")
    assert db.get_video("a").error is not None


def test_publisher_pauses_after_threshold(db, warehouse):
    for v in ("a", "b", "c", "d"):
        _ready(db, v)
    count = {"n": 0}
    def fake_publish(wd, title, cfg):
        count["n"] += 1
        return False
    publisher.run_publisher(db, _config(threshold=3, limit=10), warehouse,
                            publish_fn=fake_publish, sleep_fn=_NO_SLEEP)
    # stops after 3 failures, never attempts the 4th
    assert count["n"] == 3
    assert db.get_meta(publisher.PAUSE_KEY) == "1"


def test_publisher_skips_when_paused(db, warehouse):
    _ready(db, "a")
    db.set_meta(publisher.PAUSE_KEY, "1")
    called = {"n": 0}
    def fake_publish(wd, title, cfg):
        called["n"] += 1
        return True
    publisher.run_publisher(db, _config(), warehouse,
                            publish_fn=fake_publish, sleep_fn=_NO_SLEEP)
    assert called["n"] == 0
    assert db.get_video("a").stage == State.READY.value


def test_publisher_resets_streak_on_success(db, warehouse):
    _ready(db, "a")
    _ready(db, "b")
    # pre-seed a failure streak of 2
    db.set_meta(publisher.STREAK_KEY, "2")
    publisher.run_publisher(db, _config(threshold=3, limit=10), warehouse,
                            publish_fn=lambda wd, t, c: True, sleep_fn=_NO_SLEEP)
    # a success must reset the streak so a later single failure won't trip pause
    assert db.get_meta(publisher.STREAK_KEY) == "0"


# --------------------------------------------------------------------------
# Resume + gap
# --------------------------------------------------------------------------

def test_resume_publishing_clears_flags(db):
    db.set_meta(publisher.PAUSE_KEY, "1")
    db.set_meta(publisher.STREAK_KEY, "5")
    publisher.resume_publishing(db)
    assert db.get_meta(publisher.PAUSE_KEY) == "0"
    assert db.get_meta(publisher.STREAK_KEY) == "0"


def test_publisher_gap_between_publishes(db, warehouse):
    _ready(db, "a")
    _ready(db, "b")
    sleeps = []
    publisher.run_publisher(db, _config(limit=10, gap=30), warehouse,
                            publish_fn=lambda wd, t, c: True,
                            sleep_fn=lambda s: sleeps.append(s))
    # exactly one gap between the two publishes, of 30 minutes
    assert sleeps == [30 * 60]
