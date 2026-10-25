# Setup — exact steps

Do these in order. Each step says what to type, what you should see, and what to do
if it goes wrong.

Total time: ~20 minutes. After that it runs by itself every morning.

> **Status: setup is finished.** The runner, ffmpeg, repo, secrets and workflow are all
> installed and a full batch has run successfully. You can skip to
> **[Your part, every day](#your-part-every-day-10-minutes)** — the steps below are kept
> for reference and for rebuilding on another PC.

Paths assume the project stays where it is:

```
C:\Users\Amin\OneDrive\Documents\Default Project\core-clip-pipeline
```

---

## Step 0 — Check your prerequisites

You need a **GitHub account** (free) and **Windows Terminal / PowerShell**.

Git is not installed on this PC yet. That is step 1.

> Why GitHub at all: your PC has to be on for a local job to run at 6am. GitHub's
> servers are always on, and a **public** repo gets unlimited free Actions minutes.

---

## Step 1 — Install Git

In PowerShell:

```powershell
winget install --id Git.Git -e --source winget
```

**You should see:** `Successfully installed`.

**If `winget` is not recognised:** install "App Installer" from the Microsoft Store,
then run the command again.

**Now close the terminal and open a new one** (Git won't be on PATH in the old one),
then confirm:

```powershell
git --version
```

**You should see:** `git version 2.x.x`

---

## Step 2 — Create the GitHub repository

1. Go to **https://github.com/new**
2. **Repository name:** `core-clip-pipeline`
3. **Visibility:** choose **Public** (see note)
4. Do **not** tick "Add a README file"
5. Click **Create repository**

You land on a page titled *"Quick setup"*. Keep it open — you need the URL from it.

> **Public vs Private.** Public gives you unlimited free Actions minutes and free
> artifact storage. Private gives 2,000 minutes + 500 MB/month on the free plan; your
> daily clips are ~250 MB, so a private repo will hit the storage cap in about two days.
> The repo contains no secrets (cookies live in GitHub Secrets, never in the code), so
> Public is the practical choice. If you must use Private, change
> `retention-days: 3` to `1` in `.github/workflows/daily-clips.yml`.

---

## Step 3 — Push the project

> **Already done** (2026-10-25). The project is pushed to
> `https://github.com/Eminelyasi/core-clip-pipeline` and the workflow is active.
> Skip to **Step 4**. Keep this section only for rebuilding from scratch.

Copy this block, replace `YOURUSERNAME` with your GitHub username, and run it:

```powershell
cd "C:\Users\Amin\OneDrive\Documents\Default Project\core-clip-pipeline"
git init
git add .
git commit -m "Daily CORE clip pipeline"
git branch -M main
git remote add origin https://github.com/YOURUSERNAME/core-clip-pipeline.git
git push -u origin main
```

**You should see:** a list of files being written, then `branch 'main' set up to track
'origin/main'`.

A browser window may pop up asking you to sign in to GitHub — approve it.

**If push is rejected with "Authentication failed":** GitHub no longer accepts account
passwords. Install the helper and retry the `git push` line:

```powershell
winget install --id GitHub.cli -e
gh auth login
```

Choose **GitHub.com → HTTPS → Login with a web browser**.

**Verify:** refresh your repo page. You should see `config.yaml`, `src/`, `README.md`.

---

## Step 4 — Let the job write to your repo

The workflow publishes a release and saves its dedupe memory. It needs permission.

1. In your repo: **Settings** (top bar)
2. Left sidebar: **Actions** → **General**
3. Scroll to **Workflow permissions**
4. Select **Read and write permissions**
5. Click **Save**

**You should see:** a green "Saved" toast.

---

## Step 5 — Add YouTube cookies (the one fragile part)

GitHub's servers are on datacenter IPs. YouTube often answers them with
*"Sign in to confirm you're not a bot"* instead of the channel page. Cookies fix it.

1. In Chrome/Edge, install the extension **"Get cookies.txt LOCALLY"**
2. Go to **https://www.youtube.com** and make sure you are **signed in**
3. Click the extension icon → **Export** → save the file as `cookies.txt`
   (it usually lands in `Downloads`)
4. In PowerShell, validate + encode it and copy the secret value to your clipboard:

```powershell
cd "C:\Users\Amin\OneDrive\Documents\Default Project\core-clip-pipeline"
.\tools\make_cookies_secret.ps1
```

**You should see** `SUCCESS: value is on your clipboard.` plus a line like
`youtube rows: 412` and `base64 : 41230 characters (ok)`.

If that script says `FAIL`, fix the file first — the workflow will fail the same way.
Never paste a value you have not produced with this script.

5. In your repo: **Settings → Secrets and variables → Actions**
6. Click **New repository secret**
7. **Name:** `YT_COOKIES_B64`
8. **Secret:** paste (Ctrl+V)
9. Click **Add secret**

**Do not** upload `cookies.txt` itself — it is already gitignored, and it contains your
YouTube session.

> Cookies expire every few weeks. When runs start failing with bot checks, redo steps
> 1–9. If it still fails, see "Plan B: self-hosted runner" at the bottom.

---

## Step 6 — Test run (no downloads)

1. Open your repo → **Actions** tab
2. Left sidebar: **Daily clips**
3. Right side: **Run workflow** ▾
4. Tick **Discovery only (no downloads)**
5. Click **Run workflow**
6. Wait ~1 minute, refresh, click the run → **clip** job

**You should see** in the log, near the end:

```
[CORE] 15 listed, 15 usable, taking 2 candidate(s)
...
12 candidate(s)
```

That means YouTube is reachable and cookies work. If instead you see
`Sign in to confirm you're not a bot` → cookies are missing/expired, redo Step 5.

---

## Step 7 — First real run

Same place: **Actions → Daily clips → Run workflow**, but leave the checkbox **unticked**.

This takes 15–25 minutes (downloading ~6 videos and rendering 10 clips).

**When it finishes**, you should see under the run:

- **Artifacts:** `clips-1` — a single zip with everything (kept 3 days)
- A new **Release** tagged `clips-1` — permanent download links

**If it says "0 clips produced":** open the log and search for `min_score`. If a video
scored below `0.30`, lower `min_score` to `0.2` in `config.yaml`, commit, and push.

---

## Step 8 — Get the clips onto your phone

**Option A (fastest, one zip):**
1. On the run page, scroll to **Artifacts** → click `clips-N` to download the zip
2. Extract it
3. Drag the `clips` and `captions` folders into your **OneDrive** — they appear on your
   phone in the OneDrive app within a minute

**Option B (direct links):** repo → **Releases** → `clips-N` → download individual files.

Each clip has a matching caption file:

```
clips/01_core_H438v5IcxFs_1204.mp4
captions/01_core_H438v5IcxFs_1204.tiktok.txt
captions/01_core_H438v5IcxFs_1204.youtube_shorts.txt
captions/01_core_H438v5IcxFs_1204.instagram_reels.txt
```

`UPLOAD.txt` lists them best-first.

---

## Step 9 — Post them (this part is you, ~10 min)

For each clip, best-first from `UPLOAD.txt`:

**TikTok**
1. Open TikTok app → **+** → **Upload**
2. Pick the clip
3. Paste the contents of `.tiktok.txt` as the caption
4. Leave the sound alone (the audio is already in the video)
5. Post

**YouTube Shorts**
1. YouTube app → **+** → **Upload a video**
2. Pick the clip, then paste `.youtube_shorts.txt` as the title/description
3. Set **Made for kids: No**, then Post

**Instagram Reels**
1. Instagram app → **+** → **Reel**
2. Pick the clip, paste `.instagram_reels.txt`
3. Share to Reels (not Stories)

Post the same clip to all three — they don't penalize cross-posting.

---

## Step 10 — After about a week

Open `config.yaml` and adjust based on what actually performed:

| If this happens | Change this |
| --- | --- |
| Fewer than 10 clips most days | `min_score: 0.20`, or raise `max_candidates` |
| Lots of clips but low views | raise `min_score` to `0.4` — be pickier |
| Clips feel repetitive | lower `max_clips_per_source_video` to `1` |
| Runs take too long | lower `max_videos_downloaded` to `4` |
| Captions hidden behind TikTok UI | raise `caption_margin_v` to `500` |
| Want 30s clips instead of 60s | `clip_length_sec: 30` |

Commit and push after any change; the next run picks it up:

```powershell
cd "C:\Users\Amin\OneDrive\Documents\Default Project\core-clip-pipeline"
git add config.yaml
git commit -m "tune scoring"
git push
```

---

## Optional — run it locally instead

The `.venv` is already set up in the project. You only need ffmpeg on PATH:

```powershell
winget install --id Gyan.FFmpeg -e
```

Then in a **new** terminal:

```powershell
cd "C:\Users\Amin\OneDrive\Documents\Default Project\core-clip-pipeline"
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -m clipforge doctor
.\.venv\Scripts\python.exe -m clipforge run --clips 2 --max-videos 1
```

`--clips 2 --max-videos 1` produces 2 clips from 1 video so you can check the look
without a long run. Output lands in `output\<today>\`.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Sign in to confirm you're not a bot` on **download** (discovery still works) | YouTube blocks GitHub's datacenter IPs; cookies alone no longer defeat it. The code now retries with the `tv_embedded` client first | If the run still fails, use **Plan B** below |
| `Sign in to confirm you're not a bot` on **discovery** | No/expired cookies | Step 5, then re-run |
| `This channel does not have a videos tab` | Wrong channel ID | Recheck the `UC...` in `config.yaml` |
| `ffmpeg: not found` locally | Not on PATH | `winget install Gyan.FFmpeg`, new terminal |
| `0 clips produced` | Nothing above `min_score` | Lower `min_score` to `0.2` |
| Run fails at "Persist dedupe state" | Repo not writable | Redo Step 4, or Settings → Branches → unprotect `main` |
| Private repo, actions disabled | Out of minutes/storage | Make repo public, or set `retention-days: 1` |
| Clips look zoomed | `blur_pad` crops the sides | Set `render.layout: fit` (letterboxed) |
| `fatal: not a git repository` | Ran git in wrong folder | `cd` into `core-clip-pipeline` first |

### Plan B: self-hosted runner — **this is the live setup**

You do not need to do anything here. This is what is already running on your PC.

| Thing | Where |
| --- | --- |
| Actions runner `amin-desktop` | `C:\Users\Amin\actions-runner` |
| Auto-start at logon | `…\Startup\GitHubActionsRunner.vbs` |
| ffmpeg + ffprobe | `C:\Users\Amin\clipforge\ffmpeg\bin` |
| Workflow target | `runs-on: [self-hosted, Windows, X64]` |

Why: YouTube blocks GitHub's datacenter IPs with *"Sign in to confirm you're not a bot"*.
A runner on your home connection uses your normal IP, so downloads work.

**The three rules that keep it working**

1. The PC must be **on and logged in** around 13:00 UTC. The runner starts by itself at
   logon, so a restart is harmless — a powered-off PC is not.
2. If the PC is off at 13:00, GitHub keeps the job **queued** and it starts on its own
   when the PC comes back. Nothing is lost, it just runs later that day.
3. Set **Power & sleep → Sleep: Never** while plugged in, or the PC can fall asleep
   mid-render.

**Is it alive?** One line back means healthy:

```powershell
Get-Process Runner.Listener
```

If that prints nothing, restart it:

```powershell
wscript.exe "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\GitHubActionsRunner.vbs"
```

Re-register only if you ever delete the runner on GitHub
(**Settings → Actions → Runners → New self-hosted runner → Windows x64** gives the token):

```powershell
cd C:\Users\Amin\actions-runner
.\config.cmd --unattended --url https://github.com/Eminelyasi/core-clip-pipeline --token <TOKEN> --name amin-desktop --labels clipforge --replace
.\run.cmd
```

---

## What runs every morning

```
13:00 UTC  →  Actions wakes the job on your PC (self-hosted runner)
  → scans 8 channels, ranks newest uploads by views + freshness
  → downloads up to 6 videos, grabs auto-captions
  → scores every 60s window (loudness, hooks, laughter, speech)
  → cuts the best 10 non-repeating moments, renders 1080x1920
  → burns captions, normalises audio to -14 LUFS
  → writes per-platform captions + UPLOAD.txt
  → publishes release (`clips-N`) + artifact, saves dedupe memory
```

13:00 UTC is 09:00 in New York in summer, 08:00 in winter (cron does not follow
daylight saving). Change the `cron:` line in `.github/workflows/daily-clips.yml` to move it.

---

## Your part, every day (~10 minutes)

1. Open **https://github.com/Eminelyasi/core-clip-pipeline/actions** and click the newest
   **Daily clips** run (green check).
2. Scroll to **Artifacts** and click **`clips-N`** — one zip with everything (~260 MB).
3. Unzip it. You get `2026-10-26\clips\` (10 mp4) and `2026-10-26\captions\` and
   `UPLOAD.txt`.
4. Open **`UPLOAD.txt`**. Clips are listed best-first with score, hook and source.
5. For each clip: upload the mp4, open its `*.tiktok.txt` (or `*.youtube_shorts.txt`,
   `*.instagram_reels.txt`) and paste the text as the caption. Post.

The **Releases** page keeps a permanent copy if the artifact has expired:
https://github.com/Eminelyasi/core-clip-pipeline/releases

Nothing posts automatically — that was the deal. Upload is yours.

