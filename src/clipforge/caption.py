"""Per-platform caption + hashtag generation (no paid API)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .transcript import Cue, cues_between

BASE_TAGS = ["fyp", "foryou", "clips", "streamer", "funny", "gaming"]

SOURCE_TAGS = {
    "core": ["core", "coreboys", "thecoreboys", "stableronaldo", "jasontheween", "lacy", "silky", "adapt"],
}

HYPE_TAGS = {
    "laugh": ["lmao", "funny", "comedy"],
    "shout": ["reaction", "insane"],
    "hook": ["viral", "waitforit"],
}

# Auto-captions are unfiltered speech. This is a safety net for titles only -
# not a guarantee. Skim titles before you post.
UNSAFE_TERMS = (
    "faggot",
    "nigger",
    "retard",
    "rape",
    "kys",
    "kill yourself",
    "chink",
    "tranny",
    "spic",
    "kike",
    "fat pig",
)


def _is_safe(text: str) -> bool:
    lowered = text.lower()
    return not any(term in lowered for term in UNSAFE_TERMS)


@dataclass
class ClipCaption:
    title: str
    hook: str
    platform: dict[str, str]

    def as_files(self) -> dict[str, str]:
        return self.platform


# Auto-captions arrive lowercase, unpunctuated and stuttery ("so just , so just").
# These cleanups only touch the caption text, never the audio or the burned subs.
_FILLER_PREFIX = re.compile(
    r"^(?:okay|ok|yeah|yea|yep|uh|uhh|um|umm|erm|hmm|so|and|but|like|well|just|"
    r"i mean|you know|to be honest|honestly|basically|literally|right)[\s,]+",
    re.IGNORECASE,
)

# YouTube's auto-captions censor swearing as "[ __ ]". It looks like a glitch on screen.
CENSORED = re.compile(r"\[\s*_+\s*\]")


def strip_censored(text: str) -> str:
    text = CENSORED.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip(" ,;:-")


def _words(text: str) -> list[str]:
    return [re.sub(r"[^\w']", "", w).lower() for w in text.split()]


def _collapse_repeats(text: str) -> str:
    """Drop an immediately repeated phrase: "no way no way he did" -> "no way he did"."""
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        collapsed = False
        for size in (4, 3, 2, 1):
            if i + 2 * size > len(words):
                continue
            first, second = _words(" ".join(words[i:i + size])), _words(" ".join(words[i + size:i + 2 * size]))
            if first == second and any(first):
                out.extend(words[i:i + size])
                i += 2 * size
                collapsed = True
                break
        if not collapsed:
            out.append(words[i])
            i += 1
    return " ".join(out)


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = _collapse_repeats(text)
    for _ in range(6):
        stripped = _FILLER_PREFIX.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = text.strip(" ,;:-")
    text = re.sub(r"\bi\b", "I", text)
    return text[:1].upper() + text[1:] if text else text


def _clip_text(cues: list[Cue], start: float, end: float, limit: int = 120) -> str:
    text = " ".join(c.text for c in cues_between(cues, start, end) if c.kind == "speech")
    return _tidy(strip_censored(text))[:limit].strip()


def _pick_hook(text: str) -> str:
    """A real opening line, preferring an actual hook over the shortest phrase."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    # Auto-captions often have no sentence punctuation at all: fall back to clauses.
    if len(sentences) == 1 and len(sentences[0]) > 90:
        sentences = [s.strip() for s in re.split(r",| - | \u2014 ", text) if s.strip()]
    usable = [s for s in sentences if len(s) >= 15 and _is_safe(s)]
    if not usable:
        return ""

    hook_words = ("no way", "what the", "bro what", "wait what", "look at", "chat",
                  "oh my god", "are you serious", "i can't believe", "let's go")
    strong = [s for s in usable if any(word in s.lower() for word in hook_words)]
    pool = strong or usable
    # "He's down there" is a weak title; prefer a clause with something to read.
    substantial = [s for s in pool if 25 <= len(s) <= 90]
    pick = (substantial or pool)[0]
    return pick[:90].rstrip(" .,")


def _tags_for(source_name: str, features: dict[str, float]) -> list[str]:
    tags = list(BASE_TAGS)
    key = source_name.lower().replace(" ", "")
    for name, extra in SOURCE_TAGS.items():
        if name in key:
            tags = extra + tags
            break
    if features.get("laughs", 0) > 0.4:
        tags += HYPE_TAGS["laugh"]
    if features.get("hooks", 0) > 0.4:
        tags += HYPE_TAGS["hook"]
    if features.get("energy", 0) > 0.6:
        tags += HYPE_TAGS["shout"]

    seen: list[str] = []
    for tag in tags:
        cleaned = re.sub(r"[^a-z0-9]", "", tag.lower())
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def build_caption(
    source_name: str,
    video_title: str,
    cues: list[Cue],
    start: float,
    end: float,
    features: dict[str, float],
) -> ClipCaption:
    text = _clip_text(cues, start, end)
    hook = (_pick_hook(text) if text else "") or video_title
    tags = _tags_for(source_name, features)
    tag_line = " ".join(f"#{t}" for t in tags[:12])

    short_title = hook.rstrip(" .")
    if len(short_title) > 90:
        short_title = short_title[:87].rsplit(" ", 1)[0] + "..."

    tiktok = f"{short_title}\n\n{tag_line}"
    shorts = f"{short_title} #Shorts\n\n{tag_line}"
    reels = f"{short_title}\n\n{tag_line} #reels"

    title = short_title if short_title.lower().startswith(source_name.lower()) else f"{source_name}: {short_title}"

    return ClipCaption(
        title=title[:100],
        hook=hook,
        platform={"tiktok.txt": tiktok, "youtube_shorts.txt": shorts, "instagram_reels.txt": reels},
    )
