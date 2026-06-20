"""Dashboard actions: the operations the Web UI (or a CLI) performs.

Pure business logic over the Database — no Flask, no HTTP. Each function
validates the video's current stage and raises ``ValueError`` on an illegal
transition so the web layer can return a clean 400.
"""

from __future__ import annotations

import logging
from typing import Dict

from .db import Database
from .states import State
from .worker import FAILED_RETRY_ENTRY

logger = logging.getLogger(__name__)


def approve(db: Database, video_id: str) -> None:
    """Approve a priority video awaiting review: pending_review → ready."""
    video = db.get_video(video_id)
    if video.stage != State.PENDING_REVIEW.value:
        raise ValueError(
            f"{video_id} is '{video.stage}', not pending_review; cannot approve"
        )
    db.update_stage(video_id, State.READY)
    logger.info("Approved %s → ready", video_id)


def skip(db: Database, video_id: str) -> None:
    """Skip a video so the pipeline ignores it. Disallowed once published."""
    video = db.get_video(video_id)
    if video.stage == State.PUBLISHED.value:
        raise ValueError(f"{video_id} is already published; cannot skip")
    db.update_stage(video_id, State.SKIPPED)
    logger.info("Skipped %s", video_id)


def retry(db: Database, video_id: str) -> None:
    """Reset a failed video to the predecessor stage so it re-runs that step.

    ``failed_publish`` is owned by the publisher (not the worker), so it
    resets to ``ready`` to re-enter the publish queue. Other ``failed_*``
    stages reset to their worker predecessor.
    """
    video = db.get_video(video_id)
    if not video.stage.startswith("failed_"):
        raise ValueError(f"{video_id} is '{video.stage}', not failed; nothing to retry")

    if video.stage == State.failed("publish"):
        target = State.READY.value
    else:
        target = FAILED_RETRY_ENTRY.get(video.stage)
        if target is None:
            raise ValueError(f"No retry path for stage '{video.stage}'")

    db.update_stage(video_id, target, error=None)
    logger.info("Retry %s: %s → %s", video_id, video.stage, target)


def toggle_priority(db: Database, video_id: str) -> None:
    """Flip the is_priority flag on a video."""
    video = db.get_video(video_id)
    db.set_priority(video_id, not video.is_priority)
    logger.info("Toggled priority for %s → %s", video_id, not video.is_priority)


# Buckets for the dashboard summary cards.
_PROCESSING = {
    State.DISCOVERED.value,
    State.DOWNLOADED.value,
    State.EN_SUBTITLED.value,
    State.ZH_TRANSLATED.value,
    State.BURNED.value,
}
_SKIPPED = {State.SKIPPED.value, State.SKIPPED_LONG.value}


def stats(db: Database) -> Dict[str, int]:
    """Count videos grouped into dashboard buckets."""
    buckets = {
        "processing": 0,
        "pending_review": 0,
        "ready": 0,
        "published": 0,
        "failed": 0,
        "skipped": 0,
    }
    for v in db.list_all():
        if v.stage in _PROCESSING:
            buckets["processing"] += 1
        elif v.stage == State.PENDING_REVIEW.value:
            buckets["pending_review"] += 1
        elif v.stage == State.READY.value:
            buckets["ready"] += 1
        elif v.stage == State.PUBLISHED.value:
            buckets["published"] += 1
        elif v.stage.startswith("failed_"):
            buckets["failed"] += 1
        elif v.stage in _SKIPPED:
            buckets["skipped"] += 1
    return buckets
