import json
import subprocess
from typing import Dict, List, Optional, Set

from .config import Channel, Config
from .db import Database
from .states import State


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
        entries = self._parse_entries(raw_json, channel.id, channel.priority)

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
            if entry["skip"]:
                self.db.update_stage(entry["video_id"], State.SKIPPED_LONG)

    def _fetch_channel(self, channel_id: str) -> str:
        """Call yt-dlp with --flat-playlist -J and return stdout."""
        url = f"https://www.youtube.com/channel/{channel_id}/videos"
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "-J", url],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def _parse_entries(
        self, raw_json: str, channel_id: str, is_priority: bool
    ) -> List[Dict]:
        """Parse yt-dlp flat-playlist JSON into a list of entry dicts.

        Each dict contains:
            video_id, title, url, duration, skip
        skip is True when duration > max_duration_min * 60 seconds.
        """
        max_seconds = self.config.defaults.max_duration_min * 60
        data = json.loads(raw_json)
        entries: List[Dict] = []
        for item in data.get("entries", []):
            duration: int = item.get("duration") or 0
            entries.append({
                "video_id": item["id"],
                "title": item.get("title", ""),
                "url": item.get("url", f"https://www.youtube.com/watch?v={item['id']}"),
                "duration": duration,
                "skip": duration > max_seconds,
            })
        return entries

    def _filter_new(self, entries: List[Dict], known_ids: Set[str]) -> List[Dict]:
        """Return only entries whose video_id is not already in known_ids."""
        return [e for e in entries if e["video_id"] not in known_ids]
