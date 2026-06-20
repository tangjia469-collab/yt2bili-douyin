"""Tests for the launchd runner entry points (discover / worker / publish / web)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yt2bili import runner
from yt2bili.db import Database
from yt2bili.states import State


# --------------------------------------------------------------------------
# Path resolution
# --------------------------------------------------------------------------

def test_paths_default_under_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    p = runner.Paths.resolve()
    assert p.root == tmp_path / "yt2bili"
    assert p.db == tmp_path / "yt2bili" / "db.sqlite"
    assert p.config == tmp_path / "yt2bili" / "config.yaml"
    assert p.warehouse == tmp_path / "yt2bili" / "warehouse"


def test_paths_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("YT2BILI_HOME", str(tmp_path / "custom"))
    p = runner.Paths.resolve()
    assert p.root == tmp_path / "custom"
    assert p.db == tmp_path / "custom" / "db.sqlite"


# --------------------------------------------------------------------------
# Runner wiring: each entry loads config+db and calls the right component
# --------------------------------------------------------------------------

def _write_config(path: Path):
    path.write_text(
        "channels:\n"
        "  - id: CH1\n"
        "    name: test\n"
        "defaults:\n"
        "  prefer_asr: false\n"
        "api:\n"
        "  minimax_key: testkey\n",
        encoding="utf-8",
    )


def test_run_discover_invokes_discoverer(tmp_path, monkeypatch):
    root = tmp_path / "yt2bili"
    root.mkdir()
    _write_config(root / "config.yaml")
    monkeypatch.setenv("YT2BILI_HOME", str(root))

    called = {}
    class FakeDiscoverer:
        def __init__(self, config, db):
            called["init"] = True
        def run(self):
            called["run"] = True
    monkeypatch.setattr(runner, "Discoverer", FakeDiscoverer)

    runner.run_discover()
    assert called == {"init": True, "run": True}
    # db file should have been created/initialized
    assert (root / "db.sqlite").exists()


def test_run_worker_invokes_run_worker(tmp_path, monkeypatch):
    root = tmp_path / "yt2bili"
    root.mkdir()
    _write_config(root / "config.yaml")
    monkeypatch.setenv("YT2BILI_HOME", str(root))

    seen = {}
    def fake_run_worker(db, config, warehouse):
        seen["warehouse"] = warehouse
    monkeypatch.setattr(runner, "run_worker", fake_run_worker)

    runner.run_worker_job()
    assert seen["warehouse"] == root / "warehouse"


def test_run_publish_invokes_run_publisher(tmp_path, monkeypatch):
    root = tmp_path / "yt2bili"
    root.mkdir()
    _write_config(root / "config.yaml")
    monkeypatch.setenv("YT2BILI_HOME", str(root))

    seen = {}
    def fake_run_publisher(db, config, warehouse):
        seen["called"] = True
    monkeypatch.setattr(runner, "run_publisher", fake_run_publisher)

    runner.run_publish_job()
    assert seen.get("called") is True
