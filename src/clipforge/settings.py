"""Typed configuration loading."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _value(data: dict[str, Any], key: str, default: Any) -> Any:
    value = data.get(key, default)
    return default if value is None else value


@dataclass
class Source:
    handle: str
    name: str
    rights_confirmed: bool = False
    min_duration_sec: int = 240
    max_duration_sec: int = 14400
    max_candidates: int = 3

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Source":
        handle = str(_value(raw, "handle", "")).strip()
        if not handle:
            raise ValueError("source entry is missing 'handle'")
        return cls(
            handle=handle,
            name=str(_value(raw, "name", handle.lstrip("@"))),
            rights_confirmed=bool(_value(raw, "rights_confirmed", False)),
            min_duration_sec=int(_value(raw, "min_duration_sec", 240)),
            max_duration_sec=int(_value(raw, "max_duration_sec", 14400)),
            max_candidates=int(_value(raw, "max_candidates", 3)),
        )

    @property
    def channel_url(self) -> str:
        handle = self.handle.strip()
        if handle.startswith("http"):
            return handle
        # channel IDs (UC...) are far more reliable than handles: a handle can
        # point at an empty legacy channel with the same name.
        if re.fullmatch(r"UC[\w-]{22}", handle):
            return f"https://www.youtube.com/channel/{handle}/videos"
        if not handle.startswith("@"):
            handle = f"@{handle}"
        return f"https://www.youtube.com/{handle}/videos"


@dataclass
class Targets:
    clips_per_day: int = 10
    clip_length_sec: int = 60
    max_clips_per_source_video: int = 2
    min_gap_sec: float = 45.0
    min_score: float = 0.30
    max_videos_downloaded: int = 8

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Targets":
        return cls(
            clips_per_day=int(_value(raw, "clips_per_day", 10)),
            clip_length_sec=int(_value(raw, "clip_length_sec", 60)),
            max_clips_per_source_video=int(_value(raw, "max_clips_per_source_video", 2)),
            min_gap_sec=float(_value(raw, "min_gap_sec", 45)),
            min_score=float(_value(raw, "min_score", 0.30)),
            max_videos_downloaded=int(_value(raw, "max_videos_downloaded", 8)),
        )


@dataclass
class Render:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    layout: str = "blur_pad"
    blur_sigma: int = 28
    crf: int = 20
    preset: str = "veryfast"
    audio_lufs: float = -14.0
    burn_captions: bool = True
    caption_style: str = "mrbeast"
    caption_font: str = "Komika Axis"
    caption_font_fallback: str = "Bangers"
    caption_font_dir: str = ""
    caption_size: int = 96
    caption_outline: int = 8
    caption_shadow: int = 4
    caption_margin_v: int = 560
    caption_uppercase: bool = True
    caption_bold: bool = True
    caption_words_per_card: int = 1
    caption_pop_ms: int = 90
    caption_pop_scale: int = 118
    keyword_color: str = "&H00FF00&"
    keyword_outline: str = "&HFFFFFF&"
    max_chars_per_line: int = 20

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Render":
        return cls(
            width=int(_value(raw, "width", 1080)),
            height=int(_value(raw, "height", 1920)),
            fps=int(_value(raw, "fps", 30)),
            layout=str(_value(raw, "layout", "blur_pad")),
            blur_sigma=int(_value(raw, "blur_sigma", 28)),
            crf=int(_value(raw, "crf", 20)),
            preset=str(_value(raw, "preset", "veryfast")),
            audio_lufs=float(_value(raw, "audio_lufs", -14.0)),
            burn_captions=bool(_value(raw, "burn_captions", True)),
            caption_style=str(_value(raw, "caption_style", "mrbeast")).lower(),
            caption_font=str(_value(raw, "caption_font", "Komika Axis")),
            caption_font_fallback=str(_value(raw, "caption_font_fallback", "Bangers")),
            caption_font_dir=str(_value(raw, "caption_font_dir", "")),
            caption_size=int(_value(raw, "caption_size", 96)),
            caption_outline=int(_value(raw, "caption_outline", 8)),
            caption_shadow=int(_value(raw, "caption_shadow", 4)),
            caption_margin_v=int(_value(raw, "caption_margin_v", 560)),
            caption_uppercase=bool(_value(raw, "caption_uppercase", True)),
            caption_bold=bool(_value(raw, "caption_bold", True)),
            caption_words_per_card=max(1, int(_value(raw, "caption_words_per_card", 1))),
            caption_pop_ms=max(0, int(_value(raw, "caption_pop_ms", 90))),
            caption_pop_scale=int(_value(raw, "caption_pop_scale", 118)),
            keyword_color=str(_value(raw, "keyword_color", "&H00FF00&")),
            keyword_outline=str(_value(raw, "keyword_outline", "&HFFFFFF&")),
            max_chars_per_line=int(_value(raw, "max_chars_per_line", 20)),
        )


DEFAULT_WEIGHTS = {"energy": 0.35, "speech": 0.15, "hooks": 0.30, "laughs": 0.20}


@dataclass
class Analysis:
    use_youtube_subs: bool = True
    whisper_fallback: bool = False
    whisper_model: str = "tiny"
    audio_energy: bool = True
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Analysis":
        weights = dict(DEFAULT_WEIGHTS)
        weights.update({str(k): float(v) for k, v in _value(raw, "weights", {}).items()})
        return cls(
            use_youtube_subs=bool(_value(raw, "use_youtube_subs", True)),
            whisper_fallback=bool(_value(raw, "whisper_fallback", False)),
            whisper_model=str(_value(raw, "whisper_model", "tiny")),
            audio_energy=bool(_value(raw, "audio_energy", True)),
            weights=weights,
        )


@dataclass
class Output:
    dir: Path = Path("output")
    keep_source_files: bool = False
    write_platform_captions: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any], root: Path) -> "Output":
        return cls(
            dir=root / str(_value(raw, "dir", "output")),
            keep_source_files=bool(_value(raw, "keep_source_files", False)),
            write_platform_captions=bool(_value(raw, "write_platform_captions", True)),
        )


@dataclass
class Config:
    sources: list[Source]
    targets: Targets
    render: Render
    analysis: Analysis
    output: Output
    root: Path

    @property
    def state_path(self) -> Path:
        return self.root / "state" / "processed.json"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def enabled_sources(self) -> list[Source]:
        return [s for s in self.sources if s.rights_confirmed]


def load_config(path: str | Path) -> Config:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    root = config_path.parent

    sources = [Source.from_dict(item) for item in (raw.get("sources") or [])]
    if not sources:
        raise ValueError(f"{config_path} has no sources defined")

    return Config(
        sources=sources,
        targets=Targets.from_dict(raw.get("targets") or {}),
        render=Render.from_dict(raw.get("render") or {}),
        analysis=Analysis.from_dict(raw.get("analysis") or {}),
        output=Output.from_dict(raw.get("output") or {}, root),
        root=root,
    )
