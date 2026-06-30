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
import shutil
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

# States whose warehouse dirs must NOT be cleaned up — the worker or publisher
# still needs them.
_ACTIVE_STATES = {
    State.DISCOVERED.value,
    State.DOWNLOADED.value,
    State.EN_SUBTITLED.value,
    State.ZH_TRANSLATED.value,
    State.BURNED.value,
    State.PENDING_REVIEW.value,
    State.READY.value,
}


def _dir_size(path: Path) -> int:
    """Return recursive directory size in bytes, ignoring files that vanish."""
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def cleanup_warehouse(
    db: Database,
    warehouse_root: Path,
    max_cached: int,
    max_bytes: int | None = None,
) -> int:
    """Remove old cache dirs by count and optional byte budget.

    Active videos are never removed. Inactive dirs are kept newest-first up to
    ``max_cached`` total dirs, then further pruned until total warehouse size is
    under ``max_bytes``. Returns the number of directories deleted.
    """
    warehouse_root = Path(warehouse_root)
    if not warehouse_root.is_dir():
        return 0

    active_ids = {v.video_id for v in db.list_all() if v.stage in _ACTIVE_STATES}

    active_dirs = []
    inactive_dirs = []
    for d in warehouse_root.iterdir():
        if not d.is_dir():
            continue
        if d.name in active_ids:
            active_dirs.append(d)
        else:
            inactive_dirs.append(d)

    remaining_budget = max(0, max_cached - len(active_dirs))
    inactive_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    keep_inactive = inactive_dirs[:remaining_budget]
    to_remove = inactive_dirs[remaining_budget:]

    if max_bytes is not None:
        active_size = sum(_dir_size(d) for d in active_dirs)
        keep_with_size = [(d, _dir_size(d)) for d in keep_inactive]
        total_size = active_size + sum(size for _, size in keep_with_size)
        while keep_with_size and total_size > max_bytes:
            d, size = keep_with_size.pop()  # oldest kept inactive
            to_remove.append(d)
            total_size -= size
        if active_size > max_bytes:
            logger.warning(
                "Active warehouse dirs use %.2fGB, above configured cap %.2fGB; "
                "active dirs are preserved and cannot be auto-deleted.",
                active_size / (1024 ** 3), max_bytes / (1024 ** 3),
            )

    removed = 0
    for d in to_remove:
        try:
            shutil.rmtree(d)
            removed += 1
            logger.info("Cleaned up warehouse dir %s", d.name)
        except OSError as exc:
            logger.warning("Failed to remove %s: %s", d, exc)
    if removed:
        logger.info(
            "Warehouse cleanup: removed %d dirs, keeping %d active + %d cached",
            removed, len(active_dirs), max(0, min(remaining_budget, len(inactive_dirs)) - removed),
        )
    return removed


def _gb_to_bytes(value: float) -> int:
    return int(value * 1024 ** 3)


def ensure_disk_budget(warehouse_root: Path, max_warehouse_gb: float, min_free_disk_gb: float) -> bool:
    """Return True if disk budgets are healthy enough to continue processing."""
    warehouse_root = Path(warehouse_root)
    warehouse_root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(warehouse_root)
    free_gb = usage.free / (1024 ** 3)
    warehouse_gb = _dir_size(warehouse_root) / (1024 ** 3)
    ok = free_gb >= min_free_disk_gb and warehouse_gb <= max_warehouse_gb
    if not ok:
        logger.error(
            "Disk budget exceeded: warehouse=%.2fGB/%.2fGB, free=%.2fGB/%.2fGB. "
            "Worker will skip processing until cleanup frees space.",
            warehouse_gb, max_warehouse_gb, free_gb, min_free_disk_gb,
        )
    return ok


# Stages the worker actively drives. Terminal/queue states are excluded so the
# worker never touches a published, skipped, ready, or awaiting-review video.
_TERMINAL = {
    State.READY.value,
    State.PENDING_REVIEW.value,
    State.PUBLISHED.value,
    State.SKIPPED.value,
    State.SKIPPED_LONG.value,
    State.SKIPPED_QUALITY.value,
}

# Maps the "current stage" to the stage name used for retry dispatch. A
# ``failed_<stage>`` video re-enters at the *predecessor* state so the same
# step runs again. Public so the dashboard's "retry" action can reuse it.
FAILED_RETRY_ENTRY = {
    State.failed("download"): State.DISCOVERED.value,
    State.failed("subtitle"): State.DOWNLOADED.value,
    State.failed("translate"): State.EN_SUBTITLED.value,
    State.failed("burn"): State.ZH_TRANSLATED.value,
}

# Backwards-compatible private alias (kept for the internal dispatch below).
_FAILED_RETRY_ENTRY = FAILED_RETRY_ENTRY


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

    Failed videos are retried (one step) on each invocation. The worker always
    enforces warehouse count, byte cap, and free-disk budget before starting
    expensive processing so cache growth cannot starve the rest of the machine.
    """
    max_cached = config.defaults.max_cached_videos
    max_bytes = _gb_to_bytes(config.defaults.max_warehouse_gb)
    cleanup_warehouse(db, warehouse_root, max_cached, max_bytes=max_bytes)

    if not ensure_disk_budget(
        warehouse_root,
        config.defaults.max_warehouse_gb,
        config.defaults.min_free_disk_gb,
    ):
        return

    for video in db.list_all():
        if video.stage in _TERMINAL:
            continue
        try:
            process_video(db, video.video_id, config, warehouse_root)
        except Exception:  # pragma: no cover - defensive
            logger.exception("worker crashed processing %s", video.video_id)

        cleanup_warehouse(db, warehouse_root, max_cached, max_bytes=max_bytes)
        if not ensure_disk_budget(
            warehouse_root,
            config.defaults.max_warehouse_gb,
            config.defaults.min_free_disk_gb,
        ):
            break

    cleanup_warehouse(db, warehouse_root, max_cached, max_bytes=max_bytes)
