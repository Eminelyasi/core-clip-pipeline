"""Thin yt-dlp wrappers: channel listing, metadata, download, captions."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


class YouTubeError(RuntimeError):
    pass


@dataclass
class VideoMeta:
    video_id: str
    title: str
    duration: int | None
    view_count: int | None
    channel: str
    url: str
    rank: int = 0  # 0 = newest item on the channel

    def __str__(self) -> str:
        dur = f"{self.duration // 60}m" if self.duration else "?"
        views = f"{self.view_count:,}" if self.view_count else "?"
        return f"{self.video_id} | {dur} | {views} views | {self.title[:70]}"


def cookies_path() -> Path | None:
    """Cookies keep yt-dlp working from cloud IPs (YouTube bot checks)."""
    env = os.environ.get("YT_COOKIES_FILE")
    if env and Path(env).exists():
        return Path(env)
    default = Path("cookies.txt")
    return default if default.exists() else None


def _ffmpeg_location() -> str | None:
    """yt-dlp needs its own pointer: it does not read FFMPEG_BIN."""
    explicit = os.environ.get("FFMPEG_BIN")
    if explicit:
        path = Path(explicit)
        if path.exists():
            return str(path.parent)
    found = shutil.which("ffmpeg")
    return str(Path(found).parent) if found else None


def base_opts(cookies: Path | None = None) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "ignoreerrors": False,
        "extractor_args": {"youtube": {"player_client": ["default", "web_safari"]}},
    }
    location = _ffmpeg_location()
    if location:
        opts["ffmpeg_location"] = location
    cookiefile = cookies or cookies_path()
    if cookiefile and Path(cookiefile).exists():
        opts["cookiefile"] = str(cookiefile)
    return opts


def _friendly_errors(exc: Exception) -> YouTubeError:
    message = str(exc)
    if "Sign in to confirm" in message or "bot" in message.lower():
        return YouTubeError(
            "YouTube is blocking this IP (bot check). Export browser cookies to "
            "cookies.txt, or run the job on your own machine / a residential IP."
        )
    return YouTubeError(message.splitlines()[0] if message else repr(exc))


def candidate_tab_urls(channel_url: str) -> list[str]:
    """A channel's uploads can live under /videos or /streams, so try both."""
    base = channel_url.rstrip("/")
    root = base
    for tab in ("/videos", "/streams", "/shorts"):
        if base.endswith(tab):
            root = base[: -len(tab)]
            break

    urls = [base]
    for tab in ("/videos", "/streams", ""):
        candidate = root + tab
        if candidate not in urls:
            urls.append(candidate)
    return urls


def list_channel_videos(channel_url: str, limit: int = 15, cookies: Path | None = None) -> list[VideoMeta]:
    """Newest-first flat listing of a channel's uploads (videos, then streams)."""
    opts = base_opts(cookies) | {
        "extract_flat": "in_playlist",
        "playlistend": limit,
        "skip_download": True,
    }

    info: dict | None = None
    last_error: Exception | None = None
    for url in candidate_tab_urls(channel_url):
        try:
            with YoutubeDL(opts) as ydl:
                candidate_info = ydl.extract_info(url, download=False) or {}
        except DownloadError as exc:
            last_error = exc
            continue
        if candidate_info.get("entries"):
            info = candidate_info
            break
        info = info or candidate_info

    if info is None:
        raise _friendly_errors(last_error or YouTubeError(f"no uploads found for {channel_url}"))

    entries = info.get("entries") or []
    videos: list[VideoMeta] = []
    for rank, entry in enumerate(entries):
        if not entry:
            continue
        video_id = entry.get("id")
        if not video_id:
            continue
        views = entry.get("view_count")
        videos.append(
            VideoMeta(
                video_id=video_id,
                title=entry.get("title") or video_id,
                duration=int(entry["duration"]) if entry.get("duration") else None,
                view_count=int(views) if views else None,
                channel=entry.get("channel") or entry.get("uploader") or "",
                url=f"https://www.youtube.com/watch?v={video_id}",
                rank=rank,
            )
        )
    return videos


def video_details(video_id: str, cookies: Path | None = None) -> dict:
    opts = base_opts(cookies) | {"skip_download": True}
    try:
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False) or {}
    except DownloadError as exc:
        raise _friendly_errors(exc) from exc


def download_video(
    video_id: str,
    dest_dir: Path,
    cookies: Path | None = None,
    max_height: int = 720,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / f"{video_id}.%(ext)s")
    opts = base_opts(cookies) | {
        "format": (
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "writethumbnail": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
    }
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    except DownloadError as exc:
        raise _friendly_errors(exc) from exc

    for candidate in sorted(dest_dir.glob(f"{video_id}.*")):
        if candidate.suffix.lower() in {".mp4", ".mkv", ".webm"}:
            return candidate
    raise YouTubeError(f"download produced no usable file for {video_id}")


_SKIP_SUBTITLE_SUFFIXES = {"live_chat"}


def fetch_captions(video_id: str, dest_dir: Path, cookies: Path | None = None) -> Path | None:
    """Grab YouTube auto-captions (VTT). Free transcript, no ASR compute."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = base_opts(cookies) | {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-orig", "en-US", "en-GB"],
        "subtitlesformat": "vtt/vtt",
        "outtmpl": str(dest_dir / f"{video_id}.%(ext)s"),
    }
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    except DownloadError:
        return None

    found = []
    for path in dest_dir.glob(f"{video_id}*.vtt"):
        stem = re.sub(r"\.(en|en-orig|en-US|en-GB)$", "", path.stem)
        if any(suffix in path.stem for suffix in _SKIP_SUBTITLE_SUFFIXES):
            continue
        if stem.startswith(video_id):
            found.append(path)
    if not found:
        return None
    found.sort(key=lambda p: (0 if p.stem.endswith(".en") else 1, len(p.name)))
    return found[0]
