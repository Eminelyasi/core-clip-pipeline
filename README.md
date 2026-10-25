# CORE clip pipeline

A daily, unattended job that turns CORE's long-form uploads into 10 ready-to-post
vertical clips: discovery -> virality scoring -> cut -> 9:16 render -> captions.

No paid APIs, no manual editing, no ML keys. Runs free on GitHub Actions.

**First time here? Follow [SETUP.md](SETUP.md) — step by step, with exact commands.**

```
YouTube channel ──> rank new uploads ──> download ──> auto-captions
                                                          │
                                          score 60s windows (energy / hooks /
                                          laughter / speech density)
                                                          │
                             pick best non-overlapping moments per video
                                                          │
              ffmpeg: cut + blur-pad 1080x1920 + burned captions + loudnorm
                                                          │
                        output/YYYY-MM-DD/clips/*.mp4 + captions/*.txt
                                     + manifest.json + UPLOAD.txt
```

## What lands in your folder each morning

```
output/2026-10-26/
├─ clips/
│  ├─ 01_core_Tq_SMdULMsg_0124.mp4     # 1080x1920, exactly 60s
│  └─ ...
├─ captions/
│  ├─ 01_core_Tq_SMdULMsg_0124.tiktok.txt
│  ├─ 01_core_Tq_SMdULMsg_0124.youtube_shorts.txt
│  └─ 01_core_Tq_SMdULMsg_0124.instagram_reels.txt
├─ manifest.json      # source, timestamp, score, features, captions
└─ UPLOAD.txt         # clips sorted by predicted score
```

Upload order = `UPLOAD.txt` order. Highest predicted score first.

## Quick start (local)

```powershell
# 1. dependencies
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. ffmpeg (pick one)
winget install --id Gyan.FFmpeg -e
#   or download a build and set FFMPEG_BIN / FFPROBE_BIN to the .exe paths

# 3. sanity check - config, ffmpeg, yt-dlp, YouTube reachability
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -m clipforge doctor

# 4. see what it would cut (no downloads)
.\.venv\Scripts\python.exe -m clipforge discover

# 5. real run
.\.venv\Scripts\python.exe -m clipforge run
```

Offline check of the whole cut/render path, no YouTube involved:

```powershell
.\.venv\Scripts\python.exe tests\smoke_test.py
```

## Deploy so it runs while you're away

