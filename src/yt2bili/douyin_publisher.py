"""Douyin publisher: drain Bilibili-published videos to Douyin."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from .config import Config
from .db import Database
from .states import State
from .stages.douyin_publish import publish_to_douyin

logger = logging.getLogger(__name__)

PAUSE_KEY = "douyin_paused"
STREAK_KEY = "douyin_fail_streak"
STATUS_PREFIX = "douyin:"


def status_key(video_id: str) -> str:
    return f"{STATUS_PREFIX}{video_id}:status"


def error_key(video_id: str) -> str:
    return f"{STATUS_PREFIX}{video_id}:error"


def resume_douyin_publishing(db: Database) -> None:
    db.set_meta(PAUSE_KEY, "0")
    db.set_meta(STREAK_KEY, "0")
    logger.info("Douyin publishing resumed; pause flag and failure streak cleared.")


def run_douyin_publisher(
    db: Database,
    config: Config,
    warehouse_root: Path,
    publish_fn: Callable[..., bool] = publish_to_douyin,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Publish Bilibili-published videos to Douyin.

    Douyin status is tracked in the meta table so the main video stage machine
    stays unchanged.
    """
    warehouse_root = Path(warehouse_root)
    douyin_cfg = config.douyin

    if not douyin_cfg.enabled:
        logger.info("Douyin publisher disabled; skipping run.")
        return

    if db.get_meta(PAUSE_KEY, "0") == "1":
        logger.warning("Douyin publisher is paused; skipping run. Resume to continue.")
        return

    threshold = douyin_cfg.publish_fail_threshold
    limit = douyin_cfg.daily_publish_limit
    gap_seconds = douyin_cfg.min_publish_gap_min * 60
    streak = int(db.get_meta(STREAK_KEY, "0") or "0")

    candidates = db.list_by_stage(State.PUBLISHED)
    published_count = 0

    for video in candidates:
        if published_count >= limit:
            logger.info("Douyin daily publish limit (%d) reached.", limit)
            break

        if db.get_meta(status_key(video.video_id)) == "done":
            continue

        if published_count > 0 and gap_seconds:
            sleep_fn(gap_seconds)

        wd = warehouse_root / video.video_id
        ok = publish_fn(wd, video.title, douyin_cfg)

        if ok:
            db.set_meta(status_key(video.video_id), "done")
            db.set_meta(error_key(video.video_id), "")
            published_count += 1
            streak = 0
            db.set_meta(STREAK_KEY, "0")
            logger.info("Douyin published %s", video.video_id)
        else:
            db.set_meta(status_key(video.video_id), "failed")
            db.set_meta(error_key(video.video_id), "douyin upload failed")
            streak += 1
            db.set_meta(STREAK_KEY, str(streak))
            logger.warning(
                "Douyin publish failed for %s (consecutive failures: %d/%d)",
                video.video_id, streak, threshold,
            )
            if streak >= threshold:
                db.set_meta(PAUSE_KEY, "1")
                logger.error(
                    "Reached %d consecutive Douyin publish failures — pausing publisher.",
                    threshold,
                )
                break
