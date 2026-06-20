"""Publisher: drain the ready queue to Bilibili with safety gates.

Responsibilities:
- Publish ``ready`` videos FIFO (oldest first), honoring a daily limit.
- Insert a configurable gap between successive uploads (rate-limit friendly).
- Track a consecutive-failure streak; once it hits the threshold, set a
  persistent pause flag so no further uploads are attempted until the user
  resumes via the dashboard/CLI.
- Refuse to publish at all while paused.

Priority videos never reach ``ready`` until approved (worker parks them in
``pending_review``), so the publisher does not special-case them.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from .config import Config
from .db import Database
from .states import State
from .stages.publish import publish_video

logger = logging.getLogger(__name__)

# Persistent meta keys.
PAUSE_KEY = "publish_paused"
STREAK_KEY = "publish_fail_streak"


def resume_publishing(db: Database) -> None:
    """Clear the pause flag and reset the failure streak (user action)."""
    db.set_meta(PAUSE_KEY, "0")
    db.set_meta(STREAK_KEY, "0")
    logger.info("Publishing resumed; pause flag and failure streak cleared.")


def run_publisher(
    db: Database,
    config: Config,
    warehouse_root: Path,
    publish_fn: Callable[..., bool] = publish_video,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Publish up to the daily limit of ready videos, FIFO.

    Args:
        db: State database.
        config: Loaded configuration (uses defaults + biliup section).
        warehouse_root: Root directory holding per-video warehouses.
        publish_fn: Injectable upload function (warehouse_dir, title, biliup_cfg)
            → bool. Defaults to the real biliup-backed ``publish_video``.
        sleep_fn: Injectable sleep, called with seconds between uploads.
    """
    warehouse_root = Path(warehouse_root)

    if db.get_meta(PAUSE_KEY, "0") == "1":
        logger.warning("Publisher is paused; skipping run. Resume to continue.")
        return

    threshold = config.defaults.publish_fail_threshold
    limit = config.defaults.daily_publish_limit
    gap_seconds = config.defaults.min_publish_gap_min * 60

    streak = int(db.get_meta(STREAK_KEY, "0") or "0")

    ready = db.list_by_stage(State.READY)  # already ordered by updated_at
    published_count = 0

    for video in ready:
        if published_count >= limit:
            logger.info("Daily publish limit (%d) reached.", limit)
            break

        # Gap before every upload except the first of this run.
        if published_count > 0 and gap_seconds:
            sleep_fn(gap_seconds)

        wd = warehouse_root / video.video_id
        ok = publish_fn(wd, video.title, config.biliup)

        if ok:
            db.mark_published(video.video_id)
            published_count += 1
            streak = 0
            db.set_meta(STREAK_KEY, "0")
            logger.info("Published %s", video.video_id)
        else:
            db.update_stage(video.video_id, State.failed("publish"), "biliup upload failed")
            streak += 1
            db.set_meta(STREAK_KEY, str(streak))
            logger.warning(
                "Publish failed for %s (consecutive failures: %d/%d)",
                video.video_id, streak, threshold,
            )
            if streak >= threshold:
                db.set_meta(PAUSE_KEY, "1")
                logger.error(
                    "Reached %d consecutive publish failures — pausing publisher. "
                    "Check biliup login, then resume from the dashboard.",
                    threshold,
                )
                break
