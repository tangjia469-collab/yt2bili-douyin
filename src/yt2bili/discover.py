import json
import logging
import subprocess
from statistics import mean
from typing import Dict, List, Optional, Set

from .config import Channel, Config
from .db import Database
from .states import State

logger = logging.getLogger(__name__)


class Discoverer:
    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db

    def run(self) -> None:
        """Loop over all channels in config and discover new videos."""
        for channel in self.config.channels:
            try:
                self._discover_channel(channel)
            except Exception as exc:
                print(f"[discover] ERROR channel={channel.id}: {exc}")

    def _discover_channel(self, channel: Channel) -> None:
        """Fetch new entries for one channel and persist them to the DB."""
        raw_json = self._fetch_channel(channel.id)
        entries = self._parse_entries(raw_json)
        entries = self._apply_quality_gate(entries)

        known_ids: Set[str] = {v.video_id for v in self.db.list_all()}
        new_entries = self._filter_new(entries, known_ids)

        for entry in new_entries:
            self.db.insert_video(
                video_id=entry["video_id"],
                channel_id=channel.id,
                source_url=entry["url"],
                title=entry["title"],
                is_priority=channel.priority,
            )
            if entry["skip_reason"] == "long":
                self.db.update_stage(entry["video_id"], State.SKIPPED_LONG)
            elif entry["skip_reason"] == "quality":
                self.db.update_stage(
                    entry["video_id"],
                    State.SKIPPED_QUALITY,
                    error=entry.get("quality_reason"),
                )

    def _fetch_channel(self, channel_id: str) -> str:
        """Call yt-dlp with --flat-playlist -J and return stdout."""
        url = self._channel_videos_url(channel_id)
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "-J", url],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def _parse_entries(self, raw_json: str) -> List[Dict]:
        """Parse yt-dlp flat-playlist JSON into a list of entry dicts.

        Each dict contains:
            video_id, title, url, duration, skip
        skip is True when duration > max_duration_min * 60 seconds.
        """
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse yt-dlp JSON output: %s", exc)
            raise

        if "entries" not in data:
            logger.warning("Parsed JSON has no 'entries' key; treating as empty playlist")

        max_seconds = self.config.defaults.max_duration_min * 60
        entries: List[Dict] = []
        for item in data.get("entries", []):
            video_id = item.get("id")
            if video_id is None:
                logger.warning("Skipping entry with missing 'id': %s", item)
                continue
            duration: int = item.get("duration") or 0
            entries.append({
                "video_id": video_id,
                "title": item.get("title", ""),
                "url": item.get("url", f"https://www.youtube.com/watch?v={video_id}"),
                "duration": duration,
                "skip": duration > max_seconds,
                "skip_reason": "long" if duration > max_seconds else None,
                "like_count": item.get("like_count"),
                "comment_count": item.get("comment_count"),
            })
        return entries

    def _filter_new(self, entries: List[Dict], known_ids: Set[str]) -> List[Dict]:
        """Return only entries whose video_id is not already in known_ids."""
        return [e for e in entries if e["video_id"] not in known_ids]

    def _channel_videos_url(self, channel_id: str) -> str:
        """Return a YouTube videos URL for either a UC id, @handle, or full URL."""
        channel_id = channel_id.strip()
        if channel_id.startswith(("http://", "https://")):
            return channel_id if channel_id.rstrip("/").endswith("/videos") else channel_id.rstrip("/") + "/videos"
        if channel_id.startswith("@"):
            return f"https://www.youtube.com/{channel_id}/videos"
        if channel_id.startswith("UC"):
            return f"https://www.youtube.com/channel/{channel_id}/videos"
        return f"https://www.youtube.com/@{channel_id}/videos"

    def _apply_quality_gate(self, entries: List[Dict]) -> List[Dict]:
        """Skip videos below the channel's recent like/comment baseline.

        A video passes when either likes or comments reach
        ``quality_gate_ratio`` of that metric's recent average.  If yt-dlp
        cannot provide enough metric samples, fail open to avoid losing videos.
        """
        defaults = self.config.defaults
        if not defaults.quality_gate_enabled:
            return entries

        recent = entries[: max(1, defaults.quality_gate_recent_count)]
        for entry in recent:
            if entry.get("like_count") is None or entry.get("comment_count") is None:
                entry.update(self._fetch_video_metrics(entry["video_id"]))

        like_values = [
            int(e["like_count"]) for e in recent
            if isinstance(e.get("like_count"), int) and e.get("like_count", 0) > 0
        ]
        comment_values = [
            int(e["comment_count"]) for e in recent
            if isinstance(e.get("comment_count"), int) and e.get("comment_count", 0) > 0
        ]
        if len(like_values) < defaults.quality_gate_min_samples and len(comment_values) < defaults.quality_gate_min_samples:
            logger.warning(
                "Quality gate enabled but insufficient metric samples: likes=%s comments=%s",
                len(like_values),
                len(comment_values),
            )
            return entries

        avg_like = mean(like_values) if len(like_values) >= defaults.quality_gate_min_samples else None
        avg_comment = mean(comment_values) if len(comment_values) >= defaults.quality_gate_min_samples else None
        ratio = defaults.quality_gate_ratio

        for entry in entries:
            if entry.get("skip_reason"):
                continue
            if entry.get("like_count") is None or entry.get("comment_count") is None:
                entry.update(self._fetch_video_metrics(entry["video_id"]))
            like_count = entry.get("like_count")
            comment_count = entry.get("comment_count")
            passes_like = avg_like is not None and isinstance(like_count, int) and like_count >= avg_like * ratio
            passes_comment = avg_comment is not None and isinstance(comment_count, int) and comment_count >= avg_comment * ratio
            if passes_like or passes_comment:
                continue
            avg_like_text = f"{avg_like:.1f}" if avg_like is not None else "n/a"
            avg_comment_text = f"{avg_comment:.1f}" if avg_comment is not None else "n/a"
            entry["skip"] = True
            entry["skip_reason"] = "quality"
            entry["quality_reason"] = (
                f"quality_gate: likes={like_count}, comments={comment_count}, "
                f"avg_likes={avg_like_text}, "
                f"avg_comments={avg_comment_text}, "
                f"ratio={ratio}"
            )
        return entries

    def _fetch_video_metrics(self, video_id: str) -> Dict[str, Optional[int]]:
        """Fetch like/comment counts for one video, returning None on failure."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            result = subprocess.run(
                ["yt-dlp", "--skip-download", "-J", url],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(result.stdout)
        except Exception as exc:
            logger.warning("Failed to fetch metrics for %s: %s", video_id, exc)
            return {"like_count": None, "comment_count": None}
        return {
            "like_count": data.get("like_count"),
            "comment_count": data.get("comment_count"),
        }