The live setup is the **self-hosted runner on this PC** (see
[SETUP.md → Plan B](SETUP.md#plan-b-self-hosted-runner--this-is-the-live-setup)):
YouTube blocks GitHub-hosted machines, so the job runs on your own connection.

If you ever rebuild from scratch:

1. Create a GitHub repo and push this folder.
2. Repo **Settings -> Actions -> General -> Workflow permissions** -> *Read and write*.
   That lets the job publish a release and save its dedupe state.
3. Register a self-hosted runner on the PC (see SETUP.md Plan B).
4. Test it: **Actions -> Daily clips -> Run workflow** with `dry_run` checked.
   Then run it unchecked for a real batch.

It then runs every day at 13:00 UTC (`0 13 * * *` in
`.github/workflows/daily-clips.yml`). Change that line to your posting time.

Each run publishes:
- an **artifact** with everything (3-day retention), and
- a **release** with permanent download links (`clips-<run number>`).

Then you download, and post. ~10 minutes of work instead of hours.

### Why the cookies secret matters

YouTube throttles/blocks datacenter IPs with *"Sign in to confirm you're not a bot."*
If a cloud run fails at discovery, this is why. Fix: export cookies from a browser
logged into YouTube (any "Get cookies.txt LOCALLY" extension), then:

```powershell
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("cookies.txt"))
$b64 | Set-Clipboard      # paste into the YT_COOKIES_B64 secret
```

If it still fails, run the job on a **self-hosted runner** (your own PC) instead of
`ubuntu-latest` — same workflow, no IP reputation problem.

## Adding sources

Edit `config.yaml`:

```yaml
sources:
  - handle: "UClK4S3kGZN8YTiWA1MRaH0g"   # channel ID, not @handle
    name: "JasonTheWeen Live"
    rights_confirmed: true     # only after you've confirmed they're fine with it
    max_candidates: 1
```

**Use the channel ID, not the handle, when you can.** Handles can point at an empty
legacy channel with the same name - that is exactly what happened with CORE. A channel
ID is the `UC...` segment in `youtube.com/channel/UC...`. If `discover` says *"this
channel does not have a videos tab"*, you have the wrong channel, not a broken tool.

`rights_confirmed: false` means the pipeline ignores that channel entirely. That flag is
the gate — nothing gets clipped from an unconfirmed source.

### Source volume

Eight verified channels are configured, giving ~12 fresh candidates per day (enough for
24 clips at 2 per video, against a quota of 10):

| Source | Subscribers | Uploads | Clips/day |
| --- | --- | --- | --- |
| CORE `UC6H8_DvqnEp1tYEp10GzH0g` | 1.31M | 32–50 min | 2 |
| Stable Ronaldo `UCLldJScHWCtsFlfanYXS8nQ` | 1.45M | 32–37 min | 2 |
| JasonTheWeen `UCxoKaKMzulp6sfQGOhNJnzQ` | 1.26M | 24–110 min | 2 |
| Marlon `UC7RNjS5nGShmMSuMcVgUyjQ` | 1.64M | 18–58 min | 2 |
| Lacy `UCeoZXkaJDtdTHafgoZeDcAA` | 975k | 23–45 min | 1 |
| Silky `UCsx2dUKBb5vAYuXq-bqlfuQ` | 394k | 11–45 min | 1 |
| Adapt `UCi87kxosIT3wrnEA-mv4jWA` | 5.92M | 15–68 min | 1 |
| CORE BOYS `UCBp2TJRE8IY9Iv-vuGLOsag` | 6.2k views | 33–43 min | 1 |

The daily plan **round-robins across channels**, so one high-volume channel cannot eat
the whole quota — the batch stays varied.

Secondary channels (stream VODs, largely redundant with the mains) are commented out at
the bottom of `config.yaml`. `state/processed.json` tracks exactly which moments are
spent, and `run` tells you when it comes up short.

## Tuning

| Knob | What it does |
| --- | --- |
| `targets.clips_per_day` | Daily quota. 10 is realistic for one active channel. |
| `targets.clip_length_sec` | 60 by default. 30-45 often retains better on TikTok. |
| `targets.min_score` | Raise to 0.4 for stricter picks, lower if you keep falling short. |
| `targets.max_clips_per_source_video` | 2 stops 10 near-identical clips from one stream. |
| `targets.max_videos_downloaded` | Bandwidth cap per run. |
| `analysis.weights` | Rebalance the four scoring signals. |
| `render.layout` | `blur_pad` (default), `center_crop`, `fit`. |
| `render.caption_margin_v` | Raise if captions sit under the TikTok UI. |

**How the score works.** Each 60s window gets four normalized signals:

- **energy** (0.35) - loudness peaks: shouting, laughing, hype moments. Peak weighted
  above average, because the payoff line is what gets clipped.
- **hooks** (0.30) - "no way", "bro what", "chat look", "wait what" from the transcript.
- **laughs** (0.20) - `[Laughter]` markers in YouTube's auto-captions.
- **speech** (0.15) - sustained talking. Windows with dead air are penalized hard.

Clips that fail `min_score` are dropped rather than padded out with filler.

## Reality check

- **It predicts, it does not know.** These are retention-flavoured heuristics, not a
  guarantee of virality. The real feedback loop: post, note views, and if a certain
  signal keeps winning, bump its weight in `analysis.weights`.
- **Titles are raw speech.** They come from auto-captions, so skim them before posting.
  There is a small blocklist filter, not a guarantee.
- **Content ID.** Reposting CORE footage may route monetization to them on YouTube and
  can mute/demonetize a Reel. Clipping pages accept this; be aware before you scale.
- **Volume.** See *Source volume* above: 10 clips/day needs roughly 5+ new source videos
  a week, or it runs out of material.
- **Captions.** Free YouTube auto-captions are good enough to find moments, not perfect
  as burned text. Set `analysis.whisper_fallback: true` and `pip install faster-whisper`
  if you want accurate local transcription at the cost of compute.
- **Nothing here posts for you.** That was the deal: files ready, you upload. Auto-posting
  to TikTok/IG needs third-party API access (Postiz, Blotato, etc.).

## Layout

```
config.yaml                     all tuning
state/processed.json            dedupe memory (committed by the workflow)
src/clipforge/
  settings.py                   typed config
  yt.py                         yt-dlp: list, download, captions
  transcript.py                 VTT -> timed cues
  score.py                      window scoring + selection
  render.py                     ffmpeg energy, ASS captions, vertical render
  caption.py                    per-platform captions + hashtags
  state.py                      dedupe memory
  cli.py                        run / discover / doctor
tests/smoke_test.py             offline end-to-end test
.github/workflows/daily-clips.yml
```
