"""Publish stage: upload final.mp4 to Bilibili via biliup CLI."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..config import BiliupConfig

logger = logging.getLogger(__name__)


def publish_video(
    warehouse_dir: Path,
    title: str,
    config: BiliupConfig,
) -> bool:
    """Upload final.mp4 to Bilibili using the biliup CLI.

    Reads ``final.mp4`` from *warehouse_dir*, then invokes::

        biliup upload final.mp4 --title <title> --tid <tid> --tag <tags>

    Login state is managed externally by the user via ``biliup login``.
    If biliup reports an auth/cookie error the function logs a clear
    human-readable message and returns False (no automatic re-login).

    Args:
        warehouse_dir: Directory that contains ``final.mp4``.
        title: Video title sent to Bilibili.
        config: ``BiliupConfig`` supplying binary path, category id, and tags.

    Returns:
        True on success (biliup exit-code 0), False otherwise.
    """
    warehouse_dir = Path(warehouse_dir)
    final_mp4 = warehouse_dir / "final.mp4"

    if not final_mp4.exists():
        logger.warning("publish_video: final.mp4 not found in %s", warehouse_dir)
        return False

    tags_str = ",".join(config.tags) if config.tags else "搬运"

    cmd = [
        config.binary,
        "upload",
        str(final_mp4),
        "--title", title,
        "--tid", str(config.tid),
        "--tag", tags_str,
    ]

    logger.info("Invoking biliup: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        stderr_lower = (result.stderr or "").lower()
        # Surface a helpful message when the session has expired
        if any(kw in stderr_lower for kw in ("login", "cookie", "auth", "expired", "unauthorized")):
            logger.error(
                "biliup auth failure — run 'biliup login' to re-authenticate. "
                "stderr: %s",
                result.stderr.strip(),
            )
        else:
            logger.warning(
                "biliup upload failed (rc=%d): %s",
                result.returncode,
                result.stderr.strip(),
            )
        return False

    logger.info("biliup upload succeeded for video %s", warehouse_dir.name)
    return True
