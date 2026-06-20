"""Flask dashboard: view pipeline state and control videos from a browser.

A thin HTTP layer over :mod:`yt2bili.actions`. All business rules live in
``actions``; this module only maps routes to those calls and translates
exceptions into status codes:

- ``KeyError``   → 404 (unknown video_id)
- ``ValueError`` → 400 (illegal stage transition)

Run locally with ``yt2bili.web:run``; binds to 127.0.0.1 only (no auth, this
is a single-user local tool).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template, request

from . import actions
from .db import Database
from .publisher import PAUSE_KEY, resume_publishing

logger = logging.getLogger(__name__)


def create_app(db: Database) -> Flask:
    """Build a Flask app bound to a given Database (factory for testability)."""
    app = Flask(__name__)

    # ---- pages ----
    @app.get("/")
    def index():
        return render_template("index.html")

    # ---- read API ----
    @app.get("/api/stats")
    def api_stats():
        return jsonify(actions.stats(db))

    @app.get("/api/videos")
    def api_videos():
        stage = request.args.get("stage")
        videos = db.list_by_stage(stage) if stage else db.list_all()
        return jsonify([asdict(v) for v in videos])

    @app.get("/api/publish-status")
    def api_publish_status():
        paused = db.get_meta(PAUSE_KEY, "0") == "1"
        return jsonify({"paused": paused})

    # ---- action API ----
    def _do(fn, video_id):
        """Run an action, mapping domain exceptions to HTTP statuses."""
        try:
            fn(db, video_id)
        except KeyError:
            return jsonify({"error": "video not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True})

    @app.post("/api/videos/<video_id>/approve")
    def api_approve(video_id):
        return _do(actions.approve, video_id)

    @app.post("/api/videos/<video_id>/skip")
    def api_skip(video_id):
        return _do(actions.skip, video_id)

    @app.post("/api/videos/<video_id>/retry")
    def api_retry(video_id):
        return _do(actions.retry, video_id)

    @app.post("/api/videos/<video_id>/priority")
    def api_priority(video_id):
        return _do(actions.toggle_priority, video_id)

    @app.post("/api/resume-publish")
    def api_resume_publish():
        resume_publishing(db)
        return jsonify({"ok": True})

    return app


def run(db_path: Optional[Path] = None, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Entry point: start the dashboard server.

    Args:
        db_path: Path to the SQLite state DB. Defaults to ~/yt2bili/db.sqlite.
        host: Bind address. Localhost only by default (no auth on this app).
        port: TCP port.
    """
    if db_path is None:
        db_path = Path.home() / "yt2bili" / "db.sqlite"
    db = Database(db_path)
    db.init()
    app = create_app(db)
    logger.info("Dashboard on http://%s:%d (db=%s)", host, port, db_path)
    app.run(host=host, port=port)
