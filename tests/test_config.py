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


def test_per_channel_prefer_asr_override(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
channels:
  - id: UCxxxx
    name: test_channel
    prefer_asr: true
defaults:
  prefer_asr: false
""")
    cfg = load_config(cfg_file)
    assert cfg.defaults.prefer_asr == False
    assert cfg.channels[0].prefer_asr == True


def test_channel_priority_flag(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
channels:
  - id: UCxxxx
    name: test_channel
    priority: true
defaults:
  prefer_asr: false
""")
    cfg = load_config(cfg_file)
    assert cfg.channels[0].priority == True


def test_api_biliup_and_douyin_fields(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
channels: []
defaults:
  prefer_asr: false
api:
  minimax_key: "test_key_abc"
biliup:
  binary: "biliup"
  tid: 200
  tags:
    - "搬运"
    - "测试"
douyin:
  enabled: true
  daily_publish_limit: 3
  min_publish_gap_min: 15
  publish_fail_threshold: 2
  tags:
    - "ASMR"
    - "助眠"
""")
    cfg = load_config(cfg_file)
    assert cfg.api.minimax_key == "test_key_abc"
    assert cfg.biliup.tid == 200
    assert cfg.biliup.tags == ["搬运", "测试"]
    assert cfg.douyin.enabled is True
    assert cfg.douyin.daily_publish_limit == 3
    assert cfg.douyin.min_publish_gap_min == 15
    assert cfg.douyin.publish_fail_threshold == 2
    assert cfg.douyin.tags == ["ASMR", "助眠"]


def test_missing_config_file_raises(tmp_path):
    missing = tmp_path / "nonexistent.yaml"
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_config(missing)
