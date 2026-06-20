"""End-to-end smoke test: a video flows discovered → published on REAL infra.

Uses the real Database, real Config, real worker state machine, real publisher,
and the real Flask test client. Only the outermost side-effecting externals
(yt-dlp, ffmpeg, whisper, MiniMax HTTP, biliup) are stubbed — everything else
is the shipping code path. This proves the wiring no unit test exercises end
to end.

Run: PYTHONPATH=src .venv/bin/python scripts/e2e_smoke.py
Exits 0 on success, 1 on any assertion failure.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yt2bili import worker as worker_mod
from yt2bili import publisher as publisher_mod
from yt2bili import web
from yt2bili.db import Database
from yt2bili.states import State
from yt2bili.config import Config, Channel, Defaults, ApiConfig, BiliupConfig


def _stub_externals(warehouse: Path) -> None:
    """Replace side-effecting externals with fakes that write expected files."""

    def fake_download(url, wd):
        Path(wd).mkdir(parents=True, exist_ok=True)
        (Path(wd) / "source.mp4").write_bytes(b"FAKE_MP4")
        return True

    def fake_load_meta(wd):
        return {"duration": 300}  # 5 min, under the 60-min cap

    def fake_subtitle(url, wd, prefer):
        srt = "1\n00:00:01,000 --> 00:00:03,000\nHello world\n"
        (Path(wd) / "en.srt").write_text(srt, encoding="utf-8")
        return srt, "youtube"

    def fake_translate(en_srt, key, title):
        # Real parse/build path would run; just swap text to prove zh write.
        return en_srt.replace("Hello world", "你好世界")

    def fake_burn(wd, **kw):
        (Path(wd) / "final.mp4").write_bytes(b"FAKE_FINAL_MP4")
        return True

    worker_mod.download_video = fake_download
    worker_mod.load_meta = fake_load_meta
    worker_mod.get_english_subtitle = fake_subtitle
    worker_mod.translate_srt = fake_translate
    worker_mod.burn_subtitles = fake_burn


def main() -> int:
    root = Path(tempfile.mkdtemp()) / "yt2bili"
    warehouse = root / "warehouse"
    warehouse.mkdir(parents=True)

    db = Database(root / "db.sqlite")
    db.init()

    config = Config(
        channels=[Channel(id="CH1", name="chan", priority=False)],
        defaults=Defaults(prefer_asr=False, max_duration_min=60,
                          daily_publish_limit=10, min_publish_gap_min=0),
        api=ApiConfig(minimax_key="dummy"),
        biliup=BiliupConfig(binary="biliup", tid=122, tags=["搬运"]),
    )

    _stub_externals(warehouse)

    failures = []

    def check(label, cond):
        status = "OK " if cond else "FAIL"
        print(f"  [{status}] {label}")
        if not cond:
            failures.append(label)

    # --- Stage 1: discover writes a video row ------------------------------
    print("1) discover → DB row")
    db.insert_video("vid1", "CH1", "https://youtu.be/vid1", "Test Video", False)
    check("video inserted at 'discovered'",
          db.get_video("vid1").stage == State.DISCOVERED.value)

    # --- Stage 2: worker drives discovered → ready (real state machine) -----
    print("2) worker runs the full pipeline")
    worker_mod.run_worker(db, config, warehouse)
    v = db.get_video("vid1")
    check("reached 'ready'", v.stage == State.READY.value)
    check("subtitle_source recorded", v.subtitle_source == "youtube")
    check("en.srt on disk", (warehouse / "vid1" / "en.srt").exists())
    check("zh.srt on disk", (warehouse / "vid1" / "zh.srt").exists())
    zh = (warehouse / "vid1" / "zh.srt").read_text(encoding="utf-8")
    check("zh.srt holds translated text", "你好世界" in zh)
    check("final.mp4 burned", (warehouse / "vid1" / "final.mp4").exists())

    # --- Stage 3: Dashboard sees it via real Flask client ------------------
    print("3) Web dashboard reflects real state")
    client = web.create_app(db).test_client()
    stats = client.get("/api/stats").get_json()
    check("stats counts 1 ready", stats["ready"] == 1)
    listing = client.get("/api/videos").get_json()
    check("listing shows vid1", any(x["video_id"] == "vid1" for x in listing))

    # --- Stage 4: publisher uploads ready → published ----------------------
    print("4) publisher drains ready queue")
    publisher_mod.publish_video = lambda wd, title, cfg: True  # stub biliup
    publisher_mod.run_publisher(db, config, warehouse,
                                publish_fn=lambda wd, t, c: True,
                                sleep_fn=lambda s: None)
    v = db.get_video("vid1")
    check("reached 'published'", v.stage == State.PUBLISHED.value)
    check("published_at stamped", v.published_at is not None)

    # --- Stage 5: priority video parks at pending_review -------------------
    print("5) priority video gates at pending_review")
    db.insert_video("vid2", "CH1", "https://youtu.be/vid2", "Key Video", True)
    worker_mod.run_worker(db, config, warehouse)
    check("priority video at 'pending_review'",
          db.get_video("vid2").stage == State.PENDING_REVIEW.value)
    # approve via real web endpoint
    client.post("/api/videos/vid2/approve")
    check("approve endpoint → ready",
          db.get_video("vid2").stage == State.READY.value)

    print()
    if failures:
        print(f"E2E FAILED: {len(failures)} check(s) failed: {failures}")
        return 1
    print("E2E PASSED: discovered → downloaded → subtitled → translated → "
          "burned → ready → published, plus priority gate + web approve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
