"""Score 60-second windows so the best moments get cut first.

No ML, no paid API. Four signals that correlate with clip performance:
  energy  - loudness peaks (shouting, laughing, hype)
  speech  - sustained talking (dead air kills retention)
  hooks   - "no way", "bro what", "chat look at this"
  laughs  - [Laughter] markers in the auto-captions
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .settings import Analysis, Targets
from .transcript import Cue

STRONG_HOOKS = (
    "no way",
    "oh my god",
    "what the",
    "are you serious",
    "i can't believe",
    "wait what",
    "bro what",
    "what happened",
    "it's over",
    "we're cooked",
    "let's go",
    "that's crazy",
    "holy",
    "look at this",
    "watch this",
    "chat look",
    "you won't believe",
    "is that real",
    "he actually",
    "she actually",
)

WEAK_HOOKS = (
    "bro",
    "chat",
    "dude",
    "wait",
    "yo",
    "crazy",
    "insane",
    "literally",
    "actually",
    "never",
    "best",
    "worst",
    "why",
)

HOOK_STRONG_WEIGHT = 1.0
HOOK_WEAK_WEIGHT = 0.25


@dataclass
class Window:
    start: float
    end: float
    score: float = 0.0
    features: dict[str, float] = field(default_factory=dict)
    text: str = ""
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return [0.0 for _ in values]
    return [(v - low) / (high - low) for v in values]


def _energy_stats(energy: np.ndarray, start: float, end: float) -> tuple[float, float]:
    """Mean and peak level (0..1) inside a second-resolution energy curve."""
    if energy.size == 0:
        return 0.0, 0.0
    lo = max(0, int(start))
    hi = min(energy.size, max(lo + 1, int(round(end))))
    segment = energy[lo:hi]
    if segment.size == 0:
        return 0.0, 0.0
    return float(segment.mean()), float(segment.max())


def _count_hooks(text: str) -> tuple[int, int]:
    lowered = f" {text.lower()} "
    strong = sum(lowered.count(hook) for hook in STRONG_HOOKS)
    weak = sum(lowered.count(hook) for hook in WEAK_HOOKS)
    return strong, weak


def score_windows(
    duration: float,
    cues: list[Cue],
    energy: np.ndarray,
    targets: Targets,
    analysis: Analysis,
    hop: float = 5.0,
) -> list[Window]:
    clip_len = float(targets.clip_length_sec)
    if duration <= clip_len:
        candidates = [Window(0.0, min(duration, clip_len))]
    else:
        starts = np.arange(0.0, duration - clip_len + 0.001, hop)
        candidates = [Window(float(s), float(s) + clip_len) for s in starts]

    speech_cues = [c for c in cues if c.kind == "speech"]
    laugh_cues = [c for c in cues if c.kind == "laugh"]

    raw_energy: list[float] = []
    raw_speech: list[float] = []
    raw_hooks: list[float] = []
    raw_laughs: list[float] = []

    for window in candidates:
        inside = [c for c in speech_cues if c.end > window.start and c.start < window.end]
        text = " ".join(c.text for c in inside)
        chars = sum(len(c.text) for c in inside)
        seconds = max(1.0, window.duration)
        strong, weak = _count_hooks(text)
        laughs = sum(1 for c in laugh_cues if c.end > window.start and c.start < window.end)

        mean_energy, peak_energy = _energy_stats(energy, window.start, window.end)
        # peak matters more than average: that is where the payoff line lands
        energy_score = 0.65 * peak_energy + 0.35 * mean_energy

        window.text = text
        raw_energy.append(energy_score)
        raw_speech.append(chars / seconds)
        raw_hooks.append(strong * HOOK_STRONG_WEIGHT + weak * HOOK_WEAK_WEIGHT)
        raw_laughs.append(float(laughs))

    norm = {
        "energy": _minmax(raw_energy),
        "speech": _minmax(raw_speech),
        "hooks": _minmax(raw_hooks),
        "laughs": _minmax(raw_laughs),
    }

    weights = analysis.weights
    total_weight = sum(weights.get(k, 0.0) for k in norm) or 1.0

    for i, window in enumerate(candidates):
        features = {key: norm[key][i] for key in norm}
        score = sum(features[key] * weights.get(key, 0.0) for key in features) / total_weight

        # dead air is unwatchable no matter how loud it is
        if raw_speech[i] < 0.8:
            score *= 0.35
        # a window with no talking at all is almost never a clip
        if not window.text.strip():
            score *= 0.2

        window.features = {
            "energy": round(features["energy"], 3),
            "speech": round(features["speech"], 3),
            "hooks": round(features["hooks"], 3),
            "laughs": round(features["laughs"], 3),
            "chars_per_sec": round(raw_speech[i], 2),
            "hook_hits": int(round(raw_hooks[i] / HOOK_WEAK_WEIGHT)),
        }
        window.score = round(float(score), 4)
        window.reason = _reason(window.features)

    return candidates


def _reason(features: dict[str, float]) -> str:
    parts = []
    if features.get("energy", 0) > 0.6:
        parts.append("loud peak")
    if features.get("hooks", 0) > 0.5:
        parts.append("hook line")
    if features.get("laughs", 0) > 0.5:
        parts.append("laughter")
    if features.get("speech", 0) > 0.6:
        parts.append("dense talk")
    return ", ".join(parts) or "flat"


def pick_windows(windows: list[Window], count: int, min_gap: float, min_score: float = 0.0) -> list[Window]:
    """Greedy non-overlapping selection, best score first."""
    chosen: list[Window] = []
    for window in sorted(windows, key=lambda w: w.score, reverse=True):
        if window.score < min_score:
            break
        if any(abs(window.start - other.start) < min_gap for other in chosen):
            continue
        chosen.append(window)
        if len(chosen) >= count:
            break
    return sorted(chosen, key=lambda w: w.start)


def trim_to_sentence(window: Window, cues: list[Cue], clip_len: float, duration: float) -> Window:
    """Nudge the start so the clip opens on a phrase boundary, not mid-word."""
    tolerance = 4.0
    for cue in cues:
        if cue.kind != "speech":
            continue
        if window.start - tolerance <= cue.start <= window.start + tolerance:
            start = max(0.0, min(cue.start, duration - clip_len))
            return Window(start, start + clip_len, window.score, dict(window.features), window.text, window.reason)
    return window
