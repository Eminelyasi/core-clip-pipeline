"""CLI: `run` (full daily job), `discover` (cheap dry run), `doctor` (env check).

    python -m clipforge doctor
    python -m clipforge discover
    python -m clipforge run
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import yt
from .caption import build_caption
from .render import FFmpegError, audio_energy, ffmpeg_bin, ffprobe_bin, probe, render_clip
from .score import Window, pick_windows, score_windows, trim_to_sentence
from .settings import Config, Source, load_config
from .state import is_complete, load_state, record_clip, record_run, save_state, used_starts
from .transcript import Cue, parse_vtt


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def slugify(value: str, limit: int = 24) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return (value[:limit] or "clip").strip("-")


# ------------------------------------------------------------------ discovery


def _video_rank(meta: yt.VideoMeta, total: int) -> float:
    freshness = 1.0 - (meta.rank / max(1, total))
    views = min(1.0, (meta.view_count or 0) / 2_000_000)
    return freshness * 0.6 + views * 0.4


def discover(cfg: Config, state: dict, per_channel: int = 15) -> list[tuple[Source, yt.VideoMeta]]:
    buckets: list[tuple[Source, list[yt.VideoMeta]]] = []

    for source in cfg.enabled_sources:
        log(f"[{source.name}] scanning {source.channel_url}")
        try:
            videos = yt.list_channel_videos(source.channel_url, limit=per_channel)
        except yt.YouTubeError as exc:
            log(f"[{source.name}] discovery failed: {exc}")
            continue

        usable: list[yt.VideoMeta] = []
        for video in videos:
            if is_complete(state, video.video_id, cfg.targets.max_clips_per_source_video):
                continue
            if video.duration is None:
                continue
            if not (source.min_duration_sec <= video.duration <= source.max_duration_sec):
                continue
            usable.append(video)

        usable.sort(key=lambda v: _video_rank(v, len(videos)), reverse=True)
        selected = usable[: source.max_candidates]
        log(
            f"[{source.name}] {len(videos)} listed, {len(usable)} usable, "
            f"taking {len(selected)} candidate(s)"
        )
        for video in selected:
            log(f"    -> {video}")
        if selected:
            buckets.append((source, selected))

    # Round-robin across channels so a single high-volume source cannot eat
    # the whole daily quota.
    ranked: list[tuple[Source, yt.VideoMeta]] = []
    depth = max((len(items) for _, items in buckets), default=0)
    for index in range(depth):
        for source, items in buckets:
            if index < len(items):
                ranked.append((source, items[index]))
    return ranked


# ------------------------------------------------------------------ per video


def _transcribe_fallback(path: Path, model_name: str) -> list[Cue]:
    """Optional local ASR when a video has no captions. Heavy; off by default."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        log("  whisper fallback requested but faster-whisper is not installed - skipping")
        return []

    log(f"  transcribing with faster-whisper ({model_name}) - this is slow")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(path), vad_filter=True, beam_size=1)
    cues: list[Cue] = []
    for segment in segments:
        text = (segment.text or "").strip()
        if text:
            cues.append(Cue(float(segment.start), float(segment.end), text, "speech"))
    return cues


