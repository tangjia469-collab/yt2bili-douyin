"""Tests for the Flask dashboard: JSON API + action endpoints."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yt2bili.db import Database
from yt2bili.states import State
from yt2bili import web


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.sqlite")
    d.init()
    return d


@pytest.fixture
def client(db):
    app = web.create_app(db)
    app.config.update(TESTING=True)
    return app.test_client()


def _insert(db, vid, stage=None, priority=False):
    db.insert_video(vid, "CH1", "https://yt/" + vid, "Title " + vid, priority)
    if stage is not None:
        db.update_stage(vid, stage)


# --------------------------------------------------------------------------
# Read endpoints
# --------------------------------------------------------------------------

def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"yt2bili" in resp.data


def test_api_stats(client, db):
    _insert(db, "a", State.READY)
    _insert(db, "b", State.PUBLISHED)
    _insert(db, "c")  # discovered → processing
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ready"] == 1
    assert data["published"] == 1
    assert data["processing"] == 1


def test_api_videos_lists_all(client, db):
    _insert(db, "a")
    _insert(db, "b", State.READY)
    resp = client.get("/api/videos")
    assert resp.status_code == 200
    vids = {v["video_id"] for v in resp.get_json()}
    assert vids == {"a", "b"}


def test_api_videos_filter_by_stage(client, db):
    _insert(db, "a")
    _insert(db, "b", State.READY)
    resp = client.get("/api/videos?stage=ready")
    data = resp.get_json()
    assert [v["video_id"] for v in data] == ["b"]


# --------------------------------------------------------------------------
# Action endpoints
# --------------------------------------------------------------------------

def test_approve_endpoint(client, db):
    _insert(db, "a", State.PENDING_REVIEW)
    resp = client.post("/api/videos/a/approve")
    assert resp.status_code == 200
    assert db.get_video("a").stage == State.READY.value


def test_approve_bad_state_returns_400(client, db):
    _insert(db, "a", State.DOWNLOADED)
    resp = client.post("/api/videos/a/approve")
    assert resp.status_code == 400


def test_skip_endpoint(client, db):
    _insert(db, "a", State.DOWNLOADED)
    resp = client.post("/api/videos/a/skip")
    assert resp.status_code == 200
    assert db.get_video("a").stage == State.SKIPPED.value


def test_retry_endpoint(client, db):
    _insert(db, "a", State.failed("translate"))
    resp = client.post("/api/videos/a/retry")
    assert resp.status_code == 200
    assert db.get_video("a").stage == State.EN_SUBTITLED.value


def test_priority_toggle_endpoint(client, db):
    _insert(db, "a", priority=False)
    resp = client.post("/api/videos/a/priority")
    assert resp.status_code == 200
    assert db.get_video("a").is_priority is True


def test_missing_video_returns_404(client):
    resp = client.post("/api/videos/ghost/approve")
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Publish control
# --------------------------------------------------------------------------

def test_publish_status_reports_paused(client, db):
    db.set_meta("publish_paused", "1")
    resp = client.get("/api/publish-status")
    assert resp.status_code == 200
    assert resp.get_json()["paused"] is True


def test_resume_publish_clears_pause(client, db):
    db.set_meta("publish_paused", "1")
    resp = client.post("/api/resume-publish")
    assert resp.status_code == 200
    assert db.get_meta("publish_paused") == "0"
