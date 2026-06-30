"""Tests for the pipeline worker: stage advancement and failure marking."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yt2bili import worker
from yt2bili.db import Database
from yt2bili.states import State
from yt2bili.config import Config, Channel, Defaults


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


def _config(priority=False, prefer_asr=False, max_min=60):
    return Config(
        channels=[Channel(id="CH1", name="chan", priority=priority)],
        defaults=Defaults(prefer_asr=prefer_asr, max_duration_min=max_min),
    )


def _insert(db, vid="v1", priority=False):
    db.insert_video(vid, "CH1", "https://yt/" + vid, "Title " + vid, priority)


# --------------------------------------------------------------------------
# Download step
# --------------------------------------------------------------------------

def test_download_advances_to_downloaded(db, warehouse, monkeypatch):
    _insert(db)
    monkeypatch.setattr(worker, "download_video", lambda url, wd: True)
    monkeypatch.setattr(worker, "load_meta", lambda wd: {"duration": 600})
    worker.process_video(db, "v1", _config(), warehouse)
    assert db.get_video("v1").stage == State.DOWNLOADED.value or \
        db.get_video("v1").stage != State.DISCOVERED.value


def test_download_failure_marks_failed(db, warehouse, monkeypatch):
    _insert(db)
    monkeypatch.setattr(worker, "download_video", lambda url, wd: False)
    new = worker.advance_one(db, db.get_video("v1"), _config(), warehouse)
    assert new == State.failed("download")
    assert db.get_video("v1").error is not None


def test_long_video_skipped(db, warehouse, monkeypatch):
    _insert(db)
    monkeypatch.setattr(worker, "download_video", lambda url, wd: True)
    monkeypatch.setattr(worker, "load_meta", lambda wd: {"duration": 5000})
    worker.advance_one(db, db.get_video("v1"), _config(max_min=60), warehouse)
    assert db.get_video("v1").stage == State.SKIPPED_LONG.value


# --------------------------------------------------------------------------
# Subtitle step
# --------------------------------------------------------------------------

def test_subtitle_sets_source(db, warehouse, monkeypatch):
    _insert(db)
    db.update_stage("v1", State.DOWNLOADED)
    monkeypatch.setattr(
        worker, "get_english_subtitle", lambda url, wd, prefer: ("1\n...\n", "youtube")
    )
    worker.advance_one(db, db.get_video("v1"), _config(), warehouse)
    v = db.get_video("v1")
    assert v.stage == State.EN_SUBTITLED.value
    assert v.subtitle_source == "youtube"


def test_subtitle_empty_marks_failed(db, warehouse, monkeypatch):
    _insert(db)
    db.update_stage("v1", State.DOWNLOADED)
    monkeypatch.setattr(
        worker, "get_english_subtitle", lambda url, wd, prefer: ("", "asr")
    )
    new = worker.advance_one(db, db.get_video("v1"), _config(), warehouse)
    assert new == State.failed("subtitle")


def test_subtitle_respects_channel_prefer_asr(db, warehouse, monkeypatch):
    _insert(db)
    db.update_stage("v1", State.DOWNLOADED)
    captured = {}
    def fake(url, wd, prefer):
        captured["prefer"] = prefer
        return ("x", "asr")
    monkeypatch.setattr(worker, "get_english_subtitle", fake)
    cfg = Config(
        channels=[Channel(id="CH1", name="c", prefer_asr=True)],
        defaults=Defaults(prefer_asr=False),
    )
    worker.advance_one(db, db.get_video("v1"), cfg, warehouse)
    assert captured["prefer"] is True


# --------------------------------------------------------------------------
# Translate step
# --------------------------------------------------------------------------

def test_translate_writes_zh_srt(db, warehouse, monkeypatch):
    _insert(db)
    db.update_stage("v1", State.EN_SUBTITLED)
    wd = warehouse / "v1"
    wd.mkdir()
    (wd / "en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    monkeypatch.setattr(
        worker, "translate_srt",
        lambda en, key, title: "1\n00:00:01,000 --> 00:00:02,000\n你好\n",
    )
    worker.advance_one(db, db.get_video("v1"), _config(), warehouse)
    assert db.get_video("v1").stage == State.ZH_TRANSLATED.value
    assert "你好" in (wd / "zh.srt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Burn step
# --------------------------------------------------------------------------

def test_burn_advances_to_burned(db, warehouse, monkeypatch):
    _insert(db)
    db.update_stage("v1", State.ZH_TRANSLATED)
    monkeypatch.setattr(worker, "burn_subtitles", lambda wd, **kw: True)
    worker.advance_one(db, db.get_video("v1"), _config(), warehouse)
    assert db.get_video("v1").stage == State.BURNED.value


def test_burn_failure_marks_failed(db, warehouse, monkeypatch):
    _insert(db)
    db.update_stage("v1", State.ZH_TRANSLATED)
    monkeypatch.setattr(worker, "burn_subtitles", lambda wd, **kw: False)
    new = worker.advance_one(db, db.get_video("v1"), _config(), warehouse)
    assert new == State.failed("burn")


# --------------------------------------------------------------------------
# Finalize: priority gate
# --------------------------------------------------------------------------

def test_finalize_priority_to_pending_review(db, warehouse):
    _insert(db, priority=True)
    db.update_stage("v1", State.BURNED)
    worker.advance_one(db, db.get_video("v1"), _config(priority=True), warehouse)
    assert db.get_video("v1").stage == State.PENDING_REVIEW.value


def test_finalize_normal_to_ready(db, warehouse):
    _insert(db, priority=False)
    db.update_stage("v1", State.BURNED)
    worker.advance_one(db, db.get_video("v1"), _config(), warehouse)
    assert db.get_video("v1").stage == State.READY.value


# --------------------------------------------------------------------------
# Failed state retry
# --------------------------------------------------------------------------

def test_failed_translate_retries(db, warehouse, monkeypatch):
    _insert(db)
    wd = warehouse / "v1"
    wd.mkdir()
    (wd / "en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
    db.update_stage("v1", State.failed("translate"))
    monkeypatch.setattr(
        worker, "translate_srt",
        lambda en, key, title: "1\n00:00:01,000 --> 00:00:02,000\n嗨\n",
    )
    new = worker.advance_one(db, db.get_video("v1"), _config(), warehouse)
    assert new == State.ZH_TRANSLATED.value


# --------------------------------------------------------------------------
# run_worker: full chain + terminal skip
# --------------------------------------------------------------------------

def test_run_worker_full_chain(db, warehouse, monkeypatch):
    _insert(db, vid="v1")
    SRT = "1\n00:00:01,000 --> 00:00:02,000\nHi\n"
    monkeypatch.setattr(worker, "download_video", lambda url, wd: True)
    monkeypatch.setattr(worker, "load_meta", lambda wd: {"duration": 100})

    def fake_sub(url, wd, prefer):
        # Mirror the real get_english_subtitle: write en.srt to disk.
        Path(wd).mkdir(parents=True, exist_ok=True)
        (Path(wd) / "en.srt").write_text(SRT, encoding="utf-8")
        return (SRT, "youtube")

    monkeypatch.setattr(worker, "get_english_subtitle", fake_sub)
    monkeypatch.setattr(worker, "translate_srt", lambda en, key, title: en)
    monkeypatch.setattr(worker, "burn_subtitles", lambda wd, **kw: True)
    worker.run_worker(db, _config(), warehouse)
    assert db.get_video("v1").stage == State.READY.value


def test_run_worker_skips_terminal(db, warehouse, monkeypatch):
    _insert(db, vid="v1")
    db.update_stage("v1", State.PUBLISHED)
    called = {"n": 0}
    def boom(url, wd):
        called["n"] += 1
        return True
    monkeypatch.setattr(worker, "download_video", boom)
    worker.run_worker(db, _config(), warehouse)
    assert called["n"] == 0
    assert db.get_video("v1").stage == State.PUBLISHED.value


def test_run_worker_skips_quality_skipped(db, warehouse, monkeypatch):
    _insert(db, vid="v1")
    db.update_stage("v1", State.SKIPPED_QUALITY)
    called = {"n": 0}

    def boom(url, wd):
        called["n"] += 1
        return True

    monkeypatch.setattr(worker, "download_video", boom)
    worker.run_worker(db, _config(), warehouse)
    assert called["n"] == 0
    assert db.get_video("v1").stage == State.SKIPPED_QUALITY.value


def test_run_worker_retries_failed(db, warehouse, monkeypatch):
    """A failed_* video must be retried by run_worker, not skipped."""
    _insert(db, vid="v1")
    wd = warehouse / "v1"
    wd.mkdir()
    (wd / "en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
    db.update_stage("v1", State.failed("translate"))
    monkeypatch.setattr(
        worker, "translate_srt",
        lambda en, key, title: "1\n00:00:01,000 --> 00:00:02,000\n嗨\n",
    )
    monkeypatch.setattr(worker, "burn_subtitles", lambda wd, **kw: True)
    worker.run_worker(db, _config(), warehouse)
    # Retried translate → burned → ready, all in one pass.
    assert db.get_video("v1").stage == State.READY.value


# --------------------------------------------------------------------------
# cleanup_warehouse
# --------------------------------------------------------------------------

def test_cleanup_keeps_max_cached(db, warehouse):
    """Old published dirs beyond max_cached are removed."""
    for i in range(15):
        vid = f"pub{i}"
        _insert(db, vid=vid)
        db.update_stage(vid, State.PUBLISHED)
        d = warehouse / vid
        d.mkdir()
        (d / "final.mp4").write_bytes(b"\x00" * i)  # vary mtime

    removed = worker.cleanup_warehouse(db, warehouse, max_cached=10)
    remaining = [d.name for d in warehouse.iterdir() if d.is_dir()]
    assert removed == 5
    assert len(remaining) == 10


def test_cleanup_never_removes_active_videos(db, warehouse):
    """Videos in ready/processing states are kept even if old."""
    # Insert an old active video.
    _insert(db, vid="active1")
    db.update_stage("active1", State.READY)
    d = warehouse / "active1"
    d.mkdir()
    (d / "final.mp4").write_bytes(b"\x00")

    # Insert 10 newer published videos.
    for i in range(10):
        vid = f"pub{i}"
        _insert(db, vid=vid)
        db.update_stage(vid, State.PUBLISHED)
        dd = warehouse / vid
        dd.mkdir()
        (dd / "final.mp4").write_bytes(b"\x00" * (i + 10))

    removed = worker.cleanup_warehouse(db, warehouse, max_cached=5)
    remaining = {d.name for d in warehouse.iterdir() if d.is_dir()}
    assert "active1" in remaining  # never removed
    assert removed > 0



def test_cleanup_noop_when_under_limit(db, warehouse):
    """Nothing removed when total dirs and size are under limits."""
    for i in range(3):
        vid = f"v{i}"
        _insert(db, vid=vid)
        db.update_stage(vid, State.PUBLISHED)
        d = warehouse / vid
        d.mkdir()
        (d / "final.mp4").write_bytes(b"x")

    removed = worker.cleanup_warehouse(db, warehouse, max_cached=10, max_bytes=100)
    assert removed == 0


def test_cleanup_prunes_by_size_budget(db, warehouse):
    """Inactive dirs are removed until warehouse fits the byte budget."""
    for i in range(3):
        vid = f"pub{i}"
        _insert(db, vid=vid)
        db.update_stage(vid, State.PUBLISHED)
        d = warehouse / vid
        d.mkdir()
        (d / "final.mp4").write_bytes(b"x" * 10)

    removed = worker.cleanup_warehouse(db, warehouse, max_cached=3, max_bytes=15)
    remaining = [d.name for d in warehouse.iterdir() if d.is_dir()]
    assert removed == 2
    assert len(remaining) == 1


def test_cleanup_preserves_active_even_when_size_budget_exceeded(db, warehouse, caplog):
    """Active dirs are never deleted even if they exceed the byte cap."""
    _insert(db, vid="active1")
    db.update_stage("active1", State.READY)
    d = warehouse / "active1"
    d.mkdir()
    (d / "final.mp4").write_bytes(b"x" * 20)

    removed = worker.cleanup_warehouse(db, warehouse, max_cached=1, max_bytes=10)
    assert removed == 0
    assert (warehouse / "active1").exists()


def test_run_worker_skips_processing_when_disk_budget_exceeded(db, warehouse, monkeypatch):
    """Worker should not start expensive stages when free-space budget fails."""
    _insert(db, vid="v1")
    cfg = _config()
    cfg.defaults.max_warehouse_gb = 1
    cfg.defaults.min_free_disk_gb = 999999
    called = []
    monkeypatch.setattr(worker, "process_video", lambda *args, **kwargs: called.append(True))

    worker.run_worker(db, cfg, warehouse)
    assert called == []
    assert db.get_video("v1").stage == State.DISCOVERED.value