def process_video(
    cfg: Config,
    source: Source,
    meta: yt.VideoMeta,
    date_dir: Path,
    state: dict,
    start_index: int,
    remaining: int,
    state_path: Path | None = None,
) -> tuple[list[dict], bool]:
    """Returns (clips, download_succeeded)."""
    state_path = state_path or cfg.state_path
    clips_dir = date_dir / "clips"
    captions_dir = date_dir / "captions"
    src_dir = cfg.work_dir / "src"
    tmp_dir = cfg.work_dir / "tmp"

    log(f"[{source.name}] downloading {meta.video_id} - {meta.title[:60]}")
    try:
        source_path = yt.download_video(meta.video_id, src_dir)
    except yt.YouTubeError as exc:
        log(f"  download failed: {exc}")
        return [], False

    captions_path = None
    try:
        info = probe(source_path)
        duration = float(info["duration"] or meta.duration or 0)
        if duration <= cfg.targets.clip_length_sec:
            log("  video shorter than one clip - skipped")
            return [], True

        cues: list[Cue] = []
        if cfg.analysis.use_youtube_subs:
            captions_path = yt.fetch_captions(meta.video_id, src_dir)
            if captions_path:
                cues = parse_vtt(captions_path)
                log(f"  captions: {len(cues)} cues")
            else:
                log("  no captions found")

        if not cues and cfg.analysis.whisper_fallback:
            cues = _transcribe_fallback(source_path, cfg.analysis.whisper_model)
        if not cues:
            log("  no transcript - scoring on audio energy only")

        if cfg.analysis.audio_energy:
            log("  analysing audio energy")
            energy = audio_energy(source_path)
        else:
            energy = np.full(int(duration) + 1, 0.5, dtype=np.float32)

        windows = score_windows(duration, cues, energy, cfg.targets, cfg.analysis)
        previous = used_starts(state, meta.video_id)
        fresh = [
            w for w in windows if not any(abs(w.start - used) < cfg.targets.min_gap_sec for used in previous)
        ]

        want = min(
            remaining,
            cfg.targets.max_clips_per_source_video - len(previous),
        )
        chosen = pick_windows(fresh, want, cfg.targets.min_gap_sec, cfg.targets.min_score)
        if not chosen:
            best = max((w.score for w in fresh), default=0.0)
            log(f"  nothing above min_score ({best:.2f} < {cfg.targets.min_score}) - skipped")
            return [], True

        produced: list[dict] = []
        for window in chosen:
            index = start_index + len(produced)
            trimmed = trim_to_sentence(window, cues, float(cfg.targets.clip_length_sec), duration)
            clip_start = round(trimmed.start, 2)
            clip_end = round(clip_start + cfg.targets.clip_length_sec, 2)
            stem = f"{index:02d}_{slugify(source.name)}_{meta.video_id}_{int(clip_start):04d}"
            target = clips_dir / f"{stem}.mp4"

            log(f"  rendering {target.name} @ {clip_start:.0f}s (score {window.score:.2f}, {window.reason})")
            try:
                render_clip(
                    source_path,
                    target,
                    clip_start,
                    float(cfg.targets.clip_length_sec),
                    cfg.render,
                    cues,
                    work_dir=tmp_dir,
                )
            except FFmpegError as exc:
                log(f"  render failed: {exc}")
                continue

            caption = build_caption(
                source.name,
                meta.title,
                cues,
                clip_start,
                clip_end,
                window.features,
            )

            if cfg.output.write_platform_captions:
                captions_dir.mkdir(parents=True, exist_ok=True)
                for filename, body in caption.platform.items():
                    (captions_dir / f"{stem}.{filename}").write_text(body + "\n", encoding="utf-8")

            record = {
                "file": str(target.relative_to(date_dir)).replace("\\", "/"),
                "source_video_id": meta.video_id,
                "source_title": meta.title,
                "source_channel": source.name,
                "source_url": meta.url,
                "start": clip_start,
                "end": clip_end,
                "duration": round(clip_end - clip_start, 2),
                "score": window.score,
                "reason": window.reason,
                "features": window.features,
                "title": caption.title,
                "hook": caption.hook,
                "platform_captions": caption.platform if cfg.output.write_platform_captions else {},
            }
            produced.append(record)
            record_clip(
                state,
                meta.video_id,
                meta.title,
                {"start": clip_start, "end": clip_end, "file": record["file"]},
            )
            save_state(state_path, state)

        log(f"  {len(produced)} clip(s) from {meta.video_id}")
        return produced, True

    finally:
        if not cfg.output.keep_source_files:
            for path in src_dir.glob(f"{meta.video_id}.*"):
                path.unlink(missing_ok=True)


