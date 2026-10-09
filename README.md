# scene-recall

Semantic search over your film library: describe a scene in plain English and get the matching shots back.

The implemented system, architectural boundaries, and deliberately deferred
directions are documented in
[docs/search-architecture.md](docs/search-architecture.md). Historical reasons
for material choices are indexed in
[docs/decisions/](docs/decisions/README.md).

## Evidence and ranking

Search reads per-film evidence ([ADR-0093](docs/decisions/0093-evidence-v2.md)):
open film metadata, synced subtitles, a hosted understanding pass (scenes,
characters, actions, fame and craft per shot), local measurements (camera
motion, subjects, look), hero frames and priors. Everything is a versioned
artifact under `assets_dir/<film_id>/evidence/`; search tables are compiled from
those artifacts and can be rebuilt at any time.

New films get every pass automatically after ingestion (`ingest.evidence`,
hosted passes gated by `ingest.evidence_hosted`). For the library:

```powershell
uv run python -m pipeline.evidence status                      # coverage per pass
uv run python -m pipeline.evidence metadata                    # Wikidata, Wikipedia, Wikiquote, IMDb votes, pageviews
uv run python -m pipeline.evidence subtitles --max-downloads 20  # OpenSubtitles daily quota; synced and validated
uv run python -m pipeline.evidence refresh-dialogue            # re-ingest films whose accepted subtitles are not adopted yet
uv run python -m pipeline.evidence understand --batch run --max-usd 110 [--wave-chunks 300]   # half-price Gemini batch, unattended
uv run python -m pipeline.evidence understand --retry-refused  # recover filter-refused clips in smaller pieces
uv run python -m pipeline.evidence measure                     # local GPU pass (~3-4 min per film)
uv run python -m pipeline.evidence hero
uv run python -m pipeline.evidence moments                     # match-cut GPU pass, every instant at 4 fps (~4 min per film)
uv run python -m pipeline.matching.moments index               # rebuild the library match-cut index afterwards
uv run python -m pipeline.evidence synthesize
uv run python -m pipeline.evidence compile                     # search tables + semantic text views
uv run python -m pipeline.evidence refresh --film "Title"      # every pass for chosen films
uv run python -m pipeline.evidence prune [--apply]             # remove superseded producer profiles
```

Every command accepts `--film` (repeatable: film ID, 8+ character ID prefix or
title substring). Hosted passes need `GEMINI_API_KEY` (understanding) and
`OPENSUBTITLES_API_KEY`, `OPENSUBTITLES_USERNAME`, `OPENSUBTITLES_PASSWORD`
(subtitles) in `.env`. `understand --batch status` lists submitted batch jobs;
`--batch collect` writes their results once finished. New nullable columns in
the compiled tables migrate in place; `compile --rebuild` drops and recompiles
them after an incompatible schema change.

Search ranks by relevance first, then applies a preset: **Balanced** (default)
lets iconic and well-made shots rise a little, **Famous** favours iconic
moments, **Hidden gems** favours well-made shots people rarely see. Shots of
one dramatic scene fold into one card. `retrieval.rerank_shortlist` (default 40)
sets the cross-encoder rerank of the fused shortlist; `0` disables it.

Measure changes against the personal eval set:

```powershell
uv run python -m pipeline.eval.searchset [--preset famous] [--rerank 0]
```

