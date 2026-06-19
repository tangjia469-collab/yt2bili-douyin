import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List
from .states import State


@dataclass
class Video:
    video_id: str
    channel_id: str
    source_url: str
    title: str
    stage: str
    is_priority: bool
    subtitle_source: Optional[str]
    published_at: Optional[str]
    error: Optional[str]
    updated_at: str


class Database:
    def __init__(self, path):
        self.path = str(path)

    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def init(self):
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    is_priority INTEGER NOT NULL DEFAULT 0,
                    subtitle_source TEXT,
                    published_at TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_stage ON videos(stage)")

    def insert_video(self, video_id: str, channel_id: str, source_url: str, title: str, is_priority: bool):
        with self._conn() as c:
            c.execute(
                "INSERT INTO videos (video_id, channel_id, source_url, title, stage, is_priority) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (video_id, channel_id, source_url, title, State.DISCOVERED.value, int(is_priority))
            )

    def get_video(self, video_id: str) -> "Video":
        with self._conn() as c:
            row = c.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()
        if row is None:
            raise KeyError(f"Video not found: {video_id}")
        return self._row_to_video(row)

    def update_stage(self, video_id: str, state, error: Optional[str] = None):
        stage_val = state.value if isinstance(state, State) else state
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE videos SET stage=?, error=?, updated_at=datetime('now') WHERE video_id=?",
                (stage_val, error, video_id)
            )
            if cur.rowcount == 0:
                raise KeyError(f"video_id not found: {video_id}")

    def update_subtitle_source(self, video_id: str, source: str):
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE videos SET subtitle_source=? WHERE video_id=?",
                (source, video_id)
            )
            if cur.rowcount == 0:
                raise KeyError(f"video_id not found: {video_id}")

    def list_by_stage(self, state: State) -> List["Video"]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM videos WHERE stage=? ORDER BY updated_at",
                (state.value,)
            ).fetchall()
        return [self._row_to_video(r) for r in rows]

    def list_all(self) -> List["Video"]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM videos").fetchall()
        return [self._row_to_video(r) for r in rows]

    def _row_to_video(self, row) -> "Video":
        return Video(
            video_id=row["video_id"],
            channel_id=row["channel_id"],
            source_url=row["source_url"],
            title=row["title"],
            stage=row["stage"],
            is_priority=bool(row["is_priority"]),
            subtitle_source=row["subtitle_source"],
            published_at=row["published_at"],
            error=row["error"],
            updated_at=row["updated_at"],
        )
