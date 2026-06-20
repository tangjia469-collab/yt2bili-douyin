"""Tests for dashboard actions: approve / skip / retry / toggle priority / stats."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yt2bili import actions
from yt2bili.db import Database
from yt2bili.states import State


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.sqlite")
    d.init()
    return d


def _insert(db, vid="v1", priority=False):
    db.insert_video(vid, "CH1", "https://yt/" + vid, "Title " + vid, priority)


# --------------------------------------------------------------------------
# approve
# --------------------------------------------------------------------------

def test_approve_moves_pending_review_to_ready(db):
    _insert(db, priority=True)
    db.update_stage("v1", State.PENDING_REVIEW)
    actions.approve(db, "v1")
    assert db.get_video("v1").stage == State.READY.value


def test_approve_rejects_non_pending(db):
    _insert(db)
    db.update_stage("v1", State.DOWNLOADED)
    with pytest.raises(ValueError):
        actions.approve(db, "v1")


# --------------------------------------------------------------------------
# skip
# --------------------------------------------------------------------------

def test_skip_sets_skipped(db):
    _insert(db)
    db.update_stage("v1", State.EN_SUBTITLED)
    actions.skip(db, "v1")
    assert db.get_video("v1").stage == State.SKIPPED.value


def test_skip_rejects_published(db):
    _insert(db)
    db.update_stage("v1", State.PUBLISHED)
    with pytest.raises(ValueError):
        actions.skip(db, "v1")


# --------------------------------------------------------------------------
# retry
# --------------------------------------------------------------------------

def test_retry_resets_failed_translate_to_predecessor(db):
    _insert(db)
    db.update_stage("v1", State.failed("translate"), error="boom")
    actions.retry(db, "v1")
    v = db.get_video("v1")
    assert v.stage == State.EN_SUBTITLED.value
    assert v.error is None


def test_retry_failed_publish_resets_to_ready(db):
    _insert(db)
    db.update_stage("v1", State.failed("publish"), error="biliup down")
    actions.retry(db, "v1")
    assert db.get_video("v1").stage == State.READY.value


def test_retry_rejects_non_failed(db):
    _insert(db)
    db.update_stage("v1", State.READY)
    with pytest.raises(ValueError):
        actions.retry(db, "v1")


# --------------------------------------------------------------------------
# toggle priority
# --------------------------------------------------------------------------

def test_toggle_priority_flips_flag(db):
    _insert(db, priority=False)
    actions.toggle_priority(db, "v1")
    assert db.get_video("v1").is_priority is True
    actions.toggle_priority(db, "v1")
    assert db.get_video("v1").is_priority is False


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------

def test_stats_groups_by_bucket(db):
    _insert(db, "a")                                  # discovered → processing
    _insert(db, "b"); db.update_stage("b", State.EN_SUBTITLED)   # processing
    _insert(db, "c"); db.update_stage("c", State.PENDING_REVIEW)
    _insert(db, "d"); db.update_stage("d", State.READY)
    _insert(db, "e"); db.update_stage("e", State.PUBLISHED)
    _insert(db, "f"); db.update_stage("f", State.failed("download"), error="x")
    _insert(db, "g"); db.update_stage("g", State.SKIPPED)

    s = actions.stats(db)
    assert s["processing"] == 2
    assert s["pending_review"] == 1
    assert s["ready"] == 1
    assert s["published"] == 1
    assert s["failed"] == 1
    assert s["skipped"] == 1