# ------------------------------------------------------------------- commands


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    state_path = Path(args.state) if args.state else cfg.state_path
    state = load_state(state_path)
    day = args.date or datetime.now().strftime("%Y-%m-%d")
    date_dir = cfg.output.dir / day
    (date_dir / "clips").mkdir(parents=True, exist_ok=True)

    if args.clips:
        cfg.targets.clips_per_day = args.clips
    if args.max_videos:
        cfg.targets.max_videos_downloaded = args.max_videos

    log(f"=== clip run {day} | target {cfg.targets.clips_per_day} clips ===")
    candidates = discover(cfg, state, per_channel=args.discover_limit)
    if not candidates:
        log("no candidates found - check handles, rights flags, or YouTube bot blocking")
        return 2

    if args.dry_run:
        log("dry run - stopping after discovery")
        return 0

    produced: list[dict] = []
    downloaded = 0
    for source, meta in candidates:
        if len(produced) >= cfg.targets.clips_per_day:
            break
        if downloaded >= cfg.targets.max_videos_downloaded:
            log("download cap reached for this run")
            break
        downloaded += 1
        try:
            clips, download_ok = process_video(
                cfg,
                source,
                meta,
                date_dir,
                state,
                start_index=len(produced) + 1,
                remaining=cfg.targets.clips_per_day - len(produced),
                state_path=state_path,
            )
        except Exception as exc:  # one bad video must not kill the batch
            log(f"  unexpected failure on {meta.video_id}: {exc}")
            clips, download_ok = [], False
        if not download_ok:
            # a blocked/failed download should not eat the bandwidth budget
            downloaded -= 1
        produced.extend(clips)

    manifest = {
        "date": day,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested": cfg.targets.clips_per_day,
        "produced": len(produced),
        "clip_length_sec": cfg.targets.clip_length_sec,
        "clips": produced,
    }
    (date_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_upload_sheet(date_dir, manifest)

    record_run(state, len(produced), cfg.targets.clips_per_day)
    save_state(state_path, state)

    shutil.rmtree(cfg.work_dir / "tmp", ignore_errors=True)
    if not cfg.output.keep_source_files:
        shutil.rmtree(cfg.work_dir / "src", ignore_errors=True)

    log(f"=== done: {len(produced)}/{cfg.targets.clips_per_day} clips in {date_dir} ===")
    if len(produced) < cfg.targets.clips_per_day:
        log("short of target - add more sources, lower min_score, or raise max_candidates")
    return 0 if produced else 3


def _write_upload_sheet(date_dir: Path, manifest: dict) -> None:
    lines = [
        f"Clips for {manifest['date']}",
        f"{manifest['produced']} of {manifest['requested']} produced",
        "",
        "Upload order = highest predicted score first.",
        "Each clip has matching caption files in ./captions/",
        "",
    ]
    for index, clip in enumerate(sorted(manifest["clips"], key=lambda c: c["score"], reverse=True), start=1):
        lines += [
            f"{index}. {clip['file']}",
            f"   score {clip['score']:.2f}  ({clip['reason']})",
            f"   title: {clip['title']}",
            f"   source: {clip['source_url']} @ {clip['start']:.0f}s",
            "",
        ]
    (date_dir / "UPLOAD.txt").write_text("\n".join(lines), encoding="utf-8")


def cmd_discover(cfg: Config, args: argparse.Namespace) -> int:
    state_path = Path(args.state) if args.state else cfg.state_path
    state = load_state(state_path)
    candidates = discover(cfg, state, per_channel=args.discover_limit)
    if not candidates:
        log("no candidates")
        return 2
    log(f"{len(candidates)} candidate(s):")
    for source, meta in candidates:
        log(f"  {source.name:12s} {meta}")
    return 0


def cmd_doctor(cfg: Config, args: argparse.Namespace) -> int:
    ok = True
    log(f"config: {len(cfg.sources)} source(s), {len(cfg.enabled_sources)} enabled")
    for source in cfg.sources:
        flag = "OK" if source.rights_confirmed else "SKIPPED (rights_confirmed: false)"
        log(f"  {source.name:12s} {source.channel_url} -> {flag}")
        ok = ok and source.rights_confirmed

    for name, binary in (("ffmpeg", ffmpeg_bin()), ("ffprobe", ffprobe_bin())):
        try:
            result = subprocess.run([binary, "-version"], capture_output=True, text=True)
            first = (result.stdout or "").splitlines()[0] if result.returncode == 0 else "failed"
            log(f"{name:12s} {first}")
        except FileNotFoundError:
            log(f"{name:12s} NOT FOUND - install ffmpeg and put it on PATH")
            ok = False

    try:
        import yt_dlp

        log(f"yt-dlp       {yt_dlp.version.__version__}")
    except Exception as exc:
        log(f"yt-dlp       missing ({exc})")
        ok = False

    cookies = yt.cookies_path()
    log(f"cookies      {cookies if cookies else 'none (cloud IPs may hit bot checks)'}")

    try:
        videos = yt.list_channel_videos(cfg.enabled_sources[0].channel_url, limit=3) if cfg.enabled_sources else []
        log(f"youtube      reachable, {len(videos)} recent video(s) listed")
    except Exception as exc:
        log(f"youtube      unreachable: {exc}")
        ok = False

    log("doctor: " + ("all good" if ok else "fix the items above"))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clipforge", description="Daily CORE clip pipeline")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--discover-limit", type=int, default=15, help="newest videos to list per channel")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="discover, score, cut and package clips")
    run.add_argument("--date", help="output folder name (default: today)")
    run.add_argument("--clips", type=int, help="override clips_per_day for this run")
    run.add_argument("--max-videos", type=int, help="override the per-run download cap")
    run.add_argument("--state", help="override the dedupe state file")
    run.add_argument("--dry-run", action="store_true", help="discovery only")

    discover_parser = sub.add_parser("discover", help="show candidates without downloading")
    discover_parser.add_argument("--state", help="override the dedupe state file")

    sub.add_parser("doctor", help="check ffmpeg, yt-dlp and config")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        log(f"config error: {exc}")
        return 1

    if args.command == "doctor":
        return cmd_doctor(cfg, args)
    if args.command == "discover":
        return cmd_discover(cfg, args)
    return cmd_run(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
