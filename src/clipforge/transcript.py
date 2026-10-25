"""Parse YouTube VTT captions into timed cues.

Auto-captions come in two shapes:
  1. word-level tags  -> okay<00:00:00.800><c> so</c><00:00:01.200><c> this</c>
  2. rolling segments -> the same sentence repeated across cues

Both are handled here so the scorer always gets clean, non-duplicated text.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

TIMING_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
)
INLINE_TS_RE = re.compile(r"<(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})>")
TAG_RE = re.compile(r"<[^>]*>")
NON_SPEECH_RE = re.compile(r"^\[?(laughter|music|applause|cheering|inaudible|silence)\]?$", re.I)

NON_SPEECH_KINDS = {
    "laughter": "laugh",
    "applause": "laugh",
    "cheering": "laugh",
    "music": "music",
    "inaudible": "noise",
    "silence": "noise",
}

MAX_CUE_SEC = 4.0


@dataclass
class Cue:
    start: float
    end: float
    text: str
    kind: str = "speech"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _clock(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    parts = [float(p) for p in parts]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    hours, minutes, seconds = parts[-3:]
    return hours * 3600 + minutes * 60 + seconds


def _clean(text: str) -> str:
    text = html.unescape(text)
    text = TAG_RE.sub("", text)
    text = re.sub(r">>\s*", " ", text)  # YouTube speaker-change markers
    text = text.replace("\u200b", "").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _kind_of(text: str) -> str | None:
    stripped = text.strip().strip("[]() ").lower()
    match = NON_SPEECH_RE.match(stripped)
    if match:
        return NON_SPEECH_KINDS.get(match.group(1).lower(), "noise")
    return None


def _blocks(raw: str) -> list[tuple[float, float, list[str]]]:
    """Split a VTT file into (start, end, text lines) blocks."""
    out: list[tuple[float, float, list[str]]] = []
    current: tuple[float, float] | None = None
    lines: list[str] = []

    for line in raw.splitlines():
        stripped = line.strip()
        match = TIMING_RE.search(stripped)
        if match:
            if current and lines:
                out.append((current[0], current[1], lines))
            current = (_clock(match.group("start")), _clock(match.group("end")))
            lines = []
            continue
        if not stripped:
            if current and lines:
                out.append((current[0], current[1], lines))
                current, lines = None, []
            continue
        if stripped.upper().startswith("WEBVTT") or stripped.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if current is not None:
            lines.append(stripped)

    if current and lines:
        out.append((current[0], current[1], lines))
    return out


def _word_cues(start: float, end: float, lines: list[str]) -> list[Cue] | None:
    """Word-level captions: rebuild exact timings from inline timestamps."""
    joined = " ".join(lines)
    if not INLINE_TS_RE.search(joined):
        return None

    chunks = INLINE_TS_RE.split(joined)
    # chunks: [text, ts, text, ts, text, ...]
    words: list[tuple[float, float, str]] = []
    pending = _clean(chunks[0])
    for i in range(1, len(chunks), 2):
        stamp = _clock(chunks[i])
        text = _clean(chunks[i + 1]) if i + 1 < len(chunks) else ""
        if pending:
            words.append((start, stamp, pending))
            start = stamp
        pending = text
    if pending:
        words.append((start, end, pending))
    if not words:
        return None

    cues: list[Cue] = []
    for word_start, word_end, text in words:
        if not text:
            continue
        kind = _kind_of(text) or "speech"
        cues.append(Cue(word_start, max(word_end, word_start + 0.05), text, kind))
    return cues


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


TAG_TOKEN_RE = re.compile(r"^\s*\[(music|laughter|applause|cheering|inaudible|silence)\]\s*", re.I)


def _leading_tags(text: str) -> tuple[list[str], str]:
    tags: list[str] = []
    while True:
        match = TAG_TOKEN_RE.match(text)
        if not match:
            break
        tags.append(match.group(1).lower())
        text = text[match.end() :]
    return tags, text


def _content_line(lines: list[str]) -> str:
    """YouTube blocks are [carry-over line, new line]. Only the new one is new."""
    for line in reversed(lines):
        if _clean(line):
            return line
    return ""


def parse_vtt(path: str | Path) -> list[Cue]:
    raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    cues: list[Cue] = []
    seen: set[tuple[int, str]] = set()

    # Each real block repeats the previous line as context, and every block is
    # echoed again as a 0.01s duplicate. Without this, speech density and hook
    # counts inflate roughly 3x.
    recent = ""
    last_key = ""

    for block_start, block_end, lines in _blocks(raw):
        content = _content_line(lines)
        if not content:
            continue

        tags, rest = _leading_tags(content)
        for tag in tags:
            cues.append(
                Cue(block_start, block_start + 0.6, f"[{tag}]", NON_SPEECH_KINDS.get(tag, "noise"))
            )
        if not _clean(rest):
            continue

        word_cues = _word_cues(block_start, block_end, [rest])
        if word_cues:
            for cue in word_cues:
                cue_key = _key(cue.text)
                if not cue_key:
                    continue
                dedupe = (round(cue.start * 10), cue_key)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                cues.append(cue)
                recent = (recent + cue_key)[-800:]
                last_key = cue_key
            continue

        text = _clean(rest)
        key = _key(text)
        if not key:
            continue
        if key == last_key:
            continue
        if len(key) >= 8 and key in recent:
            continue
        cues.append(Cue(block_start, max(block_end, block_start + 0.2), text, "speech"))
        recent = (recent + key)[-800:]
        last_key = key

    cues.sort(key=lambda c: c.start)
    return _merge(cues)


def _merge(cues: list[Cue]) -> list[Cue]:
    """Glue word-level cues into readable phrases for subtitles and scoring."""
    merged: list[Cue] = []
    buffer: list[Cue] = []

    def flush() -> None:
        if not buffer:
            return
        text = " ".join(c.text for c in buffer if c.text).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            merged.append(
                Cue(
                    buffer[0].start,
                    max(buffer[-1].end, buffer[0].start + 0.2),
                    text,
                    buffer[0].kind if all(c.kind == buffer[0].kind for c in buffer) else "speech",
                )
            )
        buffer.clear()

    for cue in cues:
        if cue.kind != "speech":
            flush()
            merged.append(cue)
            continue
        if buffer:
            span = cue.end - buffer[0].start
            punctuation = buffer[-1].text.endswith((".", "?", "!", ","))
            if span > MAX_CUE_SEC or (punctuation and span > 1.5):
                flush()
        buffer.append(cue)
    flush()
    return merged


def transcript_text(cues: list[Cue]) -> str:
    return " ".join(c.text for c in cues if c.kind == "speech")


def cues_between(cues: list[Cue], start: float, end: float) -> list[Cue]:
    return [c for c in cues if c.end > start and c.start < end]
