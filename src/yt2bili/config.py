from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List
import yaml


@dataclass
class Channel:
    id: str
    name: str
    priority: bool = False
    prefer_asr: Optional[bool] = None  # None = follow global default


@dataclass
class SubtitleStyle:
    font: str = "思源黑体"
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
    subtitle_style: SubtitleStyle = field(default_factory=SubtitleStyle)


@dataclass
class Config:
    channels: List[Channel] = field(default_factory=list)
    defaults: Defaults = field(default_factory=Defaults)


def load_config(path) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)

    channels = []
    for c in raw.get("channels", []):
        channels.append(Channel(
            id=c["id"],
            name=c["name"],
            priority=c.get("priority", False),
            prefer_asr=c.get("prefer_asr", None),
        ))

    defaults_raw = raw.get("defaults", {})
    style_raw = defaults_raw.pop("subtitle_style", {})
    subtitle_style = SubtitleStyle(**style_raw) if style_raw else SubtitleStyle()
    defaults = Defaults(subtitle_style=subtitle_style, **defaults_raw)

    return Config(channels=channels, defaults=defaults)
