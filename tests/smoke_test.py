"""Offline smoke test: no YouTube needed.

    set PYTHONPATH=src && python tests/smoke_test.py

Builds a fake 3-minute source video with ffmpeg, runs the real scoring and
render path, and asserts the output is a valid 1080x1920 / 60s clip.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clipforge.caption import build_caption
from clipforge.render import audio_energy, ffmpeg_bin, probe, render_clip
from clipforge.score import pick_windows, score_windows
from clipforge.settings import Analysis, Render, Targets
from clipforge.transcript import parse_vtt

WORDS = (
    "okay so we are back in the house bro no way what the are you serious "
    "chat look at this wait what he actually did it that is insane holy "
    "we are cooked let's go i cannot believe that just happened "
)

VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:04.000
okay so we are back in the house

00:00:04.000 --> 00:00:08.000
[Laughter]

00:00:08.000 --> 00:00:12.000
bro no way what the

00:00:12.000 --> 00:00:16.000
chat look at this

00:00:16.000 --> 00:00:20.000
[Laughter]
"""


def build_source(path: Path, seconds: int = 180) -> Path:
    volume = "volume='0.12+0.85*(lt(mod(t\\,40)\\,8))':eval=frame"
    cmd = [
        ffmpeg_bin(), "-y", "-nostdin", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=320:duration={seconds}",
        "-af", volume,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"could not build source video:\n{proc.stderr[:800]}")
    return path


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="clipforge-smoke-"))
    source = build_source(work / "source.mp4")
    print(f"source: {source}")

    vtt_path = work / "captions.en.vtt"
    vtt_path.write_text(VTT, encoding="utf-8")
    cues = parse_vtt(vtt_path)
    print(f"cues parsed: {len(cues)} -> {[c.text for c in cues][:4]}")
    assert any(c.kind == "laugh" for c in cues), "laughter markers not detected"
    assert any("no way" in c.text for c in cues), "speech cues missing"

    targets = Targets(clips_per_day=3, clip_length_sec=20, min_score=0.0, min_gap_sec=25)
    analysis = Analysis()
    render = Render(caption_size=64, caption_margin_v=300)

    info = probe(source)
    energy = audio_energy(source)
    print(f"duration {info['duration']:.1f}s | energy samples {energy.size} | "
          f"min {energy.min():.2f} max {energy.max():.2f}")
    assert energy.size > 100, "energy curve too short"
    assert energy.max() > energy.min(), "energy curve is flat - loudness analysis broken"

    windows = score_windows(info["duration"], cues, energy, targets, analysis)
    picked = pick_windows(windows, 1, targets.min_gap_sec, targets.min_score)
    assert picked, "no window selected"
    best = max(windows, key=lambda w: w.score)
    print(f"windows {len(windows)} | best score {best.score:.3f} @ {best.start:.0f}s ({best.reason})")

    out = render_clip(source, work / "clips" / "test.mp4", best.start, 20, render, cues, work_dir=work / "tmp")
    clip_info = probe(out)
    size_mb = out.stat().st_size / 1_048_576

    print(f"clip: {out.name} | {clip_info['width']}x{clip_info['height']} | "
          f"{clip_info['duration']:.2f}s | {size_mb:.2f} MB | audio={clip_info['has_audio']}")
    assert (clip_info["width"], clip_info["height"]) == (1080, 1920), "wrong dimensions"
    assert 18.5 <= clip_info["duration"] <= 21.5, "wrong duration"
    assert clip_info["has_audio"], "clip has no audio"
    assert size_mb > 0.2, "clip looks empty"

    ass = list((work / "tmp").glob("*.ass"))
    assert ass, "no caption file written"
    assert "Dialogue:" in ass[0].read_text(encoding="utf-8"), "captions are empty"

    caption = build_caption("CORE", "test video", cues, best.start, best.start + 20, best.features)
    assert caption.title and caption.platform["tiktok.txt"].count("#") >= 4, "captions not generated"
    print(f"caption: {caption.title}")
    print(f"hashtags: {caption.platform['tiktok.txt'].splitlines()[-1][:120]}")

    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