It reports known-item ranks (MRR, hit@1/5/12) and keeps each query's top results
in `pipeline/eval/runs/` for a side-by-side read.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/) (package manager)
- CUDA 12.8 (GPU recommended; CPU falls back automatically)
- [ffmpeg](https://ffmpeg.org/) on your `PATH`
- Node.js 20.9+

## Setup

```bash
# Install Python dependencies
uv sync --dev

# Configure the annotation provider
cp .env.example .env
# Add OPENAI_API_KEY to .env (or GEMINI_API_KEY if using Gemini)
# Evidence v2: GEMINI_API_KEY (understanding) and OPENSUBTITLES_API_KEY,
# OPENSUBTITLES_USERNAME, OPENSUBTITLES_PASSWORD (English subtitles)

# Configure the web frontend
cd web
npm install
cd ..
cp web/.env.local.example web/.env.local
# Edit web/.env.local if your API runs on a non-default port

# Edit config.yaml with your paths and model preferences
```

Verify PyTorch, the selected annotation key, ffmpeg/ffprobe and writable assets:

```bash
uv run python -m pipeline.check_env
```

The check reports the CUDA device when available and accepts CPU fallback.

`paths.state_dir` stores durable user-authored state such as saved scenes. Keep
it outside the replaceable `assets_dir` and include it in normal backups. Older
configs default it to a `state` directory beside `films`. Its
`interactions.sqlite3` is a local taste log of the searches, plays and saves
you make in the web app; scripts and agents calling the API are not recorded,
and nothing ranks with it yet.

The default annotator uses OpenAI `gpt-5.6-luna`. Annotation sends up to three
derived keyframes per shot with response storage disabled. Set
`models.annotator_provider: gemini` and a Gemini model name in `config.yaml`
to use Gemini instead. The OpenAI API project must have active billing or
credits; a ChatGPT subscription does not supply API quota.

## Run

On this Windows workstation, connect the Seagate drive first, then double-click
**Start Scene Recall.cmd** in the repository. It checks the services and opens
the app in your default browser. To start the services without opening a browser:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-scene-recall.ps1
```

The on-demand launcher checks installed dependencies and configured storage,
starts missing services hidden, reuses recognized running services, and checks
API/frontend readiness, worker ownership/heartbeats and the download connection.
It includes qBittorrent, Prowlarr and the separate acquisition monitor when
managed downloads are configured. It stops with an explanation for an unrelated
port owner or unavailable drive; it never kills that process, installs packages
or creates replacement library folders. Re-running the launcher is safe.

Newly launched services write logs under `.tmp/services/`; a previous launch's
logs are retained as `.previous`. Reused services keep their existing log files.
The launcher does not retry failed ingestions automatically. Their retained
sources and job details remain available in **Films** for an explicit retry
after the cause is addressed. Nothing is installed to run at Windows sign-in.

To load new code or configuration, restart the API and both Lab workers.
Running jobs finish first and queued jobs are kept. The web server, downloads
and `pipeline.evidence` runs are left alone:

```powershell
.\scripts\restart-scene-recall.ps1 -DryRun          # show what would happen
.\scripts\restart-scene-recall.ps1 -WaitForJobs     # restart once running jobs finish
.\scripts\restart-scene-recall.ps1 -WaitForJobs -MaintainDatabase   # also drop index rollback history
```

`-MaintainDatabase` runs the index maintenance below while everything is
stopped, and skips it while a `pipeline.evidence` run is active. To free disk
space held by regenerable scratch, run `.\scripts\cleanup-storage.ps1`
(a report) or add `-Apply`. It clears old `.tmp` test runs, evaluation
renders, interrupted proxies, superseded evidence profiles and orphaned Lab
job files. Films, previews, keyframes and current evidence are never touched.

New workers use fixed code for ordinary use. During backend development, pass
`-ReloadWorkers` to enable between-job source reload for newly started workers.
This option does not restart or change already-running workers. Configuration
changes still require restarting the affected API/workers as described below.

For individual services or non-Windows setups:

Start the API, worker launcher, and Next.js server in separate terminals:

```bash
# Terminal 1 — FastAPI backend
uv run uvicorn pipeline.api.main:app --host 127.0.0.1 --port 8000

# Terminal 2 — separate editor and library workers, one launcher
uv run python -m pipeline.lab.worker --reload

# Terminal 3 — Next.js frontend
cd web && npm run dev
```

On Windows PowerShell, use `npm.cmd run dev` if the execution policy blocks
`npm.ps1`.

During development, `--reload` loads changed backend Python code in a fresh
worker between jobs. Each role finishes its own active job before reloading;
the other role continues independently. Omit `--reload` for a
fixed-code worker. Restart both API and worker after configuration changes;
`--reload` watches runtime Python source only and cannot be combined with
`--once`.

The API stores all jobs in `state_dir/lab/lab.sqlite3`. The **editor** worker
handles music generation, analysis, scene search and export using CPU local
models. The **ingest** worker handles film ingestion, evidence backfill and
advanced GPU Match searches. Both run at once using the same configured API key.
Each role processes its own queue serially, with one process owning that role.
The editor searches a stable, complete library while new evidence is published;
newly indexed footage becomes available on a subsequent job. A fresh editor
started mid-publication can briefly wait for a complete searchable index.

While idle, the editor prepares its configured search models in CPU memory so
the first **Find scenes** request usually avoids loading them. Warmup yields to
queued work, stop requests and development reloads between model loads; a load
already in progress finishes first. Missing index readiness is checked again
later, and failed warmup is reported without stopping the worker. `--once` skips
idle preparation. Runtime code changes with `--reload` reset these model caches;
omit `--reload` when running without active backend development.

Manage the workers from another terminal:

```bash
uv run python -m pipeline.lab.worker --status
uv run python -m pipeline.lab.worker --stop
# Stop only the editor after its active job:
uv run python -m pipeline.lab.worker --stop --role editor
# Start that role independently:
uv run python -m pipeline.lab.worker --role editor --reload
```

`--stop` and Ctrl+C finish active jobs before stopping and preserve queued work.
Use the app's Cancel action to cancel a job. `--status` shows current work and
queue counts; queued task details also explain whether their worker is offline
or busy. API restarts preserve jobs. A restarted worker marks only its own
abandoned jobs **interrupted**, without automatically replaying hosted requests.
`--role ingest` runs only library/GPU work. `--role all` retains the old serial
mode; do not run it alongside separate roles. Unqualified `--once` uses serial
mode to process at most one queued job; add `--role` to restrict it.

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Use it from your phone

The phone reaches the app privately over [Tailscale](https://tailscale.com).
Nothing is exposed to the internet.

1. Set `NEXT_PUBLIC_API_URL=/api` in `web/.env.local`. The web server then
   proxies API calls to the local API, so the phone needs only one address. The
   dev server picks up the change by itself.
2. Install Tailscale on this PC and on the phone, and sign in to both with the
   same account.
3. On the PC, run `tailscale serve --bg 3000`. The first time, it may ask you to
   enable HTTPS certificates for your tailnet.
4. On the phone, open the `https://<pc-name>.<tailnet>.ts.net` address that
   `tailscale serve status` shows.

Keep the PC awake while you use it. `tailscale serve reset` stops sharing. Don't
use `tailscale funnel`: it publishes the app to the internet with no sign-in.

For managed film downloads, also run qBittorrent and the independent acquisition
monitor. See [Managed downloads](#managed-downloads) for connections and commands.

The API accepts browser requests from `http://localhost:3000` and
`http://127.0.0.1:3000` by default. If the frontend runs elsewhere, set the
comma-separated `SCENE_RECALL_ALLOWED_ORIGINS` value in `.env`.

## Search and Lab

The **Info** tab is a read-only technical guide organized into ingestion,
search, editing and storage topics. Each topic has an overview diagram and
visible steps, with model/method details alongside and code references below.
Model names and tuning values
come from `GET /project/info`, an allowlisted snapshot of the API's loaded
configuration. **Refresh settings** rereads that snapshot; it does not reload
configuration files, inspect per-film readiness, load models or start jobs.
The explanations stay available when the API is offline. Guide content lives
in `web/features/info/guide.ts`, separate from its UI and the processing code.

For music and editing experiments, use the **Lab** link beside search or open
[/lab](http://localhost:3000/lab), a directory of available experiments and
recent projects. Every workspace uses **Labs** to return to this directory.
Opening a new editor starts an unsaved draft; leaving without changes creates no
project and shows no prompt. **Save** keeps actual edits. Returning to Labs with
unsaved changes offers Save/Discard/Cancel; a clean exit can leave a durable job
running. **Project → Delete** confirms the named saved project and removes its
saved versions, job history, generated previews/exports, private request receipts
and job thumbnails, while keeping original music and films. Cancel active work
before deleting its project. If a file is locked, deletion succeeds with cleanup
pending; the existing editor worker retries while idle, including after restart.
New Match Cuts discovery and Transitions are sessions and create no edit
project. Experiments follow the shared
[Lab workspace conventions](docs/lab-workspaces.md).

Lab storage maintenance runs in the existing editor worker while idle, at most
every five minutes. Each project keeps only its newest finished preview and
newest finished export (the ones the editor offers); older renders of that
project are removed. A failed or unfinished render never replaces the last good
one. Maintenance also removes leftover render intermediates, recognized job
files whose job no longer exists after a 24-hour grace period, and decoded-audio
scratch older than 24 hours when no editor work is active or queued. New renders
remove intermediates after success, failure or cancellation. Each maintenance
pass handles up to 100 deletion tickets and 100 garbage paths; large backlogs
drain over subsequent passes. To inspect or run this manually:

```powershell
uv run python -m pipeline.lab.cleanup
uv run python -m pipeline.lab.cleanup --apply
```

The first command only reports eligible paths/bytes and pending deletions.
Both accept `--config <path>`. Maintenance preserves saved projects, original
music/films, active work, each project's newest preview/export and other job
outputs and diagnostic receipts, shared versioned evidence/model caches and
separately named experiment results. It does not reclaim every file in
`assets_dir` or automatically remove imported originals. Renders inside the app
are a cache of saved revisions: rendering a revision again recreates any removed
one. A deleted project's renders are removed; copies you downloaded elsewhere
are outside this cleanup.

Search begins with a large, centered query bar and a row of category controls.
Each category keeps the same footprint whether empty, filled with text or holding
a reference. Selecting it opens a compact editor near that category without
moving the search bar; example searches hide while any category is open. Empty
text categories open directly to a text field and **Apply**. Existing references
open their preview and actions; **Use text instead** preserves the reference until
nonempty text is applied. **Change scene** opens a clearly labeled reference
lookup independent of the main query. **Remove** lives in the editor, or drag a
reference out of the Refine area to remove it (with **Undo**). Drag a
search result onto a category to add it to the current search, or use its
**Related** menu to start a new search from that scene (movie scope and preset
stay). Saved cards keep playback, bookmarks and Related without dragging or a
drag badge. The player distinguishes its retrieved frame from the current
playback time; its action bar saves the scene, finds related scenes, opens
**Match cuts** at the playhead in a new tab and copies the film and time.
**Details** shows each result's description and how each retrieval channel ranked
it. Every card in a search lists the same rows in a fixed order: Visual (image-text
embedding), Semantic (text embedding, with the view it matched such as Story or
On-screen text), Lexical (BM25 keywords over the description and subtitles; it needs
two of your words and shows the ones it shares and where), Quote (spoken lines), any
categories, then the Rerank score. Each
channel keeps only its top few hundred scenes; one that did not return a scene shows
"–" and, for example, "not in its top 600".

### AI Music Video and Match Cuts

**Editor harness v2** ([ADR-0096](docs/decisions/0096-editor-harness-v2.md),
[ADR-0103](docs/decisions/0103-pacing-is-a-shape.md),
[ADR-0104](docs/decisions/0104-casting-and-purposeful-pacing.md),
[ADR-0105](docs/decisions/0105-song-profile-and-treatment.md)) is the default editor;
`lab.harness: v1` in `config.yaml` restores the earlier one. **Regenerate edit**:
- measures the song's beats, accents and loudness;
- recognizes the song from its track name and chooses a style for the edit
  from what the song is (genre, scene, how its lyrics are read) and how the
  excerpt sounds. Your direction comes first. With `planner_settings.auto`
  (no UI control yet) the style also sets pace, footage and match cuts;
- plans a concept with one act and a few footage queries per musical section.
  The pace you choose is every section's default: sections can go faster, at
  most one step slower, or hold one shot for a long stretch. A burst of images
  on the beat (a flash) happens only where your direction asks for one. For
  an edit of one to three films, the plan starts from those films' key moments;
- casts each section: picks the moments that carry it, in order, and the one
  that lands its biggest musical moment;
- assembles cuts, shots and source windows together, so action peaks land on
  accents and cuts follow continuity;
- lets the planner swap shots once among pre-timed alternatives.

`scripts/edit_tools.py` holds optional helpers that drive the same API from a
terminal: make an edit from a JSON spec, render it, print its recipe and pacing
shape, make a contact sheet, or build a review page of rendered edits.
A generated (AI) clip, such as a Runway push, can be placed in an edit like a
film shot once registered; the command prints its `gen-` source ID:

```bash
uv run python -m pipeline.lab.generated register push.mp4 --title "Push into the TV" --provenance '{"model": "seedance2_fast"}'
```

**Footage** in AI direction sets the recognizable ↔ fresh balance (the search
Famous / Balanced / Hidden gems presets) for every editor search. **Match cuts**
(*Where they fit* by default, *As often as possible*, *Plain cuts*) sets how
strongly v2 prefers cuts whose frames match across the cut, measured on the
actual frames once the Match Cuts index is built (see Match Cuts below).
**Fill gaps** keeps your cuts and placed shots and lets the optimizer pick shots
for the empty slots. A slot with its own written search uses that search.
Single-shot replacement keeps the v1 path. **Resolve timeline**
(or `GET /lab/projects/{id}/timeline.otio`) downloads the saved edit as an
OpenTimelineIO timeline on the original films, song and dialogue, for finishing
in DaVinci Resolve.

AI Music Video imports an original audio file into durable `state_dir/lab/tracks`
and starts with a 30-second passage (or the whole track when shorter). Importing
into a new draft does not save a project; cancelling the music picker restores
the opening draft while preserving the imported original. Applying the passage
saves its input before local timing preparation. Passages
can be adjusted up to 10 minutes. **Use full song** selects the entire track when
it fits; longer tracks offer **Use 10 minutes**. **Fit song** only changes the
waveform view. Importing another song starts a new arrangement;
**Undo** or **History** restores the previous edit. Install and prepare the local beat tracker:

```bash
uv sync --dev --extra lab
uv run --extra lab python -m pipeline.lab.prepare_music
```

The preparation command downloads the authors' Beat This! `final0` checkpoint,
pins its SHA-256 in `assets_dir/lab/beat-this/profile.json`, and verifies it on
reuse. Inference never downloads weights. `lab.beat_checkpoint` plus
`lab.beat_checkpoint_sha256` can select an explicitly prepared local file.
The default `lab.beat_device: cpu` keeps timing analysis off the search GPU.
Without this optional profile, waveform/intensity and manual timing remain
available; the interface reports that automatic beats are unavailable.

AI Music Video has two tabs in one project: **AI direction** for describing and
generating the video, and **Edit** for its preview, timeline and right-hand
**Scenes** panel. Save, Undo, History, song selection and task progress are shared.
New work opens AI direction; a saved arrangement opens Edit. An optional
`view=ai` or `view=edit` URL parameter chooses the initial tab without changing
the document. Open **AI Music Video** from Labs, choose a song, and drag the
waveform selection or either edge to choose a section. Space auditions the music.
**Use this section** applies it; Cancel restores the previous song/passage.
The white preview marker sits above the waveform, separate from its edge grips;
scrubbing stays inside the section without moving the crop. Trimming preserves
ongoing playback and its position, including while the boundaries cross the
playhead; after releasing the grip, playback stops if it has passed the updated
end. Paused trimming only moves the playhead when it falls outside the crop.
Start/end fields support 0.01-second adjustments. Zoom changes gradually around
the visible playhead, with +/− steps and **Fit section** / **Fit song** controls;
playback follows into view without panning during a drag. On a trim handle, arrow
keys adjust 0.1 seconds, Alt adjusts 0.01 seconds, and Shift adjusts 1 second.
Fade-in is under **More options**.

Applying a passage prepares local beat guides and starter placeholders. In
**AI direction**, describe the imagery and progression or leave the instruction
empty. Optional passage directions let you drag across the waveform, then write
an instruction for that part of the song. Drag a range or either edge to adjust
it; its boundaries do not require cuts. Press **Generate edit** to listen, plan
timing across the selected passage, plan visual directions, find footage and
assemble the edit in one undoable revision. A compact
timing request considers the whole passage before footage work is divided into
smaller batches. It can place quick clusters and sustained holds around the music;
the selected pace is guidance, with no required average duration or cut count.
New whole-edit generations let the selector adjust nearby cuts after seeing
available scenes. It preserves the planned order and count, allows at most two
seconds of local boundary movement and uses legal source windows. Both **Generate
edit** and **Regenerate edit** choose fresh whole-passage pacing. **Fill gaps**
and manual scene search preserve every current cut, including starter placeholders.
The selector can leave an explained gap when none of its candidates fits the intent.
The timeline's missing-scene count jumps to an empty position, and its reason is
shown above scene search results.
These are source-aware timing proposals, not verified action-completion detection.

To change the style of a completed edit, open **AI direction**, revise its
instruction or choose a pace such as **Energetic**, then press **Regenerate edit**.
Writing direction or changing preferences only changes the next generation's
input; regeneration rebuilds the scenes and cuts explicitly. **Patient** favors
sustained development, **Balanced** balances movement and holds, **Energetic** favors active
changes, and **Rapid** favors quick clusters while allowing the music room to
breathe. These preferences do not impose evenly spaced cuts or guarantee a
particular count. Changing pace reuses valid listening evidence for the same
song passage. The previous edit stays saved until the new one finishes and can
be recovered with Undo or History. Unlock
placed clips before a full rebuild. Old footage stays in Saved clips. **Fill gaps**
is in Edit's timeline toolbar and preserves current cuts and placed scenes,
including an untouched starter arrangement.
Regeneration searches for different footage from the previous version. Automatic
selection avoids repeated source windows; if it cannot find a suitable fresh
choice, it leaves an explained gap. Manual search and placement still allow reuse.
Different indexed shots can still depict very similar compositions; exact reuse
prevention does not guarantee visual variety.

When generating new cuts, the model suggests scenes and nearby cut positions.
The editor fits those offered cuts to the selected footage before setting exact
source trims; task details report any adjusted cuts. Existing cuts stay fixed
when filling gaps or searching for a replacement scene.

Choosing a pace alone keeps existing cuts; use Regenerate to rebuild. The editor
supports up to 300 timeline positions for every pace and 600 saved clips.

Direction accepts up to 24,000 characters globally and 32 nonoverlapping passage
ranges with up to 2,000 characters each. Ranges stay attached to their original
song times when you change the selected section; out-of-section ranges remain
saved. Importing a different song keeps the global direction and clears its old
time ranges. Undo or History restores the previous song and its ranges together;
saving that restored edit preserves them. Older briefs and user-written visual
plans appear as the starting direction. Explicitly clearing it prevents those
older instructions from returning.

Listening uses sections no longer than 90 seconds and keeps their timestamped
evidence. Timing considers the complete selected passage. Visual planning and
selection then process at most 32 planned shots at a time, usually spanning no
more than 90 seconds, with shared song context, visual motifs and previously
chosen footage. A longer planned hold remains one shot in its own batch; a
processing limit does not insert a cut. Progress distinguishes listening, timing,
visual planning and scene selection. The complete result is saved together after
validation. **Export video** uses the full-quality export profile rather than
the smaller preview profile.

The controls are placed with the thing they affect:

- **Song name** changes the passage. **Music analysis**, beside it, shows the saved
  interpretation and lets you Analyze music or Listen again independently.
  It separates **Song meaning** from **Musical atmosphere** and can reveal heard
  paraphrases with approximate song times and uncertainty. Unclear vocals remain
  unknown; the model's inferred meanings are not verified lyrics. Optional song
  notes can guide or correct the interpretation.
- **AI direction** contains one global instruction, optional passage directions,
  pace, lyric approach and source-film selection. **Song meaning & lyric cues**
  holds optional song notes and timed words/meaning. These fields share the
  project's Save and Undo; typing into one field or dragging one range forms one
  Undo step. **Generate edit / Regenerate edit** starts the complete rebuild.
  Passage directions do not provide partial-range regeneration. The shared
  **Format** control chooses landscape or portrait output.
- **Scenes** shows the selected clip's description and results. Edit the description,
  press **Find scenes**, preview an option and **Use scene**, or drag a result onto
  a fitting timeline placeholder. This search does not call the editorial LLM.
  **Search options** exposes optional clues/references. Editing the main description
  clears an older generated recipe so the search follows the visible text.
- **Why this shot**, under Scenes, shows current neighboring films, the saved
  musical intention and selection note, and separate indexed search evidence.
  It reads saved data without another model call. Sampled frames and selection
  notes do not prove exact movement or explain transitions that were never recorded.
  **Suggest
  description** or **Rewrite description** uses saved musical evidence and neighboring
  clips before another search. This optional text-model action does not place footage.
- The preview shows the current film. Zoom reveals finer measured waveform detail;
  during playback the timeline pages when the playhead leaves the visible area.
  Paused scrubbing and timeline drags retain their viewport.
- **Adjust clip** opens the source **Moment** and **Crop & zoom** controls. Preview
  the exact window before **Use this footage**. Clip options contain Lock, Remove
  scene, and contextual **Find following scene**. The latter auditions up to three
  source-backed pairs with music before explicit placement; its optional flexible
  cut/sample inspection remains a separate experiment.
- **Split** at the playhead, double-click the ruler, or use M while the timeline is
  focused. Drag numbered cuts; select a cut and press Delete to join it. Select a
  placed scene and press Delete/Backspace to leave an empty placeholder without
  changing its timing. Locked scenes are protected. Arrow keys
  move one frame. **Cut tools** can end the selected clip at the playhead or join
  it to the next. **Suggest cuts** explicitly rebuilds empty positions from music;
  it replaces the arrangement, retains old footage in **Saved clips**, rejects locks
  and supports Undo. Clear removes only unused, unlocked saved clips.
- **Beats**, beside the timeline, shows/hides guides, Detect beats and optional
  musical/lyric cues. Detection preserves current cuts. Beats are guides, not an
  instruction to cut on every pulse. **Snap** is optional; Alt bypasses it while
  dragging. Zoom, Fit and Expand control the timeline view.

Space plays/pauses the active tab's music or edit without interrupting typing.
Switching tabs pauses the outgoing player and keeps the timeline's zoom and
scroll position. Preview transport has
play/pause, previous/next cut, start/end, timecode and mute. The music clock drives
source playback; gaps remain visible while music continues. Every generated edit
and explicit timing rebuild applies as one revision. Undo and History restore work.
**Save** persists changes. **Labs** offers Save/Discard/Cancel for unsaved edits;
a clean exit can leave a durable task running. **Project → Delete** opens a named
confirmation. The full-width timeline sits below the preview and scene library;
zoom reveals clip text when the complete sequence is too dense to read. **Export video** requires all slots
filled and produces the same 24fps original-speed trims/crops and audio arrangement.

Dialogue clips keep an original film's audio on a separate song-time lane, so a
spoken line can continue across several picture cuts. Choose its source range,
placement, level (including up to +24 dB for quiet speech) and short entrance/exit
fades; clips must fit the selected passage. Dialogue clips can overlap for room-tone
handoffs or voice bridges. **Voice focus** uses a real surround center when available
and gently reduces rumble/high hiss; stereo copies retain their original mix. It
does not separate speech from music or effects. **Original** keeps the unfiltered
source mix. Both audition and export use the same prepared audio.
The music bed has an independent level and ending fade, and ducks beneath each
line with adjustable attack/release. New clips use gentler .6/1.2-second ramps;
existing edits retain their earlier timings. Source times and labels remain
editable; listen to verify each utterance and the transitions between voices.
Live audition and rendered preview/export share the same envelope convention.
Save, Undo and History include the arrangement. Replacing the song clears its audio
clips while retaining the previous revision; generating new pictures preserves them.
Missing or changed source audio produces an error instead of a silent replacement.

Each job has a compact status row with its actual stage and elapsed time. **Details**
opens above the editor without shifting it, showing recorded worker steps, the
provider/model used and available candidate/selection/gap counts. Steps survive a
reload. Cancel requests a stop at the next operation boundary; an in-flight model
call may need to return or time out first. Failures and unapplied late results stay
inspectable. There are no invented percentages, automatic hosted retries or hidden
provider fallbacks. Timing diagnostics retain the proposed cuts and their musical
reasons, available evidence and uncertainty, plus any later source-fitting
adjustments. These records make a decision inspectable; they do not prove that
the resulting pacing works creatively.

Set `OPENAI_API_KEY` for interpretation and planning. Music defaults to
`lab.music_provider: openai` and `lab.music_model: gpt-audio-1.5`, independently
of annotation. It sends only the selected PCM passage plus scoped musical context,
using audio-capable Chat Completions with storage disabled. Text timing/direction/selection
uses `lab.planner_model: gpt-5.6-terra` with Structured Outputs and local validation.
The model reads bounded indexed descriptions, context and search evidence; whole-edit
selection normally uses that text evidence. Optional targeted footage inspection
is disabled by default (`lab.footage_inspection: false`). When enabled, Generate
reviews at most two flagged positions across the edit, sampling the selected
window and at most one offered alternative for each before one joint review.
It may adjust a legal trim, use the inspected alternative or leave an explained
gap when footage contradicts the intention. Existing/manual cuts and protected
work retain their ownership rules. Progress and Details record the inspected
scope, cache use, changes and unavailable evidence. Sparse samples cannot verify
continuous actions, precise movement, object tracking or film-plot continuity.
The optional sampled-frame check for following-scene suggestions remains separate.
New listening receives measured music guides and optional supplied song context;
editor direction, visual plans and editing preferences belong to the later
planning requests. A versioned cache reuses matching interpretation inputs.
Changing creative direction or pace preserves current listening evidence; **Listen again**
runs the separate analysis action with its normal cache rules. Changing the text
planner does not require listening again. Gemini remains
an explicitly configured provider. Restart API/worker after configuration changes.

The [frozen comparison runner](docs/experiments/music-edit-comparisons.md) measures
timing with/without measured beat guides and inspection off/on against the same
private evidence. Its `assembly` stage compares the current fixed-slot selector,
joint footage/timing assembly, and one optional targeted discovery expansion.
It keeps a complete retrieved-candidate ledger separate from the model catalog
and renders valid comparison arms privately. It defaults to a dry run, requires explicit hosted-call budgets,
and never changes saved projects or listens again. Mechanical results and model
judgments are separate from played creative acceptance.

Projects, complete JSON revision history and frozen job snapshots live in
`state_dir/lab/lab.sqlite3` (SQLite WAL); original imported music lives in
`state_dir/lab/tracks`. Films and derived assets remain separate. Back up the state
database consistently with SQLite's backup facility and preserve tracks; copying a
live database file alone can omit WAL changes. This storage suits a local single-user
editor. Jobs run through two local workers sharing the ledger; growing history/list payloads and
model/GPU throughput are the practical scaling limits. Pagination/retention can be
added when measured need arises. Multiuser hosting would also need user isolation,
shared object storage and a leased job queue; those are not implemented.

Match Cuts (the existing `/lab/visual-rhymes` route) provides A→B audition
with precise trims, fixed reference
instants or bounded windows, region marking, and whole-picture cropping. It
also offers the bounded Match Cuts experiment described below. The separate
[shadow evaluation workflow](docs/experiments/visual-rhymes.md) measures whether
better instants and resizing help before admitting new retrieval machinery.
An [offline DINOv3 challenger](docs/experiments/dense-geometry.md) can compare
independently pinned dense descriptors on a small explicit image set. It
requires a local checkpoint and has not been evaluated on the film library.
The [editorial study packet](docs/experiments/editorial-reference-study.md)
and three-passage template keep human quality review separate from code tests.

Provider contracts: [OpenAI audio input](https://developers.openai.com/api/docs/guides/audio-chat-completions),
[GPT-Audio-1.5](https://developers.openai.com/api/docs/models/gpt-audio-1.5),
[GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra),
[Gemini audio understanding](https://ai.google.dev/gemini-api/docs/audio),
and [Beat This!](https://github.com/CPJKU/beat_this).

### Alg Mods

Open **Labs → Alg Mods** at [/lab/alg-mods](http://localhost:3000/lab/alg-mods)
to try every treatment on every scene. The board has scenes down and
treatments across (**Painted dots** with **Vivid** after Yoon Hyup or **Pastel**
after alg.comp.mod, **Time stripes**, **Mosaic**, **Quadtree**); a cell is the latest render
of that pair. **Add scene** searches the library; open a cell to play it, set
**In** and **Out** (0.2–12 s), adjust the treatment's controls (**Fine tune**
shows the rest) and **Render**; the pair's variants are listed under the
player. Scenes pinned before their first render are kept in the browser. Renders are durable jobs on the
editor worker; each variant stays listed with a download and a receipt.
The worker needs the OpenCV extra, and the `measure` extra for subject
detection (crop centring and keeping the subject real):

```bash
uv sync --dev --extra algmods --extra measure
```

The mosaic needs its tile bank, built once from the indexed keyframes (about
25 minutes for 176 films, 51 MB at `<assets>/algmods/`); rebuild after large
ingests to include new films:

```bash
uv run python -m pipeline.algmods.tiles build
```

What makes each treatment work, with the numbers that held and what failed, is in
[`docs/experiments/alg-mods-craft.md`](docs/experiments/alg-mods-craft.md).
**Tiles from** chooses what the mosaic is made of: the whole library, the film
itself, or a search (**Search for**: comma-separated queries, pinned when you
render). **Where** confines the mosaic to the segmented subject or its background.
**Live layer** keeps part of the frame as untouched film inside the treatment:
the subject, the background, or the nearest or farthest share of the frame by
depth (**Live share of depth** under Fine tune). People are cut by a video
matte with hair-level edges. Depth and the matte each download a small model
(Depth Anything V2, Robust Video Matting, about 15 MB each) into
`<assets>/models/` on first use. **Ground** picks the colour behind the dots
(the scene's own shadow colour, navy, or black); **Colour variety**, **Bigger
marks on flat areas** and **Dashes along structure** are the round-10 dots
controls.

The same renderer runs from the command line for batch study:

```bash
uv run --extra algmods --extra measure python -m pipeline.algmods render \
  --film "Fallen Angels" --start 4753.7 --end 4758.1 --mod dots \
  --param style=vivid --out out.mp4 --side-by-side
```

### Transitions

Open **Labs → Transitions** at [/lab/transitions](http://localhost:3000/lab/transitions)
to experiment on two indexed film clips. **Local effects** and **AI transitions**
have separate tabs. Choose the two scenes on the continuous **A → B** timeline
below the monitor, drag their trim edges and shape a transition, then choose
**Preview transition**. The 15 recipes include Camera whip,
Anchored crash zoom, Highlight kiss, Organic edge burn, Prismatic lens sweep,
Shutter double hit, Afterimage flash, Soft swipe and Luma reveal, alongside Clean
exposure flash, Dip to black, Linear-light dissolve and a Hard cut baseline.
**Experimental** adds Defocus bridge and Prism push. These are local image
effects; the prism does not reconstruct a 3D scene or camera move.
Each family exposes useful controls first; **Fine tune** opens the additional
timing, blur, light, texture and anchor controls it supports.
Light effects start with restrained amounts and short picture crossovers. The
burn and flashes are procedural; no external overlay pack is required.
Whip and crash zoom use smooth acceleration, a restrained adjustable settle and
blur tied to the actual output-frame interval. **Shutter exposure** controls the
smear; **Settle / rebound** adds a small single recovery. **Fine tune** includes
edge-protection crop and curve controls. Camera whip moves each full picture in
the same direction and changes shots through a short crossover near the blur peak.
**Cut blend** adjusts that crossover. Its 12-frame starter uses 65% exposure,
55% shutter softness and 4% cut blend. Judge the landing with your footage.

**Speed** changes the source footage itself and combines with any local effect,
including a hard cut. **Rush** accelerates A toward the join and eases B back;
**Slow hit** slows around the join; **Pulse** adds a short speed burst on each
side. Tune the source-speed multiplier, ramp window in source seconds and curve.
The selected footage stays fixed, so output duration changes; the panel shows
the resulting clip durations. Long overlaps can hide a ramp's edge-speed peak;
the saved preview reports estimated speed at the picture change.

**Curve & smoothing** offers captured-frame sampling, linear-light frame blending
or local motion-compensated **Optical flow**. Interpolation runs inside each
clip's ramp before the visual effect. Flow is slower and can warp moving details;
it uses FFmpeg's CPU implementation, not a neural interpolation model. Compare
the played result before keeping it. No additional model, account or asset is
required. Speed off retains ordinary source playback. Active speed preparation
allows four minutes per clip, up to 4096 native frames and a monitored 1 GiB of
temporary native/flow files per clip. Use shorter footage, Draft quality or
simpler smoothing if a limit is reached; the lab never silently truncates the source.

One monitor switches between **Clip A**, **Transition** and **Clip B**. Click a
clip on the timeline to inspect it there; this pauses transition playback and its
music. Drag either end of either clip to trim. The striped overlap marks the
transition; drag its lower handle to change duration or click it to return to the
transition monitor. A matching saved preview can be scrubbed on the timeline's
ruler. With Speed enabled, source inspection positions use an approximate speed
map; they are not native-frame evidence. Focus a trim handle and use arrow keys
for 1/30-second source-time increments, Shift for 0.1 seconds, or Home/End for its
limits. **Reframe** lets you drag the inspected picture, choose Fit/Fill and adjust zoom.
**Precision controls** holds optional numeric times, cut nudges and position sliders.
For a rendered pair, **Inspect end / start** shows the retained native endpoint
frame while that clip's source window matches; framing updates the same still.
Changing the other clip does not reload this source. Playing or scrubbing returns
to video. **Near end / start** is an approximate browser seek when no matching
render exists; compressed-video seeking can land across a nearby shot boundary.

Use frame presets or **Time it to music** to convert BPM and beat subdivisions
into 30-fps durations. **Render 3 timings** compares the current duration with
two neighboring timings, keeps the existing preview playable while rendering,
and opens the middle timing for comparison. **Versions of saved preview pair** provides
quick preview and comparison choices. **Compare hard cut** creates or reuses a
matching baseline at the same speed. For a retimed version, **Compare without
speed** isolates the speed change while retaining the effect and its duration.
**Compare the same pair** shares one transport aligned
to each variant's transition center, with seam looping, slower playback and frame
stepping. **Audition with music** loads a local audio file and aligns its chosen
cue to that center; audio stays in the browser and is not included in the MP4.
Favorites, notes and named recipes are saved in this browser. Render history and
downloadable manifests are retained by the server. **Saved renders** is collapsed
initially and shows six results at a time, with **Show more** for older versions.

Identical completed local requests reuse their verified render instead of
creating another file. The saved preview shows its actual resolution and size.
Use **Draft · 480p** while experimenting, **Review · 720p** for closer inspection,
and explicitly **Render 1080p copy** for a chosen saved local variant. The export
uses its frozen source windows and settings; it is rendered from originals,
not upscaled from the draft. Saved experiments and imported/generated originals
are retained. Temporary render files are cleaned after completion or cancellation;
rendering checks free headroom and a bounded temporary-file budget.

Each source window spans 0.2–12 seconds. Local effects last 0.10–2 seconds (at
least three output frames) and must be shorter than both clips after retiming. Overlap
shortens the pair; Hard cut plays both windows consecutively. Each clip has
**Fit / Fill**, direct picture positioning and zoom controls for landscape, portrait or
square output. The RGB renderer normalizes source color to SDR Rec.709 and
composites light in linear RGB. Tagged PQ/HLG inputs are tone mapped; this is
an SDR preview/export profile. Output is muted 30-fps H.264 MP4 at 480p, 720p or
explicit 1080p export quality, with fast-start playback and keyframes at most two
seconds apart. This is a compact video asset for editing, not a lossless/HDR
master or an editable Resolve/Premiere effect. The existing **editor** worker performs rendering, progress and
cancellation; no new service or model is required. The saved-preview receipt
shows whether working controls differ. **Preview needs update** keeps that
distinction visible beside the monitor, and **Update preview** renders the current
draft. **Load to refine** restores the saved source pair and recipe. Selecting
history previews a result without replacing a working draft. Opening the workspace or adjusting controls
creates no edit project.

**AI transitions** shows the saved source pair it will use. **Prepare working pair**
prepares a local Draft hard cut at original speed, without replacing your local
effect, speed or quality controls. Preparation does not generate an AI video or
make a paid call. **Edit clips** returns to the working timeline and monitor.

The AI direction panel starts with a transition direction, restrained/balanced/bold energy
and applicable travel controls. Optional anchor and source-motion notes refine
the prompt. Cinematic choices include reflection passages; experimental choices
include match morphs, aperture portals, material dissolves and point-cloud scans.
These are editable prompts, not guaranteed provider effects. **Apply direction**
updates the prompt explicitly; changing controls or source versions preserves
custom writing. Choose **Generate here** or **Bring a result** to keep one creation
flow visible. Full prompt, source notes and provider details are expandable.

Native endpoint-frame downloads and a prompt receipt support external tools.
Generate externally, then import the returned
MP4, MOV or WebM (up to 128 MiB and 30 seconds). The lab retains the original,
explicit trim and optional playback length, and renders the complete
**A → bridge → B** audition. Both joins, the inserted interval, downloads and
saved import history remain inspectable after reload. Provider/model notes are
user-supplied, not verified generation provenance.
Local speed ramps currently require a separate **Speed off** render before an
AI bridge import or hosted generation. The UI explains this before upload or
submission. The two-still generation path does not receive either original video
or its motion; prompt notes describe intent, not verified motion conditioning.

**Generate here** supports first/last-frame Seedance 2.5, MiniMax H3 Max and WAN 3
through Runway. Add
`RUNWAYML_API_SECRET` to the server's `.env` and restart the API and editor worker
to configure it; never put the key in `web/.env.local`. Choose the model and its
supported duration/resolution: Seedance 4–8 seconds, H3 Max 5–8 or WAN 3 2–8.
Advanced settings expose seed where supported and H3 prompt expansion (disabled
by default). WAN 3 has no seed control. Review the estimate, then explicitly
submit. The lab caps
reviewed requests at 300 credits ($3 at the checked rate), preserves a request
identity across retries, and retains the provider receipt, original output and
assembled audition. Quotes are local and make no paid calls. Runway may charge
once submitted; cancellation is best effort and is not a guaranteed billing cap.
Keep the full generated output, then choose **Adjust timing** to load its retained
original into the trim controls without another generation charge. Short duration
buttons change its time in the edit; saving creates a separate local audition.
Saved model, prompt and seed notes follow that original. A saved provider original
remains recoverable if local assembly fails. Generation uses the editor worker,
so other queued editor renders wait while that job runs. The
[live scene tests](docs/experiments/transition-v9-ai-scene-tests-2026-09.md) record
the tested models, costs and creative limitations. Applying transitions in the AI editor
remains a separate decision after played comparisons
([ADR-0071](docs/decisions/0071-rgb-transition-lab-and-durable-bridge-imports.md),
[ADR-0072](docs/decisions/0072-native-time-speed-ramps-and-interpolation.md),
[ADR-0073](docs/decisions/0073-compact-transition-edit-assets-and-playback.md),
[ADR-0074](docs/decisions/0074-model-aware-ai-transition-experiments.md),
[ADR-0075](docs/decisions/0075-smooth-transition-motion-and-direct-manipulation.md),
[ADR-0076](docs/decisions/0076-camera-whip-and-program-monitor-timeline.md)).
The [AI options research](docs/experiments/ai-transition-options-2026-09.md)
records current direct versus external capabilities and their limits.

### Raw source workflow

Keep active downloads separate from finalized source files:

- `V:/scene-recall/incoming/` — downloading or seeding; never ingest here.
- `V:/scene-recall/films/` — completed, canonically named, immutable sources.
- `V:/scene-recall/state/` — saved scenes, Lab projects, original imported music
  and the durable job queue; required app data, not a disposable ingestion cache.
- `C:/Users/julia/Videos/cinema-assets/` — rebuildable search indexes, keyframes,
  annotations, previews and other derived media, configured as `assets_dir`.
- `V:/scene-recall/evidence/` — intact leftover release evidence. Manual imports
  can be archived by the operator; managed downloads archive their own releases
  automatically after successful publication. Search does not read the archive.

The source flow is `incoming` → `films` → ingestion into `assets_dir`. **Add
films** handles downloading and import; **Review & add** handles files you have
already downloaded yourself. Selected SRTs remain beside their films as reusable
dialogue evidence. The configured paths can change; `state` is internal app
storage and the evidence archive is not an additional ingestion stage.

The **Films** header shows a compact storage total, its split across drives,
and films versus supporting data. Hover the supporting total for its breakdown:
derived media, indexes, downloads, release archives, saved app data and the
configured models' local caches. Refresh storage independently with its small
refresh button; catalog refreshes also update it. These are deduplicated file
sizes, not allocated disk blocks; sparse downloads can differ. Measurements run
in the background and are cached for five minutes. Missing or unreadable data
is marked as a partial total rather than reported as zero.

`GET /library/storage` starts or returns that read-only measurement;
`?refresh=true` requests a fresh one. It inspects configured app roots, exact
registered film paths, the conventional sibling `evidence` archive and only
the selected models' cache directories. It does not scan whole drives, unrelated
model caches, development folders or arbitrary external archives.

In Windows Disk Management, reserve `V:` for this drive and confirm it is
mounted before starting the API or an ingest. Create `incoming` once and set it
as the torrent client's download directory. Keep each finalized movie file
directly inside `films` because source discovery is not recursive.

For manually downloaded releases, download and seed in `incoming`. After seeding is finished and the torrent is
removed, open **Films** in the frontend. **Review & add** suggests the main
video in each release (the largest supported file), a title, year, and final
`Title (Year) [Edition].ext` filename. Confirm that downloading/seeding has
finished, then add it to the library and optionally queue ingestion. The move
is instant because `incoming` and `films` are on the same drive. Other videos
inside that release folder are left in place and are not offered as films.
The default automatic subtitle option checks associated SRT files and preserves
one passing English track beside the canonical film as
`Title (Year) [Edition].en.srt`, including a track without an English filename
marker. Checks cover the complete subtitle structure, timestamps against the
film duration, English text evidence, dialogue diversity and broad timeline
coverage. They flag obvious malformed, mislabeled or partial tracks; they do
not prove exact audio synchronization, translation accuracy or completeness.
The incoming listing does not probe videos. Import runs a bounded local
metadata probe and rechecks subtitles before moving the film.

**Review & add** also permits choosing an eligible track explicitly or skipping
external subtitles. Multiple conflicting tracks remain separate review choices.
Byte-identical validated copies count as one automatic choice; every original
and explicit review choice remains available, with ordinary tracks preferred over SDH/CC.
Automatic mode uses no external track when none passes uniquely, allowing
ingestion to use its existing embedded-subtitle or local Whisper fallback.
Forced, commentary, extra-associated, foreign-marked, unassociated and
structurally invalid files are not offered as choices. Every release SRT stays
untouched as raw evidence; a selected canonical sidecar is copied byte-for-byte.

After a manually imported canonical film has published successfully and the torrent is no
longer active, an operator may move the entire marked release directory intact
from `incoming` into archival storage such as
`V:/scene-recall/evidence/imported-releases/`. This is a manual preservation
step, not application-managed cleanup: keep the `.scene-recall-imported`
marker and every raw subtitle track together, and do not delete timestamped
evidence merely to reclaim space. Without an external archive, leave the
release in `incoming`.

### Managed downloads

In **Films → Add films**, search configured indexers, paste a magnet, or upload
a `.torrent` file (maximum 2 MiB). Search, choose a release, then confirm its
library name with **Queue film**. Release filenames suggest editable title/year
values when unambiguous; the full filename and optional edition remain available
under expandable details. Search uses [Prowlarr](https://prowlarr.com/); downloads
use [qBittorrent 5.x](https://www.qbittorrent.org/download). Prowlarr is optional:
magnets and torrent files work independently. Scene Recall never installs a
service or starts a download just because you opened Films.

If a title search returns no usable releases, Scene Recall retries once with
the title as a quoted phrase in the same movie category. Some indexers return
empty broad searches even when that phrase finds the film. If both attempts
are empty, try the title and release year; a magnet bypasses release search.

After a successful addition, the form closes and **Film queue** shows progress.
Downloads and existing ingestion jobs share this view, with actual queue
positions and plain preparation stages. Other waiting films, technical details
and download settings can be expanded when needed. Completed and cancelled
acquisitions do not add a History dropdown; their durable records are retained.
An active film appears once in the queue; completed films appear in **Library**,
which can be filtered by title. **Refresh** updates both downloads and ingestion
status. Manually downloaded files remain available under **From incoming**.

Enable qBittorrent's Web UI, bind its management interface to `127.0.0.1`, keep
authentication enabled and disable external-program completion hooks. Connect
your torrent indexers in Prowlarr if using search. Put these server-only values
in `.env`, then restart the API and acquisition monitor after changes:

```dotenv
QBITTORRENT_URL=http://127.0.0.1:8085
QBITTORRENT_USERNAME=your-local-user
QBITTORRENT_PASSWORD=your-local-password
PROWLARR_URL=http://127.0.0.1:9696
PROWLARR_API_KEY=your-local-api-key
```

Run the download monitor in another terminal:

```bash
uv run python -m pipeline.acquisition.worker
```

On this Windows workstation, the portable helpers are installed beneath
`%LOCALAPPDATA%/SceneRecall/tools`, with their isolated profiles beneath
`state_dir/acquisition/integrations`. To start those existing installations
after closing them or rebooting, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-acquisition-services.ps1
```

The script starts the two configured helpers hidden, checks loopback listeners,
and leaves unrelated torrent clients alone. It neither installs missing helpers
nor starts the Python monitor. Other machines can use ordinary local service
installations with the same `.env` contract. See qBittorrent's
[portable-mode instructions](https://github.com/qbittorrent/qBittorrent/wiki/How-to-use-portable-mode)
and Prowlarr's [official releases](https://github.com/Prowlarr/Prowlarr/releases).

The acquisition monitor polls every five seconds, allows two simultaneous downloads, and persists
its queue in `state_dir/acquisition/acquisition.sqlite3`. `--once` reconciles
the queue once; `--interval 10` changes the poll interval. A lock prevents two
monitors from owning the same queue. Downloads do not occupy the worker used
by the Labs. Once a canonical source is ready, ingestion joins the existing
FIFO and waits for that worker.

The queue accepts MKV, MP4, AVI, MOV, M4V and WebM videos at the release root or
inside nested folders. It waits for the torrent to stop and checks that every
inventoried file is complete with its expected size. Automatic film selection
requires one clear match to the requested title and year evidence, using the
filename or a matching enclosing film folder for a generic filename. File size
does not choose between collection members. Ambiguous collections, unclear
names and split CD, disc, part or episode files pause at **Review**. Words such
as “Interview” that belong to the requested title do not classify it as an extra.
The application does not unpack archives, read disc images or join split films;
choose a standalone video release or prepare a supported video for manual intake.

The queue also checks safe paths, available disk space, video metadata and
three decoded sample frames. These checks do not establish film identity,
full-file decoding quality or subtitle synchronization. The measured duration
feeds the shared external-subtitle checks. One passing track is selected
automatically; uncertain subtitle choices pause at **Review**, whose default
automatic option uses a uniquely passing track or the ingestion fallback. An
eligible track or skip can also be selected explicitly.

Subtitle discovery includes nested folders under a matching film release,
including an outer wrapper folder. A flat managed acquisition containing one
video may also associate `Subs/ENG.srt` with that video. This inference does not
apply to a shared manual incoming root or an ambiguous root containing multiple
videos. Other subtitle formats remain preserved in the release; external SRT
is the supported sidecar format.

Managed torrents use their own category, unique tag and
`incoming/.scene-recall-managed/<id>/data` directory, excluded from manual
intake. Each torrent is configured to stop in qBittorrent after downloading,
even if the acquisition monitor is offline. The policy is set on new downloads,
reconciled duplicate adds, and before app-controlled starts or resumes. The
client checks completion and sharing limits periodically, so a brief transition
is possible; completed downloads are not left seeding. Uploads while a download is still in progress
remain possible. Do not force-start managed torrents in the client: force-start
overrides this automatic stopping behavior.

The monitor also requests a stop and confirms the completed torrent is stopped
before removing it from qBittorrent with file deletion disabled. It then moves
the verified movie into `films` without
overwriting an existing source. It preserves the chosen SRT beside the film.
Only after successful ingestion and publication does it move the entire
remaining acquisition directory to
`incoming_dir.parent/evidence/managed-releases/<id>`. All raw subtitle tracks,
extras and the ownership marker remain intact. It does not adopt or change
torrents in another client or category.

**Cancel** stops a managed acquisition, including a failed download,
and removes its owned download folder after ingestion has stopped and the torrent
has been detached from qBittorrent. Partial files, unselected videos, subtitles
and extras in that staging folder are discarded. Imported library films, their
canonical subtitles, shared derived assets, manual incoming files and existing
evidence archives are preserved. Cancelling an ingestion job is not library-film
deletion. The queue keeps cancellation visible until cleanup finishes; locked
files, unavailable drives or downloader failures retain a pending cleanup and
retry automatically, including after restart. Ownership conflicts retain files
and show the reason. A submitted torrent whose acceptance is still uncertain
must first be reconciled with qBittorrent before its files can be cleared.

**Try again** on a failed item resumes its saved stage; a CLI retry after completed
cancellation redownloads discarded staging or reuses an already imported source.
Failed or interrupted ingestion requires that explicit action before hosted
processing can run again. Earlier cancelled records are not automatically purged.
Changed files or conflicting destinations
stop the queue for attention. Back up `state_dir` together with your original
sources; it contains the queue's recovery journal as well as your saved scenes.

The CLI uses the running API and the same queue:

```bash
uv run python -m pipeline.cli acquisition status
uv run python -m pipeline.cli acquisition search "Film title year"
uv run python -m pipeline.cli acquisition add-release RELEASE_ID --title "Film" --year 2001
uv run python -m pipeline.cli acquisition add-magnet "magnet:?xt=urn:btih:..." --title "Film" --year 2001
uv run python -m pipeline.cli acquisition add-torrent "release.torrent" --title "Film" --year 2001
uv run python -m pipeline.cli acquisition list
uv run python -m pipeline.cli acquisition --help
```

Use `cancel`, `retry` and `review` with the current item revision shown by `list`;
their `--help` describes the required choices. Search selections expire after
30 minutes. Provider credentials and source tracker URLs are never returned in
queue responses. A release search failure can be retried or bypassed with a
magnet/torrent from your chosen source.

### Ingestion and recovery

Ingestion is FIFO and runs one film at a time in an isolated, low-priority
child process. The Films screen polls only while work is active; completed
jobs survive API and worker restarts. A separate CLI ingest fails with a
clear message while another ingest owns the shared resource lock.

Do not rename or move a source after ingestion. If relocation is necessary,
use `relink-film` below. `films` is scanned for unindexed source discovery;
published library records come from the database. The database, vectors,
keyframes, and previews remain in the internal-drive `assets_dir` for fast
interactive search.

```bash
uv run python -m pipeline.cli ingest "V:/scene-recall/films/Film (2001).mkv"
```

The CLI remains available as a fallback for an already finalized file placed
directly inside `films`. Omit `[Edition]` when there is no confirmed cut or
release variant, and use ` - ` in place of a colon.

The pipeline runs: probe → dialogue → shots → keyframes → visual embed →
annotate → publish shot/frame indexes → semantic-text derivation. Completed
hosted annotations are cached by annotation profile under that film's asset
directory. If an ingest is interrupted, re-running the same file reuses
matching dialogue, media, and annotation artifacts instead of paying for those
annotation calls again. Changing the annotation model, prompt, settings, or
keyframe content selects a new cache profile without overwriting the old one.
Dialogue prefers a usable canonical English SRT sidecar, then an eligible
English embedded text subtitle stream, then local Whisper. Embedded selection
requires an `en` or `eng` language tag and excludes forced, commentary and
signs/songs tracks. Ordinary tracks take precedence over SDH/CC variants; within
that preferred group, selection requires one candidate or one uniquely marked
default. Unknown language or an unresolved tie uses Whisper. Ingestion also
validates the extracted embedded SRT's structure, English text, density and
coverage against the film duration. A partial or otherwise ineligible track
is preserved while local Whisper transcribes the audio. These checks do not
certify exact synchronization or translation accuracy.
Already-canonical sidecars retain
their existing non-destructive minimum-content check; the stronger intake
validation does not reclassify existing library evidence or invalidate its
dialogue cache. An oversized, trivial, promo-only or unparseable canonical
sidecar is ignored rather than cached as dialogue. The
cache manifest includes the sidecar hash, embedded stream identity, or Whisper
model and transcription profile so changed evidence is rebuilt instead of
silently reusing stale text. A changed embedded-stream choice is detected on the
next ingest. Embedded validation records the extracted SRT hash, stream and
validator profile; a fallback records the actual Whisper source and rejected
subtitle dependency. Unchanged evidence and settings reuse the result without
repeating transcription. Older embedded caches are rechecked on their next
explicit ingest; canonical-sidecar and unrelated Whisper caches retain their
existing profiles. This does not start an automatic library backfill.

Known release-ad cues are excluded from sidecar-derived dialogue while the raw
SRT remains byte-for-byte unchanged. This derivation rule is profile-versioned,
so an older cached sidecar is cleaned on its next ingest.

Whisper fallback transcribes the source language after VAD. A canonical `en`
or `eng` tag on the primary audio stream is used as an English hint so a
foreign-language cold open cannot pin an otherwise English film to the wrong
global language. Missing, `und`, and other tags remain on automatic detection,
which majority-votes up to five voiced 30-second windows. Previous-window text
conditioning is disabled to reduce silence hallucinations and repetition
loops. The profile records the exact language option and its source, the other
settings, and the faster-whisper package version. A versioned, conservative
structural gate discards gross repetition-loop output and continues with
caption and visual evidence instead of allowing hallucinated dialogue to enter
search. Raw film audio is never changed, and authoritative sidecar or embedded
subtitles bypass this gate.

The final text derivation is local and non-blocking; a safely published film
remains searchable through the legacy baseline if that optional step fails.

Full-scene playback can use a prepared MP4 when an otherwise playable H.264 or
HEVC source has browser-incompatible audio, such as E-AC-3. The
`video-copy-aac-stereo-v1` recipe copies the video bitstream and converts the
source's selected default audio to stereo AAC. It preserves that track's
language: an Italian default remains Italian even when an English alternative
exists. The original film and every original audio track remain unchanged.
This repairs the supported audio case; it does not make every video codec or
HEVC-capable device universally browser-compatible.

Normal ingestion attempts this optional preparation after media extraction.
A preparation failure is reported without blocking search publication; playback
continues to use the original when no valid cache is available. To prepare an
existing film without rerunning annotation, embeddings or indexing:

```bash
python -m pipeline.ingest.playback "V:/scene-recall/films/Film (Year).mkv"
```

Preparation runs locally with bounded CPU threads and stores a versioned,
rebuildable artifact and manifest under `paths.playback_dir/<film_id>/` when
configured. This workstation uses `V:/scene-recall/playback`; small previews,
keyframes, models and search indexes remain on the SSD. An omitted
`playback_dir` keeps the legacy per-film asset location. Lookup accepts a valid
legacy copy while migration is incomplete. Validation
checks source identity, duration, decoded timestamp samples and audio decoding
before publishing the cache atomically. These sampled checks do not prove
continuous audio/video synchronization. Reopen the full-scene player after
preparation: it resolves and keeps one playback URL for that open session.
Ordinary `/video/{film_id}` requests and Lab source playback continue to use the
original source. API requests never start a conversion.

After setting `paths.playback_dir`, create that directory and restart the API
and workers to load the new configuration. Finish an active ingestion before
restarting its worker; queued jobs use the new path. An ingestion already
running may still produce a legacy copy, so migrate again after it finishes.
The supported relocation command defaults to a read-only plan:

```powershell
uv run python -m pipeline.ingest.relocate_playback --config config.yaml
uv run python -m pipeline.ingest.relocate_playback --config config.yaml --apply
```

It copies each validated full-length derivative, verifies the destination with
SHA-256, publishes its new receipt and only then removes that exact old copy.
Playback representation tokens remain stable for identical bytes. Busy films
are reported and skipped; rerunning resumes safely. Original films and evidence
are untouched. Unreferenced generations are reported rather than guessed away.

Lance table history needs separate maintenance. The pruning-only command uses
the optional, pinned maintenance runtime and defaults to retaining fourteen days:

```powershell
uv run --group maintenance python -m pipeline.index.maintenance
```

For an intentional cleanup of all superseded snapshots, first stop both workers
gracefully (`python -m pipeline.lab.worker --stop`), wait for active work to finish,
and stop the API. Preview and apply the same policy:

```powershell
uv run --group maintenance python -m pipeline.index.maintenance --retain-versions 1
uv run --group maintenance python -m pipeline.index.maintenance --retain-versions 1 --apply
```

Then restart services with the launcher. Saved queue entries remain intact.
Reader, worker, ingestion and publication locks prevent cleanup during active
use. Tagged versions and unverified files are protected. Cleanup preserves
current rows and indexes and never runs the general `optimize()` compaction
path. It removes database rollback history, not current search evidence.
Maintenance is explicit; ingestion and ordinary API requests never prune history.

Rejected releases (watermarked, duplicate or wrong cut) are deleted, not
archived. One command withdraws the film from search and deletes its files; it
requires the exact film ID and indexed source path:

```powershell
python -m pipeline.index.remove_film <film-id> --expected-path "V:/scene-recall/films/Film (Year).mkv" --delete-files
python -m pipeline.index.remove_film <film-id> --expected-path "V:/scene-recall/films/Film (Year).mkv" --delete-files --apply --receipt removal-receipt.json
```

The first command previews affected rows and files. Apply requires a new
receipt, excludes ingestion/backfill publication and removes only that film's
canonical and derived index rows. Previously ready profiles remain ready for the
remaining library; incomplete profiles remain inactive. `--delete-files` then
deletes the source video, the film's asset folder and its playback copy.
- The source is deleted only while its content still matches the film ID, so a
  replacement at the same filename is kept.
- Without `--delete-files`, only index rows go and every file stays.
- Download records, jobs, bookmarks and projects are never removed; clips that
  used the film show as unavailable.
- A receipt marked `files_pending` (for example, a file in use) is finished by
  running the same command again; it also cleans up a film that is already out
  of the index.

Complete this operation before pruning versions; a receipt marked `applying` or
`recovery_required` requires recovery first.

Hosted annotation requests run concurrently within a film; tune
`ingest.annotation_concurrency` in `config.yaml` (default 8).

New ingestion samples short shots near the beginning, middle and end instead
of relying on one midpoint image. The short-shot threshold remains
`thresholds.keyframe_short_shot_s` (default 2 seconds). These samples use
distinct decoded frames, retaining raw presentation timestamps and their
container-relative player times; an extremely short shot may supply only one
or two frames. The annotation considers visible
changes across those images, including people appearing or disappearing, and
uses `unknown` when motion evidence is insufficient. Longer shots retain their
existing sampling and annotation cache. Sparse stills improve event coverage;
they do not verify every action or the exact footage later trimmed in a Lab.

Existing films can receive a targeted temporal-evidence backfill. It reuses
source files, shot IDs and boundaries, dialogue and previews, and updates only
eligible legacy short shots that have one retained image. Additional frames,
annotations and compatible visual/text search derivations receive explicit
sampling and model lineage; older evidence and annotation profiles remain.
Saved scenes, project revisions and edit timings are unchanged.
Saved clip titles and `search_evidence` remain the historical record of their
original selection. Refreshed metadata improves new searches and future
selections; it does not rewrite the saved edit's explanation.

```bash
# Read-only scope and cost plan; project scope uses its placed clips' films.
uv run python -m pipeline.cli backfill-temporal --project-id PROJECT_ID

# Validate a small, selected set before paying for a wider refresh.
uv run python -m pipeline.cli backfill-temporal --film-id FILM_ID --unit-id UNIT_ID --apply

# Queue the wider scope after reviewing the actual samples and new descriptions.
uv run python -m pipeline.cli backfill-temporal --project-id PROJECT_ID --enqueue --output temporal-plan.json

# Inspect maintenance progress, or explicitly cancel a remaining job.
uv run python -m pipeline.cli temporal-jobs
uv run python -m pipeline.cli temporal-jobs --cancel JOB_ID
```

Repeat `--film-id` or `--unit-id` to select multiple films or shots. Omitting
`--apply` and `--enqueue` only plans; it makes no hosted requests. Apply mode
uses the existing exclusive ingest resource lock. Queue mode uses the existing
durable worker in bounded maintenance batches (`--batch-size`, default 32,
maximum 128 shots). Batches are durably staged as `waiting_worker`, which older
workers ignore; `temporal-jobs` displays **queued**. Later batches retain this
internal state while an updated worker processes earlier work.
If the worker was already running when this update was installed, let its
current ingestion and Lab work finish, then restart
`python -m pipeline.lab.worker` while idle. Do not interrupt active ingestion
to activate the backfill. An updated worker claims staged maintenance only
after foreground library/GPU jobs, including new work queued between
maintenance batches. Editor jobs run independently. Each affected shot needs one
normal hosted annotation request, subject to the existing bounded retry policy,
plus local decoding and embeddings. Matching successful annotations are reused
on an explicit retry. Verify disappearances, entrances, exits and gestures on
a small source-backed set before starting a broad library refresh.
The [initial six-case validation](docs/history/2026-09-14-temporal-evidence-validation.md)
records the observed improvements and the rollout boundary.

If shot publication succeeds but semantic-text refresh fails, the images and
descriptions remain published and cached. The maintenance job reports failure
with a repair command; direct `--apply` saves its requested JSON receipt before
exiting with status 1. Run `index-text --film-id FILM_ID`, or repeat an explicit
`--unit-id` selection with `--apply` or `--enqueue` to repair text without
repeating current shot evidence. Broad film, project and library plans skip
already-current shots and suggest the text-index repair command instead.

To ingest a whole directory, skipping films that are already fully indexed:

```bash
uv run python -m pipeline.cli ingest-batch path/to/films/
```

Films are processed one at a time (the local GPU stages don't benefit from
overlapping films). A failed film is reported and the batch continues; pass
`--force` to re-ingest films that are already indexed.

If all film data is saved but the final search-index refresh fails, do not
force a re-ingest. Repair only that derived index:

```bash
uv run python -m pipeline.cli repair-search-index
```

### Relocate an indexed source film

Copy the raw movie to its final path and keep the original until verification
finishes. `relink-film` performs a full SHA-256 comparison, updates only the
stored source path and relocation-sensitive cache identities, and leaves the
existing units, frames, vectors, annotations, and derived media untouched.
It is a dry run unless `--apply` is supplied and never moves or deletes either
raw file. Apply mode writes a per-film recovery journal before changing cache
metadata; rerunning the same command completes or rolls back an interrupted
relink before doing anything new.

If the indexed movie has a canonical `<old-stem>.en.srt` sidecar, copy it beside
the new movie as `<new-stem>.en.srt` and verify the two subtitle files have the
same full SHA-256 hash. `relink-film` does not move or validate subtitle files;
retain the old sidecar until dialogue and search checks pass.

Playback receipts remain tied to their original source path. After relinking a
film that needs browser audio repair, run
`uv run python -m pipeline.ingest.playback "NEW_SOURCE_PATH"` to prepare a valid
copy in the configured playback root. Until then, playback uses the original
film; stale receipts are never accepted for a changed source.

```powershell
# Validate the copy and show the planned metadata changes.
uv run python -m pipeline.cli relink-film "V:/scene-recall/films/Film (2001).mkv" --title-from-filename

# Commit after the dry run passes.
uv run python -m pipeline.cli relink-film "V:/scene-recall/films/Film (2001).mkv" --title-from-filename --apply
```

If apply is interrupted and either movie path is temporarily unavailable,
recover directly from the durable journal with
`uv run python -m pipeline.cli recover-relink FILM_ID`.

The old indexed source must still exist so the command can prove that both
files are byte-for-byte identical. Delete it only after library, playback, and
search checks pass. For a copied film that was never indexed, compare the full
SHA-256 hashes of the old and new files before deleting the old copy. Deleting
the old source is migration-safe after these checks, but it leaves only one raw
copy unless the film also exists in a separate backup. New ingests use the
final filename stem as their display title, rather than unreliable embedded
container-title tags.

For an index created before frame-level search was added, build the derived
frame table once from the keyframes already on disk:

```bash
uv run python -m pipeline.cli index-frames
```

This step is local, idempotent, and does not call OpenAI or Gemini. New ingests
build the frame index automatically.

Cache the existing keyframes' 6x6 spatial grids so Framing queries encode only
the reference image instead of re-encoding up to 96 candidate images:

```bash
uv run python -m pipeline.cli index-framing
```

This local, idempotent backfill does not decode or reingest source films and
does not call a hosted model. It stores about 72 KiB per PE Core keyframe
(roughly 3.94 GB for 53,414 frames, before database overhead). Search uses the
cache only when its model- and contract-versioned manifest covers the complete
current frame generation. Publishing another film makes that proof stale, so
during a multi-film ingest Framing safely uses its existing live reranker; run
`index-framing` once after the batch finishes to embed only new or changed
frames and reactivate the cache. A completed single film can instead be filled
with `index-framing --film-id FILM_ID`, but derived backfills do not run while
an ingest owns the shared resource lock.

Build or repair the independent Qwen semantic-text profile from already
published captions, dialogue, OCR, broad facets, and dedicated mood/energy
views with:

```bash
uv run python -m pipeline.cli index-text
```

This command is also local and idempotent. Its model-versioned profile becomes
active only when its manifest exactly covers the current units generation;
partial or stale data falls back as a whole to the compatible legacy index.
The dedicated Mood view excludes framing, setting, palette, subjects, and
other broad facets; it can be backfilled from existing units without
reingesting films or calling the hosted annotator.

After updating to semantic query instruction `scene-recall-semantic-query-v2`,
restart the API to activate it. An otherwise complete text profile published
with the recognized v1 instruction remains compatible: query instructions
never generated its document vectors, so this query-only update requires no
`index-text` run or film reingest. An older ingest worker may finish and publish
its compatible v1 manifest without disabling the API's v2 queries. `index-text`
remains the optional repair/reconciliation command, reusing unchanged vectors.
The new instruction keeps short queries focused on the user's description
instead of adding film-production concepts to every search.

Type `@` for movie-name completion, then a name such as `@Before`. The active
phrase is highlighted and a compact dropdown beside the caret offers up to four
indexed movies. The first match is highlighted: use arrows to navigate and Enter
or Tab to confirm, or click a movie. Escape removes the active `@` and leaves the
words as plain text; completion stays off until a fresh `@` or an empty field.
Clicking away dismisses without applying a filter. A complete title in ordinary prose can also offer matches,
initially unselected; plain Enter still searches the typed words unless you
select an option. Confirmation keeps a highlighted `@Title (year)` exactly where
you typed it, preserving the surrounding sentence and placing the caret after
the mention. Film identity is sent separately from the search description.
The bar stays the same height and scrolls horizontally. Backspace immediately
after a mention or Delete immediately before it removes that whole mention.
Editing inside its title returns it to ordinary text and removes that mention's
filter; another confirmed mention of the same movie keeps that movie selected.
With only movie mentions selected,
browse scenes in source order without a model query. This uses the normal bounded
result prefix; a description searches across the whole selected film, including
later moments. Shot types, subjects, styles and feelings can share one description.

Use the compact **All movies** control to search one movie, several movies, or
the whole library. It shares the same scope as the inline mentions. Choosing a
movie appends its mention; deselecting it removes the @ marker and leaves the
title words as plain text. The picker gains title
search automatically as the library grows. Search uses bounded ranked
candidate and result windows, not a minimum-similarity cutoff, so a globally
uncompetitive movie may be absent; select that movie in **All movies** when
you want the ranking scoped entirely to that movie.
The responsive grid shows at least three complete rows and expands its initial
display to fill the available viewport. **Show more** first reveals the rest of
the current backend-ranked prefix, then asks the backend for a deeper prefix
when more eligible scenes exist. The current progressive window is bounded by
the configured 200-result ceiling; the control does not expose internal batch
counts. There is one result stream: the browser does not discard
returned scenes through a separate Best per movie mode.

An unquoted one-word query in the main bar is treated as a broad concept: its
visual and semantic matches rank without a separate exact-word vote, so an
incidental subtitle occurrence cannot promote a weak result. Quote the word
when its literal occurrence matters; multi-word searches retain full-text
corroboration. Unscoped broad search also uses a deeper bounded candidate pool
and defers nearby scenes from the same film until more of the ranking has been
considered. This 30-second sequence spread never deletes a result. All-movies
broad search and modular recipes without an uploaded-image or Framing
candidate gate also apply a finite diminishing cost to repeated results from
one film. Multiple strong scenes from that film remain eligible; the policy
neither forces one result per movie nor excludes the dragged scene's movie.
The shared caption filter suppresses clearly described empty footage and
unrequested credit/title/logo material. A monitor with text or meaningful dark
imagery is not blank merely because its caption mentions a black screen. This
query-time correction loads at the API's next normal restart; no re-ingestion
or index rebuild is needed.
A movie selection disables cross-film balancing. Main-text and non-image
modular searches then preserve strict relevance order; image and Framing
references keep only their existing same-sequence spread. This improves
discovery without adding another search mode or control.

The main bar remains a broad scene search. The **Match** chips combine up to three
inputs across the main bar and the explicit **Scene**, **Words**, **Look**,
**Framing**, and **Mood** facets. Type into a facet, drag a result onto one,
or use a result's **Related** menu to start over from it. Results supported by
more inputs rise.
Drag a selected scene directly from its category chip to another category to
move that reference, including out of Framing. Dropping onto an occupied category
replaces its clue; moving a reference still works when all three inputs are in use.
Releasing it outside the Refine area removes it; **Undo** restores it.
A thumbnail follows the cursor while the source dims and the destination
highlights. There are no floating instruction labels or drag badges on result
cards. Saved cards do not drag; their **Related** menu remains available.
Open a category to preview its reference and choose **Change aspect**, **Change
scene**, **Use text instead** where supported, or **Remove**. **Change aspect**
provides a keyboard/touch alternative to dragging: choose the destination,
then **Move to…** or **Replace…** if it already has a clue. Selecting or cancelling
does not apply a change. The source/frame identity is preserved, and uploaded
images can move only between Look and Framing. Its editor shows what the
category reads from the scene (**Searching for its words**, and so on): the exact
caption, dialogue/OCR, or mood/energy text it contributes, which the active
chip also shows in short. Look and
Framing sources remain explicitly visual rather than being translated into
invented words. Choosing Framing from the result menu and dragging that result
onto the Framing chip create the same source clause. Framing supplies the
mandatory candidate set; other active parts can rerank it but cannot introduce
visually unrelated shots. Result-source Framing search keeps the cross-film
discovery default to prevent the source movie's style from consuming the
bounded shortlist.
Every category keeps the same footprint across empty, text and reference states.
Each chip opens one compact editor anchored near that category. Empty text
categories open a text field; references initially show only their preview and
actions. **Use text instead** opens the field while keeping the current reference
active until nonempty text is applied. **Keep reference** returns to its preview.
**Change scene** opens a clearly labeled reference lookup.
Reference lookup owns its query/results without changing the main field;
choosing a scene adds that source and reruns the recipe. Closing the editor
leaves the selected clue in the same chip. **Remove** is inside the editor.
Press Enter or **Apply** to use nonempty category text; Enter while composing
text with an IME does not apply an unfinished clue. When the current Framing
shortlist has no result also retrieved by the main description, a compact notice
explains the missing overlap in this result set. All returned scenes and paging
remain visible, alongside **Search without Framing** and **Look deeper** when
available. This does not change Framing's ranking or candidate gate.

The image button and a file dropped on the open search workspace accept one
JPEG, PNG, or WebP and place it in **Look** by default. Dropping directly on
**Look** or **Framing** chooses that meaning immediately. The still appears as
a source card in its category and can be dragged between those two categories
or removed like any other clue. **Look** retrieves by global PE appearance;
**Framing** uses the same global candidates plus the bounded 6x6 spatial-layout
reranker. Either uploaded-image ranking is mandatory, while up to two optional
text or indexed-scene clues may rerank it without introducing unrelated shots.
An uploaded Look image has its own persistent explanation and **Search without
Look image** action, just as Framing explains its visual restriction. An indexed
Look reference remains an ordinary relevance preference.

The image exists only for the request, is never added to the library, and never
becomes invented Scene, Words, or Mood evidence. Supporting images in those
categories would require an explicit, versioned caption/OCR/mood adapter rather
than silently pretending that the current visual models provide that meaning.

Look and Framing use the existing local frame index; Framing adds a
learned 6x6 spatial feature grid. The source-hashed optional cache reuses valid
candidate grids and encodes only missing or changed entries. Live and cached
grids use the same float16 numerical contract. Legacy complete caches remain
readable during migration. Neither path calls OpenAI or Gemini or requires re-ingestion. Treat
Framing as coarse framing and position similarity, not exact
subject-relation, skeletal-pose, or motion matching.

Optional search preparation uses the existing ingest worker, with one durable
job per film and 32-frame batches behind foreground work. New ingestion queues
preparation after successful publication. A paused job does not affect whether
the film is searchable. These commands inspect by default unless stated:

```powershell
uv run python -m pipeline.cli search-features storage
uv run python -m pipeline.cli search-features queue
uv run python -m pipeline.cli search-features indexes --apply
uv run python -m pipeline.cli search-features prepare --all-films --enqueue
uv run python -m pipeline.cli search-features fit-composition --apply
uv run python -m pipeline.cli search-features fit-composition --enqueue
uv run python -m pipeline.cli search-features prepare --all-films --composition-profile PROFILE_ID --enqueue
uv run python -m pipeline.cli search-features benchmark --output measurements.json
```

`fit-composition --apply` is an explicit local GPU/CPU experiment; run it while
ingestion is idle. It saves frozen 32/64-dimensional challengers and never
activates them. Alternatively `--enqueue` fits after foreground ingestion and
cache preparation, then queues one compact-feature job per film and challenger.
Fitting cancellation retains already-completed immutable profiles; retry may
repeat local fitting but cannot replay hosted work. `benchmark --ann` builds an isolated IVF_FLAT trial, counts
scratch against storage, then removes its own scratch. Production vector
indexes remain unchanged. Benchmarks report retrieval-stage time, not end-to-end
search latency. Use `review --profile ID --references references.json --output
comparison.json` for twelve distinct `frame_id` records (optional `film_ids`
scope), then supply actual human preferences and measured relevance. `promote
--receipt comparison.json` validates the acceptance gates; select the accepted
identity with `retrieval.composition_profile` and reload services. It defaults
to null; null rolls back. Incomplete profile coverage always uses baseline
Framing, with a server diagnostic.

To hold the current optional preparation batch while keeping film ingestion and
the editor available, run `uv run python -m pipeline.lab.worker --role ingest
--stop`, wait for the active batch to finish, then run `uv run python -m
pipeline.cli search-features pause`. Restart the ingest worker with `uv run
python -m pipeline.lab.worker --role ingest`. The pause preserves completed
features and each job's cursor, and survives restarts. `search-features queue`
shows held jobs; `search-features resume` releases only these explicit holds,
without retrying unrelated failed or storage-blocked jobs. New preparation
requests are not globally disabled. A duplicate request for a held job keeps its
hold until `resume`.

Before resuming a bulk build, the isolated
[framing representation pilot](docs/experiments/framing-representation-pilot.md)
provides commands to compare frozen PE final/intermediate features and an
explicitly staged small PE-Spatial checkpoint on 524 retained frames. It writes
only experiment artifacts, checkpoints every 32 frames, respects the ingest
lock and never activates a search profile. Human relevance remains a separate
gate from successful extraction or faster inference.

`retrieval.optional_storage_gib` defaults to 64. Physical optional storage,
including retained versions and scratch, counts toward admission. Repeating
the same `prepare --enqueue` request explicitly retries blocked/interrupted
work without creating a job for each batch. After stopping the API and both
workers, `search-features discard-cache --table EXACT_TABLE --apply` reclaims
an identified full spatial cache using Lance's API. It cannot remove source or
compact retrieval tables. Use the documented idle database maintenance command
to prune eligible historical versions. Queries never perform disk cleanup.
After choosing a winning profile, `search-features retire-profile --profile ID
--apply` reclaims an explicitly selected inactive challenger under the same idle
guards. It refuses the configured profile and profiles with pending preparation.

Hover a result and use its bookmark action to save that source moment. Saved
scenes persist in `paths.state_dir` independently of search-index repair or a
compatible reingest; the same action is available in the player, and
unavailable scenes remain listed until explicitly removed.

## Optional retrieval comparison

```bash
uv run python -m pipeline.eval.experiment run \
  --queries pipeline/eval/fallen_angels_queries.yaml \
  --variants pipeline/eval/variants.yaml \
  --output pipeline/eval/runs/fa-001.yaml
uv run python -m pipeline.eval.experiment score pipeline/eval/runs/fa-001.yaml
```

Use `pipeline/eval/compositional_queries.yaml` with the same command to compare
paired left/right, foreground/background, subject-relation, and negative-space
prompts across the whole indexed library.

The experiment runner records code/config/corpus/index provenance, separates
warmup from measured latency, runs true hybrid/image/text/lexical ablations,
and pools their candidates with blank 0–3 human grades. It never invents
relevance labels: quality metrics remain unavailable until a complete pooled
query is human-judged. Pass `--judgments-from` on later runs to preserve only
judgments whose query, unit, film, and source timestamps still match.
Machine-owned snapshot evidence is checksummed while grade/flags/note remain
editable. Baseline runs require a clean Git worktree; `--allow-dirty` exists
only for explicitly non-reproducible diagnostics. Local run snapshots live in
the ignored `pipeline/eval/runs/` directory; later snapshots record the hash of
their `--judgments-from` input without treating human grading as a code change.
Use `--limit 100` for ordinary candidate-recall experiments, or raise it up to
the configured 200-result ceiling when deeper recall is the subject of the
evaluation. This controls the recorded ranking independently of the UI's
viewport-responsive display batches.

The frozen shadow Match Cut scorer (`pipeline.eval.match_cut`) is described in
ADR-0008; Match Cuts itself is judged by playing cuts (ADR-0099).

## Source context pilot (frozen)

The understanding pass supersedes this pilot for story context. Its build
(`python -m pipeline.context.build`, a dry run unless `--execute`) and
`lab.context_profile` still work; read ADR-0066 and this section's Git history
before using them.

## Match Cuts

Open **Labs → Match Cuts** (`/match`) and choose a shot. Every instant of every
indexed film is compared against the frame you cut from, and results arrive in
under a second ([ADR-0099](docs/decisions/0099-moment-match-cuts.md)).

- **Cut from**: scrub to the last frame before the cut. It snaps to the four
  indexed instants per second.
- **Format**: 16:9 keeps the whole picture. 9:16 and 1:1 crop a window of that
  shape around each subject.
- **Match on**: *Best match*, *Subject*, *Shape & pose*, *Motion*,
  *Composition* or *Colour*.
- **Reframe to align** zooms the next shot up to 1.5x so its subject (a
  person's eyes) lands on the same spot.
- **Other scenes of this film** allows same-film results, never from the same
  scene.

Each result shows the incoming frame through its crop, with reasons such as
*Same pose*, *Eye line carries over* or *Movement continues left*. Hover to lay
the outgoing frame over it. Click to audition: the cut plays in the browser
(outgoing 1.5 s, incoming 2 s, looped), with an **Overlay** view and ±1 frame /
±¼ s nudges for the incoming first frame.

**Add to chain & continue** keeps the cut and searches onward from the new
shot, so you can build a match-cut sequence. **Play chain** plays it back to
back. **Undo last** steps back. The chain lives in this browser session. Nothing
here creates a project.

New films get the moments pass after ingestion, and the index rebuilds when
one ran. For the existing library, or after a producer change:

```powershell
uv run python -m pipeline.evidence moments [--film "Title"]   # GPU, ~4 min per film; skips current films
uv run python -m pipeline.matching.moments index              # a few minutes; the API picks it up on the next search
uv run python -m pipeline.matching.moments search <unit_id> <seconds> [--focus subject|shape|motion|composition|color] [--reframe] [--same-film]
```

The API boundary is `/matching/moments`:
- `GET /status`;
- `GET /shot?unit_id=` (bounds and indexed instants);
- `POST /search` (reference, direction, focus, output, reframe, outgoing crop,
  film scope, same-film, excluded shots, minimum footage);
- `GET /frame` (cached stills of the content picture).

Limits:
- Cut points are exact to a quarter second. The browser audition can be a
  frame off; renders use exact source times.
- Conceptual rhymes, such as 2001's bone into satellite, are not found by
  geometry.

## Saved Match Cuts projects (frozen)

Older Match Cuts projects open the frozen prepared-cohort editor at
`/lab/visual-rhymes` (ADR-0027, ADR-0038). Its preparation commands live in
`pipeline/matching/prepare.py` and `pipeline/matching/prepare_subjects.py`. New
work uses `/match` (above).

