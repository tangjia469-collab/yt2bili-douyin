"""Entry points invoked by launchd jobs.

Each job is a short-lived process: load config + DB, run one component, exit.
launchd handles the scheduling (discover hourly, worker every 10 min, publish
daily). The web dashboard is the one long-lived process (KeepAlive).

Paths default to ``~/yt2bili`` and can be overridden with ``YT2BILI_HOME`` for
testing or relocating the data directory.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .config import load_config
from .db import Database
from .discover import Discoverer
from .worker import run_worker
from .publisher import run_publisher
from . import web

logger = logging.getLogger(__name__)


@dataclass
class Paths:
    """Resolved on-disk locations for the pipeline."""

    root: Path
    config: Path
    db: Path
    warehouse: Path
    logs: Path

    @classmethod
    def resolve(cls) -> "Paths":
        env = os.environ.get("YT2BILI_HOME")
        root = Path(env) if env else Path.home() / "yt2bili"
        return cls(
            root=root,
            config=root / "config.yaml",
            db=root / "db.sqlite",
            warehouse=root / "warehouse",
            logs=root / "logs",
        )


def _setup_logging(paths: Paths, job: str) -> None:
    paths.logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(paths.logs / f"{job}.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def _bootstrap(job: str):
    """Common setup: resolve paths, init DB, load config. Returns (paths, db, config)."""
    paths = Paths.resolve()
    paths.root.mkdir(parents=True, exist_ok=True)
    _setup_logging(paths, job)
    db = Database(paths.db)
    db.init()
    config = load_config(paths.config)
    return paths, db, config


def run_discover() -> None:
    """launchd: hourly. Scan channels, insert new videos."""
    paths, db, config = _bootstrap("discover")
    logger.info("Discover job starting")
    Discoverer(config, db).run()
    logger.info("Discover job done")


def run_worker_job() -> None:
    """launchd: every 10 minutes. Advance all non-terminal videos."""
    paths, db, config = _bootstrap("worker")
    logger.info("Worker job starting")
    run_worker(db, config, paths.warehouse)
    logger.info("Worker job done")


def run_publish_job() -> None:
    """launchd: daily at publish_time. Drain the ready queue to Bilibili."""
    paths, db, config = _bootstrap("publish")
    logger.info("Publish job starting")
    run_publisher(db, config, paths.warehouse)
    logger.info("Publish job done")


def run_web() -> None:
    """launchd: KeepAlive. Serve the dashboard at http://127.0.0.1:8080."""
    paths, db, config = _bootstrap("web")
    logger.info("Web dashboard starting on http://127.0.0.1:8080")
    app = web.create_app(db)
    app.run(host="127.0.0.1", port=8080, debug=False)


# Convenience for `python -m yt2bili.runner <job>`.
_JOBS = {
    "discover": run_discover,
    "worker": run_worker_job,
    "publish": run_publish_job,
    "web": run_web,
}


def main(argv=None) -> int:
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in _JOBS:
        print(f"usage: python -m yt2bili.runner {{{'|'.join(_JOBS)}}}")
        return 2
    _JOBS[argv[0]]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
