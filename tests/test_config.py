import pytest
from pathlib import Path
from yt2bili.config import load_config, Config

def test_load_minimal_config(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
channels:
  - id: UCxxxx
    name: test_channel
defaults:
  prefer_asr: false
  publish_time: "19:00"
  max_duration_min: 60
  publish_fail_threshold: 3
  subtitle_style:
    font: "思源黑体"
    font_size: 22
    outline: 1
    margin_v: 30
""")
    cfg = load_config(cfg_file)
    assert isinstance(cfg, Config)
    assert cfg.channels[0].id == "UCxxxx"
    assert cfg.defaults.max_duration_min == 60
    assert cfg.defaults.publish_fail_threshold == 3

def test_channel_defaults(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
channels:
  - id: UCxxxx
    name: test_channel
defaults:
  prefer_asr: false
  publish_time: "19:00"
  max_duration_min: 60
  publish_fail_threshold: 3
  subtitle_style:
    font: "思源黑体"
    font_size: 22
    outline: 1
    margin_v: 30
""")
    cfg = load_config(cfg_file)
    assert cfg.channels[0].priority == False
    assert cfg.channels[0].prefer_asr is None  # not set → None

def test_defaults_are_applied(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
channels: []
defaults:
  prefer_asr: false
  publish_time: "19:00"
  max_duration_min: 60
  publish_fail_threshold: 3
  subtitle_style:
    font: "思源黑体"
    font_size: 22
    outline: 1
    margin_v: 30
""")
    cfg = load_config(cfg_file)
    assert cfg.defaults.publish_time == "19:00"
    assert cfg.defaults.subtitle_style.font == "思源黑体"
