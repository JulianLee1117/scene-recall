# scene-recall

Semantic search over your film library: describe a scene in plain English and get the matching shots back.

The implemented system, architectural boundaries, and deliberately deferred
directions are documented in
[docs/search-architecture.md](docs/search-architecture.md). Historical reasons
for material choices are indexed in
[docs/decisions/](docs/decisions/README.md).

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

# Configure the web frontend
cd web
npm install
cd ..
cp web/.env.local.example web/.env.local
# Edit web/.env.local if your API runs on a non-default port

# Edit config.yaml with your paths and model preferences
```

`paths.state_dir` stores durable user-authored state such as saved scenes. Keep
it outside the replaceable `assets_dir` and include it in normal backups. Older
configs default it to a `state` directory beside `films`.

The default annotator uses OpenAI `gpt-5.6-luna`. Annotation sends up to three
derived keyframes per shot with response storage disabled. Set
`models.annotator_provider: gemini` and a Gemini model name in `config.yaml`
to use Gemini instead. The OpenAI API project must have active billing or
credits; a ChatGPT subscription does not supply API quota.

## Run

Start the API server and the Next.js dev server in separate terminals:

```bash
# Terminal 1 — FastAPI backend
uv run uvicorn pipeline.api.main:app --host 127.0.0.1 --port 8000

# Terminal 2 — Next.js frontend
cd web && npm run dev
```

On Windows PowerShell, use `npm.cmd run dev` if the execution policy blocks
`npm.ps1`.

The ingestion queue lives in the API process. Keep the backend running without
`--reload` while jobs are active; a backend reload discards queued job status.
Use `--reload` only for backend development when no ingestion is queued.

Open [http://localhost:3000](http://localhost:3000) in your browser.

The API accepts browser requests from `http://localhost:3000` and
`http://127.0.0.1:3000` by default. If the frontend runs elsewhere, set the
comma-separated `SCENE_RECALL_ALLOWED_ORIGINS` value in `.env`.

## Ingest a film

### Raw source workflow

Keep active downloads separate from finalized source files:

- `V:/scene-recall/incoming/` — downloading or seeding; never ingest here.
- `V:/scene-recall/films/` — completed, canonically named, immutable sources.

In Windows Disk Management, reserve `V:` for this drive and confirm it is
mounted before starting the API or an ingest. Create `incoming` once and set it
as the torrent client's download directory. Keep each finalized movie file
directly inside `films` because source discovery is not recursive.

Download and seed in `incoming`. After seeding is finished and the torrent is
removed, open **Films** in the frontend. **Review & add** suggests the main
video in each release (the largest supported file), a title, year, and final
`Title (Year) [Edition].ext` filename. Confirm that downloading/seeding has
finished, then add it to the library and optionally queue ingestion. The move
is instant because `incoming` and `films` are on the same drive. Other videos
inside that release folder are left in place and are not offered as films.
When a release contains one usable English-marked SRT clearly associated with
the selected feature, import also preserves it beside the canonical film as
`Title (Year) [Edition].en.srt`; the original release copy remains in
`incoming` as raw evidence. If no SRT is safe to select automatically but one
or more usable candidates remain—either filename-associated with the feature
or carrying a generic English label inside its matching release—**Review &
add** shows a short dialogue preview and requires choosing one as English or
explicitly skipping subtitles before import. Multiple candidates remain
separate choices; the backend rechecks the selection before moving the film.
Skipped, malformed, oversized, trivial, promo-only, forced, commentary,
extra-associated, or
foreign-marked SRTs stay in the release and are not promoted to canonical
dialogue evidence.

Ingestion is FIFO and runs one film at a time in an isolated, low-priority
child process. The Films screen polls only while work is active; completed
jobs remain visible until the API restarts. A separate CLI ingest fails with a
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
Dialogue prefers a usable canonical English SRT sidecar, then a convertible
embedded subtitle stream, then local Whisper. Canonical sidecars receive the
same non-destructive content check, so an already-present malformed, oversized,
trivial, or promo-only file is ignored rather than cached as dialogue. The
cache manifest includes the sidecar hash, embedded stream identity, or Whisper
model and transcription profile so changed evidence is rebuilt instead of
silently reusing stale text.

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

Hosted annotation requests run concurrently within a film; tune
`ingest.annotation_concurrency` in `config.yaml` (default 8).

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

Once more than one film is indexed, use the compact **All movies** control to
search one movie, several movies, or the whole library. The picker gains title
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
A movie selection disables cross-film balancing. Main-text and non-image
modular searches then preserve strict relevance order; image and Framing
references keep only their existing same-sequence spread. This improves
discovery without adding another search mode or control.

The main bar remains a broad scene search. **Match by** combines up to three
inputs across the main bar and the explicit **Scene**, **Words**, **Look**,
**Framing**, and **Mood** facets. Type into a facet, drag a result onto one,
or use a result's single **Match by…** menu. Results supported by more inputs
rise.
Open a dragged source's compact info control (shown on hover or focus) to see
the exact caption, dialogue/OCR, or mood/energy text it contributes. Look and
Framing sources remain explicitly visual rather than being translated into
invented words. Choosing Framing from the result menu and dragging that result
onto the Framing tile create the same source clause. Framing supplies the
mandatory candidate set; other active parts can rerank it but cannot introduce
visually unrelated shots. Result-source Framing search keeps the cross-film
discovery default to prevent the source movie's style from consuming the
bounded shortlist.
Each facet's scene-reference icon temporarily turns the existing main field into
an independent broad search without moving the workspace or changing the current
recipe. Clear its compact mode chip to return unchanged; choosing a scene adds
that source and reruns the combined recipe. Press Enter in a facet's text editor
to run the current recipe with that explicit constraint.

The image button and a file dropped on the open search workspace accept one
JPEG, PNG, or WebP and place it in **Look** by default. Dropping directly on
**Look** or **Framing** chooses that meaning immediately. The still appears as
a source card in its category and can be dragged between those two categories
or removed like any other clue. **Look** retrieves by global PE appearance;
**Framing** uses the same global candidates plus the bounded 6x6 spatial-layout
reranker. Either uploaded-image ranking is mandatory, while up to two optional
text or indexed-scene clues may rerank it without introducing unrelated shots.

The image exists only for the request, is never added to the library, and never
becomes invented Scene, Words, or Mood evidence. Supporting images in those
categories would require an explicit, versioned caption/OCR/mood adapter rather
than silently pretending that the current visual models provide that meaning.

Look and Framing use the existing local frame index; Framing adds a
learned 6x6 spatial feature grid. A complete optional Framing cache removes
candidate-image encoding from the interactive query; missing, stale, or
partial cache data falls back for the whole query to the established live
reranker. Neither path calls OpenAI or Gemini or requires re-ingestion. Treat
Framing as coarse framing and position similarity, not exact
subject-relation, skeletal-pose, or motion matching.

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

To score a Match Cut matcher after it has written a gate-by-gate ranked-results
document, run:

```bash
uv run python -m pipeline.eval.match_cut score \
  --cases pipeline/eval/match_cut_cases.yaml \
  --rankings pipeline/eval/runs/match-cut-shadow.json \
  --output pipeline/eval/runs/match-cut-shadow-score.json
```

The scorer validates matcher, corpus, profile, vector-space, and gate lineage,
then reports known-positive recall, hard-negative retrieval, criterion-specific
ordering, and positives lost or gained between gates. It consumes rankings
only: it does not run a model, combine vector scores, invent judgments, or
treat ungraded candidates as negatives. The initial cases are diagnostic seeds
and must be expanded and human-graded before Match Cut activation.
