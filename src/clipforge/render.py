"""ffmpeg: audio energy analysis, ASS captions, vertical clip rendering."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np

from .settings import Render
from .transcript import Cue, cues_between
from .caption import strip_censored


class FFmpegError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    explicit = os.environ.get("FFPROBE_BIN")
    if explicit:
        return explicit
    found = shutil.which("ffprobe")
    if found:
        return found
    # derive from ffmpeg location (portable builds keep them side by side)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        candidate = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if candidate.exists():
            return str(candidate)
    return "ffprobe"


def probe(path: str | Path) -> dict:
    cmd = [
        ffprobe_bin(),
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {proc.stderr.strip()[:400]}")
    data = json.loads(proc.stdout or "{}")

    duration = float((data.get("format") or {}).get("duration") or 0.0)
    width = height = 0
    has_audio = False
    has_video = False
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video":
            has_video = True
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
        elif stream.get("codec_type") == "audio":
            has_audio = True
    return {"duration": duration, "width": width, "height": height, "has_audio": has_audio, "has_video": has_video}


def audio_energy(path: str | Path, sr: int = 8000) -> np.ndarray:
    """Per-second loudness curve normalised to 0..1 (one value per second)."""
    cmd = [
        ffmpeg_bin(),
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sr),
        "-f",
        "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise FFmpegError(f"audio decode failed: {proc.stderr.decode('utf-8', 'ignore')[:400]}")

    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    frames = samples.size // sr
    if frames == 0:
        return np.zeros(0, dtype=np.float32)

    blocks = samples[: frames * sr].reshape(frames, sr)
    rms = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1) + 1e-12)
    db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    # -50 dB (silence) .. -6 dB (shouting) mapped to 0..1
    return np.clip((db + 50.0) / 44.0, 0.0, 1.0).astype(np.float32)


# ----------------------------------------------------------------- captions


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").strip()


def _wrap(text: str, max_chars: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > max_chars:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def _cue_events(cue: Cue, clip_start: float, clip_dur: float, cfg: Render) -> list[tuple[float, float, str]]:
    """Turn one cue into 1-2 line caption cards, timed by character share."""
    text = strip_censored(cue.text)
    text = text.upper() if cfg.caption_uppercase else text
    lines = _wrap(text, cfg.max_chars_per_line)
    if not lines:
        return []

    cards: list[list[str]] = [lines[i : i + 2] for i in range(0, len(lines), 2)]

    start = max(0.0, cue.start - clip_start)
    end = min(clip_dur, cue.end - clip_start)
    if end <= start:
        return []

    total_chars = sum(len(" ".join(card)) for card in cards) or 1
    available = max(0.6, end - start)
    events: list[tuple[float, float, str]] = []
    cursor = start
    for index, card in enumerate(cards):
        share = len(" ".join(card)) / total_chars
        length = max(0.7, available * share) if index < len(cards) - 1 else max(0.7, end - cursor)
        card_end = min(end, cursor + length)
        if card_end - cursor < 0.15:
            break
        body = "\\N".join(_escape_ass(line) for line in card)
        events.append((cursor, card_end, body))
        cursor = card_end
    return events


def write_ass(cues: list[Cue], clip_start: float, clip_dur: float, path: Path, cfg: Render) -> Path:
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {cfg.width}\n"
        f"PlayResY: {cfg.height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{cfg.caption_font},{cfg.caption_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,{cfg.caption_outline},2,2,90,90,{cfg.caption_margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    rows: list[str] = []
    for cue in cues_between(cues, clip_start, clip_start + clip_dur):
        if cue.kind != "speech":
            continue
        for start, end, body in _cue_events(cue, clip_start, clip_dur, cfg):
            rows.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{body}")

    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------- render


def _video_graph(cfg: Render, ass_name: str | None) -> str:
    width, height = cfg.width, cfg.height
    if cfg.layout == "center_crop":
        base = f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1[base]"
    elif cfg.layout == "fit":
        base = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[base]"
        )
    else:  # blur_pad - the look most clip pages use
        base = (
            f"[0:v]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"gblur=sigma={cfg.blur_sigma},eq=brightness=-0.09:saturation=1.08[bg];"
            f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1[base]"
        )

    tail = "subtitles=" + ass_name if ass_name else "null"
    return f"{base};[base]{tail},format=yuv420p[v]"


def render_clip(
    src: str | Path,
    dst: str | Path,
    start: float,
    duration: float,
    cfg: Render,
    cues: list[Cue] | None = None,
    work_dir: Path | None = None,
) -> Path:
    src = Path(src).resolve()
    dst = Path(dst).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    info = probe(src)

    if info["duration"] and start + duration > info["duration"]:
        duration = max(1.0, info["duration"] - start)
    if duration <= 1.0:
        raise FFmpegError(f"clip too short at {start:.1f}s in {src.name}")

    work = Path(work_dir) if work_dir else dst.parent
    work.mkdir(parents=True, exist_ok=True)

    ass_name = None
    if cfg.burn_captions and cues:
        ass_path = write_ass(cues, start, duration, work / f"{dst.stem}.ass", cfg)
        ass_name = ass_path.name

    graph = _video_graph(cfg, ass_name)
    audio_chain = f"[0:a]highpass=f=70,loudnorm=I={cfg.audio_lufs}:TP=-1.5:LRA=11[a]"
    if not info["has_audio"]:
        audio_chain = "[1:a]anull[a]"
        graph = graph.replace("[0:v]", "[1:v]", 1)

    filter_complex = f"{graph};{audio_chain}"

    cmd = [
        ffmpeg_bin(),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(src),
    ]
    if not info["has_audio"]:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    cmd += [
        "-t",
        f"{duration:.3f}",
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        cfg.preset,
        "-crf",
        str(cfg.crf),
        "-r",
        str(cfg.fps),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(dst),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(work))
    if proc.returncode != 0 or not dst.exists():
        raise FFmpegError(f"render failed: {(proc.stderr or '').strip()[:600]}")
    return dst
