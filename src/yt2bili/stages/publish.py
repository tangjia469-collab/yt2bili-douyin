"""Publish stage: upload final.mp4 to Bilibili via biliup CLI."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from ..config import BiliupConfig

logger = logging.getLogger(__name__)


def _extract_bvid(output: str) -> str | None:
    match = re.search(r"BV[0-9A-Za-z]{10}", output or "")
    return match.group(0) if match else None


def _verify_bilibili_submission(config: BiliupConfig, bvid: str) -> bool:
    result = subprocess.run(
        [config.binary, "show", bvid],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("biliup show failed for %s: %s", bvid, result.stderr.strip())
        return False

    text = (result.stdout if isinstance(result.stdout, str) else "") + "\n" + (result.stderr if isinstance(result.stderr, str) else "")
    start = text.find("{")
    if start < 0:
        logger.warning("biliup show did not return JSON for %s", bvid)
        return False

    try:
        data = json.loads(text[start:])
    except json.JSONDecodeError as exc:
        logger.warning("failed to parse biliup show JSON for %s: %s", bvid, exc)
        return False

    archive = data.get("archive", {})
    state = archive.get("state")
    state_desc = archive.get("state_desc")
    duration = archive.get("duration") or 0
    had_passed = bool(archive.get("had_passed"))

    if state == -16 or state_desc == "转码失败" or duration <= 0:
        logger.warning(
            "Bilibili submission %s is not publishable: state=%s desc=%s duration=%s",
            bvid, state, state_desc, duration,
        )
        return False

    return had_passed or state in (0, 1, 2)


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
        "--submit", config.submit,
    ]

    logger.info("Invoking biliup: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        stderr_lower = (result.stderr or "").lower()
        # Network errors (DNS, connection, timeout) are transient. Detect
        # them first so they aren't misclassified as auth failures — the
        # OAuth URL contains "login" which would otherwise match.
        net_keywords = (
            "dns error", "failed to lookup", "nodename nor servname",
            "connection refused", "connection reset", "connection aborted",
            "network unreachable", "network is unreachable",
            "timed out", "timeout",
            "error sending request", "connect error",
        )
        if any(kw in stderr_lower for kw in net_keywords):
            logger.warning(
                "biliup network error (rc=%d): %s",
                result.returncode,
                result.stderr.strip(),
            )
        elif any(kw in stderr_lower for kw in ("cookie", "expired", "unauthorized", "not logged in", "login required", "not authenticated")):
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

    logger.info("biliup upload returned success for video %s", warehouse_dir.name)
    combined_output = (result.stdout if isinstance(result.stdout, str) else "") + "\n" + (result.stderr if isinstance(result.stderr, str) else "")
    bvid = _extract_bvid(combined_output)
    if not bvid:
        logger.warning("biliup exited 0 but no BV id was found in output")
        return False

    if not _verify_bilibili_submission(config, bvid):
        return False

    logger.info("biliup submission verified for %s (%s)", warehouse_dir.name, bvid)
    return True
