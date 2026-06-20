"""Pipeline worker: advance each video through the stage machine.

The worker scans non-terminal videos and pushes each one step further down
the pipeline:

    discovered → downloaded → en_subtitled → zh_translated → burned
              → (pending_review | ready)

A ``failed_<stage>`` video is retried from the stage that failed; completed
artifacts on disk (source.mp4, en.srt, …) are not recomputed.  Any stage that
raises or returns falsey marks the video ``failed_<stage>`` with the error
text, and the worker moves on to the next video.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .config import Config
from .db import Database, Video
from .states import State
from .stages.download import download_video, load_meta
from .stages.subtitle import get_english_subtitle
from .stages.translate import translate_srt
from .stages.burn import burn_subtitles

logger = logging.getLogger(__name__)

# Stages the worker actively drives. Terminal/queue states are excluded so the
# worker never touches a published, skipped, ready, or awaiting-review video.
_TERMINAL = {
    State.READY.value,
    State.PENDING_REVIEW.value,
    State.PUBLISHED.value,
    State.SKIPPED.value,
    State.SKIPPED_LONG.value,
}

# Maps the "current stage" to the stage name used for retry dispatch. A
# ``failed_<stage>`` video re-enters at the *predecessor* state so the same
# step runs again.
_FAILED_RETRY_ENTRY = {
    State.failed("download"): State.DISCOVERED.value,
    State.failed("subtitle"): State.DOWNLOADED.value,
    State.failed("translate"): State.EN_SUBTITLED.value,
    State.failed("burn"): State.ZH_TRANSLATED.value,
}


def _channel_prefer_asr(config: Config, channel_id: str) -> bool:
    """Resolve the effective prefer_asr flag for a video's channel."""
    for ch in config.channels:
        if ch.id == channel_id:
            if ch.prefer_asr is not None:
                return ch.prefer_asr
            break
    return config.defaults.prefer_asr


def _channel_is_priority(config: Config, channel_id: str, video: Video) -> bool:
    """A video is priority if its row is flagged or its channel is priority."""
    if video.is_priority:
        return True
    for ch in config.channels:
        if ch.id == channel_id:
            return ch.priority
    return False


def advance_one(
    db: Database,
    video: Video,
    config: Config,
    warehouse_root: Path,
) -> str:
    """Advance a single video one step. Returns the resulting stage string.

    On failure the video is marked ``failed_<stage>`` and that string is
    returned. The function is idempotent per call: it runs exactly one stage.
    """
    warehouse_root = Path(warehouse_root)
    wd = warehouse_root / video.video_id

    # Normalize a failed stage back to its retry entry point.
    stage = _FAILED_RETRY_ENTRY.get(video.stage, video.stage)

    # ---- download ----
    if stage == State.DISCOVERED.value:
        try:
            ok = download_video(video.source_url, wd)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("download crashed for %s", video.video_id)
            db.update_stage(video.video_id, State.failed("download"), str(exc))
            return State.failed("download")
        if not ok:
            db.update_stage(video.video_id, State.failed("download"), "yt-dlp failed")
            return State.failed("download")

        # Duration filter: skip overly long videos.
        meta = load_meta(wd)
        duration_s = meta.get("duration") or 0
        max_s = config.defaults.max_duration_min * 60
        if max_s and duration_s and duration_s > max_s:
            db.update_stage(video.video_id, State.SKIPPED_LONG)
            logger.info("Video %s skipped: %ds > %ds", video.video_id, duration_s, max_s)
            return State.SKIPPED_LONG.value

        db.update_stage(video.video_id, State.DOWNLOADED)
        return State.DOWNLOADED.value

    # ---- subtitle ----
    if stage == State.DOWNLOADED.value:
        prefer = _channel_prefer_asr(config, video.channel_id)
        try:
            srt, source = get_english_subtitle(video.source_url, wd, prefer)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("subtitle crashed for %s", video.video_id)
            db.update_stage(video.video_id, State.failed("subtitle"), str(exc))
            return State.failed("subtitle")
        if not srt.strip():
            db.update_stage(video.video_id, State.failed("subtitle"), "empty subtitle")
            return State.failed("subtitle")
        db.update_subtitle_source(video.video_id, source)
        db.update_stage(video.video_id, State.EN_SUBTITLED)
        return State.EN_SUBTITLED.value

    # ---- translate ----
    if stage == State.EN_SUBTITLED.value:
        en_path = wd / "en.srt"
        if not en_path.exists():
            db.update_stage(video.video_id, State.failed("translate"), "en.srt missing")
            return State.failed("translate")
        try:
            en_srt = en_path.read_text(encoding="utf-8")
            zh_srt = translate_srt(en_srt, config.api.minimax_key, video.title)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("translate crashed for %s", video.video_id)
            db.update_stage(video.video_id, State.failed("translate"), str(exc))
            return State.failed("translate")
        if not zh_srt.strip():
            db.update_stage(video.video_id, State.failed("translate"), "empty translation")
            return State.failed("translate")
        (wd / "zh.srt").write_text(zh_srt, encoding="utf-8")
        db.update_stage(video.video_id, State.ZH_TRANSLATED)
        return State.ZH_TRANSLATED.value

    # ---- burn ----
    if stage == State.ZH_TRANSLATED.value:
        style = config.defaults.subtitle_style
        try:
            ok = burn_subtitles(
                wd,
                font=style.font,
                font_size=style.font_size,
                outline=style.outline,
                margin_v=style.margin_v,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("burn crashed for %s", video.video_id)
            db.update_stage(video.video_id, State.failed("burn"), str(exc))
            return State.failed("burn")
        if not ok:
            db.update_stage(video.video_id, State.failed("burn"), "ffmpeg failed")
            return State.failed("burn")
        db.update_stage(video.video_id, State.BURNED)
        return State.BURNED.value

    # ---- finalize: priority gate ----
    if stage == State.BURNED.value:
        if _channel_is_priority(config, video.channel_id, video):
            db.update_stage(video.video_id, State.PENDING_REVIEW)
            return State.PENDING_REVIEW.value
        db.update_stage(video.video_id, State.READY)
        return State.READY.value

    # Nothing to do for terminal/queue states.
    return video.stage


def process_video(
    db: Database,
    video_id: str,
    config: Config,
    warehouse_root: Path,
    max_steps: int = 10,
) -> str:
    """Drive a single video forward until it stalls or reaches a stop state.

    Runs ``advance_one`` repeatedly until the stage stops changing, the video
    enters a terminal/queue state, or it fails. Returns the final stage.
    """
    last = db.get_video(video_id).stage
    for _ in range(max_steps):
        video = db.get_video(video_id)
        # Terminal/queue states are done. A failed_* state is NOT terminal:
        # we attempt it once so each run_worker pass retries the failed step.
        if video.stage in _TERMINAL:
            break
        new_stage = advance_one(db, video, config, warehouse_root)
        if new_stage == last:
            break
        last = new_stage
        if new_stage in _TERMINAL or new_stage.startswith("failed_"):
            break
    return db.get_video(video_id).stage


def run_worker(db: Database, config: Config, warehouse_root: Path) -> None:
    """Scan all videos and advance any that are not in a terminal/queue state.

    Failed videos are retried (one step) on each invocation.
    """
    for video in db.list_all():
        if video.stage in _TERMINAL:
            continue
        try:
            process_video(db, video.video_id, config, warehouse_root)
        except Exception:  # pragma: no cover - defensive
            logger.exception("worker crashed processing %s", video.video_id)
