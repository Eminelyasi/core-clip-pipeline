"""Persistent state so the job never cuts the same moment twice."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

EMPTY: dict = {"version": 1, "videos": {}, "runs": []}


def load_state(path: Path) -> dict:
    if not path.exists():
        return json.loads(json.dumps(EMPTY))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(EMPTY))
    data.setdefault("version", 1)
    data.setdefault("videos", {})
    data.setdefault("runs", [])
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def used_starts(state: dict, video_id: str) -> list[float]:
    record = state.get("videos", {}).get(video_id) or {}
    return [float(clip.get("start", 0)) for clip in record.get("clips", [])]


def is_complete(state: dict, video_id: str, max_per_video: int) -> bool:
    record = state.get("videos", {}).get(video_id) or {}
    return len(record.get("clips", [])) >= max_per_video


def record_clip(state: dict, video_id: str, title: str, clip: dict) -> None:
    videos = state.setdefault("videos", {})
    record = videos.setdefault(video_id, {"title": title, "clips": [], "last_run": None})
    record["title"] = title
    record["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record.setdefault("clips", []).append(clip)


def record_run(state: dict, produced: int, requested: int) -> None:
    state.setdefault("runs", []).append(
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "produced": produced,
            "requested": requested,
        }
    )
    state["runs"] = state["runs"][-60:]
