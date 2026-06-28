import sqlite3
from contextlib import contextmanager
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

    @contextmanager
    def _tx(self):
        """Yield a connection that commits on success, rolls back on error,
        and ALWAYS closes.

        ``with sqlite3.connect(...) as c`` only commits/rolls back — it does
        not close the connection. Leaked connections accumulate over a long
        worker run (hundreds of videos × multiple calls each) and eventually
        exhaust file descriptors, surfacing as
        ``sqlite3.OperationalError: unable to open database file``.
        WAL + busy_timeout also let discover/worker/publish concur safely.
        """
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self):
        with self._tx() as c:
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
            c.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

    def insert_video(self, video_id: str, channel_id: str, source_url: str, title: str, is_priority: bool):
        with self._tx() as c:
            c.execute(
                "INSERT INTO videos (video_id, channel_id, source_url, title, stage, is_priority) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (video_id, channel_id, source_url, title, State.DISCOVERED.value, int(is_priority))
            )

    def get_video(self, video_id: str) -> "Video":
        with self._tx() as c:
            row = c.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()
        if row is None:
            raise KeyError(f"Video not found: {video_id}")
        return self._row_to_video(row)

    def update_stage(self, video_id: str, state, error: Optional[str] = None):
        stage_val = state.value if isinstance(state, State) else state
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE videos SET stage=?, error=?, updated_at=datetime('now') WHERE video_id=?",
                (stage_val, error, video_id)
            )
            if cur.rowcount == 0:
                raise KeyError(f"video_id not found: {video_id}")

    def update_subtitle_source(self, video_id: str, source: str):
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE videos SET subtitle_source=? WHERE video_id=?",
                (source, video_id)
            )
            if cur.rowcount == 0:
                raise KeyError(f"video_id not found: {video_id}")

    def set_priority(self, video_id: str, is_priority: bool):
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE videos SET is_priority=?, updated_at=datetime('now') WHERE video_id=?",
                (int(is_priority), video_id)
            )
            if cur.rowcount == 0:
                raise KeyError(f"video_id not found: {video_id}")

    def list_by_stage(self, state) -> List["Video"]:
        stage_val = state.value if isinstance(state, State) else state
        with self._tx() as c:
            rows = c.execute(
                "SELECT * FROM videos WHERE stage=? ORDER BY updated_at",
                (stage_val,)
            ).fetchall()
        return [self._row_to_video(r) for r in rows]

    def mark_published(self, video_id: str):
        """Set stage=published and stamp published_at=now."""
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE videos SET stage=?, published_at=datetime('now'), "
                "error=NULL, updated_at=datetime('now') WHERE video_id=?",
                (State.PUBLISHED.value, video_id)
            )
            if cur.rowcount == 0:
                raise KeyError(f"video_id not found: {video_id}")

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._tx() as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row is not None else default

    def set_meta(self, key: str, value: str):
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value)
            )

    def list_all(self) -> List["Video"]:
        with self._tx() as c:
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
