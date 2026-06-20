import pytest
from yt2bili.db import Database
from yt2bili.states import State


def test_insert_and_get_video(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    db.insert_video("vid1", "UCxxxx", "https://youtu.be/vid1", "Title 1", is_priority=False)
    v = db.get_video("vid1")
    assert v.video_id == "vid1"
    assert v.stage == State.DISCOVERED.value


def test_update_stage(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    db.insert_video("vid2", "UCxxxx", "u", "t", False)
    db.update_stage("vid2", State.DOWNLOADED)
    v = db.get_video("vid2")
    assert v.stage == "downloaded"


def test_duplicate_insert_skipped(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    db.insert_video("vid3", "UCxxxx", "u", "t", False)
    with pytest.raises(Exception):
        db.insert_video("vid3", "UCxxxx", "u", "t", False)


def test_list_by_stage(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    db.insert_video("a", "c", "u", "t", False)
    db.insert_video("b", "c", "u", "t", False)
    db.update_stage("b", State.READY)
    pending = db.list_by_stage(State.DISCOVERED)
    assert {v.video_id for v in pending} == {"a"}


def test_update_subtitle_source(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    db.insert_video("vid4", "UCxxxx", "u", "t", False)
    db.update_subtitle_source("vid4", "youtube")
    v = db.get_video("vid4")
    assert v.subtitle_source == "youtube"


def test_list_all(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    db.insert_video("x1", "c", "u", "t", False)
    db.insert_video("x2", "c", "u", "t", False)
    all_vids = db.list_all()
    ids = {v.video_id for v in all_vids}
    assert ids == {"x1", "x2"}


def test_failed_stage_string(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    db.insert_video("v5", "c", "u", "t", False)
    db.update_stage("v5", State.failed("downloaded"), error="boom")
    v = db.get_video("v5")
    assert v.stage == "failed_downloaded"
    assert v.error == "boom"


def test_list_by_stage_accepts_string(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    db.insert_video("p1", "c", "u", "t", False)
    db.update_stage("p1", State.failed("publish"), error="net")
    rows = db.list_by_stage("failed_publish")
    assert {v.video_id for v in rows} == {"p1"}


def test_mark_published_sets_timestamp(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    db.insert_video("pub1", "c", "u", "t", False)
    db.update_stage("pub1", State.READY)
    db.mark_published("pub1")
    v = db.get_video("pub1")
    assert v.stage == State.PUBLISHED.value
    assert v.published_at is not None


def test_mark_published_missing_raises(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    with pytest.raises(KeyError):
        db.mark_published("nope")


def test_meta_get_set(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init()
    assert db.get_meta("publish_paused", "0") == "0"
    db.set_meta("publish_paused", "1")
    assert db.get_meta("publish_paused", "0") == "1"
    db.set_meta("publish_paused", "0")
    assert db.get_meta("publish_paused", "0") == "0"
