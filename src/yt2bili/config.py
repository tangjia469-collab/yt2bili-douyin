from dataclasses import dataclass, field
from typing import Optional, List, Union
from pathlib import Path
import yaml


@dataclass
class Channel:
    id: str
    name: str
    priority: bool = False
    prefer_asr: Optional[bool] = None  # None = follow global default


@dataclass
class SubtitleStyle:
    font: str = "PingFang SC"
    font_size: int = 22
    outline: int = 1
    margin_v: int = 30


@dataclass
class Defaults:
    prefer_asr: bool = False
    publish_time: str = "19:00"
    max_duration_min: int = 60
    publish_fail_threshold: int = 3
    daily_publish_limit: int = 2
    min_publish_gap_min: int = 30
    quality_gate_enabled: bool = False
    quality_gate_ratio: float = 0.8
    quality_gate_recent_count: int = 20
    quality_gate_min_samples: int = 3
    subtitle_style: SubtitleStyle = field(default_factory=SubtitleStyle)


@dataclass
class ApiConfig:
    minimax_key: str = ""


@dataclass
class BiliupConfig:
    binary: str = "biliup"
    tid: int = 122
    tags: list = field(default_factory=lambda: ["搬运", "中文字幕"])


@dataclass
class Config:
    channels: List[Channel] = field(default_factory=list)
    defaults: Defaults = field(default_factory=Defaults)
    api: ApiConfig = field(default_factory=ApiConfig)
    biliup: BiliupConfig = field(default_factory=BiliupConfig)


def load_config(path: Union[str, Path]) -> Config:
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in config file: {e}")

    channels = []
    for c in raw.get("channels", []):
        channels.append(Channel(
            id=c["id"],
            name=c["name"],
            priority=c.get("priority", False),
            prefer_asr=c.get("prefer_asr", None),
        ))

    defaults_raw = raw.get("defaults", {})
    style_raw = defaults_raw.get("subtitle_style", {})
    defaults_raw_clean = {k: v for k, v in defaults_raw.items() if k != "subtitle_style"}
    subtitle_style = SubtitleStyle(**style_raw) if style_raw else SubtitleStyle()
    defaults = Defaults(subtitle_style=subtitle_style, **defaults_raw_clean)

    api_raw = raw.get("api", {})
    api = ApiConfig(**api_raw) if api_raw else ApiConfig()

    biliup_raw = raw.get("biliup", {})
    biliup = BiliupConfig(**biliup_raw) if biliup_raw else BiliupConfig()

    return Config(channels=channels, defaults=defaults, api=api, biliup=biliup)
