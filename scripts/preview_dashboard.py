"""Dev-only: launch the dashboard against a throwaway DB with seed rows.

Used by the Launch preview panel to render the UI with realistic data.
Not part of the shipped pipeline.
"""

import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yt2bili.db import Database
from yt2bili.states import State
from yt2bili import web


def _seed(db: Database) -> None:
    rows = [
        ("yt_aaa", "CH1", "已发现的新视频", "discovered", False, None, None),
        ("yt_bbb", "CH1", "正在翻译的视频", "zh_translated", False, "youtube", None),
        ("yt_ccc", "CH2", "重点视频待审核", "pending_review", True, "asr", None),
        ("yt_ddd", "CH1", "已就绪等待发布", "ready", False, "youtube", None),
        ("yt_eee", "CH2", "昨天已发布的视频", "published", False, "youtube", None),
        ("yt_fff", "CH1", "翻译失败的视频", "failed_translate", False, "youtube", "MiniMax timeout"),
        ("yt_ggg", "CH2", "超长被跳过的视频", "skipped_long", False, None, None),
    ]
    for vid, ch, title, stage, prio, sub, err in rows:
        db.insert_video(vid, ch, f"https://youtu.be/{vid}", title, prio)
        db.update_stage(vid, stage, error=err)
        if sub:
            db.update_subtitle_source(vid, sub)


def main() -> None:
    tmp = Path(tempfile.gettempdir()) / "yt2bili_preview.sqlite"
    if tmp.exists():
        tmp.unlink()
    db = Database(tmp)
    db.init()
    _seed(db)
    app = web.create_app(db)
    app.run(host="127.0.0.1", port=8080, debug=False)


if __name__ == "__main__":
    main()
